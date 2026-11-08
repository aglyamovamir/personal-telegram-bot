"""Explicit v1 -> v2 migration. The v1 migrator remains unchanged."""
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database.migrations import _check

STATEMENTS = (
    """CREATE TABLE programs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0,1)),
        next_day_position INTEGER NOT NULL DEFAULT 0 CHECK(next_day_position>=0),
        created_at TEXT NOT NULL
    )""",
    'CREATE UNIQUE INDEX one_active_program ON programs(user_id) WHERE is_active=1',
    """CREATE TABLE program_days (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
        name TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0),
        UNIQUE(program_id,position)
    )""",
    """CREATE TABLE program_day_exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day_id INTEGER NOT NULL REFERENCES program_days(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id),
        name TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0),
        working_sets INTEGER NOT NULL CHECK(working_sets>0),
        UNIQUE(day_id,position)
    )""",
    """CREATE TABLE program_set_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day_exercise_id INTEGER NOT NULL REFERENCES program_day_exercises(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK(position>=0),
        is_warmup INTEGER NOT NULL CHECK(is_warmup IN (0,1)),
        weight REAL, reps INTEGER CHECK(reps IS NULL OR reps>0),
        UNIQUE(day_exercise_id,position)
    )""",
    """CREATE TABLE workout_sessions (
        workout_id INTEGER PRIMARY KEY REFERENCES workouts(id) ON DELETE CASCADE,
        program_id INTEGER REFERENCES programs(id) ON DELETE SET NULL,
        day_id INTEGER REFERENCES program_days(id) ON DELETE SET NULL,
        snapshot TEXT NOT NULL,
        progress TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0)
    )""",
    """CREATE TABLE workout_set_occurrences (
        set_id INTEGER PRIMARY KEY REFERENCES workout_sets(id) ON DELETE CASCADE,
        workout_id INTEGER NOT NULL REFERENCES workout_sessions(workout_id) ON DELETE CASCADE,
        exercise_position INTEGER NOT NULL CHECK(exercise_position>=0)
    )""",
    """CREATE TABLE program_imports (
        user_id INTEGER NOT NULL REFERENCES users(id),
        source TEXT NOT NULL,
        program_id INTEGER REFERENCES programs(id) ON DELETE SET NULL,
        PRIMARY KEY(user_id,source)
    )""",
)


def _validate_tables(c):
    required = {
        'programs': {'id', 'user_id', 'name', 'is_active', 'next_day_position'},
        'program_days': {'id', 'program_id', 'name', 'position'},
        'program_day_exercises': {'id', 'day_id', 'exercise_id', 'working_sets'},
        'program_set_templates': {'id', 'day_exercise_id', 'position', 'is_warmup', 'weight', 'reps'},
        'workout_sessions': {'workout_id', 'program_id', 'day_id', 'snapshot', 'progress', 'revision'},
        'workout_set_occurrences': {'set_id', 'workout_id', 'exercise_position'},
        'program_imports': {'user_id', 'source', 'program_id'},
    }
    for table, columns in required.items():
        if not columns <= {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}:
            raise ValueError(f'Incomplete v2 schema: {table}; manual review required')


def migrate_v2(database_path):
    path = Path(database_path).resolve(strict=True)
    c = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('BEGIN IMMEDIATE')
        _check(c)
        versions = c.execute('SELECT version,owner_telegram_id FROM schema_migrations ORDER BY version').fetchall()
        if len(versions) == 2 and [r[0] for r in versions] == [1, 2] and versions[0][1] == versions[1][1]:
            _validate_tables(c)
            c.commit()
            return False
        if len(versions) != 1 or versions[0][0] != 1:
            raise ValueError('Migration v2 requires exactly schema v1')
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'users', 'workouts', 'workout_sets', 'daily_metrics', 'user_settings', 'exercises'} <= tables:
            raise ValueError('Incomplete v1 schema; manual review required')
        for statement in STATEMENTS:
            c.execute(statement)
        c.execute('INSERT INTO schema_migrations VALUES (2,?,?)',
                  (versions[0][1], datetime.now(timezone.utc).isoformat(timespec='seconds')))
        _validate_tables(c)
        _check(c)
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser(description='Explicit v2 migration of an offline SQLite copy')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--confirm-offline-copy', action='store_true', required=True)
    args = parser.parse_args()
    print('Migration v2 applied' if migrate_v2(args.database) else 'Migration v2 already applied')


if __name__ == '__main__':
    main()
