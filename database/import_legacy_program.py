"""Explicit, one-time owner-only import. Never called during registration."""
import argparse
from pathlib import Path

from database import db
from database.programs import _write_days


def import_legacy_program(user_id):
    from workouts.program import WORKOUTS

    with db._connection(True) as c:
        user = db._user(c, user_id)
        owner = c.execute('SELECT owner_telegram_id FROM schema_migrations WHERE version=1').fetchone()[0]
        if user['telegram_user_id'] != owner:
            raise db.WorkoutAccessError('A/B/C можно перенести только первоначальному владельцу истории.')
        previous = c.execute("SELECT program_id FROM program_imports WHERE user_id=? AND source='legacy-workouts-v1'", (user_id,)).fetchone()
        if previous:
            return previous[0]  # Even deletion does not silently recreate it.
        active = not c.execute('SELECT 1 FROM programs WHERE user_id=? AND is_active=1', (user_id,)).fetchone()
        pid = c.execute('INSERT INTO programs(user_id,name,is_active,created_at) VALUES (?,?,?,?)',
                        (user_id, 'A/B/C', int(active), db._now(c, user_id))).lastrowid
        _write_days(c, pid, [dict(name=name, exercises=exercises) for name, exercises in WORKOUTS.items()])
        c.execute("INSERT INTO program_imports VALUES (?,'legacy-workouts-v1',?)", (user_id, pid))
        return pid


def main():
    parser = argparse.ArgumentParser(description='Explicit A/B/C import for the original history owner')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--owner-telegram-id', type=int, required=True)
    parser.add_argument('--confirm-offline-copy', action='store_true', required=True)
    args = parser.parse_args()
    db.DB_PATH = str(args.database.resolve(strict=True))
    db.init_db()
    with db._connection() as c:
        row = c.execute('SELECT id FROM users WHERE telegram_user_id=?', (args.owner_telegram_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown owner')
    print('Imported program:', import_legacy_program(row[0]))


if __name__ == '__main__':
    main()
