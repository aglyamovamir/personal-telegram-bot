"""Explicit offline migration. Never imported or run by bot startup."""

import argparse
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1


def _check(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("SQLite foreign_key_check failed")


def _rebuild(connection, table, owner_id):
    # Retain original column declarations, including columns unknown to init_db.
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    if table == "daily_metrics":
        sql, count = re.subn(
            r'\bdate\s+TEXT\s+NOT\s+NULL\s+UNIQUE\b',
            'date TEXT NOT NULL', sql, flags=re.IGNORECASE,
        )
        if count != 1:
            raise ValueError("Unsupported daily_metrics date constraint; manual review required")
    objects = connection.execute(
        "SELECT type, sql FROM sqlite_master WHERE tbl_name=? "
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL", (table,)
    ).fetchall()
    # A custom unique date index would retain global uniqueness. Refuse rather
    # than silently discard a constraint we do not understand.
    if table == "daily_metrics":
        for row in connection.execute('PRAGMA index_list("daily_metrics")'):
            if row[2] and row[3] == 'c':
                raise ValueError("Custom unique metrics index requires manual review")
    sequence = connection.execute(
        "SELECT seq FROM sqlite_sequence WHERE name=?", (table,)
    ).fetchone()
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    if "user_id" in columns:
        raise ValueError("Unversioned user_id found; refusing partial migration")
    quoted = ', '.join('"' + name.replace('"', '""') + '"' for name in columns)
    new_name = "_multiuser_" + table
    sql = re.sub(
        r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"[^"]+"|`[^`]+`|\[[^]]+\]|\w+)',
        f'CREATE TABLE "{new_name}"', sql, count=1, flags=re.IGNORECASE,
    )
    end = sql.rfind(")")
    constraints = ', user_id INTEGER NOT NULL REFERENCES users(id)'
    if table == "daily_metrics":
        constraints += ', UNIQUE(user_id, date)'
    connection.execute(sql[:end] + constraints + sql[end:])
    connection.execute(
        f'INSERT INTO "{new_name}" ({quoted}, user_id) '
        f'SELECT {quoted}, ? FROM "{table}"', (owner_id,)
    )
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(f'ALTER TABLE "{new_name}" RENAME TO "{table}"')
    for _, object_sql in objects:
        connection.execute(object_sql)
    if sequence:
        connection.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (sequence[0], table))


def migrate(database_path, owner_telegram_id):
    """Migrate one existing file atomically; return True only on first run."""
    if isinstance(owner_telegram_id, bool) or not isinstance(owner_telegram_id, int) or owner_telegram_id <= 0:
        raise ValueError("Owner Telegram ID must be a positive integer")
    path = Path(database_path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    try:
        # Disable before BEGIN only for the documented SQLite table rebuild.
        # Explicit foreign_key_check runs before COMMIT; no other connection
        # inherits this setting. BEGIN IMMEDIATE excludes concurrent writers.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        _check(connection)
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "schema_migrations" in tables:
            rows = connection.execute(
                "SELECT version, owner_telegram_id FROM schema_migrations ORDER BY version"
            ).fetchall()
            if rows:
                if rows != [(VERSION, owner_telegram_id)]:
                    raise ValueError("Migration version or initial owner does not match")
                connection.commit()
                return False
        if not {"workouts", "workout_sets", "daily_metrics", "bot_settings"} <= tables:
            raise ValueError("Expected legacy schema not found")
        if tables & {"users", "user_settings", "bot_settings_legacy", "_multiuser_workouts", "_multiuser_daily_metrics"}:
            raise ValueError("Conflicting tables; manual schema review required")
        connection.execute("""CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL UNIQUE,
            private_chat_id INTEGER,
            timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
            created_at TEXT NOT NULL
        )""")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        owner_id = connection.execute(
            "INSERT INTO users(telegram_user_id, created_at) VALUES (?, ?)",
            (owner_telegram_id, now),
        ).lastrowid
        _rebuild(connection, "workouts", owner_id)
        _rebuild(connection, "daily_metrics", owner_id)
        connection.execute("""CREATE TABLE user_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            morning_time TEXT NOT NULL,
            evening_time TEXT NOT NULL,
            morning_enabled INTEGER NOT NULL DEFAULT 1 CHECK(morning_enabled IN (0, 1)),
            evening_enabled INTEGER NOT NULL DEFAULT 1 CHECK(evening_enabled IN (0, 1))
        )""")
        connection.execute(
            "INSERT INTO user_settings(user_id, morning_time, evening_time) "
            "SELECT ?, morning_time, evening_time FROM bot_settings", (owner_id,)
        )
        # Preserve every old setting (also unexpected columns), but do not assume
        # the former recipient chat is the owner's private chat: it may be a group.
        connection.execute("ALTER TABLE bot_settings RENAME TO bot_settings_legacy")
        connection.execute("CREATE INDEX workouts_user_started ON workouts(user_id, started_at)")
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            owner_telegram_id INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        )""")
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (VERSION, owner_telegram_id, now))
        _check(connection)
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="Offline migration of an explicitly selected SQLite COPY")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--owner-telegram-id", required=True, type=int)
    parser.add_argument("--confirm-offline-copy", required=True, action="store_true",
                        help="Confirm backup exists and bot is not using this file")
    args = parser.parse_args()
    changed = migrate(args.database, args.owner_telegram_id)
    print("Migration v1 applied" if changed else "Migration v1 already applied; no changes")


if __name__ == "__main__":
    main()
