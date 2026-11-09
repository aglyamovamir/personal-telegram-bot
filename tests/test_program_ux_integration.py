"""Cross-screen regressions using temporary SQLite and fake Telegram transport."""
from aiogram.methods import SendMessage

from database import db, programs, sessions
from states.workout import WorkoutState
from test_handlers import TelegramTestCase


class ProgramWorkoutIntegrationTests(TelegramTestCase):
    async def press(self, text, *, contains=False):
        screen = next(request for request in reversed(self.session.requests)
                      if isinstance(request, SendMessage) and request.chat_id == 111
                      and request.reply_markup)
        for row in screen.reply_markup.inline_keyboard:
            for button in row:
                if (text in button.text if contains else text == button.text):
                    await self.click(111, button.callback_data)
                    return
        self.fail(f'Button not found: {text}')

    async def begin_with_deferred_exercise(self):
        self.pid = programs.create_program(self.uid, 'Интеграция', [
            dict(name='Верх', exercises=[
                dict(exercise_id='bench-press', name='Первый жим'),
                dict(exercise_id='squat', name='Отложенный присед'),
                dict(exercise_id='bench-press', name='Второй жим'),
            ]),
            dict(name='Низ', exercises=[dict(exercise_id='squat', name='Присед')]),
        ], activate=True)
        await self.click(111, 'nextday')
        self.wid = db.get_active_workout(self.uid)[0]
        await self.send(111, '50 8')
        await self.click(111, self.last_control(111, 'postpone'))
        self.before = sessions.get_session(self.uid, self.wid)
        self.assertEqual(self.before['progress']['postponed'], [1])
        self.assertEqual(self.before['progress']['current'], 2)

    async def edit_program_and_resume(self):
        await self.send(111, '/programs')
        await self.press('Интеграция', contains=True)
        await self.press('Переименовать', contains=True)
        await self.send(111, 'Обновлённая программа')
        self.assertEqual(programs.get_program(self.uid, self.pid)['name'],
                         'Обновлённая программа')
        await self.press('Закрыть редактор')
        await self.press('Продолжить тренировку')
        actual = sessions.get_session(self.uid, self.wid)
        self.assertEqual(actual, self.before)
        self.assertEqual(programs.next_day(self.uid)['id'], self.before['snapshot']['day_id'])

    async def test_pending_exercise_input_and_queues_survive_program_management(self):
        await self.begin_with_deferred_exercise()
        rows = db.get_workout_sets(self.uid, self.wid)
        pending = 'неполный ввод 55'
        await self.send(111, pending)
        await self.edit_program_and_resume()
        context = self.context(111)
        self.assertEqual(await context.get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assertEqual((await context.get_data()).get('exercise_input'), pending)
        self.assertEqual(db.get_workout_sets(self.uid, self.wid), rows)
        await self.send(111, '60 6')
        current = sessions.get_session(self.uid, self.wid)
        self.assertEqual(current['progress']['current'], 1)
        self.assertEqual(current['progress']['completed'], [0, 2])

    async def test_pending_correction_survives_editing_and_replaces_only_its_occurrence(self):
        await self.begin_with_deferred_exercise()
        await self.click(111, self.last_control(111, 'fix'))
        pending = 'неполное исправление 52'
        await self.send(111, pending)
        await self.edit_program_and_resume()
        context = self.context(111)
        self.assertEqual(await context.get_state(), WorkoutState.correcting_previous.state)
        data = await context.get_data()
        self.assertEqual(data.get('correction_input'), pending)
        self.assertEqual(data['correction_position'], 0)
        await self.send(111, '52 9')
        after = sessions.get_session(self.uid, self.wid)
        self.assertEqual(after['progress'], self.before['progress'])
        self.assertEqual(after['snapshot'], self.before['snapshot'])
        self.assertEqual(sessions.get_occurrence_sets(self.uid, self.wid, 0)[0][-2:], (52, 9))
        self.assertEqual(sessions.get_occurrence_sets(self.uid, self.wid, 2), [])

    async def test_deleting_card_clears_only_its_workout_cache(self):
        wid = db.create_workout(self.uid, 'Случайная')
        context = self.context(111)
        await context.update_data(unrelated_context={'keep': 'this'}, workout_id=wid)
        await self.send(111, '🏋️ Тренировка')
        await self.press('Удалить тренировку')
        await self.press('Да, удалить')
        data = await context.get_data()
        self.assertEqual(data.get('unrelated_context'), {'keep': 'this'})
        self.assertNotIn('workout_id', data)
        self.assertNotIn('unfinished', data)
        self.assertIsNone(db.get_active_workout(self.uid))
        self.assertEqual(await context.get_state(), None)

    async def test_expired_editor_button_cannot_suspend_a_resumed_workout(self):
        await self.begin_with_deferred_exercise()
        await self.send(111, '/programs')
        editor = (await self.context(111).get_data())['editor']
        index = next(index for index, action in enumerate(editor['actions'])
                     if action[0] == 'open' and action[1] == self.pid)
        old_button = f"pe:{editor['token']}:{index}"
        await self.press('Закрыть редактор')
        await self.press('Продолжить тренировку')
        context = self.context(111)
        before = await context.get_data()
        await self.click(111, old_button)
        self.assertEqual(await context.get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assertEqual(await context.get_data(), before)
        self.assertEqual(sessions.get_session(self.uid, self.wid), self.before)
