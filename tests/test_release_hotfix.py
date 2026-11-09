"""Release regressions: temporary SQLite and fake Telegram only."""
from unittest.mock import patch, AsyncMock
from aiogram.methods import SendMessage
from database import db, programs, sessions
from google_sheets import export_enabled
from access import allowed_telegram_ids
from scheduler import send_reminder
from test_editor import EditorTestCase


class ReleaseHotfixTests(EditorTestCase):
    def texts(self, uid=111):
        return [r.text for r in self.session.requests if isinstance(r, SendMessage) and r.chat_id == uid]

    def legacy(self, label='A', user=None):
        uid = self.uid if user is None else user
        wid = db.create_workout(uid, label)
        db.save_set(uid, wid, 'Old bench', 1, 40, 8)
        return wid

    async def test_owner_history_days_legacy_details_and_back(self):
        wid = self.legacy()
        db.finish_workout(self.uid, wid)
        await self.send(111, '📚 История')
        self.assertEqual(self.texts()[-1], 'Какую тренировку посмотреть?')
        groups = db.get_history_days(self.uid)
        self.assertEqual([g['title'] for g in groups], ['A', 'B', 'C'])
        a = groups[0]
        self.assertEqual([w[0] for w in a['workouts']], [wid])
        self.assertNotIn('history:', str(self.session.requests[-1].reply_markup))
        await self.click(111, 'hday:' + a['key'])
        self.assertTrue(any('Old bench' in t and '40' in t for t in self.texts()))
        await self.click(111, 'historydays')
        self.assertEqual(self.texts()[-1], 'Какую тренировку посмотреть?')

    async def test_legacy_does_not_block_editor_and_history(self):
        wid = self.legacy()
        before = db.get_workout_sets(self.uid, wid)
        await self.send(111, '🏋️ Тренировка')
        self.assertIn('Завершить', str(self.session.requests[-1].reply_markup))
        await self.send(111, '🗂 Мои программы')
        self.assertIn('Список программ', self.texts()[-1])
        await self.press('Создать программу')
        await self.send(111, 'Extra')
        self.assertEqual((await self.draft())['draft']['name'], 'Extra')
        await self.send(111, '📚 История')
        self.assertIn('Какую тренировку', self.texts()[-1])
        self.assertEqual((await self.draft())['draft']['name'], 'Extra')
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)

    async def test_safe_legacy_finish_stale_foreign_and_start_abc(self):
        wid = self.legacy()
        before = db.get_workout_sets(self.uid, wid)
        await self.click(222, f'legacyfinish:{wid}')
        self.assertIsNone(db.get_workout(self.uid, wid)[3])
        await self.send(111, '🏋️ Тренировка')
        await self.click(111, f'legacyfinish:{wid}')
        finished = db.get_workout(self.uid, wid)[3]
        self.assertIsNotNone(finished)
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)
        self.assertIn('Выберите тренировочный день', self.texts()[-1])
        await self.click(111, f'legacyfinish:{wid}')
        self.assertEqual(db.get_workout(self.uid, wid)[3], finished)
        await self.click(111, 'workout_A')
        active = db.get_active_workout(self.uid)
        self.assertNotEqual(active[0], wid)
        await self.click(111, f'legacyfinish:{active[0]}')
        self.assertIsNone(db.get_workout(self.uid, active[0])[3])

    async def test_equal_names_in_other_programs_and_users_never_merge(self):
        old = self.legacy()
        db.finish_workout(self.uid, old)
        pid = programs.create_program(self.uid, 'Other', [dict(name='A', exercises=[dict(exercise_id='bench-press')])], activate=True)
        new = sessions.start_day(self.uid)
        sessions.complete_exercise(self.uid, new['workout_id'], new['revision'], [dict(weight=99, reps=5, is_warmup=False)])
        groups = db.get_history_days(self.uid)
        a = [g for g in groups if g['name'] == 'A']
        self.assertEqual(len(a), 2)
        self.assertNotEqual(a[0]['key'], a[1]['key'])
        self.assertEqual([len(g['workouts']) for g in a], [1, 1])
        self.assertNotEqual(a[0]['title'], a[1]['title'])
        await self.click(222, 'hday:' + a[0]['key'])
        await self.click(222, f'history:{old}')
        self.assertFalse(any('Old bench' in t for t in self.texts(222)))
        self.assertEqual([g['name'] for g in db.get_history_days(self.friend)], ['Day one'])
        programs.delete_program(self.uid, pid)
        self.assertTrue(any(new['workout_id'] in [w[0] for w in g['workouts']] for g in db.get_history_days(self.uid)))

    async def test_open_registration_personal_program_and_export_disabled(self):
        with patch.dict('os.environ', {'OPEN_REGISTRATION': 'true', 'ALLOWED_TELEGRAM_IDS': '', 'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': '111'}):
            self.assertEqual(allowed_telegram_ids(), set())
            await self.send(333, '/start')
            uid = db.get_or_create_user(333, 333)['id']
            self.assertEqual(programs.list_programs(uid), [])
            self.assertEqual(db.get_history_days(uid), [])
            self.assertFalse(export_enabled(uid))
            await self.new_program('Friend plan', telegram_id=333)
            await self.add_day('My day', telegram_id=333)
            await self.send(333, 'Bench')
            await self.press('Жим лёжа', 333, contains=True)
            await self.press('Закрыть редактор', 333)
            await self.context(333).clear()
            await self.click(333, 'nextday')
            await self.send(333, '60 8')
            self.assertEqual(len(db.get_recent_workouts(uid)), 1)
            self.assertEqual([g['name'] for g in db.get_history_days(uid)], ['My day'])
            db.save_user_settings(uid, '07:00', '22:00')
            bot = AsyncMock()
            await send_reminder(bot, uid, 'morning')
            bot.send_message.assert_awaited_once()
            await self.send(444, '/start', group=True)
            with db._connection() as c:
                self.assertIsNone(c.execute('SELECT id FROM users WHERE telegram_user_id=444').fetchone())

    async def test_closed_registration_and_invalid_configuration(self):
        with patch.dict('os.environ', {'OPEN_REGISTRATION': 'false'}):
            await self.send(333, '/start')
            with db._connection() as c:
                self.assertIsNone(c.execute('SELECT id FROM users WHERE telegram_user_id=333').fetchone())
        with patch.dict('os.environ', {'OPEN_REGISTRATION': 'maybe'}):
            from access import open_registration_enabled
            with self.assertRaises(RuntimeError):
                open_registration_enabled()

    async def test_existing_program_edit_with_legacy_workout_preserves_sets(self):
        wid = self.legacy()
        before = db.get_workout_sets(self.uid, wid)
        await self.send(111, '🗂 Мои программы')
        await self.press('A/B/C', contains=True)
        await self.press('Переименовать программу')
        await self.send(111, 'My ABC')
        self.assertEqual(programs.get_active_program(self.uid)['name'], 'My ABC')
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)
        self.assertIsNone(db.get_workout(self.uid, wid)[3])

    async def test_snapshot_workout_allows_editor_without_losing_progress(self):
        await self.click(111, 'nextday')
        before = await self.context(111).get_data()
        await self.send(111, '🗂 Мои программы')
        self.assertIn('Список программ', self.texts()[-1])
        self.assertEqual((await self.context(111).get_data())['parked_workout']['data'], before)
        await self.press('Закрыть редактор')
        await self.send(111, '/resume')
        await self.send(111, '50 8')
        self.assertEqual(len(db.get_workout_sets(self.uid, before['workout_id'])), 1)

    async def test_history_opens_latest_by_date_without_list_or_other_day(self):
        ids = []
        for _ in range(31):
            wid = self.legacy()
            db.finish_workout(self.uid, wid)
            ids.append(wid)
        other = self.legacy('B')
        db.finish_workout(self.uid, other)
        with db._connection(True) as c:
            c.execute("UPDATE workouts SET started_at='2020-01-01T10:00:00' WHERE id=?", (ids[-1],))
            c.execute("UPDATE workouts SET started_at='2026-11-10T10:00:00' WHERE id=?", (ids[0],))
            c.execute("UPDATE workout_sets SET exercise='Latest by date' WHERE workout_id=?", (ids[0],))
            c.execute("UPDATE workout_sets SET exercise='Wrong by ID' WHERE workout_id=?", (ids[-1],))
        group = next(g for g in db.get_history_days(self.uid) if g['name'] == 'A')
        self.session.requests.clear()
        await self.click(111, 'hday:' + group['key'])
        markup = next(r.reply_markup for r in reversed(self.session.requests)
                      if isinstance(r, SendMessage) and r.chat_id == 111)
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertEqual(callbacks, ['historydays'])
        self.assertTrue(any('Latest by date' in t for t in self.texts()))
        self.assertFalse(any('Wrong by ID' in t for t in self.texts()))
