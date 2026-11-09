"""Display only recorded facts; elapsed time is never workout duration."""
from datetime import datetime, timezone
from workouts.presentation import (format_date, format_datetime, format_number,
                                   local_datetime, timezone_label)


def plural(number, forms):
    if number % 100 in (11, 12, 13, 14):
        return forms[2]
    return forms[0] if number % 10 == 1 else forms[1] if number % 10 in (2, 3, 4) else forms[2]


def elapsed_since(started, now):
    seconds = int((now.astimezone(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())
    if seconds < 0:
        return 'время начала позже текущего времени'
    if seconds < 60:
        return 'меньше минуты'
    minutes = seconds // 60
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    values = ((days, ('день', 'дня', 'дней')), (hours, ('час', 'часа', 'часов')),
              (minutes, ('минута', 'минуты', 'минут')))
    return ' '.join(f'{value} {plural(value, forms)}' for value, forms in values if value) or 'меньше минуты'


def start_time(details):
    return local_datetime(details.get('started_at'), details.get('timezone'))


def date_label(details, *, include_time=True):
    formatter = format_datetime if include_time else format_date
    return formatter(details.get('started_at'), details.get('timezone'))


def metadata(details, *, now=None):
    started, naive = start_time(details)
    now, _ = local_datetime(now or datetime.now(timezone.utc), details.get('timezone'))
    lines = [f"Незавершённая тренировка {details['day_name']}"]
    if details.get('program_name'):
        lines.append(f"Программа: {details['program_name']}")
    lines.append(f"Начата: {date_label(details)} ({timezone_label(details.get('timezone'))})")
    lines.append('С начала прошло: ' + (elapsed_since(started, now) if started and now else 'неизвестно'))
    count, sets = details['exercise_count'], details['set_count']
    lines.append(f"Записано: {count} {plural(count, ('упражнение', 'упражнения', 'упражнений'))}, "
                 f"{sets} {plural(sets, ('подход', 'подхода', 'подходов'))}")
    if naive:
        lines.append('Время старой записи сохранено без часового пояса; принято местное время пользователя.')
    return '\n'.join(lines)


def format_set(warmup, weight, reps, count=1):
    label = 'Разминка' if warmup else 'Рабочий' if count == 1 else 'Рабочие'
    weight_text = format_number(weight, missing='')
    weight_text = f'{weight_text} кг' if weight_text else 'вес не записан'
    value = f'{label}: {weight_text} × {format_number(reps, missing="повторения не записаны")}'
    if count > 1:
        value += f" — {count} {plural(count, ('подход', 'подхода', 'подходов'))}"
    return value


def compact_card(details, *, now=None, limit=3800):
    text = metadata(details, now=now)
    if not details['set_count']:
        return text + '\n\nПодходы ещё не записаны', False
    omitted = False
    for exercise in details['exercises']:
        groups = []
        for item in exercise['sets']:
            if groups and tuple(groups[-1][:3]) == tuple(item):
                groups[-1][3] += 1
            else:
                groups.append([*item, 1])
        block = '\n\n' + exercise['name']
        for values in groups:
            block += '\n' + format_set(*values)
        if len(text) + len(block) > limit - 100:
            omitted = True
            break
        text += block
    if omitted:
        text += '\n\nОстальные записи — по кнопке «Посмотреть все подходы».'
    return text, omitted


def all_sets_pages(details, *, now=None, limit=3800):
    header = metadata(details, now=now)
    pages, body = [], ''
    for exercise in details['exercises']:
        for number, item in enumerate(exercise['sets'], 1):
            # Repeating the exercise name makes page boundaries unambiguous.
            line = f"\n\n{exercise['name']}\n{number}. {format_set(*item)}"
            if len(header) + len(body) + len(line) > limit and body:
                pages.append(header + body)
                body = ''
            body += line
    pages.append(header + (body or '\n\nПодходы ещё не записаны'))
    return pages


def deletion_warning(details):
    return (f"Удалить незавершённую тренировку «{details['day_name']}» от {date_label(details, include_time=False)}?\n"
            f"Будут безвозвратно удалены ВСЕ её записанные подходы — {details['set_count']}. "
            'Восстановить их будет нельзя. Другие тренировки и программа не изменятся.')
