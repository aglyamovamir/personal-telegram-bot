from workouts.parser import parse_exercise_input, format_parsed_sets


tests = [
    """20 10
50 10

60 9 3""",

    """20×10
50×10

60×9×3""",

    """20 по 10
50 по 10

60 по 9 3""",

    """20 10
20 10

60 9 3
65 8 2""",

    """60 9 3""",

    """60 9""",
]


for i, text in enumerate(tests, 1):
    print(f"\n=== ТЕСТ {i} ===")

    try:
        sets = parse_exercise_input(text)

        print(format_parsed_sets(sets))
        print(f"Всего подходов: {len(sets)}")

    except ValueError as e:
        print(f"ОШИБКА: {e}")