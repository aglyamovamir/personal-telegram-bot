"""Unfinished workout facts and exact, transactional deletion confirmations."""
import json
from datetime import datetime, timezone

from aiogram.methods import SendMessage

from database import db, programs, sessions
from handlers import unfinished_workout_views as views
from states.workout import WorkoutState
from test_handlers import TelegramTestCase


class UnfinishedWorkoutTests(TelegramTestCase):
    def messages(self):
        return [request for request in self.session.requests
                if isinstance(request, SendMessage) and request.chat_id == 111]

    def button(self, label):
        for request in reversed(self.messages()):
            for row in getattr(request.reply_markup, 'inline_keyboard', []):
                for button in row:
                    if button.text == label:
                        return button.callback_data
        self.fail(f'Button not sent: {label}')

    def legacy(self, *, sets=True):
        wid = db.create_workout(self.uid, 'B')
        if sets:
            db.save_set(self.uid, wid, 'Жим штанги лёжа', 1, 20, 10, True)
            for number in range(2, 5):
                db.save_set(self.uid, wid, 'Жим штанги лёжа', number, 50, 8)
            db.save_set(self.uid, wid, 'Подтягивания', 1, 0, 6)
        return wid

    async def test_legacy_card_shows_actual_sets_without_guessed_program_or_resume(self):
        wid = self.legacy()
        with db._connection(True) as c:
            c.execute('UPDATE workouts SET started_at=? WHERE id=?', ('2026-11-08T18:30:00+03:00', wid))
        await self.send(111, '🏋️ Тренировка')
        card = self.messages()[-1]
        self.assertIn('Незавершённая тренировка B', card.text)
        self.assertIn('08.11.2026 18:30 (Europe/Moscow)', card.text)
        self.assertIn('С начала прошло:', card.text)
        self.assertIn('Записано: 2 упражнения, 5 подходов', card.text)
        self.assertIn('Разминка: 20 кг × 10', card.text)
        self.assertIn('Рабочие: 50 кг × 8 — 3 подхода', card.text)
        self.assertIn('Рабочий: 0 кг × 6', card.text)
        self.assertNotIn('Программа:', card.text)
        self.assertNotIn('длительность', card.text.lower())
        labels = [button.text for row in card.reply_markup.inline_keyboard for button in row]
        self.assertEqual(labels, ['Завершить с сохранением', 'Удалить тренировку', 'Назад'])

    async def test_empty_card_and_navigation_to_normal_day_selection(self):
        wid = self.legacy(sets=False)
        await self.send(111, '🏋️ Тренировка')
        self.assertIn('Подходы ещё не записаны', self.messages()[-1].text)
        self.assertIn('0 упражнений, 0 подходов', self.messages()[-1].text)
        await self.click(111, self.button('Назад'))
        text = self.messages()[-1].text
        self.assertIn('Выберите тренировочный день', text)
        self.button('Редактировать программу')
        self.button('Список программ')
        self.assertEqual(db.get_active_workout(self.uid)[0], wid)

    async def test_snapshot_card_resume_uses_immutable_snapshot(self):
        session = sessions.start_day(self.uid)
        expected = session['snapshot']
        await self.send(111, '🏋️ Тренировка')
        self.assertIn('Программа:', self.messages()[-1].text)
        await self.click(111, self.button('Продолжить тренировку'))
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assertEqual((await self.context(111).get_data())['workout_id'], session['workout_id'])
        self.assertEqual(sessions.get_session(self.uid, session['workout_id'])['snapshot'], expected)

    def test_timezone_elapsed_and_naive_legacy_convention_are_explicit(self):
        wid = self.legacy(sets=False)
        with db._connection(True) as c:
            c.execute('UPDATE users SET timezone=? WHERE id=?', ('Asia/Yekaterinburg', self.uid))
            c.execute('UPDATE workouts SET started_at=? WHERE id=?', ('2026-11-08T15:30:00+00:00', wid))
        details = sessions.get_unfinished_workout(self.uid, wid)
        text, _ = views.compact_card(details, now=datetime(2026, 11, 9, 17, 30, tzinfo=timezone.utc))
        self.assertIn('08.11.2026 20:30 (Asia/Yekaterinburg)', text)
        self.assertIn('С начала прошло: 1 день 2 часа', text)
        self.assertNotIn('длительность', text.lower())
        self.assertNotIn('Окончание', text)
        details['started_at'] = '2026-11-08T20:30:00'
        text, _ = views.compact_card(details)
        self.assertIn('без часового пояса', text)

    async def test_long_actual_sets_have_bounded_pages_without_lost_records(self):
        wid = self.legacy(sets=False)
        db.save_exercise_sets(self.uid, wid, 'Жим', None,
                             [dict(weight=number, reps=8, is_warmup=number < 3) for number in range(180)])
        await self.send(111, '🏋️ Тренировка')
        self.assertIn('180 подходов', self.messages()[-1].text)
        await self.click(111, self.button('Посмотреть все подходы'))
        first_page = self.messages()[-1].text
        self.assertLessEqual(len(first_page), 3900)
        self.assertIn('Начата:', first_page)
        self.assertIn('С начала прошло:', first_page)
        await self.click(111, self.button('Подходы →'))
        self.assertIn('Страница 2', self.messages()[-1].text)
        pages = views.all_sets_pages(sessions.get_unfinished_workout(self.uid, wid))
        self.assertTrue(all(len(page) <= 3800 for page in pages))
        for number in range(180):
            self.assertIn(f'{number + 1}. ', '\n'.join(pages))

    async def test_delete_requires_confirmation_and_back_preserves_all_data(self):
        wid = self.legacy()
        before = db.get_workout_sets(self.uid, wid)
        await self.send(111, '🏋️ Тренировка')
        await self.click(111, self.button('Удалить тренировку'))
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)
        self.assertIn('5', self.messages()[-1].text)
        self.assertIn('Другие тренировки и программа не изменятся', self.messages()[-1].text)
        stale = self.button('Да, удалить')
        await self.click(111, self.button('Назад'))
        self.assertIn('Незавершённая тренировка B', self.messages()[-1].text)
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)
        await self.click(111, stale)
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)

    async def test_legacy_confirmation_detects_same_count_set_edit_and_new_set(self):
        wid = self.legacy()
        await self.send(111, '🏋️ Тренировка')
        await self.click(111, self.button('Удалить тренировку'))
        confirmation = self.button('Да, удалить')
        with db._connection(True) as c:
            c.execute('UPDATE workout_sets SET reps=9 WHERE workout_id=?', (wid,))
        await self.click(111, confirmation)
        self.assertIsNotNone(db.get_active_workout(self.uid))
        self.assertIn('Незавершённая тренировка B', self.messages()[-1].text)
        await self.click(111, self.button('Удалить тренировку'))
        confirmation = self.button('Да, удалить')
        db.save_set(self.uid, wid, 'Новое упражнение', 1, 10, 8)
        await self.click(111, confirmation)
        self.assertEqual(len(db.get_workout_sets(self.uid, wid)), 6)

    async def test_delete_selected_owner_only_preserves_program_other_workouts_and_cycle(self):
        older = self.legacy()
        db.finish_workout(self.uid, older)
        other = db.create_workout(self.friend, 'Other')
        selected = sessions.start_day(self.uid)
        wid = selected['workout_id']
        program_before = programs.get_active_program(self.uid)
        await self.send(111, '🏋️ Тренировка')
        await self.click(111, self.button('Удалить тренировку'))
        confirmation = self.button('Да, удалить')
        await self.click(222, confirmation)
        self.assertEqual(db.get_active_workout(self.uid)[0], wid)
        await self.click(111, confirmation)
        self.assertIsNone(db.get_active_workout(self.uid))
        self.assertEqual(programs.get_active_program(self.uid), program_before)
        self.assertIsNotNone(db.get_workout(self.uid, older)[3])
        self.assertEqual(db.get_active_workout(self.friend)[0], other)
        self.assertIn('Выберите тренировочный день', self.messages()[-1].text)
        next_workout = sessions.start_day(self.uid)
        await self.click(111, confirmation)
        self.assertEqual(db.get_active_workout(self.uid)[0], next_workout['workout_id'])
        with db._connection() as c:
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])

    async def test_finished_deleted_and_changed_snapshot_confirmation_cannot_delete(self):
        session = sessions.start_day(self.uid)
        wid = session['workout_id']
        first = sessions.get_unfinished_workout(self.uid, wid)
        sessions.postpone_exercise(self.uid, wid, session['revision'])
        with self.assertRaises(db.WorkoutAccessError):
            sessions.delete_unfinished_workout(self.uid, wid, first['fingerprint'])
        with self.assertRaises(db.WorkoutAccessError):
            sessions.delete_unfinished_workout(self.friend, wid, first['fingerprint'])
        current = sessions.get_unfinished_workout(self.uid, wid)
        db.finish_workout(self.uid, wid)
        with self.assertRaises(db.WorkoutAccessError):
            sessions.delete_unfinished_workout(self.uid, wid, current['fingerprint'])
        empty = self.legacy(sets=False)
        fingerprint = sessions.get_unfinished_workout(self.uid, empty)['fingerprint']
        sessions.delete_unfinished_workout(self.uid, empty, fingerprint)
        with self.assertRaises(db.WorkoutAccessError):
            sessions.delete_unfinished_workout(self.uid, empty, fingerprint)

    async def test_finish_saves_only_existing_facts_and_returns_to_day_selection(self):
        session = sessions.start_day(self.uid)
        wid = session['workout_id']
        sessions.complete_exercise(self.uid, wid, session['revision'],
                                   [dict(weight=45, reps=8, is_warmup=False)])
        before = db.get_workout_sets(self.uid, wid)
        await self.send(111, '🏋️ Тренировка')
        await self.click(111, self.button('Завершить с сохранением'))
        self.assertIsNotNone(db.get_workout(self.uid, wid)[3])
        self.assertEqual(db.get_workout_sets(self.uid, wid), before)
        self.assertEqual(len(before), 1)
        self.assertIn('Выберите тренировочный день', self.messages()[-1].text)

    async def test_stale_continue_does_not_resume_another_workout(self):
        first = sessions.start_day(self.uid)
        await self.send(111, '🏋️ Тренировка')
        stale = self.button('Продолжить тренировку')
        db.finish_workout(self.uid, first['workout_id'])
        second = sessions.start_day(self.uid)
        await self.click(111, stale)
        self.assertNotEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assertEqual(db.get_active_workout(self.uid)[0], second['workout_id'])
        self.assertIn('Незавершённая тренировка', self.messages()[-1].text)

    async def test_corrupt_snapshot_is_displayable_but_cannot_be_resumed(self):
        session = sessions.start_day(self.uid)
        wid = session['workout_id']
        with db._connection(True) as c:
            snapshot = session['snapshot']
            snapshot.pop('day_name')
            c.execute('UPDATE workout_sessions SET snapshot=? WHERE workout_id=?', (json.dumps(snapshot), wid))
        details = sessions.get_unfinished_workout(self.uid, wid)
        self.assertFalse(details['can_resume'])
        await self.send(111, '/resume')
        self.assertIn('Подходы ещё не записаны', self.messages()[-1].text)
        with self.assertRaises(db.WorkoutAccessError):
            sessions.get_resume_session(self.uid, wid)

    async def test_parked_input_after_external_step_change_is_labeled_and_never_applied(self):
        session = sessions.start_day(self.uid)
        wid = session['workout_id']
        await self.send(111, '/resume')
        await self.send(111, '50 нераспознанные повторения')
        await self.send(111, '🗂 Мои программы')
        sessions.postpone_exercise(self.uid, wid, session['revision'])
        await self.send(111, '/resume')
        self.assertIn('Предыдущий ввод упражнения №1', self.messages()[-1].text)
        self.assertIn('не применён к текущему упражнению', self.messages()[-1].text)
        self.assertEqual((await self.context(111).get_data())['exercise_index'], 1)
        self.assertEqual(db.get_workout_sets(self.uid, wid), [])
