"""Owned factual exercise history; isolated migrated SQLite databases only."""
import json

from database import db, programs, sessions
from database.import_legacy_program import import_legacy_program
from test_simple_programs import PlanTestCase


class WorkoutUxDatabaseTests(PlanTestCase):
    def legacy(self, day, started, rows=(), *, owner=None, finish=True):
        owner = self.user_id if owner is None else owner
        wid = db.create_workout(owner, day)
        for name, external_id, number, warmup, weight, reps in rows:
            db.save_set(owner, wid, name, number, weight, reps, warmup, external_id)
        if finish:
            db.finish_workout(owner, wid)
        with db._connection(True) as c:
            c.execute('UPDATE workouts SET started_at=? WHERE id=?', (started, wid))
        return wid

    def snapshot(self, day='B', *, ids=('bench-press',), owner=None):
        owner = self.user_id if owner is None else owner
        programs.create_program(owner, 'Source ' + day,
                                [dict(name=day, exercises=[dict(exercise_id=value, name='Same custom name')
                                                           for value in ids])], activate=True)
        return sessions.start_day(owner)

    def complete(self, session, weight=50, *, owner=None):
        owner = self.user_id if owner is None else owner
        return sessions.complete_exercise(owner, session['workout_id'], session['revision'],
                                          [dict(weight=20.5, reps=10, is_warmup=True),
                                           dict(weight=weight, reps=8, is_warmup=False)])

    def test_previous_result_crosses_days_uses_id_and_excludes_active_foreign_and_plans(self):
        source = self.legacy('Другой день', '2026-11-08T18:30:00+03:00',
                             [('Моё название', 'bench-press', 1, True, 20.5, 10),
                              ('Моё название', 'bench-press', 2, False, 0, 6)])
        self.legacy('Похожее имя', '2026-11-09T20:00:00+03:00',
                    [('Моё название', 'squat', 1, False, 100, 8),
                     ('Моё название', None, 1, False, 120, 8)])
        self.legacy('Чужой день', '2026-11-10T20:00:00+03:00',
                    [('Моё название', 'bench-press', 1, False, 200, 8)], owner=self.other)
        planned = self.snapshot('План без подходов')
        db.finish_workout(self.user_id, planned['workout_id'])
        active = self.snapshot('Текущая тренировка', ids=('bench-press', 'squat'))
        active = self.complete(active, 250)
        result = db.get_previous_exercise_result(self.user_id, 'bench-press')
        self.assertEqual(result['workout_id'], source)
        self.assertEqual(result['day_name'], 'Другой день')
        self.assertEqual(result['started_at'], '2026-11-08T18:30:00+03:00')
        self.assertEqual(result['timezone'], 'Europe/Moscow')
        self.assertEqual([item['weight'] for item in result['occurrences'][0]['sets']], [20.5, 0])
        self.assertEqual([item['is_warmup'] for item in result['occurrences'][0]['sets']], [True, False])
        self.assertEqual(db.get_active_workout(self.user_id)[0], active['workout_id'])

    def test_previous_latest_is_parsed_start_instant_and_stable_id_not_lexical_or_max_id(self):
        early = self.legacy('C', '2026-11-09T10:00:00+03:00', [('Жим', 'bench-press', 1, False, 40, 8)])
        latest = self.legacy('B', '2026-11-09T08:30:00+00:00', [('Жим', 'bench-press', 1, False, 50, 8)])
        self.legacy('A', '2026-11-08T23:00:00+00:00', [('Жим', 'bench-press', 1, False, 60, 8)])
        self.assertNotEqual(early, latest)
        self.assertEqual(db.get_previous_exercise_result(self.user_id, 'bench-press')['workout_id'], latest)
        tied = self.legacy('C', '2026-11-09T11:30:00+03:00', [('Жим', 'bench-press', 1, False, 70, 8)])
        self.assertEqual(db.get_previous_exercise_result(self.user_id, 'bench-press')['workout_id'], tied)
        self.assertEqual(db.get_last_exercise_sets(self.user_id, 'bench-press'), [(70, 8)])

    def test_naive_times_use_user_timezone_and_invalid_dates_have_deterministic_fallback(self):
        with db._connection(True) as c:
            c.execute('UPDATE users SET timezone=? WHERE id=?', ('Asia/Yekaterinburg', self.user_id))
        self.legacy('A', '2026-11-09T12:00:00', [('Жим', 'bench-press', 1, False, 40, 8)])
        later = self.legacy('A', '2026-11-09T08:00:00Z', [('Жим', 'bench-press', 1, False, 50, 8)])
        invalid = self.legacy('A', 'неизвестно', [('Жим', 'bench-press', 1, False, 60, 8)])
        missing = self.legacy('A', '', [('Жим', 'squat', 1, False, 70, 8)])
        self.assertEqual(db.get_previous_exercise_result(self.user_id, 'bench-press')['workout_id'], later)
        group = next(group for group in db.get_history_days(self.user_id) if group['name'] == 'A')
        self.assertEqual([row[0] for row in group['workouts']][-2:], [missing, invalid])
        with db._connection(True) as c:
            c.execute("UPDATE workouts SET started_at='' WHERE user_id=?", (self.user_id,))
        self.assertEqual(db.get_previous_exercise_result(self.user_id, 'bench-press')['workout_id'], invalid)
        self.assertEqual(db.get_last_workout_by_type(self.user_id, 'A')[0], missing)

    def test_repeated_linked_occurrences_remain_separate_after_correction_reorders_rows(self):
        session = self.snapshot(ids=('bench-press', 'squat', 'bench-press'))
        wid = session['workout_id']
        session = self.complete(session, 50)
        session = self.complete(session, 80)
        session = sessions.replace_previous_sets(self.user_id, wid, 1, session['revision'],
                                                 [dict(weight=85, reps=9, is_warmup=False)])
        self.assertIsNone(self.complete(session, 60))
        result = db.get_previous_exercise_result(self.user_id, 'bench-press')
        self.assertEqual([group['position'] for group in result['occurrences']], [0, 2])
        self.assertEqual([group['sets'][-1]['weight'] for group in result['occurrences']], [50, 60])
        whole = db.get_history_workout(self.user_id, wid)
        self.assertEqual([group['position'] for group in whole['occurrences']], [0, 1, 2])
        self.assertEqual(whole['occurrences'][1]['sets'][0]['weight'], 85)

    def test_legacy_occurrences_follow_contiguous_rows_and_number_resets_without_plan_guessing(self):
        wid = self.legacy('B', '2026-11-08T18:30:00', [
            ('Жим', 'bench-press', 1, True, 20, 10),
            ('Жим', 'bench-press', 2, False, 50, 8),
            ('Жим', 'bench-press', 1, False, 55, 8),
            ('Приседания', 'squat', 1, False, 70, 8),
            ('Жим', 'bench-press', 2, False, 60, 6),
            ('Старое без ID', None, 1, False, 0, 12),
        ])
        result = db.get_previous_exercise_result(self.user_id, 'bench-press')
        self.assertEqual([len(group['sets']) for group in result['occurrences']], [2, 1, 1])
        self.assertEqual([group['position'] for group in result['occurrences']], [None, None, None])
        whole = db.get_history_workout(self.user_id, wid)
        self.assertEqual(len(whole['occurrences']), 5)
        self.assertIsNone(whole['occurrences'][-1]['exercise_id'])
        self.assertEqual(whole['occurrences'][-1]['sets'][0]['weight'], 0)

    def test_unknown_and_null_ids_never_match_by_name(self):
        self.legacy('B', '2026-11-09T12:00:00', [('Жим штанги лёжа', None, 1, False, 50, 8)])
        self.assertIsNone(db.get_previous_exercise_result(self.user_id, 'bench-press'))
        self.assertIsNone(db.get_previous_exercise_result(self.user_id, None))
        self.assertIsNone(db.get_previous_exercise_result(self.user_id, ''))
        self.assertEqual(db.get_last_exercise_sets(self.user_id, 'bench-press'), [])

    def test_reading_invalid_timezone_and_date_only_is_consistent_with_display(self):
        valid = self.legacy('A', '2026-11-09T12:00:00Z', [('Жим', 'bench-press', 1, False, 50, 8)])
        date_only = self.legacy('A', '2030-12-31', [('Жим', 'bench-press', 1, False, 60, 8)])
        with db._connection(True) as c:
            c.execute('UPDATE users SET timezone=? WHERE id=?', ('Invalid/Time_Zone', self.user_id))
        self.assertEqual(db.get_previous_exercise_result(self.user_id, 'bench-press')['workout_id'], valid)
        group = next(group for group in db.get_history_days(self.user_id) if group['name'] == 'A')
        self.assertEqual([row[0] for row in group['workouts']], [valid, date_only])

    def test_history_owner_completed_and_snapshot_day_identity_are_preserved(self):
        first = self.snapshot('A')
        first_id = first['workout_id']
        self.complete(first)
        second = self.snapshot('A')
        second_id = second['workout_id']
        self.complete(second)
        with db._connection(True) as c:
            c.execute("UPDATE workouts SET started_at='2026-11-09T18:30:00+03:00' WHERE id=?", (first_id,))
            c.execute("UPDATE workouts SET started_at='2026-11-08T18:30:00+03:00' WHERE id=?", (second_id,))
        groups = [group for group in db.get_history_days(self.user_id) if group['name'] == 'A']
        self.assertEqual(len(groups), 2)
        self.assertNotEqual(groups[0]['key'], groups[1]['key'])
        self.assertEqual(sorted(len(group['workouts']) for group in groups), [1, 1])
        with self.assertRaises(db.WorkoutAccessError):
            db.get_history_workout(self.other, first_id)
        active = self.snapshot('New')
        with self.assertRaises(db.WorkoutAccessError):
            db.get_history_workout(self.user_id, active['workout_id'])
        self.assertEqual(db.get_history_days(self.other), [])

    def test_imported_abc_absorbs_only_legacy_and_latest_uses_timezone_instant(self):
        imported = import_legacy_program(self.user_id)
        old = self.legacy('A', '2026-11-09T11:00:00+03:00', [('Без ID', None, 1, False, 40, 8)])
        latest = self.legacy('A', '2026-11-09T08:30:00+00:00', [('Без ID', None, 1, False, 50, 8)])
        extra = self.snapshot('A')
        self.complete(extra)
        groups = [group for group in db.get_history_days(self.user_id) if group['name'] == 'A']
        self.assertEqual(len(groups), 2)
        original_day = programs.get_program(self.user_id, imported)['days'][0]['id']
        imported_group = next(group for group in groups if group['key'] == f'd:{original_day}')
        self.assertEqual([row[0] for row in imported_group['workouts']], [latest, old])

    def test_legacy_keys_stay_stable_and_corrupt_snapshot_does_not_hide_facts_or_mix_foreign_day(self):
        first = self.legacy('Legacy', '2026-11-08T18:30:00', [('Без ID', None, 1, False, 40, 8)])
        key = db.get_history_days(self.user_id)[0]['key']
        self.legacy('Legacy', '2026-11-09T18:30:00', [('Без ID', None, 1, False, 45, 8)])
        self.assertEqual(db.get_history_days(self.user_id)[0]['key'], key)
        source = self.snapshot('Corrupt')
        self.complete(source)
        foreign = self.snapshot('Чужой день', owner=self.other)
        with db._connection(True) as c:
            c.execute('UPDATE workout_sessions SET snapshot=? WHERE workout_id=?',
                      (json.dumps(foreign['snapshot']), source['workout_id']))
        groups = db.get_history_days(self.user_id)
        self.assertFalse(any(group['key'] == f"d:{foreign['snapshot']['day_id']}" for group in groups))
        with db._connection(True) as c:
            c.execute("UPDATE workout_sessions SET snapshot='not-json' WHERE workout_id=?", (source['workout_id'],))
        self.assertTrue(any(source['workout_id'] in [row[0] for row in group['workouts']]
                            for group in db.get_history_days(self.user_id)))
        self.assertEqual(db.get_history_workout(self.user_id, source['workout_id'])['occurrences'][0]['sets'][-1]['weight'], 50)
        self.assertEqual(db.get_workout(self.user_id, first)[1], 'Legacy')
