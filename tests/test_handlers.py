"""Offline dispatcher tests: real routing/FSM, fake Telegram transport."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.methods import SendMessage
from aiogram.types import Update, Message, User, Chat, CallbackQuery

from access import PrivateAccessMiddleware
from access import allowed_telegram_ids
from database import db, programs, sessions
from database.migrations import migrate
from database.migrations_v2 import migrate_v2
from database.migrations_v3 import migrate_v3
from database.migrations_v4 import migrate_v4
from handlers import start, daily, today, program_editor
from legacy_schema import create_legacy_database
from scheduler import send_reminder, reschedule_notifications
from states.workout import WorkoutState
from program_fixtures import seed_catalog, friend_program, import_legacy_program, WORKOUTS


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.requests), date=datetime.now(timezone.utc),
                           chat=Chat(id=int(method.chat_id), type='private'), text=method.text,
                           from_user=User(id=bot.id, is_bot=True, first_name='Bot'),
                           reply_markup=method.reply_markup if hasattr(method.reply_markup, 'inline_keyboard') else None)
        return True

    async def stream_content(self, *args, **kwargs):
        raise AssertionError('Network is forbidden in tests')
        yield b''


class TelegramTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix='life-bot-handlers-')
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'test.sqlite3'
        create_legacy_database(path)
        migrate(path, 111)
        migrate_v2(path)
        migrate_v3(path)
        migrate_v4(path)
        patcher = patch.object(db, 'DB_PATH', str(path))
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict('os.environ', {'ALLOWED_TELEGRAM_IDS': '111,222', 'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': ''})
        env.start()
        self.addCleanup(env.stop)
        # A second guard catches any accidental attempt to authorize Google.
        google = patch('google_sheets.gspread.authorize', side_effect=AssertionError('Google network forbidden'))
        self.authorize = google.start()
        self.addCleanup(google.stop)
        self.session = FakeSession()
        self.bot = Bot(token='123456:FAKE_TEST_TOKEN', session=self.session)
        self.dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
        self.dp.message.outer_middleware(PrivateAccessMiddleware())
        self.dp.callback_query.outer_middleware(PrivateAccessMiddleware())
        # Copy registered handlers to fresh routers (global routers cannot be
        # attached to multiple dispatchers). No production startup is invoked.
        for module in (start, program_editor, daily, today):
            router = Router()
            router.message.handlers.extend(module.router.message.handlers)
            router.callback_query.handlers.extend(module.router.callback_query.handlers)
            self.dp.include_router(router)
        self.counter = 0
        self.uid = db.get_or_create_user(111, 111)['id']
        self.friend = db.get_or_create_user(222, 222)['id']
        seed_catalog()
        import_legacy_program(self.uid)
        friend_program(self.friend)

    async def asyncTearDown(self):
        self.authorize.assert_not_called()
        await self.dp.storage.close()
        await self.bot.session.close()

    def message(self, telegram_id, text=None, *, group=False, bot_author=False):
        self.counter += 1
        return Message(message_id=self.counter, date=datetime.now(timezone.utc),
                       chat=Chat(id=-10 if group else telegram_id, type='group' if group else 'private'),
                       from_user=User(id=self.bot.id if bot_author else telegram_id,
                                      is_bot=bot_author, first_name='Test'), text=text)

    async def send(self, telegram_id, text, **kwargs):
        message = self.message(telegram_id, text, **kwargs)
        await self.dp.feed_update(self.bot, Update(update_id=self.counter, message=message))

    async def click(self, telegram_id, data):
        if data.startswith('workout_') and telegram_id in (111, 222):
            uid = self.uid if telegram_id == 111 else self.friend
            program = programs.get_active_program(uid)
            day = next((d for d in program['days'] if d['name'] == data[8:]), program['days'][0])
            data = f"day:{day['id']}"
        message = self.message(telegram_id, 'bot controls', bot_author=True)
        callback = CallbackQuery(id=str(self.counter), from_user=User(id=telegram_id, is_bot=False, first_name='Test'),
                                 chat_instance='test', message=message, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=self.counter, callback_query=callback))

    def context(self, telegram_id):
        return self.dp.fsm.get_context(bot=self.bot, chat_id=telegram_id, user_id=telegram_id)

    def last_control(self, telegram_id, action):
        for request in reversed(self.session.requests):
            if isinstance(request, SendMessage) and request.chat_id == telegram_id and request.reply_markup:
                for row in request.reply_markup.inline_keyboard:
                    for button in row:
                        if button.callback_data.startswith(action + ':'):
                            return button.callback_data
        self.fail('No workout control was sent')



class HandlerTests(TelegramTestCase):
    async def test_private_allowlist_blocks_before_registration(self):
        await self.send(333, '/start')
        await self.send(444, '/start', group=True)
        c = db.get_connection()
        try:
            self.assertEqual(c.execute('SELECT telegram_user_id FROM users ORDER BY id').fetchall(), [(111,), (222,)])
        finally:
            c.close()
        await self.click(333, 'workout_A')
        self.assertIsNone(db.get_active_workout(self.uid))
        with patch.dict('os.environ', {'ALLOWED_TELEGRAM_IDS': ''}):
            with self.assertRaises(RuntimeError):
                allowed_telegram_ids()

    async def test_full_abc_workouts_keep_parsing_and_auto_completion(self):
        for name in ('A', 'B', 'C'):
            await self.click(111, f'workout_{name}')
            context = self.context(111)
            wid = db.get_active_workout(self.uid)[0]
            count = len(WORKOUTS[name])
            for _ in range(count):
                await self.send(111, '20 10\n\n50 8 3')
            self.assertIsNone(await context.get_state())
            self.assertIsNone(db.get_active_workout(self.uid))
            self.assertEqual(len(db.get_workout_sets(self.uid, wid)), count * 4)
            self.assertIsNotNone(db.get_last_workout_by_type(self.uid, name))

    async def test_daily_date_is_captured_at_start_and_cancel_is_explicit(self):
        with patch('handlers.daily.user_today', return_value='2026-11-08'):
            await self.send(111, '/morning')
        for text in ('23:00', '07:00', '8:00', '80'):
            await self.send(111, text)
        self.assertEqual(db.get_daily_metrics(self.uid, '2026-11-08')[4], 80)
        await self.send(111, '/evening')
        await self.send(111, '/cancel')
        self.assertIsNone(await self.context(111).get_state())

    async def test_callback_identity_is_sender_not_bot_and_workouts_are_isolated(self):
        await self.click(111, 'workout_A')
        await self.click(222, 'workout_A')
        await self.send(111, '20 10')
        await self.send(222, '90 8')
        a, b = db.get_active_workout(self.uid)[0], db.get_active_workout(self.friend)[0]
        self.assertEqual(db.get_workout_sets(self.uid, a)[0][4:], (20, 10))
        self.assertEqual(db.get_workout_sets(self.friend, b)[0][4:], (90, 8))

    async def test_commands_and_reminders_do_not_replace_active_workout_fsm(self):
        await self.click(111, 'workout_A')
        context = self.context(111)
        before = await context.get_data()
        for text in ('/morning', '/evening', '/change_time', '/edit_day', '🌅 Утро', '/start', '/today', '/help'):
            await self.send(111, text)
            self.assertEqual(await context.get_state(), WorkoutState.waiting_for_exercise_input.state)
            self.assertEqual(await context.get_data(), before)
        db.save_user_settings(self.uid, '07:00', '22:00')
        await send_reminder(self.bot, self.uid, 'morning')
        self.assertEqual(await context.get_data(), before)
        self.assertEqual(await context.get_state(), WorkoutState.waiting_for_exercise_input.state)

    async def test_postpone_stale_and_foreign_buttons_and_finish(self):
        await self.click(111, 'workout_A')
        stale = self.last_control(111, 'postpone')
        await self.click(111, stale)
        context = self.context(111)
        self.assertEqual((await context.get_data())['exercise_index'], 1)
        await self.click(111, stale)
        self.assertEqual((await context.get_data())['exercise_index'], 1)
        await self.click(222, 'workout_A')
        other_context = self.context(222)
        other_before = await other_context.get_data()
        await self.click(222, self.last_control(111, 'finish'))
        self.assertEqual(await other_context.get_data(), other_before)
        await self.send(111, '50 10')
        self.assertEqual((await context.get_data())['exercise_index'], 0)
        await self.click(111, self.last_control(111, 'finish'))
        self.assertIsNone(await context.get_state())
        self.assertIsNotNone(db.get_last_workout(self.uid))
        self.assertIsNotNone(db.get_active_workout(self.friend))

    async def test_daily_dialogs_editing_and_settings_are_personal(self):
        for telegram_id, score in ((111, '80'), (222, '40')):
            for text in ('/morning', '23:00', '07:00', '8:00', score):
                await self.send(telegram_id, text)
        day = db.user_today(self.uid)
        self.assertEqual(db.get_daily_metrics(self.uid, day)[4], 80)
        self.assertEqual(db.get_daily_metrics(self.friend, day)[4], 40)
        for text in ('/evening', '7', '3', '8', '1234', '2:00'):
            await self.send(222, text)
        self.assertEqual(db.get_daily_metrics(self.friend, day)[8], 1234)
        self.assertIsNone(db.get_daily_metrics(self.uid, day)[8])
        for text in ('/edit_day', datetime.fromisoformat(day).strftime('%d.%m.%Y'), '4', '90'):
            await self.send(111, text)
        self.assertEqual(db.get_daily_metrics(self.uid, day)[4], 90)
        self.assertEqual(db.get_daily_metrics(self.friend, day)[4], 40)
        for text in ('/change_time', '09:00', '23:00'):
            await self.send(222, text)
        self.assertEqual(db.get_user_settings(self.friend)[:2], ('09:00', '23:00'))
        self.assertIsNone(db.get_user_settings(self.uid))

    async def test_restart_orphan_can_be_explicitly_finished_or_cancelled(self):
        wid = db.create_workout(self.uid, 'A')  # No in-memory FSM after restart.
        await self.click(111, 'workout_B')
        self.assertEqual(db.get_active_workout(self.uid)[0], wid)
        await self.send(111, '/exit')
        self.assertIsNotNone(db.get_workout(self.uid, wid)[3])
        other = db.create_workout(self.uid, 'B')
        await self.send(111, '/cancel')
        await self.click(111, self.last_control(111, 'deleteworkout'))
        with self.assertRaises(db.WorkoutAccessError):
            db.get_workout(self.uid, other)

    async def test_snapshot_workout_resumes_after_memory_loss_and_program_edit(self):
        await self.click(111, 'nextday')
        await self.click(111, self.last_control(111, 'postpone'))
        await self.send(111, '40 8')
        active = db.get_active_workout(self.uid)
        before = sessions.get_session(self.uid, active[0])
        program = programs.get_active_program(self.uid)
        programs.update_program(self.uid, program['id'], name='Edited while training', days=[
            dict(name='New day', exercises=[dict(exercise_id='bench-press', working_sets=1)])
        ])
        await self.context(111).clear()  # Simulate complete loss of MemoryStorage.
        await self.send(111, '/resume')
        cache = await self.context(111).get_data()
        self.assertEqual(cache['exercise_index'], before['progress']['current'])
        self.assertEqual(cache['revision'], before['revision'])
        self.assertEqual(sessions.get_session(self.uid, active[0])['snapshot'], before['snapshot'])
        await self.send(111, '50 10')
        self.assertEqual(len(db.get_workout_sets(self.uid, active[0])), 2)

    async def test_foreign_day_and_new_user_without_program(self):
        day = programs.get_active_program(self.uid)['days'][0]['id']
        await self.click(222, f'day:{day}')
        self.assertIsNone(db.get_active_workout(self.friend))
        with patch.dict('os.environ', {'ALLOWED_TELEGRAM_IDS': '111,222,333'}):
            await self.send(333, '/start')
            await self.send(333, '/resume')
        new = db.get_or_create_user(333, 333)
        self.assertEqual(programs.list_programs(new['id']), [])

    async def test_schedule_job_ids_and_timezones_are_personal(self):
        db.save_user_settings(self.uid, '07:00', '22:00')
        db.save_user_settings(self.friend, '09:00', '23:00')
        with patch('scheduler.scheduler') as scheduler:
            scheduler.get_job.return_value = None
            reschedule_notifications(self.bot, self.uid)
            reschedule_notifications(self.bot, self.friend)
            ids = [call.kwargs['id'] for call in scheduler.add_job.call_args_list]
            self.assertEqual(set(ids), {f'morning:{self.uid}', f'evening:{self.uid}', f'morning:{self.friend}', f'evening:{self.friend}'})
            self.assertTrue(all(call.kwargs['timezone'] == 'Europe/Moscow' for call in scheduler.add_job.call_args_list))
        with patch('scheduler.allowed_telegram_ids', return_value={111}):
            bot = AsyncMock()
            await send_reminder(bot, self.friend, 'evening')
            bot.send_message.assert_not_called()
