"""Owned program documents. List order defines day/exercise/template order."""
import math
import sqlite3

from database import db


class ProgramAccessError(db.WorkoutAccessError):
    pass


def _program(c, user_id, program_id):
    c.row_factory = sqlite3.Row
    row = c.execute('SELECT * FROM programs WHERE id=? AND user_id=?', (program_id, user_id)).fetchone()
    c.row_factory = None
    if row is None:
        raise ProgramAccessError('Программа не найдена или недоступна.')
    return dict(row)


def _name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Название не может быть пустым')
    if len(value.strip()) > 120:
        raise ValueError('Название должно быть не длиннее 120 символов')
    return value.strip()


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Требуется положительное целое число')
    return value


def _template(item, warmup):
    weight, reps = item.get('weight'), item.get('reps')
    if weight is not None and (isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight)):
        raise ValueError('Некорректный вес')
    if reps is not None:
        _positive(reps)
    return (int(warmup), weight, reps)


def _write_days(c, program_id, days, *, existing_ids=()):
    if not isinstance(days, list) or not days:
        raise ValueError('Нужен хотя бы один тренировочный день')
    for position, day in enumerate(days):
        if day.get('id') in existing_ids:
            day_id = day['id']
            c.execute('UPDATE program_days SET name=?,position=? WHERE id=? AND program_id=?',
                      (_name(day['name']), position, day_id, program_id))
            c.execute('DELETE FROM program_day_exercises WHERE day_id=?', (day_id,))
        else:
            day_id = c.execute('INSERT INTO program_days(program_id,name,position) VALUES (?,?,?)',
                               (program_id, _name(day['name']), position)).lastrowid
        items = day['exercises']
        if not isinstance(items, list) or not items:
            raise ValueError('В дне нужно хотя бы одно упражнение')
        for order, exercise in enumerate(items):
            catalog = c.execute('SELECT id,name_ru,name_en FROM exercises WHERE external_id=?', (exercise['exercise_id'],)).fetchone()
            if catalog is None:
                raise ValueError(f"Упражнение отсутствует в каталоге: {exercise['exercise_id']}")
            count = exercise.get('working_sets')
            working_plan = exercise.get('working_plan')
            if count is None and working_plan:
                count = len(working_plan)
            if count is None and working_plan is None and exercise.get('working'):
                count = 1
            if count is not None:
                _positive(count)
            eid = c.execute('INSERT INTO program_day_exercises(day_id,exercise_id,name,position,working_sets) VALUES (?,?,?,?,?)',
                            (day_id, catalog[0], _name(exercise.get('name') or catalog[1] or catalog[2]), order, count)).lastrowid
            templates = [_template(item, True) for item in exercise.get('warmup', [])]
            if working_plan is not None:
                if len(working_plan) != (count or 0):
                    raise ValueError('Количество рабочих шаблонов не совпадает с working_sets')
                templates += [_template(item, False) for item in working_plan]
            else:
                templates += [_template(exercise.get('working', {}), False)] * (count or 0)
            c.executemany('INSERT INTO program_set_templates(day_exercise_id,position,is_warmup,weight,reps) VALUES (?,?,?,?,?)',
                          [(eid, index, *item) for index, item in enumerate(templates)])


def _read(c, user_id, program_id):
    result = _program(c, user_id, program_id)
    result['days'] = []
    for did, name, position in c.execute('SELECT id,name,position FROM program_days WHERE program_id=? ORDER BY position', (program_id,)).fetchall():
        day = dict(id=did, name=name, position=position, exercises=[])
        rows = c.execute('SELECT de.id,e.external_id,de.name,de.working_sets FROM program_day_exercises de JOIN exercises e ON e.id=de.exercise_id WHERE de.day_id=? ORDER BY de.position', (did,)).fetchall()
        for eid, external, label, count in rows:
            templates = [dict(is_warmup=bool(w), weight=weight, reps=reps) for w, weight, reps in c.execute('SELECT is_warmup,weight,reps FROM program_set_templates WHERE day_exercise_id=? ORDER BY position', (eid,))]
            day['exercises'].append(dict(id=eid, exercise_id=external, name=label, working_sets=count,
                                         warmup=[dict(weight=t['weight'], reps=t['reps']) for t in templates if t['is_warmup']],
                                         working=next((dict(weight=t['weight'], reps=t['reps']) for t in templates if not t['is_warmup']), {}),
                                         working_plan=[dict(weight=t['weight'], reps=t['reps']) for t in templates if not t['is_warmup']],
                                         templates=templates))
        result['days'].append(day)
    return result


def create_program(user_id, name, days, *, activate=False):
    with db._connection(True) as c:
        if activate:
            c.execute('UPDATE programs SET is_active=0 WHERE user_id=?', (user_id,))
        pid = c.execute('INSERT INTO programs(user_id,name,is_active,created_at) VALUES (?,?,?,?)',
                        (user_id, _name(name), int(activate), db._now(c, user_id))).lastrowid
        _write_days(c, pid, days)
        return pid


