"""Pure workout output: local dates, exact facts and safe bounded HTML."""
import re
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser

from handlers import unfinished_workout_views
from workouts import presentation as views


def actual(weight, reps, *, warmup=False, number=1):
    return dict(weight=weight, reps=reps, is_warmup=warmup, set_number=number)


def plain(html):
    return unescape(re.sub(r'</?b>', '', html))


class BoldValidator(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open = 0

    def handle_starttag(self, tag, attrs):
        if tag != 'b' or attrs or self.open:
            raise AssertionError('Unexpected/nested HTML tag')
        self.open += 1

    def handle_endtag(self, tag):
        if tag != 'b' or not self.open:
            raise AssertionError('Unbalanced HTML tag')
        self.open -= 1


class WorkoutPresentationTests(unittest.TestCase):
    def history(self, occurrences):
        return dict(day_name='C', started_at='2026-11-08T12:30:10.123456+00:00',
                    finished_at='2026-11-08T16:45:00+03:00', timezone='Europe/Moscow',
                    occurrences=occurrences)

    def assert_valid_chunks(self, chunks, limit):
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertTrue(chunk)
            self.assertLessEqual(len(chunk.encode('utf-16-le')) // 2, limit)
            parser = BoldValidator()
            parser.feed(chunk)
            parser.close()
            self.assertEqual(parser.open, 0)
            self.assertFalse(re.search(r'&(?:amp|lt|gt)?$', chunk))

    def test_one_conversion_to_user_timezone_and_readable_dates(self):
        utc = '2026-11-08T21:30:10.123456+00:00'
        self.assertEqual(views.format_datetime(utc, 'Europe/Moscow'), '09.11.2026 00:30')
        self.assertEqual(views.format_date(utc, 'Europe/Moscow'), '09.11.2026')
        aware_local = '2026-11-09T00:30:10.123456+03:00'
        self.assertEqual(views.format_datetime(aware_local, 'Europe/Moscow'), '09.11.2026 00:30')
        self.assertEqual(views.format_datetime(utc, 'Asia/Yekaterinburg'), '09.11.2026 02:30')
        self.assertEqual(views.format_date('2026-11-08'), '08.11.2026')
        self.assertEqual(views.format_datetime('2026-11-08'), views.UNKNOWN_TIME)
        for value in (None, '', 'not-a-date', '2026-13-01T12:00:00', object()):
            with self.subTest(value=value):
                self.assertEqual(views.format_datetime(value, 'Europe/Moscow'), views.UNKNOWN_TIME)
                self.assertEqual(views.format_date(value, 'Europe/Moscow'), views.UNKNOWN_TIME)
        self.assertEqual(views.format_datetime(utc, 'Invalid/Timezone'), '08.11.2026 21:30')

    def test_legacy_local_time_note_and_unknown_values_do_not_crash(self):
        details = dict(day_name='B', started_at='2026-11-08T18:30:10.123456',
                       timezone='Europe/Moscow', exercise_count=0, set_count=0,
                       exercises=[])
        now = datetime(2026, 11, 9, 17, 30, 10, 123456, tzinfo=timezone.utc)
        text, _ = unfinished_workout_views.compact_card(details, now=now)
        self.assertIn('Начата: 08.11.2026 18:30 (Europe/Moscow)', text)
        self.assertIn('С начала прошло: 1 день 2 часа', text)
        self.assertIn('без часового пояса', text)
        self.assertNotIn('длительность', text.lower())
        self.assertNotIn('T18:', text)
        self.assertNotIn('+03:00', text)
        self.assertIn('от 08.11.2026?', unfinished_workout_views.deletion_warning(details))
        details.update(started_at='invalid', timezone='+03:00')
        text, _ = unfinished_workout_views.compact_card(details, now=now)
        self.assertIn('дата не сохранена', text)
        self.assertIn('С начала прошло: неизвестно', text)
        self.assertNotIn('+03:00', text)

    def test_numbers_preserve_zero_fractional_values_without_float_suffix(self):
        for value, expected in ((0, '0'), (0.0, '0'), (-0.0, '0'), (25.0, '25'),
                                (25.5, '25.5'), (Decimal('25.50'), '25.5'),
                                (11.0, '11'), (1e-7, '0.0000001')):
            with self.subTest(value=value):
                self.assertEqual(views.format_number(value), expected)
        self.assertEqual(views.format_number(None), 'не записано')
        self.assertEqual(views.format_number(float('nan')), 'не записано')
        self.assertEqual(views.format_number(float('inf')), 'не записано')

    def test_current_card_has_compact_header_source_exact_sets_and_initial_only_help(self):
        previous = dict(day_name='C', started_at='2026-11-08T12:30:00+03:00',
                        timezone='Europe/Moscow', occurrences=[
                            dict(position=0, name='Брусья', sets=[actual(20, 10, warmup=True),
                                                                  actual(25.0, 11.0), actual(25, 11)]),
                            dict(position=4, name='Брусья', sets=[actual(0, 6)]),
                        ])
        text = views.current_exercise_card('Пресс', 3, 6, 1, previous)
        self.assertEqual(text.splitlines()[0], '<b>Следующее упражнение — Пресс.</b>')
        self.assertIn('Выполнено: 3 из 6 · Отложено: 1', text)
        self.assertIn('Прошлый результат · C · 08.11.2026:', text)
        self.assertIn('Включение №1', text)
        self.assertIn('Включение №2', text)
        self.assertIn('Разминка: 20 кг × 10', text)
        self.assertEqual(text.count('\n25 кг × 11'), 2)
        self.assertIn('\n0 кг × 6', text)
        self.assertNotIn('Рабочий:', text)
        self.assertNotIn('одним сообщением', text)
        initial = views.current_exercise_card('Пресс', 0, 6, 0, initial=True)
        self.assertIn('Прошлых результатов пока нет.', initial)
        self.assertIn('59 10 3', initial)
        self.assertIn('одним сообщением', initial)
        self.assertIn('пустой строкой', initial)
        self.assertNotIn('59 10 3', views.current_exercise_card('Пресс', 0, 6, 0))

    def test_saved_confirmation_has_every_set_separate_and_html_names_escaped(self):
        sets = [actual(0, 10, warmup=True), actual(25.5, 11.0), actual(25.5, 11), actual(25.5, 11)]
        text = views.saved_sets_confirmation('Брусья <A&B>', sets)
        self.assertEqual(text.splitlines()[0], '✅ Брусья &lt;A&amp;B&gt; — подходы сохранены.')
        self.assertIn('🔥 Разминка: 0 кг × 10', text)
        self.assertEqual(text.count('💪 25.5 кг × 11'), 3)
        self.assertEqual(len(text.splitlines()), 6)
        self.assertNotIn('11.0', text)

    def test_history_compacts_only_adjacent_identical_sets_inside_occurrence(self):
        first = dict(position=0, name='Жим <штанги>&', sets=[
            actual(20, 10, warmup=True), actual(150, 12), actual(150, 12), actual(150, 12),
            actual(155.5, 9), actual(150, 12)])
        second = dict(position=2, name='Жим <штанги>&', sets=[actual(150, 12), actual(150, 12)])
        history = self.history([first, second])
        chunks = views.completed_history_chunks(history)
        self.assertEqual(len(chunks), 1)
        text = chunks[0]
        self.assertIn('📊 Последняя тренировка C\n\nНачало: 08.11.2026 15:30\nОкончание: 08.11.2026 16:45', text)
        self.assertEqual(text.count('<b>Жим &lt;штанги&gt;&amp;</b>'), 2)
        self.assertIn('Разминка: 20 кг × 10', text)
        self.assertIn('Рабочий: 150 кг × 12 × 3', text)
        self.assertIn('Рабочий: 155.5 кг × 9\nРабочий: 150 кг × 12', text)
        self.assertIn('Рабочий: 150 кг × 12 × 2', text)
        self.assertNotIn('Рабочие', text)
        self.assertNotIn('T12:', text)
        self.assertNotIn('123456', text)
        self.assertNotIn('+00:00', text)

    def test_long_html_history_and_current_cards_are_bounded_without_lost_text(self):
        name = 'Название <&> 💪 ' * 450
        occurrences = [dict(position=number, name=name + str(number),
                            sets=[actual(number + .5, 12), actual(number, 10, warmup=True)])
                       for number in range(4)]
        history = self.history(occurrences)
        chunks = views.completed_history_chunks(history, limit=250)
        self.assertGreater(len(chunks), 10)
        self.assert_valid_chunks(chunks, 250)
        expected = plain(''.join(views.completed_history_chunks(history, limit=50000)))
        self.assertEqual(''.join(plain(chunk) for chunk in chunks), expected)
        text = views.current_exercise_card(name, 1, 4, 0)
        small = views.html_chunks(text, limit=100)
        self.assert_valid_chunks(small, 100)
        self.assertEqual(''.join(plain(chunk) for chunk in small), plain(text))
        confirmation = views.saved_sets_confirmation(name, [actual(25.5, 11)] * 90)
        small = views.html_chunks(confirmation, limit=100)
        self.assert_valid_chunks(small, 100)
        self.assertEqual(''.join(plain(chunk) for chunk in small), plain(confirmation))

    def test_history_bad_dates_empty_sets_and_unlinked_boundaries_are_honest(self):
        history = self.history([dict(position=None, name='Жим', sets=[actual(50, 8)]),
                                dict(position=None, name='Жим', sets=[actual(50, 8)])])
        history.update(started_at=None, finished_at='bad-date')
        text = ''.join(views.completed_history_chunks(history))
        self.assertEqual(text.count('<b>Жим</b>'), 2)
        self.assertEqual(text.count('Рабочий: 50 кг × 8'), 2)
        self.assertNotIn('× 2', text)
        self.assertNotIn('Включение №', text)
        self.assertEqual(text.count('дата не сохранена'), 2)
        text = ''.join(views.completed_history_chunks(self.history([])))
        self.assertIn('Подходы не записаны.', text)


if __name__ == '__main__':
    unittest.main()
