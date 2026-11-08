"""Immutable plan snapshot plus transactional, restart-safe training progress."""
import json

from database import db
from database.programs import _read, ProgramAccessError


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _session(c, user_id, workout_id, revision=None):
    workout = db._workout(c, user_id, workout_id, active=True)
    row = c.execute('SELECT snapshot,progress,revision FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone()
    if row is None:
        raise db.WorkoutAccessError('У старой тренировки нет снимка. Используйте /exit или /cancel.')
    if revision is not None and revision != row[2]:
        raise db.WorkoutAccessError('Этот шаг уже изменился. Откройте тренировку заново.')
    return dict(workout_id=workout_id, workout=workout[1], snapshot=json.loads(row[0]),
                progress=json.loads(row[1]), revision=row[2])


def get_session(user_id, workout_id):
    with db._connection() as c:
        return _session(c, user_id, workout_id)


def start_day(user_id, day_id=None):
    """Without day_id choose the next day of the active program, not a weekday."""
    with db._connection(True) as c:
        if c.execute('SELECT 1 FROM workouts WHERE user_id=? AND finished_at IS NULL', (user_id,)).fetchone():
            raise db.WorkoutAccessError('Уже есть активная тренировка: откройте её или используйте /exit, /cancel.')
        if day_id is None:
            row = c.execute('SELECT id FROM programs WHERE user_id=? AND is_active=1 AND archived_at IS NULL', (user_id,)).fetchone()
        else:
            row = c.execute('SELECT p.id FROM programs p JOIN program_days d ON d.program_id=p.id WHERE p.user_id=? AND d.id=? AND p.archived_at IS NULL', (user_id, day_id)).fetchone()
        if not row:
            raise ProgramAccessError('Программа или тренировочный день недоступны.')
        program = _read(c, user_id, row[0])
        days = program['days']
        if not days:
            raise ProgramAccessError('Программа не содержит дней.')
        day = next((d for d in days if d['id'] == day_id), None) if day_id is not None else days[program['next_day_position'] % len(days)]
        if not day or not day['exercises']:
            raise ProgramAccessError('В тренировочном дне нет упражнений.')
        wid = c.execute('INSERT INTO workouts(user_id,workout_type,started_at) VALUES (?,?,?)',
                        (user_id, day['name'], db._now(c, user_id))).lastrowid
        snapshot = dict(version=1, program_id=program['id'], program_name=program['name'],
                        day_id=day['id'], day_name=day['name'], exercises=day['exercises'])
        progress = dict(current=0, remaining=list(range(1, len(day['exercises']))), postponed=[], completed=[])
        c.execute('INSERT INTO workout_sessions(workout_id,program_id,day_id,snapshot,progress) VALUES (?,?,?,?,?)',
                  (wid, program['id'], day['id'], _dump(snapshot), _dump(progress)))
        return _session(c, user_id, wid)


def advance_cycle(c, user_id, workout_id):
    """Completion advances the source program; cancellation does not."""
    row = c.execute('SELECT program_id,day_id FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone()
    if not row or row[0] is None or row[1] is None:
        return  # Program/day deleted or replaced; snapshot is still intact.
    days = [r[0] for r in c.execute('SELECT id FROM program_days WHERE program_id=? ORDER BY position', (row[0],))]
    if row[1] in days:
        c.execute('UPDATE programs SET next_day_position=? WHERE id=? AND user_id=?',
                  ((days.index(row[1]) + 1) % len(days), row[0], user_id))


def _advance(c, user_id, session, *, postpone):
    progress = session['progress']
    current = progress['current']
    if postpone:
        progress['postponed'].append(current)
    else:
        progress['completed'].append(current)
    # Preserve the previous behavior: return to deferred exercise after a
    # completed exercise; after deferring continue through the main sequence.
    queues = ('remaining', 'postponed') if postpone else ('postponed', 'remaining')
    progress['current'] = None
    for key in queues:
        if progress[key]:
            progress['current'] = progress[key].pop(0)
            break
    c.execute('UPDATE workout_sessions SET progress=?,revision=revision+1 WHERE workout_id=?',
              (_dump(progress), session['workout_id']))
    if progress['current'] is None:
        db._finish_workout(c, user_id, session['workout_id'])
        return None
    return _session(c, user_id, session['workout_id'])


def complete_exercise(user_id, workout_id, revision, sets):
    if not sets:
        raise ValueError('Нужно ввести выполненные подходы')
    with db._connection(True) as c:
        session = _session(c, user_id, workout_id, revision)
        index = session['progress']['current']
        exercise = session['snapshot']['exercises'][index]
        now = db._now(c, user_id)
        for number, item in enumerate(sets, 1):
            db._insert_set(c, workout_id, exercise['name'], number, item['weight'], item['reps'],
                           item['is_warmup'], exercise['exercise_id'], now)
            set_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO workout_set_occurrences VALUES (?,?,?)', (set_id, workout_id, index))
        return _advance(c, user_id, session, postpone=False)


def postpone_exercise(user_id, workout_id, revision):
    with db._connection(True) as c:
        return _advance(c, user_id, _session(c, user_id, workout_id, revision), postpone=True)


def get_occurrence_sets(user_id, workout_id, position):
    """Read an exact snapshot position with an ownership check.

    Names and catalog IDs are deliberately not used to identify an occurrence.
    Reading is allowed for owned finished workouts too.
    """
    with db._connection() as c:
        db._workout(c, user_id, workout_id)
        row = c.execute('SELECT snapshot FROM workout_sessions WHERE workout_id=?', (workout_id,)).fetchone()
        if row is None:
            raise db.WorkoutAccessError('У тренировки нет снимка включений упражнений.')
        exercises = json.loads(row[0])['exercises']
        if type(position) is not int or not 0 <= position < len(exercises):
            raise db.WorkoutAccessError('Включение упражнения недоступно.')
        return c.execute('''SELECT s.id,s.set_number,s.is_warmup,s.weight,s.reps
            FROM workout_sets s JOIN workout_set_occurrences o
            ON o.set_id=s.id AND o.workout_id=s.workout_id
            WHERE s.workout_id=? AND o.exercise_position=? ORDER BY s.id''',
                         (workout_id, position)).fetchall()


def _last_recorded(c, user_id, workout_id, revision=None):
    session = _session(c, user_id, workout_id, revision)
    completed = session['progress']['completed']
    if not completed:
        raise db.WorkoutAccessError('В этой тренировке ещё нет записанного предыдущего упражнения.')
    position = completed[-1]
    if (type(position) is not int or position == session['progress']['current']
            or not 0 <= position < len(session['snapshot']['exercises'])):
        raise db.WorkoutAccessError('Не удалось безопасно определить предыдущее включение. Данные не изменены.')
    rows = c.execute('''SELECT s.is_warmup,s.weight,s.reps FROM workout_sets s
        JOIN workout_set_occurrences o ON o.set_id=s.id AND o.workout_id=s.workout_id
        WHERE s.workout_id=? AND o.exercise_position=? ORDER BY s.id''', (workout_id, position)).fetchall()
    if not rows:
        raise db.WorkoutAccessError('Связанные подходы не найдены. Исправление остановлено без удаления данных.')
    return session, position, rows


def previous_exercise(user_id, workout_id, revision=None):
    with db._connection() as c:
        session, position, rows = _last_recorded(c, user_id, workout_id, revision)
        return dict(workout_id=workout_id, position=position, revision=session['revision'],
                    exercise=session['snapshot']['exercises'][position], sets=rows)


def replace_previous_sets(user_id, workout_id, position, revision, sets):
    """Replace exactly one completed occurrence, without advancing the workout."""
    if not sets:
        raise ValueError('Нужен полный исправленный список подходов.')
    with db._connection(True) as c:
        session, previous, _ = _last_recorded(c, user_id, workout_id, revision)
        if position != previous:
            raise db.WorkoutAccessError('Это включение больше не является последним записанным. Данные не изменены.')
        exercise = session['snapshot']['exercises'][position]
        c.execute('''DELETE FROM workout_sets WHERE workout_id=? AND id IN (
            SELECT set_id FROM workout_set_occurrences WHERE workout_id=? AND exercise_position=?)''',
                  (workout_id, workout_id, position))
        now = db._now(c, user_id)
        for number, item in enumerate(sets, 1):
            db._insert_set(c, workout_id, exercise['name'], number, item['weight'], item['reps'],
                           item['is_warmup'], exercise['exercise_id'], now)
            set_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO workout_set_occurrences VALUES (?,?,?)', (set_id, workout_id, position))
        # Keep progress JSON and the immutable snapshot byte-for-byte unchanged.
        c.execute('UPDATE workout_sessions SET revision=revision+1 WHERE workout_id=?', (workout_id,))
        return _session(c, user_id, workout_id)
