"""Deterministic descriptions; no AI or claims about strength/causation."""
from html import escape
from database.analytics import composition, occurrence_map, measures, weight_label
from workouts.presentation import format_number as n, format_date, local_datetime


def delta(before, after):
    if before == 0:
        return 'без изменений' if after == 0 else 'сравнение в процентах недоступно: раньше было 0'
    percent = (after - before) * 100 / before
    return f'{percent:+.1f}%'


def elapsed(record):
    start, _ = local_datetime(record['started_at'], record['timezone'])
    end, _ = local_datetime(record['finished_at'], record['timezone'])
    if start is None or end is None:
        return None
    # UTC subtraction is necessary across daylight saving changes.
    from datetime import timezone
    minutes = int((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() // 60)
    return f'{minutes // 60} ч {minutes % 60} мин' if 0 <= minutes <= 24 * 60 else None


def summary(current, previous, *, completed=False):
    m = current['metrics']
    title = 'Тренировка завершена 💪' if completed else 'Разбор тренировки ' + current['name']
    lines = [f'<b>{escape(title)}</b>', escape(current['name']) + ' · ' + format_date(current['started_at'], current['timezone'])]
    duration = elapsed(current)
    if duration:
        lines.append('Время между началом и завершением: ' + duration)
    lines += [f"Рабочих подходов: {m['sets']}", f"Повторений: {n(m['reps'])}",
              f"Объём по записанному весу: {n(m['volume'])} кг·повт."]
    if previous:
        old = previous['metrics']
        lines += ['', 'Сравнение с ' + escape(previous['name']) + ' от ' + format_date(previous['started_at'], previous['timezone']),
                  f"Рабочих подходов: {old['sets']} → {m['sets']}",
                  f"Объём: {n(old['volume'])} → {n(m['volume'])} ({delta(old['volume'], m['volume'])})"]
        if composition(current) != composition(previous):
            lines.append('Состав тренировки изменился. Объём учитывает добавленные и убранные упражнения.')
    else:
        lines += ['', 'Предыдущей записи этого дня с достоверной датой нет. Эта запись станет начальной точкой для сравнения.']
    if m['invalid'] or (previous and previous['metrics']['invalid']):
        lines.append('Некорректные числовые значения исключены из расчётов; исходные записи сохранены.')
    return '\n'.join(lines)


def comparison(current, previous, *, short=False):
    old = occurrence_map(previous) if previous else {}
    new = occurrence_map(current)
    lines = []
    keys = list(new) + [key for key in old if key not in new]
    for key in keys:
        after, before = new.get(key), old.get(key)
        name = (after or before)['name']
        title = escape(name) + (f' · включение {key[-1]}' if key[-1] > 1 else '')
        lines.append('\n<b>' + title + '</b>')
        if after is None:
            lines.append('В этой тренировке не записано.')
            continue
        a = measures([after])
        if before is None:
            lines.append('Новая запись в сравнении.' if previous else 'Записанные рабочие подходы:')
        b = measures([before]) if before else None
        if not a['sets']:
            lines.append('Рабочие подходы не записаны.')
            continue
        lines.append('Максимальный записанный рабочий вес: ' +
                     (n(b['maximum']) + ' → ' if b else '') + n(a['maximum']) + ' кг')
        if b and not short:
            lines.append('Повторения на максимальном весе — было: ' + ' / '.join(map(n, b['maximum_reps'])))
        lines.append('Повторения на максимальном весе' + (' — стало: ' if b else ': ') + ' / '.join(map(n, a['maximum_reps'])))
        if not short:
            lines += [f"Рабочих подходов: " + (f"{b['sets']} → " if b else '') + str(a['sets']),
                      'Объём: ' + (n(b['volume']) + ' → ' if b else '') + n(a['volume']) + ' кг·повт.']
    return '\n'.join(lines)


def exercise_summary(exercise, records):
    lines = [f"<b>{escape(exercise['name'])}</b>", weight_label(exercise),
             'Разминка исключена. Объём считается по введённому весу, без удвоения гантелей и без массы тела.']
    if not records:
        return '\n'.join(lines + ['За выбранный период записей нет.'])
    dated = [r for r in records if r['date']]
    first, last = (dated[-1], dated[0]) if dated else (records[-1], records[0])
    lines += [f'Тренировок: {len(records)}',
              f"Первая запись: {n(first['metrics']['maximum'])} кг · {format_date(first['started_at'], first['timezone'])}",
              f"Последняя: {n(last['metrics']['maximum'])} кг · {format_date(last['started_at'], last['timezone'])}",
              'Повторения на этом весе: ' + ' / '.join(map(n, last['metrics']['maximum_reps'])),
              f"Максимальный записанный вес за период: {n(max(r['metrics']['maximum'] for r in records))} кг",
              'Нулевое дополнительное отягощение не означает отсутствие выполненной работы.']
    if len(dated) != len(records):
        lines.append('Записи без достоверной даты не нанесены на график.')
    return '\n'.join(lines)
