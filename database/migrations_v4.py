"""Explicit v3 -> v4: allow programs without planned working sets."""
import argparse
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database.migrations import _check


def migrate_v4(database_path):
    path = Path(database_path).resolve(strict=True)
    c = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        # Rebuild only the plan table, preserving templates and all existing IDs.
        c.execute('PRAGMA foreign_keys=OFF')
        c.execute('BEGIN IMMEDIATE')
        _check(c)
        rows = c.execute('SELECT version,owner_telegram_id FROM schema_migrations ORDER BY version').fetchall()
        versions = [r[0] for r in rows]
        if versions not in ([1, 2, 3], [1, 2, 3, 4]) or len({r[1] for r in rows}) != 1:
            raise ValueError('Migration v4 requires schema v3')
        info = c.execute('PRAGMA table_info(program_day_exercises)').fetchall()
        if versions == [1, 2, 3, 4]:
            if not any(r[1] == 'working_sets' and not r[3] for r in info):
                raise ValueError('Incomplete schema v4')
            c.commit()
            return False
        source = c.execute("SELECT sql FROM sqlite_master WHERE name='program_day_exercises' AND type='table'").fetchone()
        if not source:
            raise ValueError('Missing plan table; manual schema review required')
        sql = source[0]
        sql, count = re.subn(r'working_sets\s+INTEGER\s+NOT\s+NULL\s+CHECK\s*\(working_sets\s*>\s*0\)',
                            'working_sets INTEGER CHECK(working_sets IS NULL OR working_sets>0)', sql, flags=re.I)
        if count != 1:
            raise ValueError('Unexpected plan constraint; manual review required')
        objects = c.execute("SELECT sql FROM sqlite_master WHERE tbl_name='program_day_exercises' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
        sequence = c.execute("SELECT seq FROM sqlite_sequence WHERE name='program_day_exercises'").fetchone()
        columns = ','.join('"' + r[1].replace('"', '""') + '"' for r in info)
        sql = re.sub(r'^(CREATE\s+TABLE\s+)(?:"program_day_exercises"|program_day_exercises)',
                     r'\1_new_program_day_exercises', sql, count=1, flags=re.I)
        c.execute(sql)
        c.execute(f'INSERT INTO _new_program_day_exercises ({columns}) SELECT {columns} FROM program_day_exercises')
        c.execute('DROP TABLE program_day_exercises')
        c.execute('ALTER TABLE _new_program_day_exercises RENAME TO program_day_exercises')
        for (object_sql,) in objects:
            c.execute(object_sql)
        if sequence:
            c.execute("UPDATE sqlite_sequence SET seq=? WHERE name='program_day_exercises'", sequence)
        c.execute('INSERT INTO schema_migrations VALUES (4,?,?)', (rows[0][1], datetime.now(timezone.utc).isoformat(timespec='seconds')))
        _check(c)
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser(description='Explicit v4 migration of an offline SQLite copy')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--confirm-offline-copy', action='store_true', required=True)
    args = parser.parse_args()
    print('Migration v4 applied' if migrate_v4(args.database) else 'Migration v4 already applied')


if __name__ == '__main__':
    main()