def get_program(user_id, program_id):
    with db._connection() as c:
        return _read(c, user_id, program_id)


def get_day(user_id, day_id):
    with db._connection() as c:
        row = c.execute('SELECT p.id FROM programs p JOIN program_days d ON d.program_id=p.id WHERE p.user_id=? AND d.id=? AND p.archived_at IS NULL', (user_id, day_id)).fetchone()
        if not row:
            raise ProgramAccessError('Тренировочный день не найден или недоступен.')
        return next(d for d in _read(c, user_id, row[0])['days'] if d['id'] == day_id)


def list_programs(user_id, *, archived=False):
    with db._connection() as c:
        condition = 'IS NOT NULL' if archived else 'IS NULL'
        return [dict(id=i, name=n, is_active=bool(a)) for i, n, a in c.execute(
            f'SELECT id,name,is_active FROM programs WHERE user_id=? AND archived_at {condition} ORDER BY id', (user_id,))]


def update_program(user_id, program_id, *, name=None, days=None, expected_revision=None):
    """Atomic document edit retaining IDs of supplied, owned existing days.

    Keep the next day's identity; after its deletion choose an old successor.
    Active workout snapshot JSON is never rewritten.
    """
    with db._connection(True) as c:
        current = _program(c, user_id, program_id)
        _editable(current, expected_revision)
        if name is not None:
            c.execute('UPDATE programs SET name=? WHERE id=?', (_name(name), program_id))
        if days is not None:
            old_ids = [r[0] for r in c.execute('SELECT id FROM program_days WHERE program_id=? ORDER BY position', (program_id,))]
            requested = [d['id'] for d in days if d.get('id') is not None]
            if len(set(requested)) != len(requested) or not set(requested) <= set(old_ids):
                raise ProgramAccessError('День не принадлежит программе или повторяется.')
            # Move positions out of the destination range before reordering.
            offset = c.execute('SELECT COALESCE(MAX(position),0) FROM program_days WHERE program_id=?', (program_id,)).fetchone()[0] + len(days) + 1
            c.execute('UPDATE program_days SET position=position+? WHERE program_id=?', (offset, program_id))
            for day_id in set(old_ids) - set(requested):
                c.execute('DELETE FROM program_days WHERE id=? AND program_id=?', (day_id, program_id))
            _write_days(c, program_id, days, existing_ids=set(requested))
            new_ids = [r[0] for r in c.execute('SELECT id FROM program_days WHERE program_id=? ORDER BY position', (program_id,))]
            position = 0
            if old_ids:
                start = current['next_day_position'] % len(old_ids)
                # Keep the next day by identity; if removed choose its first
                # surviving successor in the old circular sequence.
                for candidate in old_ids[start:] + old_ids[:start]:
                    if candidate in new_ids:
                        position = new_ids.index(candidate)
                        break
            c.execute('UPDATE programs SET next_day_position=? WHERE id=?', (position, program_id))
        c.execute('UPDATE programs SET revision=revision+1 WHERE id=?', (program_id,))


def delete_program(user_id, program_id):
    with db._connection(True) as c:
        _program(c, user_id, program_id)
        c.execute('DELETE FROM programs WHERE id=?', (program_id,))


def activate_program(user_id, program_id):
    with db._connection(True) as c:
        _editable(_program(c, user_id, program_id))
        c.execute('UPDATE programs SET is_active=0 WHERE user_id=?', (user_id,))
        c.execute('UPDATE programs SET is_active=1 WHERE id=?', (program_id,))


def get_active_program(user_id):
    with db._connection() as c:
        row = c.execute('SELECT id FROM programs WHERE user_id=? AND is_active=1 AND archived_at IS NULL', (user_id,)).fetchone()
        return _read(c, user_id, row[0]) if row else None


def next_day(user_id):
    program = get_active_program(user_id)
    if not program or not program['days']:
        raise ProgramAccessError('Нет активной программы с тренировочными днями.')
    return program['days'][program['next_day_position'] % len(program['days'])]


def _editable(program, revision=None):
    if program['archived_at'] is not None:
        raise ProgramAccessError('Программа в архиве. Сначала восстановите её.')
    if revision is not None and program['revision'] != revision:
        raise ProgramAccessError('Программа изменилась. Откройте её заново, чтобы не затереть изменения.')


def archive_program(user_id, program_id, *, expected_revision):
    with db._connection(True) as c:
        _editable(_program(c, user_id, program_id), expected_revision)
        c.execute('UPDATE programs SET archived_at=?,is_active=0,revision=revision+1 WHERE id=?',
                  (db._now(c, user_id), program_id))


def restore_program(user_id, program_id, *, expected_revision):
    with db._connection(True) as c:
        program = _program(c, user_id, program_id)
        if program['revision'] != expected_revision or program['archived_at'] is None:
            raise ProgramAccessError('Экран устарел. Откройте архив заново.')
        c.execute('UPDATE programs SET archived_at=NULL,revision=revision+1 WHERE id=?', (program_id,))
