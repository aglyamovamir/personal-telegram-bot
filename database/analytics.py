"""Read-only, owner-scoped analysis of completed facts. No migrations or caches."""
from calendar import monthrange
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from database import db, catalog
from workouts.presentation import local_datetime, workout_zone


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def measures(occurrences):
    sets, invalid = [], 0
    for occurrence in occurrences:
        for item in occurrence['sets']:
            if item['is_warmup']:
                continue
            weight, reps = number(item['weight']), number(item['reps'])
            if weight is None or reps is None or weight < 0 or reps <= 0:
                invalid += 1
                continue
            sets.append((weight, reps))
    maximum = max((w for w, _ in sets), default=None)
    return dict(sets=len(sets), reps=sum((r for _, r in sets), Decimal(0)),
                volume=sum((w * r for w, r in sets), Decimal(0)), maximum=maximum,
                maximum_reps=[r for w, r in sets if w == maximum], invalid=invalid)


def load(user_id):
    """Only owned completed rows can enter the dataset, including legacy facts."""
    groups = db.get_history_days(user_id)
    with db._connection() as c:
        c.execute('BEGIN')
        zone = db._user(c, user_id)['timezone']
        metadata = {}
        for eid, external, en, ru, equipment, bodyweight in c.execute(
                'SELECT id,external_id,name_en,name_ru,equipment,is_bodyweight FROM exercises'):
            metadata[external] = dict(id=eid, name=catalog.names(external, ru, en)[0],
                                      equipment=equipment, bodyweight=bool(bodyweight))
        records = {}
        for wid, label, started, finished in c.execute(
                'SELECT id,workout_type,started_at,finished_at FROM workouts WHERE user_id=? AND finished_at IS NOT NULL',
                (user_id,)).fetchall():
            occurrences = db._history_occurrences(c, wid)
            local, _ = local_datetime(started, zone)
            record = dict(id=wid, name=label, started_at=started, finished_at=finished,
                          date=local, timezone=zone, occurrences=occurrences,
                          metrics=measures(occurrences))
            records[wid] = record
    for group in groups:
        group['records'] = [records[row[0]] for row in group['workouts'] if row[0] in records]
        for record in group['records']:
            record['group_key'] = group['key']
            record['group_title'] = group['title']
    return dict(groups=groups, records=records, catalog=metadata, timezone=zone)


def group_for(data, workout_id):
    if workout_id not in data['records']:
        raise db.WorkoutAccessError('Тренировка недоступна или ещё не завершена.')
    group = next((g for g in data['groups'] if any(r['id'] == workout_id for r in g['records'])), None)
    if group is None:
        raise db.WorkoutAccessError('История этого дня больше недоступна.')
    return group


def breakdown(data, workout_id):
    group = group_for(data, workout_id)
    records = group['records']  # same date ordering and day identity as History
    index = next(i for i, r in enumerate(records) if r['id'] == workout_id)
    current = records[index]
    previous = next((r for r in records[index + 1:] if r['date'] is not None), None) if current['date'] else None
    return current, previous


def occurrence_map(record):
    counts, result = Counter(), {}
    for occurrence in record['occurrences']:
        identity = ('catalog', occurrence['exercise_id']) if occurrence['exercise_id'] else ('legacy', occurrence['name'])
        counts[identity] += 1
        result[(*identity, counts[identity])] = occurrence
    return result


def composition(record):
    return Counter((o['exercise_id'] or ('legacy', o['name'])) for o in record['occurrences'])


def available_exercises(data, query=''):
    ids = {o['exercise_id'] for record in data['records'].values()
           for o in record['occurrences'] if measures([o])['sets']}
    query = catalog.normalize(query)
    return sorted([dict(external=external, **meta) for external, meta in data['catalog'].items()
                   if external in ids and (not query or query in catalog.normalize(meta['name'])
                                           or query in catalog.normalize(external))], key=lambda row: row['name'])


def exercise_for(data, catalog_id):
    row = next((item for item in available_exercises(data) if item['id'] == catalog_id), None)
    if row is None:
        raise db.WorkoutAccessError('У вас нет записанных рабочих подходов этого упражнения.')
    return row


def weight_label(exercise):
    if exercise['equipment'] == 'dumbbell':
        return 'Вес одной гантели'
    external = exercise['external']
    if (external.startswith('weighted-') or exercise['bodyweight']) and not any(
            word in external for word in ('assisted', 'machine', 'band')):
        return 'Дополнительный вес'
    return 'Максимальный рабочий вес'


def period_records(records, months=0, *, now=None, timezone_name='UTC'):
    if months not in (0, 3, 6):
        raise ValueError('Неверный период.')
    if months == 0:
        return list(records)
    now = (now or datetime.now(timezone.utc)).astimezone(workout_zone(timezone_name))
    month = now.year * 12 + now.month - 1 - months
    year, m = divmod(month, 12)
    cutoff = now.date().replace(year=year, month=m + 1,
                              day=min(now.day, monthrange(year, m + 1)[1]))
    return [r for r in records if r['date'] and cutoff <= r['date'].date() <= now.date()]


def exercise_records(data, exercise):
    result = []
    for record in data['records'].values():
        occurrences = [o for o in record['occurrences'] if o['exercise_id'] == exercise['external']]
        metrics = measures(occurrences)
        if metrics['sets']:
            result.append({**record, 'occurrences': occurrences, 'metrics': metrics})
    return sorted(result, key=lambda row: db._workout_order(
        (row['id'], row['name'], row['started_at'], row['finished_at']), data['timezone']), reverse=True)
