"""Executable assertions replacing the old root-level print-only demo."""
import unittest

from workouts.parser import parse_exercise_input


class ParserInputExamplesTests(unittest.TestCase):
    def test_equivalent_warmup_and_working_formats(self):
        expected = [dict(weight=20, reps=10, is_warmup=True),
                    dict(weight=50, reps=10, is_warmup=True),
                    *[dict(weight=60, reps=9, is_warmup=False) for _ in range(3)]]
        for text in ('20 10\n50 10\n\n60 9 3',
                     '20×10\n50×10\n\n60×9×3',
                     '20 по 10\n50 по 10\n\n60 по 9 3'):
            with self.subTest(text=text):
                self.assertEqual(parse_exercise_input(text), expected)

    def test_multiple_working_variants_keep_order_and_duplicate_warmups(self):
        parsed = parse_exercise_input('20 10\n20 10\n\n60 9 3\n65 8 2')
        self.assertEqual([(s['weight'], s['reps'], s['is_warmup']) for s in parsed],
                         [(20, 10, True)] * 2 + [(60, 9, False)] * 3 + [(65, 8, False)] * 2)

    def test_without_separator_all_sets_are_working(self):
        for text, count in (('60 9 3', 3), ('60 9', 1)):
            with self.subTest(text=text):
                self.assertEqual(parse_exercise_input(text),
                                 [dict(weight=60, reps=9, is_warmup=False)] * count)
