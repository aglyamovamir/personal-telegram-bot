import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import db
from database.migrations import migrate
from database.migrations_v2 import migrate_v2
from database.migrations_v3 import migrate_v3
from database.migrations_v4 import migrate_v4
from legacy_schema import create_legacy_database


class MigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="life-bot-migration-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "legacy.sqlite3"
        create_legacy_database(self.path)
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        c = self.connection
        c.execute("ALTER TABLE exercises ADD COLUMN image_url TEXT")
        c.execute("INSERT INTO exercises(id, external_id, name_en, image_url) VALUES (7, 'bench', 'Bench', 'keep-me')")
        c.execute("INSERT INTO exercise_tags VALUES (7, 'strength')")
        c.execute("INSERT INTO workouts VALUES (12, 'A', '2026-01-01T12:00:00', NULL)")
        c.execute("INSERT INTO workouts VALUES (13, 'B', '2026-01-02T12:00:00', '2026-01-02T13:00:00')")
        c.execute("INSERT INTO workout_sets VALUES (42, 12, 'Bench', 1, 1, 20, 10, 'original', 'bench')")
        c.execute("INSERT INTO daily_metrics(id,date,sleep_start,sleep_score,productivity,steps,created_at,updated_at) VALUES (51,'2026-01-01','23:00',88,7.5,1234,'created','updated')")
        c.execute("INSERT INTO bot_settings VALUES (1, -999, '07:30', '22:00')")
        c.execute("UPDATE sqlite_sequence SET seq=100 WHERE name IN ('workouts','daily_metrics')")
        c.commit()

    def snapshot(self, table):
        return self.connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()

    def test_preserves_data_and_catalog_and_initializes_owner(self):
        tables = ('workouts', 'workout_sets', 'daily_metrics', 'exercises', 'exercise_tags', 'bot_settings')
        before = {name: self.snapshot(name) for name in tables}
        catalog_sql = self.connection.execute("SELECT sql FROM sqlite_master WHERE name='exercises'").fetchone()
        self.assertTrue(migrate(self.path, 123456))
        for table in ('workouts', 'daily_metrics'):
            self.assertEqual([r[:-1] for r in self.snapshot(table)], before[table])
            self.assertTrue(all(r[-1] == 1 for r in self.snapshot(table)))
        for table in ('workout_sets', 'exercises', 'exercise_tags'):
            self.assertEqual(self.snapshot(table), before[table])
        self.assertEqual(self.snapshot('bot_settings_legacy'), before['bot_settings'])
        self.assertEqual(self.connection.execute("SELECT sql FROM sqlite_master WHERE name='exercises'").fetchone(), catalog_sql)
        self.assertEqual(self.connection.execute('SELECT telegram_user_id,private_chat_id,timezone FROM users').fetchone(), (123456, None, 'Europe/Moscow'))
        self.assertEqual(self.snapshot('user_settings'), [(1, '07:30', '22:00', 1, 1)])
        self.assertEqual(self.connection.execute("SELECT seq FROM sqlite_sequence WHERE name='workouts'").fetchone(), (100,))
        self.assertEqual(self.connection.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_two_users_same_date_and_workout_ownership(self):
        migrate(self.path, 123456)
        c = self.connection
        c.execute('PRAGMA foreign_keys=ON')
        other = c.execute("INSERT INTO users(telegram_user_id,created_at) VALUES (654321,'now')").lastrowid
        c.execute("INSERT INTO daily_metrics(user_id,date,created_at,updated_at) VALUES (?,'2026-01-01','now','now')", (other,))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO daily_metrics(user_id,date,created_at,updated_at) VALUES (?,'2026-01-01','now','now')", (other,))
        self.assertEqual(c.execute("SELECT s.id FROM workout_sets s JOIN workouts w ON w.id=s.workout_id WHERE w.user_id=?", (other,)).fetchall(), [])
        self.assertEqual(c.execute("SELECT s.id FROM workout_sets s JOIN workouts w ON w.id=s.workout_id WHERE w.user_id=1").fetchall(), [(42,)])
        other_workout = c.execute(
            "INSERT INTO workouts(workout_type,started_at,user_id) VALUES ('A','now',?)", (other,)
        ).lastrowid
        self.assertEqual(c.execute('SELECT user_id FROM workouts WHERE id=?', (other_workout,)).fetchone(), (other,))
        c.execute("INSERT INTO user_settings(user_id,morning_time,evening_time) VALUES (?,'09:00','23:00')", (other,))
        self.assertEqual(c.execute('SELECT morning_time FROM user_settings ORDER BY user_id').fetchall(), [('07:30',), ('09:00',)])
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE workout_sets SET workout_id=999 WHERE id=42")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO workouts(workout_type,started_at,user_id) VALUES ('A','now',999)")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO workouts(workout_type,started_at) VALUES ('A','now')")

    def test_repeat_is_noop_and_different_owner_is_rejected(self):
        migrate(self.path, 123456)
        before = '\n'.join(self.connection.iterdump())
        self.assertFalse(migrate(self.path, 123456))
        self.assertEqual('\n'.join(self.connection.iterdump()), before)
        with self.assertRaises(ValueError):
            migrate(self.path, 654321)
        self.assertEqual('\n'.join(self.connection.iterdump()), before)

    def test_failure_after_rebuild_rolls_back_all_changes(self):
        # The second settings row violates the new one-settings-row-per-owner
        # constraint, after both history tables have already been rebuilt.
        c = self.connection
        c.execute('DROP TABLE bot_settings')
        c.execute('CREATE TABLE bot_settings(id INTEGER, chat_id INTEGER, morning_time TEXT, evening_time TEXT)')
        c.executemany('INSERT INTO bot_settings VALUES (?,1,?,?)', [(1,'07:00','22:00'), (2,'08:00','23:00')])
        c.commit()
        before = '\n'.join(c.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            migrate(self.path, 123456)
        self.assertEqual('\n'.join(c.iterdump()), before)

    def test_orphan_sets_rejected_without_changes(self):
        self.connection.execute('UPDATE workout_sets SET workout_id=999')
        self.connection.commit()
        before = '\n'.join(self.connection.iterdump())
        with self.assertRaises(ValueError):
            migrate(self.path, 123456)
        self.assertEqual('\n'.join(self.connection.iterdump()), before)

    def test_startup_accepts_migrated_database_without_changes(self):
        migrate(self.path, 123456)
        migrate_v2(self.path)
        migrate_v3(self.path)
        migrate_v4(self.path)
        before = '\n'.join(self.connection.iterdump())
        with patch.object(db, 'DB_PATH', str(self.path)):
            db.init_db()
        self.assertEqual('\n'.join(self.connection.iterdump()), before)

    def test_missing_file_is_not_created(self):
        missing = self.path.parent / 'missing.sqlite3'
        with self.assertRaises(FileNotFoundError):
            migrate(missing, 123456)
        self.assertFalse(missing.exists())

    def test_extra_history_columns_indexes_and_empty_settings(self):
        c = self.connection
        c.execute('ALTER TABLE workouts ADD COLUMN note TEXT')
        c.execute("UPDATE workouts SET note='keep history'")
        c.execute('ALTER TABLE daily_metrics ADD COLUMN custom_value REAL')
        c.execute('UPDATE daily_metrics SET custom_value=1.25')
        c.execute('CREATE INDEX custom_metrics_steps ON daily_metrics(steps)')
        c.execute('DELETE FROM bot_settings')
        c.commit()
        before_workouts = self.snapshot('workouts')
        before_metrics = self.snapshot('daily_metrics')
        migrate(self.path, 123456)
        self.assertEqual([r[:-1] for r in self.snapshot('workouts')], before_workouts)
        self.assertEqual([r[:-1] for r in self.snapshot('daily_metrics')], before_metrics)
        self.assertEqual(self.snapshot('user_settings'), [])
        self.assertIsNotNone(c.execute("SELECT sql FROM sqlite_master WHERE name='custom_metrics_steps'").fetchone())
        self.assertEqual(c.execute("SELECT seq FROM sqlite_sequence WHERE name='daily_metrics'").fetchone(), (100,))

    def test_final_validation_failure_rolls_back(self):
        from database import migrations
        original_check = migrations._check
        calls = []

        def fail_final_check(connection):
            original_check(connection)
            calls.append(True)
            if len(calls) == 2:
                raise ValueError('Simulated final validation failure')

        before = '\n'.join(self.connection.iterdump())
        with patch.object(migrations, '_check', side_effect=fail_final_check):
            with self.assertRaises(ValueError):
                migrate(self.path, 123456)
        self.assertEqual(len(calls), 2)
        self.assertEqual('\n'.join(self.connection.iterdump()), before)

    def test_invalid_owner_is_rejected(self):
        before = '\n'.join(self.connection.iterdump())
        for owner in (0, -1, True, '123456'):
            with self.assertRaises(ValueError):
                migrate(self.path, owner)
        self.assertEqual('\n'.join(self.connection.iterdump()), before)

    def test_unknown_unique_index_rejected_atomically(self):
        self.connection.execute('CREATE UNIQUE INDEX custom_unique_date ON daily_metrics(date)')
        self.connection.commit()
        before = '\n'.join(self.connection.iterdump())
        with self.assertRaises(ValueError):
            migrate(self.path, 123456)
        self.assertEqual('\n'.join(self.connection.iterdump()), before)
