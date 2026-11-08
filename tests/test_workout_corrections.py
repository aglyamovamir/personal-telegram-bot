import sqlite3
from unittest.mock import patch

from aiogram.methods import SendMessage

from database import db, programs, sessions
from states.workout import WorkoutState
from test_handlers import TelegramTestCase
from test_simple_programs import PlanTestCase, simple_days
from test_programs import SETS


class CorrectionDatabaseTests(PlanTestCase):
    def begin(self):
        days = simple_days(2, 4)
        for exercise in days[0]['exercises']:
            exercise['name'] = 'Same bench'
        programs.create_program(self.user_id, 'Correction', days, activate=True)
        session = sessions.start_day(self.user_id)
        return sessions.complete_exercise(self.user_id, session['workout_id'], 0, SETS)

    def test_replaces_exact_occurrence_not_same_name_and_keeps_others(self):
        session = self.begin()
        wid = session['workout_id']
        for _ in range(2):
            session = sessions.complete_exercise(self.user_id, wid, session['revision'], SETS)
        before = db.get_history_sets(self.user_id, wid)
        self.assertEqual(sessions.previous_exercise(self.user_id, wid)['position'], 2)
        revised = [dict(weight=60, reps=9, is_warmup=False), dict(weight=20, reps=10, is_warmup=True)]
        result = sessions.replace_previous_sets(self.user_id, wid, 2, session['revision'], revised)
        after = db.get_history_sets(self.user_id, wid)
        self.assertEqual(after[:2], before[:2])
        self.assertEqual([row[-1] for row in after], [0, 1, 2, 2])
        self.assertEqual(sessions.previous_exercise(self.user_id, wid)['sets'], [(0, 60, 9), (1, 20, 10)])
        self.assertEqual(result['progress'], session['progress'])
        self.assertEqual(result['snapshot'], session['snapshot'])
        self.assertEqual(len(db.get_workout_sets(self.user_id, wid)), 4)

    def test_queues_cycle_and_raw_snapshot_progress_are_unchanged(self):
        session = self.begin()
        wid = session['workout_id']
        session = sessions.postpone_exercise(self.user_id, wid, session['revision'])
        next_day = programs.next_day(self.user_id)
        with db._connection() as c:
            before = c.execute('SELECT snapshot,progress FROM workout_sessions WHERE workout_id=?', (wid,)).fetchone()
        result = sessions.replace_previous_sets(self.user_id, wid, 0, session['revision'], SETS)
        with db._connection() as c:
            self.assertEqual(c.execute('SELECT snapshot,progress FROM workout_sessions WHERE workout_id=?', (wid,)).fetchone(), before)
        self.assertEqual(result['progress']['postponed'], [1])
        self.assertEqual(result['progress']['current'], 2)
        self.assertEqual(programs.next_day(self.user_id), next_day)

    def test_owner_stale_position_finished_and_atomic_rollback(self):
        session = self.begin()
        wid, revision = session['workout_id'], session['revision']
        for operation in (
            lambda: sessions.previous_exercise(self.other, wid),
            lambda: sessions.replace_previous_sets(self.other, wid, 0, revision, SETS),
            lambda: sessions.replace_previous_sets(self.user_id, wid, 2, revision, SETS),
        ):
            with self.assertRaises(db.WorkoutAccessError):
                operation()
        rows = sessions.get_occurrence_sets(self.user_id, wid, 0)
        with self.assertRaises(KeyError):
            sessions.replace_previous_sets(self.user_id, wid, 0, revision, [*SETS, {}])
        self.assertEqual(sessions.get_occurrence_sets(self.user_id, wid, 0), rows)
        self.assertEqual(sessions.get_session(self.user_id, wid), session)
        result = sessions.replace_previous_sets(self.user_id, wid, 0, revision, SETS)
        with self.assertRaises(db.WorkoutAccessError):
            sessions.replace_previous_sets(self.user_id, wid, 0, revision, SETS)
        db.finish_workout(self.user_id, wid)
        with self.assertRaises(db.WorkoutAccessError):
            sessions.replace_previous_sets(self.user_id, wid, 0, result['revision'], SETS)

    def test_cancel_revision_checked_in_same_transaction(self):
        session = self.begin()
        wid, revision = session['workout_id'], session['revision']
        sessions.postpone_exercise(self.user_id, wid, revision)
        with self.assertRaises(db.WorkoutAccessError):
            db.cancel_workout(self.user_id, wid, expected_revision=revision)
        self.assertIsNotNone(db.get_active_workout(self.user_id))
        with self.assertRaises(db.WorkoutAccessError):
            db.cancel_workout(self.other, wid, expected_revision=revision)

    def test_missing_occurrence_links_refuse_edit_without_guessing_by_name(self):
        session = self.begin()
        wid = session['workout_id']
        before = db.get_workout_sets(self.user_id, wid)
        with db._connection(True) as c:
            c.execute('DELETE FROM workout_set_occurrences WHERE workout_id=?', (wid,))
        with self.assertRaises(db.WorkoutAccessError):
            sessions.previous_exercise(self.user_id, wid)
        with self.assertRaises(db.WorkoutAccessError):
            sessions.replace_previous_sets(self.user_id, wid, 0, session['revision'], SETS)
        self.assertEqual(db.get_workout_sets(self.user_id, wid), before)


