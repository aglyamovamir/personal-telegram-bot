"""Parse planned sets without ever inserting actual workout sets."""
import math


def parse_plan(text, *, allow_empty=False):
    text = (text or '').strip()
    if allow_empty and text == '-':
        return []
    if not text or len(text) > 4000:
        raise ValueError('Нужны строки «вес повторения [количество]», например: 50 8 3')
    result = []
    for line in text.splitlines():
        parts = line.replace('×', ' ').replace('x', ' ').split()
        if len(parts) not in (2, 3):
            raise ValueError('Каждая строка: вес повторения [количество]')
        try:
            weight = None if parts[0] == '-' else float(parts[0].replace(',', '.'))
            reps = None if parts[1] == '-' else int(parts[1])
            count = int(parts[2]) if len(parts) == 3 else 1
        except ValueError as error:
            raise ValueError('Не удалось распознать числа') from error
        if (weight is not None and not math.isfinite(weight)) or (reps is not None and not 1 <= reps <= 2147483647) or count <= 0 or len(result) + count > 100:
            raise ValueError('Повторения и количество должны быть положительными; максимум 100 подходов')
        result.extend(dict(weight=weight, reps=reps) for _ in range(count))
    return result
