import re


def parse_exercise_input(text):
    """
    Разбирает ввод подходов одного упражнения.

    Формат:

    20 10
    50 10

    60 9 3
    65 8 2

    До пустой строки:
        2 числа = разминка

    После пустой строки:
        2 числа = один рабочий подход
        3 числа = несколько одинаковых рабочих подходов

    Также поддерживаются:
        60×9×3
        60 x 9 x 3
        60 по 9 3
        60 по 9 по 3
    """

    # Нормализуем разные символы умножения и слово "по"
    normalized = text.lower()
    normalized = normalized.replace("×", "x")
    normalized = re.sub(r"\bпо\b", " ", normalized)

    # Убираем лишние пробелы в строках
    raw_lines = normalized.splitlines()

    # Удаляем пустые строки с начала и конца,
    # но сохраняем пустую строку-разделитель внутри
    while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)

    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()

    if not raw_lines:
        raise ValueError("Пустой ввод.")

    # Ищем пустую строку между разминкой и работой
    separator_indexes = [
        i for i, line in enumerate(raw_lines)
        if not line.strip()
    ]

    if len(separator_indexes) > 1:
        raise ValueError("Должна быть максимум одна пустая строка между разминкой и рабочими подходами.")

    if separator_indexes:
        separator = separator_indexes[0]
        warmup_lines = raw_lines[:separator]
        working_lines = raw_lines[separator + 1:]
    else:
        warmup_lines = []
        working_lines = raw_lines

    # Если пустая строка есть, но после неё ничего нет
    if separator_indexes and not working_lines:
        raise ValueError("После пустой строки должны быть рабочие подходы.")

    # Если пустой строки нет, считаем весь ввод рабочими подходами.
    # Это позволяет писать просто:
    # 60 9 3
    # или
    # 60 9
    if not separator_indexes:
        warmup_lines = []
        working_lines = raw_lines

    result = []

    # -------------------------
    # РАЗМИНКА
    # -------------------------

    for line in warmup_lines:
        values = _parse_numbers(line)

        if len(values) != 2:
            raise ValueError(
                f"Некорректная разминка: «{line}».\n"
                "Для разминки нужно указать: вес повторения"
            )

        weight, reps = values

        result.append({
            "weight": weight,
            "reps": reps,
            "is_warmup": True,
        })

    # -------------------------
    # РАБОЧИЕ ПОДХОДЫ
    # -------------------------

    for line in working_lines:
        values = _parse_numbers(line)

        if len(values) == 2:
            weight, reps = values
            count = 1

        elif len(values) == 3:
            weight, reps, count = values

            if not float(count).is_integer() or count < 1:
                raise ValueError(
                    f"Количество подходов должно быть целым числом: «{line}»."
                )

            count = int(count)

        else:
            raise ValueError(
                f"Некорректный рабочий подход: «{line}».\n"
                "Используй: вес повторения или вес повторения количество"
            )

        for _ in range(count):
            result.append({
                "weight": weight,
                "reps": reps,
                "is_warmup": False,
            })

    if not result:
        raise ValueError("Не удалось распознать ни одного подхода.")

    return result


def _parse_numbers(line):
    """
    Превращает строку вроде:
        60 9 3
        60x9x3
        60 x 9 x 3
        60 по 9 3

    в список чисел.
    """

    line = line.strip()

    # Оставляем цифры, точку, запятую, минус и разделители.
    # Запятую считаем десятичным разделителем.
    line = line.replace(",", ".")

    # x уже нормализован в parse_exercise_input
    line = re.sub(r"[xX]", " ", line)

    # Остаточные пробелы
    parts = line.split()

    values = []

    for part in parts:
        try:
            values.append(float(part))
        except ValueError:
            raise ValueError(
                f"Не удалось распознать число: «{part}»."
            )

    return values

def format_parsed_sets(sets):
    """
    Форматирует распознанные подходы для подтверждения пользователю.
    """

    lines = []

    for item in sets:
        prefix = "🔥 " if item["is_warmup"] else "💪 "
        lines.append(
            f"{prefix}{item['weight']:g} кг × {item['reps']}"
        )

    return "\n".join(lines)