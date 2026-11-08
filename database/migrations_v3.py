"""Explicit v2 -> v3: program archive and optimistic editor revisions."""
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database.migrations import _check


def migrate_v3(database_path):
    path = Path(database_path).resolve(strict=True)
    c = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('BEGIN IMMEDIATE')
        _check(c)
        rows = c.execute('SELECT version,owner_telegram_id FROM schema_migrations ORDER BY version').fetchall()
        if [r[0] for r in rows] not in ([1, 2], [1, 2, 3]) or len({r[1] for r in rows}) != 1:
            raise ValueError('Migration v3 requires schema v2')
        if len(rows) == 3:
            if not {'archived_at', 'revision'} <= {r[1] for r in c.execute('PRAGMA table_info(programs)')}:
                raise ValueError('Incomplete schema v3')
            c.commit()
            return False
        c.execute('ALTER TABLE programs ADD COLUMN archived_at TEXT')
        c.execute('ALTER TABLE programs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0)')
        c.execute('INSERT INTO schema_migrations VALUES (3,?,?)',
                  (rows[0][1], datetime.now(timezone.utc).isoformat(timespec='seconds')))
        _check(c)
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser(description='Explicit editor migration of an offline SQLite copy')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--confirm-offline-copy', action='store_true', required=True)
    args = parser.parse_args()
    print('Migration v3 applied' if migrate_v3(args.database) else 'Migration v3 already applied')


if __name__ == '__main__':
    main()
