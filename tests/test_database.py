"""Smoke tests using a separate temporary database for every test."""

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


class TemporaryDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="life-bot-test-")
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "test.sqlite3"

        # Patch the value actually used by get_connection(), not the environment:
        # DB_PATH is read from the environment only when the module is imported.
        database_path = patch.object(db, "DB_PATH", str(self.db_path))
        database_path.start()
        self.addCleanup(database_path.stop)

        # Initialize only this new temporary file, never the development database.
        create_legacy_database(self.db_path)
        migrate(self.db_path, 111)
        migrate_v2(self.db_path)
        migrate_v3(self.db_path)
        migrate_v4(self.db_path)
        db.init_db()
        self.user_id = db.get_or_create_user(111, 111)["id"]


class DatabaseSmokeTests(TemporaryDatabaseTestCase):
    def test_connection_uses_temporary_database(self):
        connection = db.get_connection()
        try:
            path = connection.execute("PRAGMA database_list").fetchone()[2]
            self.assertEqual(Path(path).resolve(), self.db_path.resolve())
            count = connection.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            connection.close()

    def test_workout_and_set_are_saved(self):
        workout_id = db.create_workout(self.user_id, "A")
        db.save_set(
            self.user_id, workout_id, "Bench press", 1, 20, 10,
            is_warmup=True, exercise_id="bench-press",
        )

        self.assertEqual(
            db.get_workout_sets(self.user_id, workout_id),
            [("Bench press", "bench-press", 1, 1, 20.0, 10)],
        )
        # Read through a separate connection to verify that the write committed.
        connection = sqlite3.connect(str(self.db_path))
        try:
            workout = connection.execute(
                "SELECT workout_type, finished_at FROM workouts WHERE id = ?",
                (workout_id,),
            ).fetchone()
            self.assertEqual(workout, ("A", None))
        finally:
            connection.close()
