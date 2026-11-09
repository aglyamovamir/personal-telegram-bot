"""Only temporary SQLite databases, fake Telegram and in-memory PNGs."""
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from aiogram.methods import SendMessage, SendPhoto
from database import db, programs, sessions
from database import analytics as facts
from workouts import analytics_presentation as view
from workouts.charts import render_chart
from test_database import TemporaryDatabaseTestCase
from test_handlers import TelegramTestCase
from program_fixtures import seed_catalog, import_legacy_program


class AnalyticsDatabaseTests(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        seed_catalog()
        import_legacy_program(self.user_id)
        self.friend = db.get_or_create_user(222, 222)['id']

    def workout(self, day='A', sets=((50, 10, False),), *, user=None,
                exercise='bench-press', started='2026-11-01T12:00:00+03:00', completed=True):
        user = user or self.user_id
        wid = db.create_workout(user, day)
        for i, (weight, reps, warmup) in enumerate(sets, 1):
            db.save_set(user, wid, 'Recorded name', i, weight, reps, warmup, exercise)
        if completed:
            db.finish_workout(user, wid)
        with db._connection(True) as c:
            c.execute('UPDATE workouts SET started_at=?,finished_at=? WHERE id=? AND user_id=?',
                      (started, '2026-11-01T13:00:00+03:00' if completed else None, wid, user))
        return wid

    def test_calculations_ignore_warmup_keep_zero_fractional_and_never_double(self):
        wid = self.workout(sets=((100, 20, True), (16, 10, False), (16, 10, False),
                                 (16, 10, False), (0, 8, False), (16.5, 4, False)))
        m = facts.load(self.user_id)['records'][wid]['metrics']
        self.assertEqual(m['sets'], 5)
        self.assertEqual(m['volume'], Decimal(546))
        self.assertEqual(m['reps'], 42)
        self.assertEqual(m['maximum'], Decimal('16.5'))
        self.assertEqual(m['maximum_reps'], [4])

    def test_all_maximum_weight_repetitions_remain_visible(self):
        wid = self.workout(sets=((60, 10, False), (70, 8, False), (70, 7, False), (70, 6, False)))
        m = facts.load(self.user_id)['records'][wid]['metrics']
        self.assertEqual(m['maximum_reps'], [8, 7, 6])

    def test_invalid_numbers_not_counted_and_warned_without_modifying_facts(self):
        occurrences = [dict(sets=[dict(weight='NaN', reps=8, is_warmup=False),
                                 dict(weight=-5, reps=8, is_warmup=False),
                                 dict(weight=10, reps=0, is_warmup=False),
                                 dict(weight=0, reps=12, is_warmup=False)])]
        metrics = facts.measures(occurrences)
        self.assertEqual(metrics['invalid'], 3)
        self.assertEqual(metrics['sets'], 1)
        self.assertEqual(metrics['reps'], 12)
        self.assertEqual(metrics['volume'], 0)

    def test_period_all_keeps_large_history_and_labels_do_not_change_values(self):
        wid = self.workout()
        data = facts.load(self.user_id)
        record = data['records'][wid]
        records = [{**record, 'id': i} for i in range(250)]
        self.assertEqual(len(facts.period_records(records)), 250)
        exercise = facts.available_exercises(data)[0]
        summary = view.exercise_summary({**exercise, 'equipment': 'dumbbell'}, records)
        self.assertIn('Вес одной гантели', summary)
        self.assertIn('Тренировок: 250', summary)
        self.assertEqual(record['metrics']['volume'], 500)

    def test_zero_baseline_never_divides_by_zero_or_calls_work_absent(self):
        old = self.workout(sets=((0, 12, False),))
        wid = self.workout(sets=((10, 10, False),), started='2026-11-08T12:00:00+03:00')
        data = facts.load(self.user_id)
        current, previous = facts.breakdown(data, wid)
        self.assertEqual(previous['id'], old)
        self.assertIn('раньше было 0', view.summary(current, previous))
        self.assertEqual(previous['metrics']['sets'], 1)
        self.assertNotIn('100%', view.summary(current, previous))

    def test_latest_same_day_by_date_not_id_or_another_day(self):
        first = self.workout(started='2026-10-01T12:00:00+03:00')
        latest = self.workout(started='2026-11-08T12:00:00+03:00')
        backdated = self.workout(started='2026-10-20T12:00:00+03:00')
        self.workout('C', started='2026-11-09T12:00:00+03:00')
        current, previous = facts.breakdown(facts.load(self.user_id), latest)
        self.assertEqual((current['id'], previous['id']), (latest, backdated))
        self.assertNotEqual(previous['id'], first)

    def test_isolation_active_foreign_ids_and_null_legacy_ids(self):
        own = self.workout(exercise=None)
        other = self.workout(user=self.friend, exercise='squat')
        active = self.workout(completed=False)
        data = facts.load(self.user_id)
        self.assertEqual(set(data['records']), {own})
        self.assertEqual(facts.available_exercises(data), [])
        for wid in (other, active, 99999):
            with self.assertRaises(db.WorkoutAccessError):
                facts.breakdown(data, wid)
        self.assertEqual(data['records'][own]['metrics']['sets'], 1)

    def test_same_day_name_other_program_is_separate_and_snapshot_stays(self):
        old = self.workout()
        pid = programs.create_program(self.user_id, 'Another', [dict(name='A', exercises=[dict(exercise_id='bench-press')])], activate=True)
        session = sessions.start_day(self.user_id)
        sessions.complete_exercise(self.user_id, session['workout_id'], session['revision'],
                                   [dict(weight=80, reps=8, is_warmup=False)])
        data = facts.load(self.user_id)
        self.assertNotEqual(facts.group_for(data, old)['key'], facts.group_for(data, session['workout_id'])['key'])
        self.assertIsNone(facts.breakdown(data, session['workout_id'])[1])
        programs.delete_program(self.user_id, pid)
        data = facts.load(self.user_id)
        self.assertIn(session['workout_id'], data['records'])

    def test_repeated_occurrences_separate_in_report_one_point_per_session(self):
        wid = self.workout()
        with db._connection(True) as c:
            c.execute('INSERT INTO workout_sets(workout_id,exercise,exercise_id,set_number,is_warmup,weight,reps,created_at) VALUES (?,?,?,?,?,?,?,?)',
                      (wid, 'Recorded name', 'bench-press', 1, 0, 60, 8, '2026-11-01'))
        data = facts.load(self.user_id)
        current, _ = facts.breakdown(data, wid)
        self.assertEqual(len(current['occurrences']), 2)
        self.assertIn('включение 2', view.comparison(current, None))
        exercise = facts.available_exercises(data)[0]
        records = facts.exercise_records(data, exercise)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['metrics']['volume'], 980)

    def test_changed_composition_and_maximum_does_not_claim_strength(self):
        self.workout()
        wid = self.workout(exercise='squat', sets=((70, 3, False),), started='2026-11-08T12:00:00+03:00')
        current, previous = facts.breakdown(facts.load(self.user_id), wid)
        self.assertIn('Состав тренировки изменился', view.summary(current, previous))
        comparison = view.comparison(current, previous)
        self.assertIn('Новая запись', comparison)
        self.assertIn('не записано', comparison)
        self.assertNotIn('стал сильнее', comparison)

    def test_calendar_periods_timezone_boundary_and_invalid_dates(self):
        boundary = self.workout(started='2026-08-08T22:00:00+00:00')
        before = self.workout(started='2026-08-08T20:00:00+00:00')
        invalid = self.workout(started='bad-date')
        records = list(facts.load(self.user_id)['records'].values())
        filtered = facts.period_records(records, 3, now=datetime(2026, 11, 9, tzinfo=timezone.utc), timezone_name='Europe/Moscow')
        self.assertEqual([r['id'] for r in filtered], [boundary])
        self.assertEqual(len(facts.period_records(records)), 3)
        with self.assertRaises(ValueError):
            facts.period_records(records, 7)
        current, previous = facts.breakdown(facts.load(self.user_id), invalid)
        self.assertIsNone(previous)
        self.assertIsNone(view.elapsed(current))

    def test_weight_labels_and_readonly_original_records(self):
        wid = self.workout()
        with db._connection() as c:
            before = c.execute('SELECT * FROM workout_sets').fetchall()
        data = facts.load(self.user_id)
        exercise = facts.available_exercises(data)[0]
        self.assertEqual(facts.weight_label({**exercise, 'equipment': 'dumbbell'}), 'Вес одной гантели')
        self.assertEqual(facts.weight_label({**exercise, 'external': 'weighted-dips'}), 'Дополнительный вес')
        facts.breakdown(data, wid)
        with db._connection() as c:
            self.assertEqual(c.execute('SELECT * FROM workout_sets').fetchall(), before)

    def test_chart_png_all_points_and_missing_dates(self):
        from PIL import Image
        self.workout()
        self.workout(started='2026-11-08T12:00:00+03:00', sets=((55, 8, False),))
        self.workout(started='bad')
        data = facts.load(self.user_id)
        records = list(data['records'].values())
        with TemporaryDirectory() as directory, patch.dict('os.environ', {'MPLCONFIGDIR': directory, 'MPLBACKEND': 'Agg'}):
            picture = render_chart(records, 'Условные данные', exercise_label='Вес одной гантели')
            self.assertTrue(picture.startswith(b'\x89PNG\r\n\x1a\n'))
            with Image.open(BytesIO(picture)) as image:
                self.assertGreater(image.height, 800)  # weight first, volume second
                self.assertGreater(image.width, 1000)
            self.assertIsNone(render_chart([r for r in records if r['date'] is None], 'Нет дат'))