class WorkoutActionTests(TelegramTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        programs.create_program(self.uid, 'Gym', [dict(name='Gym', exercises=[
            dict(exercise_id='bench-press', name='Жим'),
            dict(exercise_id='squat', name='Приседания'),
            dict(exercise_id='bench-press', name='Жим снова'),
        ])], activate=True)

    async def begin(self):
        await self.click(111, 'nextday')
        wid = db.get_active_workout(self.uid)[0]
        await self.send(111, '20 10\n\n50 8 3')
        return wid

    async def test_one_message_auto_advances_then_correction_returns_to_same_step(self):
        wid = await self.begin()
        before = sessions.get_session(self.uid, wid)
        self.assertEqual(before['progress']['current'], 1)
        self.assertEqual(len(db.get_workout_sets(self.uid, wid)), 4)
        await self.click(111, self.last_control(111, 'fix'))
        self.assertEqual(await self.context(111).get_state(), WorkoutState.correcting_previous.state)
        await self.send(111, '55 9 2')
        self.assertEqual(sessions.previous_exercise(self.uid, wid)['sets'], [(0, 55, 9), (0, 55, 9)])
        self.assertEqual(sessions.get_session(self.uid, wid)['progress'], before['progress'])
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        await self.send(111, '60 8 3')
        self.assertEqual(sessions.get_session(self.uid, wid)['progress']['current'], 2)
        await self.send(111, '50 8')
        self.assertIsNone(await self.context(111).get_state())  # No final confirmation.

    async def test_error_cancel_double_click_and_cancel_command_preserve_data(self):
        wid = await self.begin()
        before = sessions.get_session(self.uid, wid)
        rows = db.get_workout_sets(self.uid, wid)
        entry = self.last_control(111, 'fix')
        with patch('handlers.start.sessions.previous_exercise', side_effect=sqlite3.OperationalError('busy')):
            await self.click(111, entry)
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        await self.click(111, entry)
        await self.click(111, entry)
        await self.send(111, 'bad input')
        self.assertEqual(await self.context(111).get_state(), WorkoutState.correcting_previous.state)
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        with patch('handlers.start.sessions.replace_previous_sets', side_effect=sqlite3.OperationalError('busy')):
            await self.send(111, '55 8')
        self.assertEqual(await self.context(111).get_state(), WorkoutState.correcting_previous.state)
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        cancel = self.last_control(111, 'endfix')
        await self.click(111, cancel)
        await self.click(111, cancel)
        self.assertEqual(sessions.get_session(self.uid, wid), before)
        await self.click(111, self.last_control(111, 'fix'))
        await self.send(111, '/cancel')
        self.assertEqual(sessions.get_session(self.uid, wid), before)
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)

    async def test_restart_and_stale_entry_and_foreign_callback(self):
        wid = await self.begin()
        entry = self.last_control(111, 'fix')
        await self.click(111, entry)
        await self.context(111).clear()
        await self.send(111, '/resume')
        self.assertEqual((await self.context(111).get_data())['exercise_index'], 1)
        await self.click(111, self.last_control(111, 'fix'))
        await self.send(111, '57.5 8')
        await self.click(111, entry)  # Old revision cannot open the editor.
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        await self.context(111).clear()
        await self.send(111, '/resume')
        self.assertEqual(sessions.previous_exercise(self.uid, wid)['sets'], [(0, 57.5, 8)])
        await self.click(222, 'nextday')
        await self.send(222, '90 10')
        other = db.get_active_workout(self.friend)[0]
        before = sessions.get_session(self.friend, other)
        await self.click(222, self.last_control(111, 'fix'))
        self.assertEqual(sessions.get_session(self.friend, other), before)

    async def test_cancel_requires_explicit_confirmation_and_return_changes_nothing(self):
        wid = await self.begin()
        before = sessions.get_session(self.uid, wid)
        rows = db.get_workout_sets(self.uid, wid)
        await self.send(111, '/cancel')
        self.assertEqual(await self.context(111).get_state(), WorkoutState.confirming_cancel.state)
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        warnings = [r.text for r in self.session.requests if isinstance(r, SendMessage)]
        self.assertTrue(any('ВСЕ' in text and 'безвозвратно' in text for text in warnings))
        delete = self.last_control(111, 'deleteworkout')
        await self.send(111, '50 8')  # Cannot accidentally record/confirm in this mode.
        await self.click(111, self.last_control(111, 'keepworkout'))
        self.assertEqual(sessions.get_session(self.uid, wid), before)
        await self.click(111, delete)
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        await self.send(111, '/cancel')
        delete = self.last_control(111, 'deleteworkout')
        await self.click(111, delete)
        self.assertIsNone(db.get_active_workout(self.uid))
        await self.click(111, delete)
        with db._connection() as c:
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])

    async def test_cancel_restart_foreign_token_new_request_stale_revision_and_exit(self):
        wid = await self.begin()
        await self.send(111, '/cancel')
        stale = self.last_control(111, 'deleteworkout')
        await self.click(222, stale)
        self.assertIsNotNone(db.get_active_workout(self.uid))
        await self.click(222, 'nextday')
        await self.send(222, '/cancel')
        other_id = db.get_active_workout(self.friend)[0]
        await self.click(222, stale)
        self.assertEqual(db.get_active_workout(self.friend)[0], other_id)
        await self.context(111).clear()
        await self.click(111, stale)
        self.assertIsNotNone(db.get_active_workout(self.uid))
        await self.send(111, '/resume')
        await self.send(111, '/cancel')
        again = self.last_control(111, 'deleteworkout')
        await self.send(111, '/cancel')
        await self.click(111, again)
        self.assertIsNotNone(db.get_active_workout(self.uid))
        current = self.last_control(111, 'deleteworkout')
        session = sessions.get_session(self.uid, wid)
        sessions.postpone_exercise(self.uid, wid, session['revision'])  # External change after warning.
        await self.click(111, current)
        self.assertIsNotNone(db.get_active_workout(self.uid))
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        await self.send(111, '/cancel')
        old = self.last_control(111, 'deleteworkout')
        await self.send(111, '/exit')
        self.assertIsNotNone(db.get_workout(self.uid, wid)[3])
        await self.click(111, old)
        self.assertIsNotNone(db.get_workout(self.uid, wid)[3])

    async def test_no_previous_or_finished_workout_cannot_enter_correction(self):
        await self.click(111, 'nextday')
        wid = db.get_active_workout(self.uid)[0]
        card = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        self.assertFalse(any(b.callback_data.startswith('fix:') for row in card.reply_markup.inline_keyboard for b in row))
        session = sessions.get_session(self.uid, wid)
        await self.click(111, f"fix:{wid}:0:{session['revision']}")
        self.assertEqual(sessions.get_session(self.uid, wid), session)
        await self.send(111, '50 8')
        entry = self.last_control(111, 'fix')
        await self.send(111, '/exit')
        rows = db.get_workout_sets(self.uid, wid)
        await self.click(111, entry)
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        self.assertIsNone(await self.context(111).get_state())

    async def test_correction_revision_conflict_recovers_without_overwriting_data(self):
        wid = await self.begin()
        await self.click(111, self.last_control(111, 'fix'))
        session = sessions.get_session(self.uid, wid)
        sessions.postpone_exercise(self.uid, wid, session['revision'])
        rows = db.get_workout_sets(self.uid, wid)
        await self.send(111, '100 8')
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assertEqual((await self.context(111).get_data())['exercise_index'], 2)
