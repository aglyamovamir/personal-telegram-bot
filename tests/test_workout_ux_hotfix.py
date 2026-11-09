"""Workout/history UX hotfix: real Dispatcher, temporary SQLite, fake Telegram."""
from copy import deepcopy
from html.parser import HTMLParser
from unittest.mock import patch

from aiogram.methods import SendMessage

from database import db, programs, sessions
from states.workout import WorkoutState
from test_handlers import TelegramTestCase


class CheckedHTML(HTMLParser):
    """Check complete Telegram HTML and retain rendered text/bold spans."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.bold = []
        self.bold_parts = []

    def handle_starttag(self, tag, attrs):
        if tag not in {'b', 'strong', 'i', 'em', 'u', 's', 'code', 'pre', 'blockquote'} or attrs:
            raise AssertionError(f'Unexpected Telegram HTML: {tag}, {attrs}')
        self.stack.append(tag)
        if tag in ('b', 'strong'):
            self.bold_parts.append([])

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError('Broken HTML at message boundary')
        if tag in ('b', 'strong'):
            self.bold.append(''.join(self.bold_parts.pop()))

    def handle_data(self, data):
        self.parts.append(data)
        for span in self.bold_parts:
            span.append(data)

    def result(self, text):
        self.feed(text)
        self.close()
        if self.stack:
            raise AssertionError('Unclosed Telegram HTML at message boundary')
        return ''.join(self.parts)


class WorkoutUXHotfixTests(TelegramTestCase):
    def messages(self, user=111):
        return [request for request in self.session.requests
                if isinstance(request, SendMessage) and request.chat_id == user]

    def last_message(self, user=111):
        return self.messages(user)[-1]

    def rendered(self, request):
        self.assertLessEqual(len(request.text), 4096)
        html = CheckedHTML()
        text = html.result(request.text)
        if '<b>' in request.text:
            self.assertEqual(request.parse_mode, 'HTML')
        return text, html.bold

    def controls(self, request=None):
        request = request or self.last_message()
        return [button for row in getattr(request.reply_markup, 'inline_keyboard', []) for button in row]

    async def press(self, label, *, user=111):
        button = next((button for button in self.controls(self.last_message(user)) if button.text == label), None)
        self.assertIsNotNone(button, f'Missing button: {label}')
        await self.click(user, button.callback_data)
        return button.callback_data

    def create_plan(self, *, day_names=('C', 'A'), names=('Первый жим', 'Присед', 'Второй жим'),
                    ids=('bench-press', 'squat', 'bench-press'), user=None):
        user = self.uid if user is None else user
        days = [dict(name=day, exercises=[dict(exercise_id=external, name=name)
                                        for external, name in zip(ids, names)]) for day in day_names]
        pid = programs.create_program(user, 'Проверка интерфейса', days, activate=True)
        return programs.get_program(user, pid)

    def timestamp(self, user, wid, start, finish=None):
        with db._connection(True) as connection:
            connection.execute('UPDATE workouts SET started_at=?,finished_at=? WHERE id=? AND user_id=?',
                               (start, finish, wid, user))

    def legacy(self, *, day='C', rows=None, start='2026-11-08T12:00:00+03:00',
               finish='2026-11-08T13:00:00+03:00', user=None):
        user = self.uid if user is None else user
        wid = db.create_workout(user, day)
        rows = rows if rows is not None else [('Старый жим', 'bench-press', False, 50, 8)]
        for number, (name, external, warmup, weight, reps) in enumerate(rows, 1):
            db.save_set(user, wid, name, number, weight, reps, warmup, exercise_id=external)
        if finish is not None:
            db.finish_workout(user, wid)
        self.timestamp(user, wid, start, finish)
        return wid

    def completed_snapshot(self, plan, *, day=0, sets=None,
                           start='2026-11-08T12:00:00+03:00', finish='2026-11-08T13:00:00+03:00', user=None):
        user = self.uid if user is None else user
        session = sessions.start_day(user, plan['days'][day]['id'])
        wid = session['workout_id']
        sets = sets if sets is not None else [[dict(weight=50, reps=8, is_warmup=False)]
                                             for _ in plan['days'][day]['exercises']]
        for actual in sets:
            if session is None:
                break
            session = sessions.complete_exercise(user, wid, session['revision'], actual)
        if session is not None:
            db.finish_workout(user, wid)
        self.timestamp(user, wid, start, finish)
        return wid

    async def open_history(self, *, day='C', program=None):
        await self.send(111, '📚 История')
        groups = [group for group in db.get_history_days(self.uid)
                  if group['name'] == day and (program is None or group['program'] == program)]
        self.assertEqual(len(groups), 1)
        callback = 'hday:' + groups[0]['key']
        self.assertIn(callback, [button.callback_data for button in self.controls()])
        before = len(self.messages())
        await self.click(111, callback)
        return self.messages()[before:]

    async def begin(self, plan, day=0):
        await self.send(111, '🏋️ Тренировка')
        before = len(self.messages())
        callback = next(button.callback_data for button in self.controls()
                        if button.text == plan['days'][day]['name'])
        await self.click(111, callback)
        self.assertIsNotNone(db.get_active_workout(self.uid))
        return db.get_active_workout(self.uid)[0], self.messages()[before:]

    def assert_no_hint(self, request):
        self.assertNotIn('59 10 3', request.text)
        self.assertNotIn('Разминку отделите пустой строкой', request.text)
        self.assertNotIn('Отправьте ВСЕ', request.text)
        self.assertNotIn('ОДНИМ сообщением', request.text)

    async def test_01_history_day_opens_one_compact_actual_card_with_bold_and_no_extra_controls(self):
        self.legacy(rows=[('Жим ногами', 'bench-press', True, 40, 12),
                          *[('Жим ногами', 'bench-press', False, 150, 12)] * 3],
                    start='2026-10-31T14:28:00+03:00', finish='2026-10-31T15:55:00+03:00')
        cards = await self.open_history()
        self.assertEqual(len(cards), 1)
        plain, bold = self.rendered(cards[0])
        self.assertTrue(plain.startswith('📊 Последняя тренировка C'))
        self.assertIn('Начало: 31.10.2026 14:28', plain)
        self.assertIn('Окончание: 31.10.2026 15:55', plain)
        self.assertIn('Жим ногами', bold)
        self.assertIn('Разминка: 40 кг × 12', plain)
        self.assertIn('Рабочий: 150 кг × 12 × 3', plain)
        self.assertNotIn('История тренировок', plain)
        self.assertNotIn('План', plain)
        self.assertEqual(self.controls(cards[0]), [])

    async def test_02_history_keeps_newest_empty_completed_record_and_excludes_active(self):
        self.legacy(rows=[('Фактически записано', None, False, 41, 7)])
        older_cards = await self.open_history()
        self.assertIn('Фактически записано', self.rendered(older_cards[0])[0])
        self.legacy(rows=[], start='2026-11-09T12:00:00+03:00', finish='2026-11-09T13:00:00+03:00')
        self.legacy(rows=[('Активная запись', None, False, 999, 1)],
                    start='2026-11-10T12:00:00+03:00', finish=None)
        cards = await self.open_history()
        plain = '\n'.join(self.rendered(card)[0] for card in cards)
        self.assertIn('09.11.2026 12:00', plain)
        self.assertIn('Подходы не записаны.', plain)
        self.assertNotIn('Фактически записано', plain)
        self.assertNotIn('Активная запись', plain)
        self.assertNotIn('999', plain)

    async def test_03_history_last_is_selected_by_instant_not_raw_iso_text_or_id(self):
        self.legacy(rows=[('Later instant', None, False, 61, 8)],
                    start='2026-11-07T22:30:00+00:00', finish='2026-11-07T23:30:00+00:00')
        self.legacy(rows=[('Earlier instant', None, False, 62, 8)],
                    start='2026-11-08T00:20:00+14:00', finish='2026-11-08T01:20:00+14:00')
        cards = await self.open_history()
        plain = self.rendered(cards[0])[0]
        self.assertIn('Later instant', plain)
        self.assertNotIn('Earlier instant', plain)
        self.assertIn('08.11.2026 01:30', plain)

    async def test_04_legacy_history_without_snapshot_or_exercise_id_shows_only_saved_facts(self):
        wid = self.legacy(rows=[('Название из старой записи', None, True, 0, 10),
                                ('Название из старой записи', None, False, 12.5, 8)])
        before = db.get_workout_sets(self.uid, wid)
        cards = await self.open_history()
        plain, bold = self.rendered(cards[0])
        self.assertIn('Название из старой записи', bold)
        self.assertIn('0 кг × 10', plain)
        self.assertIn('12.5 кг × 8', plain)
        self.assertNotIn('snapshot', plain)
        self.assertNotIn('Необязательный план', plain)
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)

    async def test_05_foreign_history_workout_and_day_callbacks_do_not_disclose_data(self):
        other = self.legacy(user=self.friend, rows=[('Чужое упражнение', None, False, 987, 1)])
        before = len(self.messages())
        await self.click(111, f'history:{other}')
        foreign_groups = db.get_history_days(self.friend)
        foreign = next(group for group in foreign_groups if any(row[0] == other for row in group['workouts']))
        await self.click(111, 'hday:' + foreign['key'])
        visible = '\n'.join(request.text for request in self.messages()[before:])
        self.assertNotIn('Чужое упражнение', visible)
        self.assertNotIn('987 кг', visible)
        self.assertEqual(db.get_workout_sets(self.friend, other)[0][-2:], (987, 1))

    async def test_06_equal_day_names_in_two_programs_keep_separate_history(self):
        first = self.create_plan(day_names=('Одинаковый',), names=('Первый состав',), ids=('bench-press',))
        self.completed_snapshot(first, sets=[[dict(weight=31, reps=8, is_warmup=False)]])
        second = self.create_plan(day_names=('Одинаковый',), names=('Другой состав',), ids=('squat',))
        programs.update_program(self.uid, second['id'], name='Вторая программа')
        second = programs.get_program(self.uid, second['id'])
        self.completed_snapshot(second, sets=[[dict(weight=73, reps=9, is_warmup=False)]])
        await self.send(111, '📚 История')
        groups = [group for group in db.get_history_days(self.uid) if group['name'] == 'Одинаковый']
        self.assertEqual(len(groups), 2)
        self.assertNotEqual(groups[0]['key'], groups[1]['key'])
        self.assertNotEqual(groups[0]['title'], groups[1]['title'])
        before = len(self.messages())
        await self.click(111, 'hday:' + groups[0]['key'])
        plain = self.rendered(self.messages()[before])[0]
        self.assertIn('Первый состав', plain)
        self.assertNotIn('Другой состав', plain)

    async def test_07_training_day_button_starts_directly_with_first_exercise(self):
        plan = self.create_plan()
        wid, messages = await self.begin(plan)
        self.assertEqual(len(messages), 1)
        plain, bold = self.rendered(messages[0])
        self.assertIn('Следующее упражнение — Первый жим.', bold)
        self.assertIn('Выполнено: 0 из 3 · Отложено: 0', plain)
        self.assertNotIn('Начать этот день?', plain)
        self.assertNotIn('Плановые подходы', plain)
        self.assertNotIn('Второй жим', plain)
        self.assertEqual(sessions.get_session(self.uid, wid)['snapshot']['day_id'], plan['days'][0]['id'])
        self.assertEqual(db.get_workout_sets(self.uid, wid), [])

    async def test_08_legacy_preview_callbacks_start_directly_and_cannot_duplicate_workouts(self):
        plan = self.create_plan()
        before = len(self.messages())
        await self.click(111, f"previewday:{plan['days'][1]['id']}")
        self.assertEqual(len(self.messages()[before:]), 1)
        wid = db.get_active_workout(self.uid)[0]
        self.assertEqual(sessions.get_session(self.uid, wid)['snapshot']['day_id'], plan['days'][1]['id'])
        await self.click(111, 'previewnext')
        self.assertEqual(db.get_active_workout(self.uid)[0], wid)
        self.assertEqual(len(db.get_recent_workouts(self.uid)), 0)

    async def test_09_current_card_is_compact_bold_and_progress_comes_from_real_queues(self):
        plan = self.create_plan()
        wid, _ = await self.begin(plan)
        await self.click(111, self.last_control(111, 'postpone'))
        await self.send(111, '50 8')
        current = sessions.get_session(self.uid, wid)
        plain, bold = self.rendered(self.last_message())
        name = current['snapshot']['exercises'][current['progress']['current']]['name']
        self.assertIn(f'Следующее упражнение — {name}.', bold)
        expected = f"Выполнено: {len(current['progress']['completed'])} из 3 · Отложено: {len(current['progress']['postponed'])}"
        self.assertIn(expected, plain)
        self.assertLess(len(plain), 700)
        labels = [button.text for button in self.controls()]
        self.assertEqual(labels, ['Отложить упражнение', 'Исправить предыдущее', 'Завершить тренировку'])
        self.assert_no_hint(self.last_message())

    async def test_10_short_input_hint_appears_only_on_fresh_workout_start(self):
        plan = self.create_plan()
        await self.begin(plan)
        initial = self.rendered(self.last_message())[0]
        self.assertIn('Отправьте подходы одним сообщением: 59 10 3.', initial)
        self.assertIn('Разминку отделите пустой строкой. /help — пример.', initial)
        self.assertNotIn('ВСЕ выполненные подходы', initial)
        await self.send(111, '50 8')
        self.assert_no_hint(self.last_message())
        before = len(self.messages())
        await self.send(111, '/help')
        help_text = '\n'.join(request.text for request in self.messages()[before:])
        self.assertIn('Формат ввода подходов', help_text)
        self.assertIn('Пустая строка', help_text)

    async def test_11_resume_first_step_after_memory_loss_does_not_repeat_initial_hint(self):
        plan = self.create_plan()
        wid, _ = await self.begin(plan)
        await self.context(111).clear()
        await self.send(111, '/resume')
        self.assert_no_hint(self.last_message())
        self.assertEqual((await self.context(111).get_data())['exercise_index'], 0)
        self.assertEqual(db.get_workout_sets(self.uid, wid), [])

    async def test_12_postpone_has_exact_acknowledgment_then_actual_next_step(self):
        plan = self.create_plan()
        wid, _ = await self.begin(plan)
        before = len(self.messages())
        await self.click(111, self.last_control(111, 'postpone'))
        sent = self.messages()[before:]
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].text, 'Упражнение отложено.\n\nИдём дальше по программе.')
        plain, bold = self.rendered(sent[1])
        self.assertIn('Следующее упражнение — Присед.', bold)
        self.assertIn('Выполнено: 0 из 3 · Отложено: 1', plain)
        self.assert_no_hint(sent[1])
        session = sessions.get_session(self.uid, wid)
        self.assertEqual(session['progress']['postponed'], [0])
        self.assertEqual(session['progress']['current'], 1)
        self.assertEqual(db.get_workout_sets(self.uid, wid), [])

    async def test_13_saved_confirmation_uses_snapshot_name_and_one_marked_line_per_set(self):
        plan = self.create_plan(names=('Брусья', 'Присед', 'Жим'))
        wid, _ = await self.begin(plan)
        edited = deepcopy(plan['days'])
        edited[0]['exercises'][0]['name'] = 'Название после старта'
        programs.update_program(self.uid, plan['id'], days=edited)
        before = len(self.messages())
        await self.send(111, '25 15 3')
        sent = self.messages()[before:]
        confirmation = self.rendered(sent[0])[0]
        self.assertTrue(confirmation.startswith('✅ Брусья — подходы сохранены.'))
        self.assertEqual(confirmation.count('💪 25 кг × 15'), 3)
        self.assertNotIn('Название после старта', confirmation)
        self.assertNotIn('× 3', confirmation)
        self.assertEqual(len(db.get_workout_sets(self.uid, wid)), 3)
        self.assert_no_hint(sent[-1])

    async def test_14_confirmation_distinguishes_warmup_working_and_formats_integer_decimal_zero(self):
        plan = self.create_plan()
        wid, _ = await self.begin(plan)
        before = len(self.messages())
        await self.send(111, '0 12\n20.5 10\n\n59 8\n59.5 7\n0 6')
        confirmation = self.rendered(self.messages()[before])[0]
        self.assertIn('🔥 Разминка: 0 кг × 12', confirmation)
        self.assertIn('🔥 Разминка: 20.5 кг × 10', confirmation)
        self.assertIn('💪 59 кг × 8', confirmation)
        self.assertIn('💪 59.5 кг × 7', confirmation)
        self.assertIn('💪 0 кг × 6', confirmation)
        self.assertNotIn('59.0', confirmation)
        self.assertEqual([row[3] for row in db.get_workout_sets(self.uid, wid)], [1, 1, 0, 0, 0])

    async def test_15_previous_result_is_owned_latest_completed_exercise_across_all_days(self):
        old = self.create_plan(day_names=('Другой день',), names=('Старое имя жима',), ids=('bench-press',))
        self.completed_snapshot(old, sets=[[dict(weight=42.5, reps=11, is_warmup=False)]])
        self.legacy(user=self.friend, day='Чужой день', rows=[('Чужой', 'bench-press', False, 999, 1)],
                    start='2026-11-10T12:00:00+03:00', finish='2026-11-10T13:00:00+03:00')
        current = self.create_plan(day_names=('Текущий день',), names=('Тот же жим', 'Ещё жим', 'Присед'),
                                   ids=('bench-press', 'bench-press', 'squat'))
        wid, _ = await self.begin(current)
        first = self.rendered(self.last_message())[0]
        self.assertIn('Прошлый результат · Другой день · 08.11.2026:', first)
        self.assertIn('42.5 кг × 11', first)
        self.assertNotIn('999', first)
        await self.send(111, '88 3')
        second = self.rendered(self.last_message())[0]
        self.assertIn('42.5 кг × 11', second)
        self.assertNotIn('88 кг', second)  # Current unfinished workout is not past.
        self.assertEqual(len(db.get_workout_sets(self.uid, wid)), 1)

    async def test_16_previous_result_orders_aware_dates_and_equal_instant_ties(self):
        self.legacy(day='Более поздняя', rows=[('Жим', 'bench-press', False, 71, 8)],
                    start='2026-11-07T22:30:00+00:00', finish='2026-11-07T23:30:00+00:00')
        self.legacy(day='Ранний instant', rows=[('Жим', 'bench-press', False, 72, 8)],
                    start='2026-11-08T00:20:00+14:00', finish='2026-11-08T01:20:00+14:00')
        plan = self.create_plan()
        wid, _ = await self.begin(plan)
        plain = self.rendered(self.last_message())[0]
        self.assertIn('71 кг × 8', plain)
        self.assertNotIn('72 кг', plain)
        db.finish_workout(self.uid, wid)
        await self.context(111).clear()
        self.legacy(day='Tie newer ID', rows=[('Жим', 'bench-press', False, 73, 8)],
                    start='2026-11-08T01:30:00+03:00', finish='2026-11-08T02:30:00+03:00')
        await self.begin(plan)
        plain = self.rendered(self.last_message())[0]
        self.assertIn('73 кг × 8', plain)
        self.assertIn('Tie newer ID', plain)

    async def test_17_previous_matching_uses_stable_id_despite_names_and_never_guesses_null_id(self):
        self.legacy(day='Точное совпадение ID', rows=[('Прежнее название', 'bench-press', False, 66, 9)])
        self.legacy(day='Совпадает только имя', rows=[('Новое название', None, False, 777, 1)],
                    start='2026-11-09T12:00:00+03:00', finish='2026-11-09T13:00:00+03:00')
        self.legacy(day='Другой ID', rows=[('Новое название', 'squat', False, 888, 1)],
                    start='2026-11-10T12:00:00+03:00', finish='2026-11-10T13:00:00+03:00')
        plan = self.create_plan(names=('Новое название', 'Присед', 'Жим'))
        await self.begin(plan)
        plain = self.rendered(self.last_message())[0]
        self.assertIn('66 кг × 9', plain)
        self.assertNotIn('777', plain)
        self.assertNotIn('888', plain)
        self.assertIn('Точное совпадение ID', plain)

    async def test_18_previous_repeated_occurrences_keep_boundaries_and_every_set(self):
        previous = self.create_plan(day_names=('Повторы',), names=('Жим узкий', 'Жим широкий'),
                                    ids=('bench-press', 'bench-press'))
        self.completed_snapshot(previous, sets=[
            [dict(weight=20, reps=10, is_warmup=True), dict(weight=51, reps=8, is_warmup=False)],
            [dict(weight=62, reps=6, is_warmup=False), dict(weight=62, reps=6, is_warmup=False)]])
        plan = self.create_plan()
        await self.begin(plan)
        plain = self.rendered(self.last_message())[0]
        self.assertIn('Включение №1', plain)
        self.assertIn('Включение №2', plain)
        self.assertIn('20 кг × 10', plain)
        self.assertIn('51 кг × 8', plain)
        self.assertEqual(plain.count('62 кг × 6'), 2)

    async def test_19_without_previous_result_card_shows_the_explicit_empty_placeholder(self):
        plan = self.create_plan()
        await self.begin(plan)
        plain = self.rendered(self.last_message())[0]
        self.assertNotIn('Прошлый результат', plain)
        self.assertIn('Прошлых результатов пока нет.', plain)

    async def test_20_correction_returns_compact_current_card_without_changing_progress_or_cycle(self):
        plan = self.create_plan()
        wid, _ = await self.begin(plan)
        await self.send(111, '50 8')
        await self.click(111, self.last_control(111, 'postpone'))
        before = sessions.get_session(self.uid, wid)
        cycle = programs.next_day(self.uid)
        await self.click(111, self.last_control(111, 'fix'))
        await self.send(111, '52.5 9 2')
        after = sessions.get_session(self.uid, wid)
        self.assertEqual(after['progress'], before['progress'])
        self.assertEqual(after['snapshot'], before['snapshot'])
        self.assertEqual(programs.next_day(self.uid), cycle)
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assert_no_hint(self.last_message())
        plain, bold = self.rendered(self.last_message())
        self.assertIn('Следующее упражнение — Второй жим.', bold)
        self.assertIn('Выполнено: 1 из 3 · Отложено: 1', plain)
        self.assertEqual(sessions.get_occurrence_sets(self.uid, wid, 0)[0][-2:], (52.5, 9))

    async def test_21_history_previous_and_unfinished_use_one_user_timezone(self):
        with db._connection(True) as connection:
            connection.execute("UPDATE users SET timezone='Asia/Yekaterinburg' WHERE id=?", (self.uid,))
        self.legacy(rows=[('Результат', 'bench-press', False, 45, 9)],
                    start='2026-11-08T23:30:00+00:00', finish='2026-11-09T00:15:00+00:00')
        cards = await self.open_history()
        plain = self.rendered(cards[0])[0]
        self.assertIn('09.11.2026 04:30', plain)
        self.assertIn('09.11.2026 05:15', plain)
        plan = self.create_plan()
        wid, _ = await self.begin(plan)
        self.assertIn('Прошлый результат · C · 09.11.2026:', self.rendered(self.last_message())[0])
        self.timestamp(self.uid, wid, '2026-11-08T23:30:00+00:00')
        await self.send(111, '🏋️ Тренировка')
        self.assertIn('09.11.2026 04:30', self.rendered(self.last_message())[0])

    async def test_22_naive_invalid_dates_and_unknown_timezone_never_show_raw_iso_or_crash(self):
        self.legacy(rows=[('Naive запись', None, False, 50, 8)],
                    start='2026-11-08T18:30:00', finish='2026-11-08T19:00:00')
        cards = await self.open_history()
        self.assertIn('08.11.2026 18:30', self.rendered(cards[0])[0])
        self.legacy(day='Без даты', rows=[('Нечитаемая дата', None, False, 51, 8)],
                    start='not-a-date', finish='also-not-a-date')
        with db._connection(True) as connection:
            connection.execute("UPDATE users SET timezone='Unknown/Nowhere' WHERE id=?", (self.uid,))
        cards = await self.open_history(day='Без даты')
        plain = self.rendered(cards[0])[0]
        self.assertIn('дата не сохранена', plain)
        self.assertNotIn('not-a-date', plain)
        self.assertNotIn('also-not-a-date', plain)
        self.assertIn('Нечитаемая дата', plain)

    async def test_23_long_history_is_lossless_and_every_html_message_is_independently_valid(self):
        exercise = 'Жим <угол> & "кавычки"'
        rows = [(exercise, None, number % 7 == 0, number + .5, 8 + number % 4) for number in range(180)]
        self.legacy(rows=rows)
        cards = await self.open_history()
        self.assertGreater(len(cards), 1)
        rendered = []
        for card in cards:
            plain, bold = self.rendered(card)
            rendered.append(plain)
            self.assertNotIn('История тренировок', plain)
            self.assertEqual(self.controls(card), [])
        complete = '\n'.join(rendered)
        self.assertIn(exercise, complete)
        self.assertTrue(any(exercise in self.rendered(card)[1] for card in cards))
        for number in range(180):
            self.assertIn(f'{number + .5:g} кг × {8 + number % 4}', complete)
        plan = self.create_plan(names=(exercise, 'Присед', 'Жим'))
        await self.begin(plan)
        self.assertIn(f'Следующее упражнение — {exercise}.', self.rendered(self.last_message())[1])
        before = len(self.messages())
        await self.send(111, '50 8')
        confirmation = self.rendered(self.messages()[before])[0]
        self.assertIn(f'✅ {exercise} — подходы сохранены.', confirmation)
