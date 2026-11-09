import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from database import db, programs, sessions
from database.migrations_v2 import migrate_v2
from database import migrations_v2
from database.migrations import migrate
from legacy_schema import create_legacy_database
from database.import_legacy_program import import_legacy_program
from program_fixtures import seed_catalog, WORKOUTS
from test_database import TemporaryDatabaseTestCase
import google_sheets


def document(count, repeats=1):
    return [dict(name=f'Day {i + 1}', exercises=[
        dict(exercise_id='bench-press', name=f'Bench occurrence {j + 1}', working_sets=2,
             warmup=[dict(weight=20, reps=10)],
             working_plan=[dict(weight=50, reps=8), dict(weight=55, reps=6)])
        for j in range(repeats)]) for i in range(count)]


SETS = [dict(weight=50, reps=8, is_warmup=False)]


class ProgramTests(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.other = db.get_or_create_user(222, 222)['id']
        seed_catalog()

    def test_any_positive_day_count_cycles_independently_for_two_users(self):
        for count in (1, 2, 3, 4, 6):
            with self.subTest(count=count):
                pid = programs.create_program(self.user_id, f'Plan {count}', document(count), activate=True)
                other = programs.create_program(self.other, 'Other', document(count), activate=True)
                self.assertEqual(len(programs.get_program(self.user_id, pid)['days']), count)
                for index in range(count + 1):
                    session = sessions.start_day(self.user_id)
                    self.assertEqual(session['snapshot']['day_name'], f'Day {index % count + 1}')
                    self.assertEqual(db.get_workout_sets(self.user_id, session['workout_id']), [])
                    self.assertIsNone(sessions.complete_exercise(self.user_id, session['workout_id'], 0, SETS))
                self.assertEqual(programs.get_program(self.other, other)['next_day_position'], 0)
                self.assertEqual(programs.get_active_program(self.user_id)['id'], pid)

    def test_manual_selection_cancel_and_activation(self):
        pid = programs.create_program(self.user_id, 'Four', document(4), activate=True)
        second = programs.create_program(self.user_id, 'Two', document(2))
        days = programs.get_program(self.user_id, pid)['days']
        session = sessions.start_day(self.user_id, days[2]['id'])
        db.cancel_workout(self.user_id, session['workout_id'])
        self.assertEqual(programs.next_day(self.user_id)['id'], days[0]['id'])
        session = sessions.start_day(self.user_id, days[2]['id'])
        db.finish_workout(self.user_id, session['workout_id'])
        self.assertEqual(programs.next_day(self.user_id)['id'], days[3]['id'])
        programs.activate_program(self.user_id, second)
        self.assertEqual(sum(p['is_active'] for p in programs.list_programs(self.user_id)), 1)

    def test_all_program_crud_and_session_operations_check_owner(self):
        pid = programs.create_program(self.user_id, 'Private', document(2), activate=True)
        day = programs.get_program(self.user_id, pid)['days'][0]['id']
        session = sessions.start_day(self.user_id)
        wid = session['workout_id']
        for operation in (
            lambda: programs.get_program(self.other, pid),
            lambda: programs.update_program(self.other, pid, name='stolen'),
            lambda: programs.update_program(self.other, pid, days=document(1)),
            lambda: programs.delete_program(self.other, pid),
            lambda: programs.activate_program(self.other, pid),
            lambda: sessions.start_day(self.other, day),
            lambda: sessions.get_session(self.other, wid),
            lambda: sessions.complete_exercise(self.other, wid, 0, SETS),
            lambda: sessions.postpone_exercise(self.other, wid, 0),
        ):
            with self.assertRaises(db.WorkoutAccessError):
                operation()
        self.assertEqual(programs.list_programs(self.other), [])
        self.assertIsNone(programs.get_active_program(self.other))

    def test_abc_explicit_import_only_owner_preserves_exact_plan_and_history(self):
        wid = db.create_workout(self.user_id, 'A')
        db.save_set(self.user_id, wid, 'Old name', 1, 20, 10, exercise_id='bench-press')
        db.finish_workout(self.user_id, wid)
        before = (db.get_workout(self.user_id, wid), db.get_workout_sets(self.user_id, wid))
        with self.assertRaises(db.WorkoutAccessError):
            import_legacy_program(self.other)
        pid = import_legacy_program(self.user_id)
        self.assertEqual(import_legacy_program(self.user_id), pid)
        program = programs.get_program(self.user_id, pid)
        self.assertEqual([d['name'] for d in program['days']], list(WORKOUTS))
        for actual, expected in zip(program['days'], WORKOUTS.values()):
            self.assertEqual([e['exercise_id'] for e in actual['exercises']], [e['exercise_id'] for e in expected])
            for got, want in zip(actual['exercises'], expected):
                self.assertEqual(got['warmup'], want['warmup'])
                self.assertEqual(got['working_sets'], want['working_sets'])
                self.assertEqual(got['name'], want['name'])
        self.assertEqual(before, (db.get_workout(self.user_id, wid), db.get_workout_sets(self.user_id, wid)))
        self.assertEqual(programs.list_programs(self.other), [])
        programs.update_program(self.user_id, pid, name='Edited')
        import_legacy_program(self.user_id)
        self.assertEqual(programs.get_program(self.user_id, pid)['name'], 'Edited')
        programs.delete_program(self.user_id, pid)
        self.assertIsNone(import_legacy_program(self.user_id))
        self.assertEqual(programs.list_programs(self.user_id), [])

    def test_missing_catalog_entry_import_is_atomic(self):
        with db._connection(True) as c:
            c.execute("DELETE FROM exercises WHERE external_id='bench-press'")
        with self.assertRaises(ValueError):
            import_legacy_program(self.user_id)
        self.assertEqual(programs.list_programs(self.user_id), [])
        with db._connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM program_imports').fetchone()[0], 0)

    def test_program_edit_and_delete_do_not_change_active_snapshot_or_completed_history(self):
        pid = programs.create_program(self.user_id, 'Original', document(2, 2), activate=True)
        session = sessions.start_day(self.user_id)
        wid = session['workout_id']
        snapshot = session['snapshot']
        programs.update_program(self.user_id, pid, name='New', days=document(6, 1))
        self.assertEqual(sessions.get_session(self.user_id, wid)['snapshot'], snapshot)
        programs.delete_program(self.user_id, pid)
        self.assertEqual(sessions.get_session(self.user_id, wid)['snapshot'], snapshot)
        session = sessions.complete_exercise(self.user_id, wid, 0, SETS)
        self.assertEqual(session['snapshot']['exercises'][1]['name'], 'Bench occurrence 2')
        self.assertIsNone(sessions.complete_exercise(self.user_id, wid, session['revision'], SETS))
        self.assertEqual(db.get_workout(self.user_id, wid)[1], 'Day 1')
        self.assertEqual(len(db.get_workout_sets(self.user_id, wid)), 2)
        with db._connection() as c:
            self.assertEqual(json.loads(c.execute('SELECT snapshot FROM workout_sessions WHERE workout_id=?', (wid,)).fetchone()[0]), snapshot)

    def test_repeated_exercises_progress_and_export_keep_distinct_sets(self):
        programs.create_program(self.user_id, 'Repeats', document(1, 2), activate=True)
        session = sessions.start_day(self.user_id)
        wid = session['workout_id']
        session = sessions.complete_exercise(self.user_id, wid, 0, SETS)
        sessions.complete_exercise(self.user_id, wid, session['revision'], SETS)
        self.assertEqual([r[-1] for r in db.get_history_sets(self.user_id, wid)], [0, 1])
        with patch.dict('os.environ', {'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': '111'}):
            workouts, sets = Mock(), Mock()
            workouts.get_all_values.return_value = [['header']]
            sets.get_all_values.return_value = [['header']]
            with patch.object(google_sheets, 'get_sheet', side_effect=[workouts, sets]):
                google_sheets.sync_workout(self.user_id, wid)
            exported = sets.append_rows.call_args.args[0]
            self.assertEqual(len(exported), 2)
            self.assertNotEqual(exported[0][5], exported[1][5])

    def test_postpone_restart_and_revision_preserve_current_step(self):
        programs.create_program(self.user_id, 'Restart', document(1, 3), activate=True)
        session = sessions.start_day(self.user_id)
        wid = session['workout_id']
        session = sessions.postpone_exercise(self.user_id, wid, 0)
        self.assertEqual(session['progress']['current'], 1)
        self.assertEqual(session, sessions.get_session(self.user_id, wid))  # new connection, no FSM cache
        with self.assertRaises(db.WorkoutAccessError):
            sessions.complete_exercise(self.user_id, wid, 0, SETS)
        session = sessions.complete_exercise(self.user_id, wid, 1, SETS)
        self.assertEqual(session['progress']['current'], 0)
        session = sessions.complete_exercise(self.user_id, wid, session['revision'], SETS)
        self.assertEqual(session['progress']['current'], 2)
        self.assertIsNone(sessions.complete_exercise(self.user_id, wid, session['revision'], SETS))

    def test_failed_completion_preserves_both_sets_and_progress(self):
        programs.create_program(self.user_id, 'Atomic', document(1), activate=True)
        session = sessions.start_day(self.user_id)
        with self.assertRaises(KeyError):
            sessions.complete_exercise(self.user_id, session['workout_id'], 0, [*SETS, {}])
        self.assertEqual(sessions.get_session(self.user_id, session['workout_id']), session)
        self.assertEqual(db.get_workout_sets(self.user_id, session['workout_id']), [])
        with self.assertRaises(db.WorkoutAccessError):
            db.save_set(self.user_id, session['workout_id'], 'Bypass', 1, 20, 10)

    def test_empty_days_invalid_templates_and_failed_edit_are_atomic(self):
        with self.assertRaises(ValueError):
            programs.create_program(self.user_id, 'Empty', [], activate=True)
        pid = programs.create_program(self.user_id, 'Keep', document(2), activate=True)
        before = programs.get_program(self.user_id, pid)
        for days in ([], [dict(name='Empty', exercises=[])], [dict(name='Invalid', exercises=[dict(exercise_id='missing')])]):
            with self.assertRaises(ValueError):
                programs.update_program(self.user_id, pid, name='Changed', days=days)
            self.assertEqual(programs.get_program(self.user_id, pid), before)

    def test_legacy_workouts_without_snapshot_are_not_guessed(self):
        wid = db.create_workout(self.user_id, 'A')
        with self.assertRaises(db.WorkoutAccessError):
            sessions.get_session(self.user_id, wid)
        db.finish_workout(self.user_id, wid)

    def test_cancel_snapshot_workout_removes_children_without_advancing_cycle(self):
        programs.create_program(self.user_id, 'Cancel', document(2, 2), activate=True)
        session = sessions.start_day(self.user_id)
        wid = session['workout_id']
        sessions.complete_exercise(self.user_id, wid, 0, SETS)
        db.cancel_workout(self.user_id, wid)
        self.assertEqual(programs.next_day(self.user_id)['name'], 'Day 1')
        with db._connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM workout_sessions').fetchone(), (0,))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM workout_set_occurrences').fetchone(), (0,))
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_foreign_keys_and_reorder_document(self):
        pid = programs.create_program(self.user_id, 'Order', document(3, 2), activate=True)
        plan = programs.get_program(self.user_id, pid)
        replacement = list(reversed(plan['days']))
        replacement[0]['exercises'].reverse()
        programs.update_program(self.user_id, pid, days=replacement)
        updated = programs.get_program(self.user_id, pid)
        self.assertEqual([d['name'] for d in updated['days']], ['Day 3', 'Day 2', 'Day 1'])
        self.assertEqual(updated['days'][0]['exercises'][0]['name'], 'Bench occurrence 2')
        self.assertEqual(updated['days'][0]['exercises'][0]['working_plan'], document(1)[0]['exercises'][0]['working_plan'])
        with db._connection(True) as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO program_days(program_id,name,position) VALUES (999,'bad',0)")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute('INSERT INTO program_day_exercises(day_id,exercise_id,name,position,working_sets) VALUES (?,999,?,99,1)', (updated['days'][0]['id'], 'Bad'))

    def test_pagination_reaches_arbitrary_days(self):
        from keyboards.workout_menu import workout_menu
        pid = programs.create_program(self.user_id, 'Large', document(50), activate=True)
        program = programs.get_program(self.user_id, pid)
        seen = []
        for page in range(5):
            menu = workout_menu(programs.list_programs(self.user_id), program, 0, page)
            seen.extend(int(b.callback_data.split(':')[1]) for row in menu.inline_keyboard for b in row if b.callback_data.startswith('day:'))
        self.assertTrue({d['id'] for d in program['days']} <= set(seen))


