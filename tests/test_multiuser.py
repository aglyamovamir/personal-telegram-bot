import sqlite3
import unittest
from unittest.mock import Mock, patch

from database import db
import google_sheets
from database.migrations import migrate
from legacy_schema import create_legacy_database
from test_database import TemporaryDatabaseTestCase


class MultiuserTests(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.friend = db.get_or_create_user(222, 222)['id']
        self.day = '2026-11-09'

    def test_registration_preserves_initial_owner_and_does_not_reassign_history(self):
        workout = db.create_workout(self.user_id, 'A')
        self.assertEqual(db.get_or_create_user(111, 111)['id'], self.user_id)
        self.assertNotEqual(self.friend, 222)  # Internal ID is not a Telegram ID.
        self.assertEqual(db.get_workout(self.user_id, workout)[0], workout)
        self.assertIsNone(db.get_active_workout(self.friend))
        self.assertEqual(db.get_initial_owner_telegram_id(), 111)

    def test_metrics_same_date_reads_updates_and_partial_upserts_are_isolated(self):
        for uid, score in ((self.user_id, 80), (self.friend, 40)):
            db.save_morning_metrics(uid, self.day, '23:00', '07:00', 480, score)
        db.save_evening_metrics(self.friend, self.day, 5, 6, 7, 9000, 60)
        db.update_daily_metric(self.user_id, self.day, 'steps', 1234)
        own = db.get_daily_metrics(self.user_id, self.day)
        friend = db.get_daily_metrics(self.friend, self.day)
        self.assertEqual((own[4], own[8]), (80, 1234))
        self.assertEqual((friend[4], friend[8]), (40, 9000))
        db.save_morning_metrics(self.friend, self.day, '22:00', '06:00', 480, 50)
        self.assertEqual(db.get_daily_metrics(self.friend, self.day)[8], 9000)
        self.assertFalse(db.update_daily_metric(self.friend, '2026-01-01', 'steps', 10))
        with self.assertRaises(ValueError):
            db.update_daily_metric(self.friend, self.day, 'user_id', self.user_id)

    def test_history_and_previous_exercise_are_personal(self):
        ids = []
        for uid, weight in ((self.user_id, 20), (self.friend, 90)):
            wid = db.create_workout(uid, 'A')
            db.save_set(uid, wid, 'Bench', 1, weight, 10, exercise_id='bench-press')
            db.finish_workout(uid, wid)
            ids.append(wid)
        self.assertEqual(db.get_last_workout(self.user_id)[0], ids[0])
        self.assertEqual(db.get_last_workout_by_type(self.friend, 'A')[0], ids[1])
        self.assertEqual(db.get_last_exercise_sets(self.user_id, 'bench-press'), [(20, 10)])
        self.assertEqual(db.get_last_exercise_sets(self.friend, 'bench-press'), [(90, 10)])

    def test_all_foreign_workout_operations_are_denied(self):
        wid = db.create_workout(self.user_id, 'A')
        db.save_set(self.user_id, wid, 'Bench', 1, 20, 10)
        operations = (
            lambda: db.get_workout(self.friend, wid),
            lambda: db.get_workout_sets(self.friend, wid),
            lambda: db.get_workout_sets(self.friend, wid, export=True),
            lambda: db.save_set(self.friend, wid, 'Bench', 2, 90, 10),
            lambda: db.save_exercise_sets(self.friend, wid, 'Bench', 'bench', []),
            lambda: db.finish_workout(self.friend, wid),
            lambda: db.cancel_workout(self.friend, wid),
        )
        for operation in operations:
            with self.assertRaises(db.WorkoutAccessError):
                operation()
        self.assertEqual(len(db.get_workout_sets(self.user_id, wid)), 1)
        self.assertIsNone(db.get_workout(self.user_id, wid)[3])

    def test_completed_workout_cannot_be_changed_and_duplicate_active_is_rejected(self):
        wid = db.create_workout(self.user_id, 'A')
        with self.assertRaises(db.WorkoutAccessError):
            db.create_workout(self.user_id, 'B')
        db.finish_workout(self.user_id, wid)
        for operation in (lambda: db.finish_workout(self.user_id, wid),
                          lambda: db.cancel_workout(self.user_id, wid),
                          lambda: db.save_set(self.user_id, wid, 'Bench', 1, 10, 10)):
            with self.assertRaises(db.WorkoutAccessError):
                operation()

    def test_batch_write_rolls_back_and_cancel_deletes_only_owned_sets(self):
        wid = db.create_workout(self.user_id, 'A')
        with self.assertRaises(KeyError):
            db.save_exercise_sets(self.user_id, wid, 'Bench', 'bench', [
                dict(weight=20, reps=10, is_warmup=False), {},
            ])
        self.assertEqual(db.get_workout_sets(self.user_id, wid), [])
        other = db.create_workout(self.friend, 'A')
        db.save_set(self.friend, other, 'Bench', 1, 90, 10)
        db.cancel_workout(self.user_id, wid)
        self.assertEqual(len(db.get_workout_sets(self.friend, other)), 1)

    def test_settings_are_personal(self):
        db.save_user_settings(self.user_id, '07:00', '22:00')
        db.save_user_settings(self.friend, '09:00', '23:00')
        db.save_user_settings(self.friend, '10:00', '23:30')
        self.assertEqual(db.get_user_settings(self.user_id), ('07:00', '22:00', 1, 1))
        self.assertEqual(db.get_user_settings(self.friend), ('10:00', '23:30', 1, 1))

    def test_foreign_keys_enabled_on_every_connection(self):
        c = db.get_connection()
        try:
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone(), (1,))
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO workouts(user_id,workout_type,started_at) VALUES (999,'A','now')")
        finally:
            c.close()

    def test_day_flag_respects_owner_and_timezone(self):
        wid = db.create_workout(self.user_id, 'A')
        db.finish_workout(self.user_id, wid)
        c = db.get_connection()
        try:
            with c:
                c.execute("UPDATE workouts SET started_at='2026-11-08T22:30:00+00:00' WHERE id=?", (wid,))
        finally:
            c.close()
        self.assertTrue(db.has_workout_on_date(self.user_id, self.day))
        self.assertFalse(db.has_workout_on_date(self.user_id, '2026-11-08'))
        self.assertFalse(db.has_workout_on_date(self.friend, self.day))

    def test_google_disabled_without_explicit_matching_owner(self):
        for configured in ('', '222', 'invalid'):
            with patch.dict('os.environ', {'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': configured}):
                with patch.object(google_sheets, 'get_sheet') as sheet:
                    self.assertFalse(google_sheets.sync_daily_metrics(self.user_id, self.day))
                    self.assertFalse(google_sheets.sync_workout(self.friend, 123))
                    sheet.assert_not_called()

    def test_google_friend_and_foreign_workout_never_reach_network(self):
        own = db.create_workout(self.user_id, 'A')
        other = db.create_workout(self.friend, 'A')
        with patch.dict('os.environ', {'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': '111'}):
            with patch.object(google_sheets, 'get_sheet') as sheet:
                self.assertTrue(google_sheets.export_enabled(self.user_id))
                self.assertFalse(google_sheets.sync_daily_metrics(self.friend, self.day))
                self.assertFalse(google_sheets.sync_workout(self.friend, own))
                with self.assertRaises(db.WorkoutAccessError):
                    google_sheets.sync_workout(self.user_id, other)
                sheet.assert_not_called()
            with patch.object(google_sheets.gspread, 'authorize') as authorize:
                with self.assertRaises(PermissionError):
                    google_sheets.get_sheet(self.friend)
                authorize.assert_not_called()

    def test_owner_export_uses_only_owned_rows_with_mock_sheets(self):
        for uid, score in ((self.user_id, 80), (self.friend, 40)):
            db.save_morning_metrics(uid, self.day, '23:00', '07:00', 480, score)
        with patch.dict('os.environ', {'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': '111'}):
            sheet = Mock()
            sheet.get_all_values.return_value = [['date', 'sleep']]
            with patch.object(google_sheets, 'get_sheet', return_value=sheet):
                google_sheets.sync_daily_metrics(self.user_id, self.day)
            self.assertEqual(sheet.append_row.call_args.args[0][2], 80)

    def test_google_failure_does_not_undo_completion(self):
        wid = db.create_workout(self.user_id, 'A')
        db.finish_workout(self.user_id, wid)
        with patch.object(google_sheets, 'sync_workout', side_effect=RuntimeError('offline')):
            self.assertFalse(google_sheets.safe_sync_workout(self.user_id, wid))
        self.assertIsNotNone(db.get_workout(self.user_id, wid)[3])

    def test_owner_workout_export_uses_owned_sets_only(self):
        ids = []
        for uid, weight in ((self.user_id, 20), (self.friend, 90)):
            wid = db.create_workout(uid, 'A')
            db.save_set(uid, wid, 'Bench', 1, weight, 10, exercise_id='bench-press')
            db.finish_workout(uid, wid)
            ids.append(wid)
        with patch.dict('os.environ', {'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': '111'}):
            workouts, sets = Mock(), Mock()
            workouts.get_all_values.return_value = [['header']]
            sets.get_all_values.return_value = [['header']]
            with patch.object(google_sheets, 'get_sheet', side_effect=[workouts, sets]):
                google_sheets.sync_workout(self.user_id, ids[0])
            row = sets.append_rows.call_args.args[0][0]
            self.assertEqual((row[0], row[8], row[9]), (ids[0], 20, 10))
            self.assertEqual(len(sets.append_rows.call_args.args[0]), 1)

    def test_startup_rejects_legacy_missing_and_unknown_versions_without_changes(self):
        legacy = self.db_path.parent / 'legacy.sqlite3'
        create_legacy_database(legacy)
        with patch.object(db, 'DB_PATH', str(legacy)):
            with self.assertRaises(RuntimeError):
                db.init_db()

        c = sqlite3.connect(legacy)
        try:
            self.assertEqual(c.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchall(), [])
        finally:
            c.close()
        missing = self.db_path.parent / 'missing.sqlite3'
        with patch.object(db, 'DB_PATH', str(missing)):
            with self.assertRaises(RuntimeError):
                db.init_db()
        self.assertFalse(missing.exists())
        migrate(legacy, 111)
        c = sqlite3.connect(legacy)
        try:
            with c:
                c.execute('UPDATE schema_migrations SET version=99')
        finally:
            c.close()
        with patch.object(db, 'DB_PATH', str(legacy)):
            with self.assertRaises(RuntimeError):
                db.init_db()

    def test_import_history_has_no_import_side_effect_and_requires_owner(self):
        import importlib
        import sys
        with patch('sqlite3.connect', side_effect=AssertionError('Unexpected connection')):
            if 'import_workouts' in sys.modules:
                importer = sys.modules['import_workouts']
            else:
                importer = importlib.import_module('import_workouts')
        sample = dict(date=self.day, type='A', sets=[('Bench', 1, 0, 20, 10)])
        c = db.get_connection()
        try:
            with c:
                importer.import_workout(c, sample, self.user_id)
                importer.import_workout(c, sample, self.friend)
        finally:
            c.close()
        self.assertIsNotNone(db.get_last_workout(self.user_id))
        self.assertIsNotNone(db.get_last_workout(self.friend))