class AnalyticsHandlerTests(TelegramTestCase):
    def workout(self, day='A', *, user=None, exercise='bench-press', weight=50):
        uid = user or self.uid
        wid = db.create_workout(uid, day)
        db.save_set(uid, wid, 'Personal recorded exercise', 1, weight, 8, False, exercise)
        db.finish_workout(uid, wid)
        return wid

    def texts(self):
        return '\n'.join((r.text if isinstance(r, SendMessage) else r.caption or '')
                         for r in self.session.requests if isinstance(r, (SendMessage, SendPhoto)))

    async def test_menu_aliases_and_active_fsm_unchanged(self):
        await self.click(111, 'nextday')
        before = await self.context(111).get_data()
        current = await self.context(111).get_state()
        for command in ('/analytics', '/today', '📊 Аналитика', '📊 Сегодня'):
            await self.send(111, command)
            self.assertIn('Прогресс по упражнениям', str(self.session.requests[-1].reply_markup))
            self.assertEqual(await self.context(111).get_data(), before)
            self.assertEqual(await self.context(111).get_state(), current)
        await self.click(111, 'an:search')
        self.assertEqual(await self.context(111).get_state(), current)
        await self.send(111, '50 8')
        self.assertEqual(len(db.get_workout_sets(self.uid, before['workout_id'])), 1)

    async def test_breakdown_has_exactly_two_buttons_foreign_id_denied(self):
        wid = self.workout()
        await self.click(111, f'an:report:{wid}')
        message = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        buttons = [b.text for row in message.reply_markup.inline_keyboard for b in row]
        self.assertEqual(buttons, ['График тренировок A', 'Назад'])
        self.session.requests.clear()
        await self.click(222, f'an:report:{wid}')
        self.assertNotIn('Personal recorded exercise', self.texts())
        self.assertIn('недоступна', self.texts())

    async def test_exercises_only_owned_history_search_and_foreign_catalog_id(self):
        self.workout()
        self.workout(user=self.friend, exercise='squat')
        await self.click(111, 'an:list:0')
        controls = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage)).reply_markup
        self.assertIn('Жим', str(controls))
        self.assertNotIn('Присед', str(controls))
        with db._connection() as c:
            foreign_eid = c.execute("SELECT id FROM exercises WHERE external_id='squat'").fetchone()[0]
        await self.click(111, f'an:ex:{foreign_eid}:0')
        self.assertIn('нет записанных', self.texts())
        await self.click(111, 'an:search')
        await self.send(111, 'жим')
        self.assertIn('Поиск: жим', self.texts())
        self.assertIsNone(await self.context(111).get_state())

    async def test_completion_automatic_photo_and_single_report_link(self):
        await self.click(222, 'nextday')
        await self.send(222, '50 8')
        await self.send(222, '60 8')
        self.assertIsNone(db.get_active_workout(self.friend))
        photos = [r for r in self.session.requests if isinstance(r, SendPhoto)]
        self.assertEqual(len(photos), 1)
        controls = [r.reply_markup for r in self.session.requests if isinstance(r, (SendMessage, SendPhoto)) and r.reply_markup]
        self.assertTrue(any('Подробный разбор' in str(markup) for markup in controls))

    async def test_deleted_workout_does_not_send_chart_and_report_fails_safely(self):
        await self.click(222, 'nextday')
        await self.send(222, '/cancel')
        await self.click(222, self.last_control(222, 'deleteworkout'))
        self.assertFalse(any(isinstance(r, SendPhoto) for r in self.session.requests))
        wid = self.workout()
        from handlers.analytics import send_completion_report
        with patch('handlers.analytics.render_chart', side_effect=RuntimeError('chart failed')):
            await send_completion_report(self.message(111).as_(self.bot), self.uid, wid)
        self.assertIn('Рабочих подходов: 1', self.texts())
        self.assertIsNotNone(db.get_workout(self.uid, wid)[3])

    async def test_empty_stale_malformed_callbacks_and_invalid_period(self):
        for callback in ('an:days:r:0', 'an:ex:999:0', 'an:day:999:0', 'an:sets', 'an:days:r:-1'):
            await self.click(111, callback)
        self.assertIn('Завершённых тренировок пока нет', self.texts())
        wid = self.workout()
        await self.click(111, f'an:day:{wid}:7')
        self.assertIn('Неверный период', self.texts())

    async def test_exit_and_legacy_completion_also_emit_chart(self):
        wid = self.workout()
        active = db.create_workout(self.uid, 'A')
        await self.send(111, '/exit')
        self.assertTrue(any(isinstance(r, SendPhoto) for r in self.session.requests))
        self.assertIsNotNone(db.get_workout(self.uid, active)[3])

    async def test_search_can_leave_by_menu_and_empty_period_has_no_sets_button(self):
        wid = self.workout()
        with db._connection(True) as c:
            c.execute("UPDATE workouts SET started_at='2020-01-01T12:00:00' WHERE id=?", (wid,))
            eid = c.execute("SELECT id FROM exercises WHERE external_id='bench-press'").fetchone()[0]
        await self.click(111, 'an:search')
        await self.send(111, '📚 История')
        self.assertIsNone(await self.context(111).get_state())
        await self.click(111, f'an:ex:{eid}:3')
        self.assertIn('За выбранный период записей нет', self.texts())
        message = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        self.assertNotIn('Все подходы', str(message.reply_markup))

    async def test_period_and_all_sets_navigation_preserve_workout_state(self):
        wid = self.workout()
        with db._connection() as c:
            eid = c.execute("SELECT id FROM exercises WHERE external_id='bench-press'").fetchone()[0]
        await self.click(111, 'nextday')
        before = await self.context(111).get_data()
        for callback in (f'an:ex:{eid}:0', f'an:sets:{eid}:0:0', f'an:day:{wid}:0'):
            await self.click(111, callback)
            self.assertEqual(await self.context(111).get_data(), before)
        self.assertIn('Рабочий: 50 кг × 8', self.texts())

    async def test_finish_button_and_stale_double_press_send_one_report(self):
        await self.click(222, 'nextday')
        control = self.last_control(222, 'finish')
        await self.click(222, control)
        await self.click(222, control)
        self.assertEqual(len([r for r in self.session.requests if isinstance(r, SendPhoto)]), 1)

    async def test_google_network_guard_remains_effective_for_friend_report(self):
        # Export guard is part of the fixture and asserts no gspread authorization.
        with patch.dict('os.environ', {'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': '111'}):
            await self.click(222, 'nextday')
            await self.send(222, '40 8')
            await self.send(222, '60 8')
        self.authorize.assert_not_called()
