from copy import deepcopy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import db, programs, sessions, migrations_v4
from database.migrations import migrate
from database.migrations_v2 import migrate_v2
from database.migrations_v3 import migrate_v3
from database.migrations_v4 import migrate_v4
from legacy_schema import create_legacy_database
from program_fixtures import seed_catalog
from test_database import TemporaryDatabaseTestCase
from test_programs import document, SETS


def simple_days(count=2, repeats=3):
    return [dict(name=f'Custom day {i}', exercises=[dict(exercise_id='bench-press', name=f'Bench {j}')
            for j in range(repeats)]) for i in range(count)]


class PlanTestCase(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        seed_catalog()
        self.other = db.get_or_create_user(222, 222)['id']



class SimpleProgramTests(PlanTestCase):
    def test_no_templates_for_any_day_count_and_auto_transition(self):
        for count in (1, 2, 3, 4, 6):
            pid = programs.create_program(self.user_id, 'Simple', simple_days(count), activate=True)
            plan = programs.get_program(self.user_id, pid)
            self.assertEqual(len(plan['days']), count)
            for day in plan['days']:
                for exercise in day['exercises']:
                    self.assertIsNone(exercise['working_sets'])
                    self.assertEqual(exercise['templates'], [])
            session = sessions.start_day(self.user_id)
            for step in range(3):
                self.assertEqual(session['progress']['current'], step)
                result = sessions.complete_exercise(self.user_id, session['workout_id'], session['revision'], SETS)
                if step == 2:
                    self.assertIsNone(result)
                else:
                    session = result

    def test_legacy_templates_survive_roundtrip(self):
        pid = programs.create_program(self.user_id, 'Legacy', document(2), activate=True)
        plan = programs.get_program(self.user_id, pid)
        programs.update_program(self.user_id, pid, days=deepcopy(plan['days']))
        updated = programs.get_program(self.user_id, pid)
        self.assertEqual(updated['days'][0]['exercises'][0]['working_sets'], 2)
        self.assertEqual(updated['days'][0]['exercises'][0]['working_plan'], plan['days'][0]['exercises'][0]['working_plan'])
        self.assertEqual(updated['days'][0]['exercises'][0]['warmup'], plan['days'][0]['exercises'][0]['warmup'])
        session = sessions.start_day(self.user_id)
        self.assertEqual(session['snapshot']['exercises'][0]['working_sets'], 2)

    def test_edit_and_reorder_preserve_next_day_identity(self):
        pid = programs.create_program(self.user_id, 'Cycle', simple_days(4), activate=True)
        session = sessions.start_day(self.user_id)
        db.finish_workout(self.user_id, session['workout_id'])
        plan = programs.get_program(self.user_id, pid)
        next_id = plan['days'][1]['id']
        edited = deepcopy(plan['days'])
        edited[1]['name'] = 'Renamed next'
        edited[0]['exercises'].reverse()
        programs.update_program(self.user_id, pid, days=list(reversed(edited)))
        self.assertEqual(programs.next_day(self.user_id)['id'], next_id)
        self.assertEqual(programs.next_day(self.user_id)['name'], 'Renamed next')

    def test_deleted_next_day_chooses_old_successor_and_preserves_snapshots(self):
        pid = programs.create_program(self.user_id, 'Cycle', simple_days(4), activate=True)
        active = sessions.start_day(self.user_id)
        plan = programs.get_program(self.user_id, pid)
        successor = plan['days'][1]['id']
        programs.update_program(self.user_id, pid, days=plan['days'][1:])
        self.assertEqual(programs.next_day(self.user_id)['id'], successor)
        self.assertEqual(sessions.get_session(self.user_id, active['workout_id'])['snapshot'], active['snapshot'])
        db.finish_workout(self.user_id, active['workout_id'])
        self.assertEqual(programs.next_day(self.user_id)['id'], successor)
        new_plan = programs.get_program(self.user_id, pid)
        programs.update_program(self.user_id, pid, days=new_plan['days'][1:])
        self.assertEqual(programs.next_day(self.user_id)['id'], new_plan['days'][1]['id'])

    def test_removing_last_next_day_wraps_and_all_new_days_start_at_first(self):
        pid = programs.create_program(self.user_id, 'Cycle', simple_days(3), activate=True)
        plan = programs.get_program(self.user_id, pid)
        session = sessions.start_day(self.user_id, plan['days'][1]['id'])
        db.finish_workout(self.user_id, session['workout_id'])  # Next is third.
        programs.update_program(self.user_id, pid, days=plan['days'][:2])
        self.assertEqual(programs.next_day(self.user_id)['id'], plan['days'][0]['id'])
        programs.update_program(self.user_id, pid, days=simple_days(2))
        self.assertEqual(programs.next_day(self.user_id)['position'], 0)

    def test_foreign_day_ids_cannot_be_smuggled_in_an_owned_document(self):
        own = programs.create_program(self.user_id, 'Mine', simple_days())
        other = programs.create_program(self.other, 'Other', simple_days())
        before = programs.get_program(self.user_id, own)
        with self.assertRaises(programs.ProgramAccessError):
            programs.update_program(self.user_id, own, days=programs.get_program(self.other, other)['days'])
        self.assertEqual(programs.get_program(self.user_id, own), before)

    def test_optional_template_changes_do_not_reset_cycle(self):
        pid = programs.create_program(self.user_id, 'Cycle', document(3), activate=True)
        first = sessions.start_day(self.user_id)
        db.finish_workout(self.user_id, first['workout_id'])
        before_history = db.get_workout(self.user_id, first['workout_id'])
        plan = programs.get_program(self.user_id, pid)
        next_id = plan['days'][1]['id']
        plan['days'][0]['exercises'][0]['working_plan'][0]['weight'] = 77.5
        plan['days'][1]['name'] = 'Renamed'
        programs.update_program(self.user_id, pid, name='New name', days=plan['days'])
        self.assertEqual(programs.next_day(self.user_id)['id'], next_id)
        self.assertEqual(db.get_workout(self.user_id, first['workout_id']), before_history)


class OccurrenceIdentityTests(PlanTestCase):
    def test_repeated_exercises_have_separate_owned_occurrence_links(self):
        days = simple_days(1, 3)
        for exercise in days[0]['exercises']:
            exercise['name'] = 'Same bench'
        programs.create_program(self.user_id, 'Repeated', days, activate=True)
        session = sessions.start_day(self.user_id)
        wid = session['workout_id']
        snapshot = deepcopy(session['snapshot'])
        for weight in (50, 60):
            session = sessions.complete_exercise(self.user_id, wid, session['revision'],
                                                 [dict(weight=weight, reps=8, is_warmup=False)])
        first = sessions.get_occurrence_sets(self.user_id, wid, 0)
        second = sessions.get_occurrence_sets(self.user_id, wid, 1)
        self.assertNotEqual(first[0][0], second[0][0])
        self.assertEqual(first[0][3], 50)
        self.assertEqual(second[0][3], 60)
        self.assertEqual(sessions.get_occurrence_sets(self.user_id, wid, 2), [])
        with self.assertRaises(db.WorkoutAccessError):
            sessions.get_occurrence_sets(self.other, wid, 0)
        with self.assertRaises(db.WorkoutAccessError):
            sessions.get_occurrence_sets(self.user_id, wid, -1)
        db.finish_workout(self.user_id, wid)
        self.assertEqual(sessions.get_occurrence_sets(self.user_id, wid, 0), first)
        with db._connection() as c:
            self.assertEqual(json.loads(c.execute('SELECT snapshot FROM workout_sessions WHERE workout_id=?', (wid,)).fetchone()[0]), snapshot)


class MigrationV4Tests(unittest.TestCase):
    def test_preserves_v3_rows_templates_extra_columns_and_old_snapshots(self):
        with tempfile.TemporaryDirectory(prefix='life-bot-v4-') as directory:
            path = Path(directory) / 'v3.sqlite3'
            create_legacy_database(path)
            migrate(path, 111)
            migrate_v2(path)
            migrate_v3(path)
            with patch.object(db, 'DB_PATH', str(path)):
                seed_catalog()
                programs.create_program(1, 'Old', document(2, 2), activate=True)
                session = sessions.start_day(1)
                sessions.complete_exercise(1, session['workout_id'], 0, SETS)
                c = db.get_connection()
                try:
                    with c:
                        c.execute('ALTER TABLE program_day_exercises ADD COLUMN extra TEXT')
                        c.execute("UPDATE program_day_exercises SET extra='keep'")
                        c.execute("UPDATE sqlite_sequence SET seq=999 WHERE name='program_day_exercises'")
                    names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('schema_migrations','sqlite_sequence')")]
                    before = {name: c.execute(f'SELECT * FROM "{name}"').fetchall() for name in names}
                    dump = '\n'.join(c.iterdump())
                    check = migrations_v4._check
                    calls = []

                    def fail_final(connection):
                        check(connection)
                        calls.append(True)
                        if len(calls) == 2:
                            raise ValueError('Simulated failure')

                    with patch.object(migrations_v4, '_check', side_effect=fail_final):
                        with self.assertRaises(ValueError):
                            migrate_v4(path)
                    self.assertEqual('\n'.join(c.iterdump()), dump)
                    self.assertTrue(migrate_v4(path))
                    self.assertFalse(migrate_v4(path))
                    for name in names:
                        self.assertEqual(c.execute(f'SELECT * FROM "{name}"').fetchall(), before[name], name)
                    self.assertEqual(c.execute("SELECT seq FROM sqlite_sequence WHERE name='program_day_exercises'").fetchone(), (999,))
                    self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
                    db.init_db()
                finally:
                    c.close()
