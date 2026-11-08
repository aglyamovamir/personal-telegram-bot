"""Simple constructor and compatibility checks for restored correction support."""
from aiogram.methods import SendMessage

from database import db, programs, sessions
from states.workout import WorkoutState
from test_editor import EditorTestCase
from test_handlers import TelegramTestCase


class SimpleCreationTests(EditorTestCase):
    async def test_two_days_three_exercises_exactly_27_actions_without_templates(self):
        before = self.counter
        await self.new_program('Только упражнения')
        for day in ('Первый', 'Второй'):
            await self.add_day(day)
            await self.add_exercise(open_details=False)
            await self.add_exercise('squat', open_details=False)
            await self.add_exercise('Тяга штанги', 'Тяга штанги в наклоне', open_details=False)
            editor = await self.draft()
            self.assertFalse(any(a[:2] in (['ask', 'count'], ['ask', 'working'], ['ask', 'warmup']) for a in editor['actions']))
            if day == 'Первый':
                await self.press('Назад')
        await self.press('Сохранить программу')
        self.assertEqual(self.counter - before, 27)
        plan = programs.get_program(self.uid, (await self.draft())['program_id'])
        self.assertEqual([len(d['exercises']) for d in plan['days']], [3, 3])
        for day in plan['days']:
            for exercise in day['exercises']:
                self.assertIsNone(exercise['working_sets'])
                self.assertEqual(exercise['templates'], [])
        with db._connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM workout_sets').fetchone(), (0,))


class CorrectionCompatibilityTests(TelegramTestCase):
    async def test_new_correction_support_keeps_old_callback_inert(self):
        self.assertTrue(hasattr(sessions, 'replace_previous_sets'))
        self.assertTrue(hasattr(WorkoutState, 'correcting_previous'))
        await self.click(111, 'nextday')
        await self.send(111, '50 8 3')
        wid = db.get_active_workout(self.uid)[0]
        before = sessions.get_session(self.uid, wid)
        rows = db.get_workout_sets(self.uid, wid)
        card = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        self.assertIn('ОДНИМ сообщением', card.text)
        self.assertNotIn('Необязательный план:', card.text)  # Legacy plans are not prescriptions.
        self.assertFalse(any(b.callback_data.startswith(('correct:', 'cancelcorrect:')) for row in card.reply_markup.inline_keyboard for b in row))
        self.assertTrue(any(b.callback_data.startswith('fix:') for row in card.reply_markup.inline_keyboard for b in row))
        await self.click(111, f"correct:{wid}:{before['progress']['current']}:{before['revision']}")
        self.assertEqual(sessions.get_session(self.uid, wid), before)
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        await self.context(111).clear()
        await self.send(111, '/resume')
        self.assertEqual((await self.context(111).get_data())['exercise_index'], before['progress']['current'])
