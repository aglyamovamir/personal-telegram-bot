"""Pure formatting of recorded workout facts; no storage or parser changes."""
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape, unescape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UNKNOWN_TIME = 'дата не сохранена'


def workout_zone(timezone_name):
    """Invalid settings must not prevent viewing a recorded workout."""
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return timezone.utc


def timezone_label(timezone_name):
    return getattr(workout_zone(timezone_name), 'key', 'UTC')


def local_datetime(value, timezone_name='UTC'):
    """Return (local datetime, was_naive), or (None, False).

    Old naive timestamps are wall-clock values in the user's zone, matching
    the existing importer/date convention. Aware values are converted once.
    A date without a time is not silently turned into a midnight workout.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and re.search(r'\d{2}:\d{2}', value):
        try:
            parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except (TypeError, ValueError, OverflowError):
            return None, False
    else:
        return None, False
    zone = workout_zone(timezone_name)
    try:
        naive = parsed.tzinfo is None or parsed.utcoffset() is None
        return (parsed.replace(tzinfo=zone) if naive else parsed.astimezone(zone)), naive
    except (TypeError, ValueError, OverflowError):
        return None, False


def format_datetime(value, timezone_name='UTC'):
    parsed, _ = local_datetime(value, timezone_name)
    return parsed.strftime('%d.%m.%Y %H:%M') if parsed else UNKNOWN_TIME


def format_date(value, timezone_name='UTC'):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime('%d.%m.%Y')
    parsed, _ = local_datetime(value, timezone_name)
    if parsed:
        return parsed.strftime('%d.%m.%Y')
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).strftime('%d.%m.%Y')
        except (TypeError, ValueError):
            pass
    return UNKNOWN_TIME


def format_number(value, *, missing='не записано'):
    """No float .0 or scientific notation; keep fractional and zero values."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return missing
    if not number.is_finite():
        return missing
    text = format(number, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def set_value(item):
    weight = format_number(item.get('weight'), missing='')
    reps = format_number(item.get('reps'), missing='повторения не записаны')
    return (f'{weight} кг' if weight else 'вес не записан') + f' × {reps}'


def grouped_set_lines(sets):
    """Compact only adjacent identical facts inside this one occurrence."""
    groups = []
    for item in sets:
        key = (bool(item.get('is_warmup')), item.get('weight'), item.get('reps'))
        if groups and groups[-1][0] == key:
            groups[-1][2] += 1
        else:
            groups.append([key, item, 1])
    lines = []
    for key, item, count in groups:
        label = 'Разминка' if key[0] else 'Рабочий'
        line = f'{label}: {set_value(item)}'
        if count > 1:
            line += f' × {count}'
        lines.append(line)
    return lines


def _plain(value):
    return escape(str(value if value is not None else ''), quote=False)


def current_exercise_card(name, completed, total, postponed, previous=None, initial=False):
    """Compact current step and exact prior facts; caller owns step lifecycle."""
    title = str(name if name is not None else '')
    if not title.endswith('.'):
        title += '.'
    lines = [f'<b>Следующее упражнение — {_plain(title)}</b>',
             f'Выполнено: {completed} из {total} · Отложено: {postponed}']
    occurrences = previous.get('occurrences', []) if previous else []
    if occurrences and any(occurrence.get('sets') for occurrence in occurrences):
        source = ('Прошлый результат · ' + str(previous.get('day_name') or 'день не сохранён')
                  + ' · ' + format_date(previous.get('started_at'), previous.get('timezone')) + ':')
        lines.extend(('', _plain(source)))
        for number, occurrence in enumerate(occurrences, 1):
            if len(occurrences) > 1:
                lines.append(f'Включение №{number}')
            for item in occurrence.get('sets', []):
                label = 'Разминка: ' if item.get('is_warmup') else ''
                lines.append(_plain(label + set_value(item)))
    else:
        lines.extend(('', 'Прошлых результатов пока нет.'))
    if initial:
        lines.extend(('', 'Отправьте подходы одним сообщением: 59 10 3.',
                      'Разминку отделите пустой строкой. /help — пример.'))
    return '\n'.join(lines)


def saved_sets_confirmation(name, sets):
    lines = [f'✅ {_plain(name)} — подходы сохранены.', '']
    for item in sets:
        prefix = '🔥 Разминка: ' if item.get('is_warmup') else '💪 '
        lines.append(prefix + _plain(set_value(item)))
    return '\n'.join(lines)


def _chunks_from_parts(parts, limit):
    if type(limit) is not int or limit < 32:
        raise ValueError('Лимит сообщения должен быть целым числом не меньше 32.')
    chunks, current, size = [], [], 0
    for text, bold in parts:
        offset = 0
        overhead = 7 if bold else 0  # <b></b>
        while offset < len(text):
            available = limit - size - overhead
            fragment, fragment_size = [], 0
            while offset < len(text):
                value = escape(text[offset], quote=False)
                cost = len(value.encode('utf-16-le')) // 2
                if fragment_size + cost > available:
                    break
                fragment.append(value)
                fragment_size += cost
                offset += 1
            if fragment:
                value = ''.join(fragment)
                current.append(f'<b>{value}</b>' if bold else value)
                size += fragment_size + overhead
            if offset < len(text):
                chunks.append(''.join(current))
                current, size = [], 0
    if current or not chunks:
        chunks.append(''.join(current))
    return chunks


def html_chunks(text, limit=3800):
    """Bounded valid HTML for our escaped text and nonnested <b> spans.

    Each chunk closes/reopens its bold spans and escapes each whole character,
    so tags, entities and UTF-16 surrogate pairs cannot be cut. Joining decoded
    chunk text reproduces the complete original text without adding separators.
    Other literal markup is shown as text, rather than passed through as HTML.
    """
    parts, bold = [], False
    for part in re.split(r'(<b>|</b>)', text):
        if part == '<b>':
            bold = True
        elif part == '</b>':
            bold = False
        else:
            parts.append((unescape(part), bold))
    return _chunks_from_parts(parts, limit)


def completed_history_chunks(history, limit=3800):
    """Compact completed history, with occurrence boundaries supplied by DB."""
    zone = history.get('timezone')
    text = ('📊 Последняя тренировка ' + _plain(history.get('day_name'))
            + '\n\nНачало: ' + format_datetime(history.get('started_at'), zone)
            + '\nОкончание: ' + format_datetime(history.get('finished_at'), zone))
    occurrences = history.get('occurrences', [])
    if not occurrences:
        text += '\n\nПодходы не записаны.'
    for occurrence in occurrences:
        text += '\n\n<b>' + _plain(occurrence.get('name')) + '</b>'
        for line in grouped_set_lines(occurrence.get('sets', [])):
            text += '\n' + _plain(line)
    return html_chunks(text, limit)