class MigrationV2Tests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='life-bot-v2-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'v1.sqlite3'
        create_legacy_database(self.path)
        migrate(self.path, 111)
        self.c = sqlite3.connect(self.path)
        self.addCleanup(self.c.close)
        self.c.execute('ALTER TABLE exercises ADD COLUMN image_url TEXT')
        self.c.execute("INSERT INTO exercises(external_id,name_en,image_url) VALUES ('old','Old','preserve')")
        self.c.execute("INSERT INTO workouts(id,user_id,workout_type,started_at) VALUES (7,1,'A','2026-01-01')")
        self.c.execute("INSERT INTO workout_sets(id,workout_id,exercise,set_number,weight,reps,created_at) VALUES (8,7,'Old',1,20,10,'original')")
        self.c.commit()

    def test_repeat_has_no_changes(self):
        migrate_v2(self.path)
        before = '\n'.join(self.c.iterdump())
        self.assertFalse(migrate_v2(self.path))
        self.assertEqual('\n'.join(self.c.iterdump()), before)

    def test_repeat_refuses_partial_v2(self):
        migrate_v2(self.path)
        self.c.execute('DROP TABLE program_imports')
        self.c.commit()
        with self.assertRaises(ValueError):
            migrate_v2(self.path)

    def test_migration_changes_no_existing_table_or_history(self):
        names = [r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence','schema_migrations')")]
        before = {name: (self.c.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone(), self.c.execute(f'SELECT * FROM "{name}"').fetchall()) for name in names}
        v1 = self.c.execute('SELECT * FROM schema_migrations WHERE version=1').fetchone()
        self.assertTrue(migrate_v2(self.path))
        self.assertFalse(migrate_v2(self.path))
        for name, expected in before.items():
            actual = (self.c.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone(), self.c.execute(f'SELECT * FROM "{name}"').fetchall())
            self.assertEqual(actual, expected)
        self.assertEqual(self.c.execute('SELECT * FROM schema_migrations WHERE version=1').fetchone(), v1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM programs').fetchone(), (0,))

    def test_migration_rolls_back_ddl_if_final_validation_fails(self):
        before = '\n'.join(self.c.iterdump())
        check = migrations_v2._check
        calls = []

        def fail_final(c):
            check(c)
            calls.append(True)
            if len(calls) == 2:
                raise ValueError('Simulated failure')

        with patch.object(migrations_v2, '_check', side_effect=fail_final):
            with self.assertRaises(ValueError):
                migrate_v2(self.path)
        self.assertEqual('\n'.join(self.c.iterdump()), before)
        self.assertTrue(migrate_v2(self.path))
