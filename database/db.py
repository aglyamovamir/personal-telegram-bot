"""User-scoped SQLite v4 API. No automatic migrations."""
import os
import sqlite3
import json
from contextlib import contextmanager
from datetime import date as Date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from workouts.presentation import local_datetime

DB_PATH = os.getenv("DB_PATH", "database/database.db")
METRIC_FIELDS = ('sleep_start', 'wake_time', 'sleep_duration_minutes', 'sleep_score', 'productivity', 'stress', 'mood', 'steps', 'screen_time_minutes')


class WorkoutAccessError(ValueError):
    pass


def get_connection():
    c = sqlite3.connect(Path(DB_PATH).resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
    c.execute('PRAGMA foreign_keys=ON')
    return c


@contextmanager
def _connection(write=False):
    c = get_connection()
    try:
        if write:
            c.execute('BEGIN IMMEDIATE')
        yield c
        if write:
            c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    """Validate existing v4 schema; never create or migrate data on startup."""
    try:
        with _connection() as c:
            if c.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall() != [(1,), (2,), (3,), (4,)]:
                raise RuntimeError('Требуется схема v4. Явно выполните миграции v1–v4 после проверки на копии.')
            required = {
                'users': {'id', 'telegram_user_id', 'private_chat_id', 'timezone', 'created_at'},
                'workouts': {'id', 'workout_type', 'started_at', 'finished_at', 'user_id'},
                'workout_sets': {'id', 'workout_id', 'exercise', 'exercise_id', 'set_number', 'is_warmup', 'weight', 'reps', 'created_at'},
                'daily_metrics': set(METRIC_FIELDS) | {'id', 'user_id', 'date', 'created_at', 'updated_at'},
                'user_settings': {'user_id', 'morning_time', 'evening_time', 'morning_enabled', 'evening_enabled'},
                'programs': {'id', 'user_id', 'name', 'is_active', 'next_day_position', 'archived_at', 'revision'},
                'program_days': {'id', 'program_id', 'name', 'position'},
                'program_day_exercises': {'id', 'day_id', 'exercise_id', 'name', 'position', 'working_sets'},
                'program_set_templates': {'id', 'day_exercise_id', 'position', 'is_warmup', 'weight', 'reps'},
                'workout_sessions': {'workout_id', 'program_id', 'day_id', 'snapshot', 'progress', 'revision'},
                'workout_set_occurrences': {'set_id', 'workout_id', 'exercise_position'},
                'program_imports': {'user_id', 'source', 'program_id'},
            }
            for table, columns in required.items():
                info = c.execute(f'PRAGMA table_info("{table}")').fetchall()
                if not columns <= {r[1] for r in info}:
                    raise RuntimeError(f'Неполная схема v1: {table}')
                if table in ('workouts', 'daily_metrics') and not next(r[3] for r in info if r[1] == 'user_id'):
                    raise RuntimeError(f'user_id должен быть обязательным: {table}')
            unique = []
            if next(r[3] for r in c.execute('PRAGMA table_info(program_day_exercises)') if r[1] == 'working_sets'):
                raise RuntimeError('В схеме v4 число плановых подходов должно допускать NULL.')
            for index in c.execute('PRAGMA index_list(daily_metrics)').fetchall():
                if index[2]:
                    name = index[1].replace('"', '""')
                    unique.append([r[2] for r in c.execute(f'PRAGMA index_info("{name}")')])
            if ['user_id', 'date'] not in unique or ['date'] in unique:
                raise RuntimeError('Неверная уникальность daily_metrics')
            if not c.execute('SELECT 1 FROM users u JOIN schema_migrations m ON m.owner_telegram_id=u.telegram_user_id WHERE m.version=1').fetchone():
                raise RuntimeError('Владелец миграции не найден в users.')
            foreign_keys = [
                ('workouts', 'user_id', 'users', 'id'),
                ('daily_metrics', 'user_id', 'users', 'id'),
                ('workout_sets', 'workout_id', 'workouts', 'id'),
                ('user_settings', 'user_id', 'users', 'id'),
                ('programs', 'user_id', 'users', 'id'),
                ('program_days', 'program_id', 'programs', 'id'),
                ('program_day_exercises', 'day_id', 'program_days', 'id'),
                ('program_day_exercises', 'exercise_id', 'exercises', 'id'),
                ('program_set_templates', 'day_exercise_id', 'program_day_exercises', 'id'),
                ('workout_sessions', 'workout_id', 'workouts', 'id'),
                ('workout_set_occurrences', 'set_id', 'workout_sets', 'id'),
                ('workout_set_occurrences', 'workout_id', 'workout_sessions', 'workout_id'),
            ]
            for table, column, target, target_column in foreign_keys:
                if not any(r[2] == target and r[3] == column and r[4] == target_column for r in c.execute(f'PRAGMA foreign_key_list("{table}")')):
                    raise RuntimeError(f'Нет внешнего ключа: {table}.{column}')
            if c.execute('PRAGMA foreign_key_check').fetchall() or c.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('Проверка целостности БД не пройдена.')
    except sqlite3.Error as error:
        raise RuntimeError('БД отсутствует или имеет старую схему. Явно выполните миграции v1–v4 по database/MIGRATIONS.md после проверки на копии.') from error


def get_initial_owner_telegram_id():
    with _connection() as c:
        row = c.execute('SELECT owner_telegram_id FROM schema_migrations WHERE version=1').fetchone()
        return row[0] if row else None


def _user(c, user_id):
    c.row_factory = sqlite3.Row
    row = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    c.row_factory = None
    if row is None:
        raise ValueError('Пользователь не найден')
    return dict(row)


def get_user(user_id):
    with _connection() as c:
        return _user(c, user_id)


def get_or_create_user(telegram_user_id, private_chat_id):
    if telegram_user_id <= 0 or private_chat_id != telegram_user_id:
        raise ValueError('Требуется личный чат пользователя')
    with _connection(True) as c:
        c.execute('INSERT INTO users(telegram_user_id,private_chat_id,created_at) VALUES (?,?,?) ON CONFLICT(telegram_user_id) DO UPDATE SET private_chat_id=excluded.private_chat_id', (telegram_user_id, private_chat_id, datetime.now(ZoneInfo('UTC')).isoformat(timespec='seconds')))
        uid = c.execute('SELECT id FROM users WHERE telegram_user_id=?', (telegram_user_id,)).fetchone()[0]
        return _user(c, uid)


def _now(c, user_id):
    return datetime.now(ZoneInfo(_user(c, user_id)['timezone'])).isoformat(timespec='seconds')


def user_today(user_id):
    with _connection() as c:
        return datetime.fromisoformat(_now(c, user_id)).date().isoformat()


def _workout(c, user_id, workout_id, active=False):
    row = c.execute('SELECT id,workout_type,started_at,finished_at FROM workouts WHERE id=? AND user_id=?', (workout_id, user_id)).fetchone()
    if row is None or (active and row[3] is not None):
        raise WorkoutAccessError('Активная тренировка не найдена или недоступна.')
    return row


def get_workout(user_id, workout_id):
    with _connection() as c:
        return _workout(c, user_id, workout_id)


def get_active_workout(user_id):
    with _connection() as c:
        return c.execute('SELECT id,workout_type,started_at,finished_at FROM workouts WHERE user_id=? AND finished_at IS NULL ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()


def create_workout(user_id, workout_type):
    with _connection(True) as c:
        if c.execute('SELECT 1 FROM workouts WHERE user_id=? AND finished_at IS NULL', (user_id,)).fetchone():
            raise WorkoutAccessError('Уже есть незавершённая тренировка. Используйте /exit или /cancel.')
        return c.execute('INSERT INTO workouts(workout_type,started_at,user_id) VALUES (?,?,?)', (workout_type, _now(c, user_id), user_id)).lastrowid


def _insert_set(c, workout_id, exercise, number, weight, reps, warmup, exercise_id, now):
    c.execute('INSERT INTO workout_sets(workout_id,exercise,exercise_id,set_number,is_warmup,weight,reps,created_at) VALUES (?,?,?,?,?,?,?,?)', (workout_id, exercise, exercise_id, number, int(warmup), weight, reps, now))


def save_set(user_id, workout_id, exercise, set_number, weight, reps, is_warmup=False, exercise_id=None):
    with _connection(True) as c:
        _workout(c, user_id, workout_id, active=True)
        _require_legacy_workout(c, workout_id)
        _insert_set(c, workout_id, exercise, set_number, weight, reps, is_warmup, exercise_id, _now(c, user_id))


def save_exercise_sets(user_id, workout_id, exercise, exercise_id, sets):
    # All sets in one message commit atomically.
    with _connection(True) as c:
        _workout(c, user_id, workout_id, active=True)
        _require_legacy_workout(c, workout_id)
        now = _now(c, user_id)
        for number, item in enumerate(sets, 1):
            _insert_set(c, workout_id, exercise, number, item['weight'], item['reps'], item['is_warmup'], exercise_id, now)


def _require_legacy_workout(c, workout_id):
    if c.execute('SELECT 1 FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone():
        raise WorkoutAccessError('Для тренировки со снимком используйте sessions.complete_exercise: подходы и прогресс должны сохраняться вместе.')


def finish_workout(user_id, workout_id):
    with _connection(True) as c:
        _finish_workout(c, user_id, workout_id)


def _finish_workout(c, user_id, workout_id):
    from database.sessions import advance_cycle
    _workout(c, user_id, workout_id, active=True)
    c.execute('UPDATE workouts SET finished_at=? WHERE id=? AND user_id=?', (_now(c, user_id), workout_id, user_id))
    advance_cycle(c, user_id, workout_id)


def cancel_workout(user_id, workout_id, *, expected_revision=None):
    with _connection(True) as c:
        _workout(c, user_id, workout_id, active=True)
        if expected_revision is not None:
            row = c.execute('SELECT revision FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone()
            if row is None or row[0] != expected_revision:
                raise WorkoutAccessError('Тренировка изменилась. Удаление отменено; откройте /resume и при необходимости повторите /cancel.')
        c.execute('DELETE FROM workout_sets WHERE workout_id=?', (workout_id,))
        c.execute('DELETE FROM workouts WHERE id=? AND user_id=?', (workout_id, user_id))


def get_workout_revision(user_id, workout_id):
    """None identifies a legacy workout without a snapshot/session."""
    with _connection() as c:
        _workout(c, user_id, workout_id, active=True)
        row = c.execute('SELECT revision FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone()
        return row[0] if row else None


def get_last_workout_by_type(user_id, workout_type):
    with _connection() as c:
        c.execute('BEGIN')
        zone = _user(c, user_id)['timezone']
        records = c.execute('SELECT id,workout_type,started_at,finished_at FROM workouts WHERE user_id=? AND workout_type=? AND finished_at IS NOT NULL', (user_id, workout_type)).fetchall()
        return max(records, key=lambda row: _workout_order(row, zone), default=None)


def get_last_workout(user_id):
    with _connection() as c:
        return c.execute('SELECT id,workout_type,started_at,finished_at FROM workouts WHERE user_id=? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()


def get_recent_workouts(user_id, limit=30):
    with _connection() as c:
        return c.execute('SELECT id,workout_type,started_at,finished_at FROM workouts WHERE user_id=? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT ?', (user_id, limit)).fetchall()


def get_history_sets(user_id, workout_id):
    with _connection() as c:
        c.execute('BEGIN')
        _workout(c, user_id, workout_id)
        return c.execute('''SELECT ws.exercise,ws.exercise_id,ws.set_number,ws.is_warmup,ws.weight,ws.reps,o.exercise_position
            FROM workout_sets ws JOIN workouts w ON w.id=ws.workout_id
            LEFT JOIN workout_set_occurrences o ON o.set_id=ws.id AND o.workout_id=ws.workout_id
            WHERE w.user_id=? AND w.id=? ORDER BY ws.id''', (user_id, workout_id)).fetchall()


def get_workout_sets(user_id, workout_id, *, export=False):
    with _connection() as c:
        _workout(c, user_id, workout_id)
        fields = 'ws.id,ws.exercise_id,ws.exercise,ws.set_number,ws.is_warmup,ws.weight,ws.reps' if export else 'ws.exercise,ws.exercise_id,ws.set_number,ws.is_warmup,ws.weight,ws.reps'
        return c.execute(f'SELECT {fields} FROM workout_sets ws JOIN workouts w ON w.id=ws.workout_id WHERE w.user_id=? AND w.id=? ORDER BY ws.id', (user_id, workout_id)).fetchall()


def get_last_exercise_sets(user_id, exercise_id):
    """Compatibility API; detailed displays use get_previous_exercise_result."""
    result = get_previous_exercise_result(user_id, exercise_id)
    return [(item['weight'], item['reps']) for group in result['occurrences']
            for item in group['sets']] if result else []


def _workout_order(workout, timezone_name):
    """Valid start instants first, then stable ID; invalid dates remain readable."""
    try:
        started, _ = local_datetime(workout[2], timezone_name)
        if started is None:
            return (0, datetime.min.replace(tzinfo=timezone.utc), workout[0])
        started = started.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return (0, datetime.min.replace(tzinfo=timezone.utc), workout[0])
    return (1, started, workout[0])


def _history_occurrences(c, workout_id):
    rows = c.execute('''SELECT s.exercise,s.exercise_id,s.set_number,
        s.is_warmup,s.weight,s.reps,o.exercise_position FROM workout_sets s
        LEFT JOIN workout_set_occurrences o ON o.set_id=s.id AND o.workout_id=s.workout_id
        WHERE s.workout_id=? ORDER BY s.id''', (workout_id,)).fetchall()
    groups, linked = [], {}
    previous = None
    for name, external_id, number, warmup, weight, reps, position in rows:
        if position is not None:
            # A correction can append replacement rows after other occurrences.
            # An explicit persisted link still identifies the same inclusion.
            key = (position, external_id, name)
            group = linked.get(key)
            if group is None:
                group = dict(position=position, name=name, exercise_id=external_id, sets=[])
                linked[key] = group
                groups.append(group)
        else:
            # Legacy positions are unknown. Only contiguous recorded rows and
            # the saved set-number reset establish a boundary; never match a plan.
            same_block = (previous is not None and previous[-1] is None
                          and previous[:2] == (name, external_id)
                          and type(number) is int and type(previous[2]) is int
                          and number > previous[2])
            if same_block:
                group = groups[-1]
            else:
                group = dict(position=None, name=name, exercise_id=external_id, sets=[])
                groups.append(group)
        group['sets'].append(dict(set_number=number, is_warmup=bool(warmup), weight=weight, reps=reps))
        previous = (name, external_id, number, position)
    if groups and all(group['position'] is not None for group in groups):
        groups.sort(key=lambda group: group['position'])
    return groups


def _history_workout(c, user_id, workout_id, *, timezone_name=None):
    workout = _workout(c, user_id, workout_id)
    if workout[3] is None:
        raise WorkoutAccessError('Тренировка ещё не завершена.')
    program_name = None
    row = c.execute('SELECT snapshot FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone()
    if row:
        try:
            snapshot = json.loads(row[0])
            if isinstance(snapshot, dict) and isinstance(snapshot.get('program_name'), str):
                program_name = snapshot['program_name']
        except (TypeError, ValueError):
            pass
    return dict(workout_id=workout_id, day_name=workout[1], started_at=workout[2],
                finished_at=workout[3], timezone=timezone_name or _user(c, user_id)['timezone'],
                program_name=program_name, occurrences=_history_occurrences(c, workout_id))


def get_history_workout(user_id, workout_id):
    """Read one owned completed workout and all recorded occurrences consistently."""
    with _connection() as c:
        c.execute('BEGIN')
        return _history_workout(c, user_id, workout_id)


def get_previous_exercise_result(user_id, exercise_id):
    """Latest owned completed workout with actual facts for this catalog ID.

    The current day, custom names, planned exercises and active workouts never
    determine the result. A legacy NULL ID cannot be retroactively matched.
    """
    if not isinstance(exercise_id, str) or not exercise_id:
        return None
    with _connection() as c:
        c.execute('BEGIN')
        zone = _user(c, user_id)['timezone']
        workouts = c.execute('''SELECT w.id,w.workout_type,w.started_at,w.finished_at
            FROM workouts w WHERE w.user_id=? AND w.finished_at IS NOT NULL
            AND EXISTS(SELECT 1 FROM workout_sets s WHERE s.workout_id=w.id AND s.exercise_id=?)''',
                             (user_id, exercise_id)).fetchall()
        latest = max(workouts, key=lambda row: _workout_order(row, zone), default=None)
        if latest is None:
            return None
        result = _history_workout(c, user_id, latest[0], timezone_name=zone)
        result['occurrences'] = [group for group in result['occurrences']
                                 if group['exercise_id'] == exercise_id]
        return result


def _save_metrics(user_id, date, values):
    Date.fromisoformat(date)
    if not set(values) <= set(METRIC_FIELDS):
        raise ValueError('Недопустимое поле')
    fields = list(values)
    with _connection(True) as c:
        now = _now(c, user_id)
        columns = ','.join(fields)
        assignments = ','.join(f'{field}=excluded.{field}' for field in fields)
        marks = ','.join('?' for _ in fields)
        c.execute(f'INSERT INTO daily_metrics(user_id,date,{columns},created_at,updated_at) VALUES (?,?,{marks},?,?) ON CONFLICT(user_id,date) DO UPDATE SET {assignments},updated_at=excluded.updated_at', (user_id, date, *values.values(), now, now))


def save_morning_metrics(user_id, date, sleep_start, wake_time, sleep_duration_minutes, sleep_score):
    _save_metrics(user_id, date, dict(sleep_start=sleep_start, wake_time=wake_time, sleep_duration_minutes=sleep_duration_minutes, sleep_score=sleep_score))


def save_evening_metrics(user_id, date, productivity, stress, mood, steps, screen_time_minutes):
    _save_metrics(user_id, date, dict(productivity=productivity, stress=stress, mood=mood, steps=steps, screen_time_minutes=screen_time_minutes))


def get_daily_metrics(user_id, date):
    Date.fromisoformat(date)
    with _connection() as c:
        return c.execute(f'SELECT date,{",".join(METRIC_FIELDS)} FROM daily_metrics WHERE user_id=? AND date=?', (user_id, date)).fetchone()


def update_daily_metric(user_id, date, field, value):
    Date.fromisoformat(date)
    if field not in METRIC_FIELDS:
        raise ValueError('Недопустимое поле')
    with _connection(True) as c:
        return c.execute(f'UPDATE daily_metrics SET {field}=?,updated_at=? WHERE user_id=? AND date=?', (value, _now(c, user_id), user_id, date)).rowcount > 0


def has_workout_on_date(user_id, date):
    target = Date.fromisoformat(date)
    zone = ZoneInfo(get_user(user_id)['timezone'])
    with _connection() as c:
        rows = c.execute('SELECT started_at FROM workouts WHERE user_id=? AND finished_at IS NOT NULL', (user_id,)).fetchall()
    for (value,) in rows:
        started = datetime.fromisoformat(value)
        if started.tzinfo is None:
            started = started.replace(tzinfo=zone)  # Preserved legacy wall-clock time.
        if started.astimezone(zone).date() == target:
            return True
    return False


def save_user_settings(user_id, morning_time, evening_time):
    for value in (morning_time, evening_time):
        datetime.strptime(value, '%H:%M')
    with _connection(True) as c:
        c.execute('INSERT INTO user_settings(user_id,morning_time,evening_time) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET morning_time=excluded.morning_time,evening_time=excluded.evening_time', (user_id, morning_time, evening_time))


def get_user_settings(user_id):
    with _connection() as c:
        return c.execute('SELECT morning_time,evening_time,morning_enabled,evening_enabled FROM user_settings WHERE user_id=?', (user_id,)).fetchone()


def notification_users():
    with _connection() as c:
        return c.execute('SELECT u.id FROM users u JOIN user_settings s ON s.user_id=u.id WHERE u.private_chat_id IS NOT NULL').fetchall()


def finish_legacy_workout(user_id, workout_id):
    """Owner-checked, atomic completion of an old workout; never deletes sets."""
    with _connection(True) as c:
        _workout(c, user_id, workout_id, active=True)
        if c.execute('SELECT 1 FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone():
            raise WorkoutAccessError('Кнопка устарела: эта тренировка имеет снимок.')
        _finish_workout(c, user_id, workout_id)


def get_history_days(user_id):
    """Group by stable snapshot day identity; only imported A/B/C absorbs legacy labels."""
    with _connection() as c:
        c.execute('BEGIN')
        zone = _user(c, user_id)['timezone']
        groups = {}
        legacy_days = {}
        imported = c.execute('''SELECT p.id FROM program_imports i JOIN programs p
            ON p.id=i.program_id AND p.user_id=i.user_id
            WHERE i.user_id=? AND i.source='legacy-workouts-v1' ''', (user_id,)).fetchone()
        if imported and imported[0] is not None:
            legacy_days = dict(c.execute("SELECT name,id FROM program_days WHERE program_id=? AND name IN ('A','B','C')", (imported[0],)))
        for pid, pname, did, name in c.execute(
                'SELECT p.id,p.name,d.id,d.name FROM programs p JOIN program_days d ON d.program_id=p.id WHERE p.user_id=? AND p.archived_at IS NULL ORDER BY p.id,d.position', (user_id,)):
            groups[f'd:{did}'] = dict(key=f'd:{did}', name=name, program=pname, workouts=[])
        records = c.execute('SELECT w.id,w.workout_type,w.started_at,w.finished_at,s.snapshot FROM workouts w LEFT JOIN workout_sessions s ON s.workout_id=w.id WHERE w.user_id=? AND w.finished_at IS NOT NULL', (user_id,)).fetchall()
        records.sort(key=lambda row: _workout_order(row, zone), reverse=True)
        # Keep legacy day callbacks stable when a newer workout is appended.
        legacy_keys = {}
        for wid, label, started, finished, raw in records:
            if raw is None:
                legacy_keys[label] = min(wid, legacy_keys.get(label, wid))
        for wid, label, started, finished, raw in records:
            if raw is not None:
                identity = _snapshot_day_identity(c, user_id, raw)
                if identity:
                    key, name, pname = identity
                else:
                    # Malformed metadata does not justify borrowing an unrelated
                    # day with the same name, and must not hide recorded facts.
                    key, name, pname = f'w:{wid}', label, 'Сохранённая история'
            elif label in legacy_days:
                key = 'd:' + str(legacy_days[label])
                name, pname = label, 'A/B/C'
            else:
                key = 'l:' + str(legacy_keys[label])
                name, pname = label, 'Старая история'
            group = groups.setdefault(key, dict(key=key, name=name, program=pname, workouts=[]))
            group['workouts'].append((wid, label, started, finished))
        counts = {}
        for group in groups.values():
            counts[group['name']] = counts.get(group['name'], 0) + 1
        for group in groups.values():
            group['title'] = group['name'] if counts[group['name']] == 1 else f"{group['name']} · {group['program']} [{group['key'][2:]}]"
        return list(groups.values())


def _snapshot_day_identity(c, user_id, raw):
    try:
        snapshot = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None
    day_id = snapshot.get('day_id')
    name, program_name = snapshot.get('day_name'), snapshot.get('program_name')
    if type(day_id) is not int or day_id <= 0 or not isinstance(name, str) or not isinstance(program_name, str):
        return None
    owner = c.execute('''SELECT p.user_id FROM program_days d JOIN programs p
        ON p.id=d.program_id WHERE d.id=?''', (day_id,)).fetchone()
    if owner and owner[0] != user_id:
        return None
    return f'd:{day_id}', name, program_name
