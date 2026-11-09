"""Read-only catalog presentation/search contracts, using only temporary data."""
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from database import catalog, db
from database.catalog_localization import RUSSIAN_NAMES
from test_database import TemporaryDatabaseTestCase


class CatalogSearchTests(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        entries = (
            ('bench-press', 'Barbell Bench Press'),
            ('db-bench-press', 'Dumbbell Bench Press'),
            ('incline-bench-press', 'Incline Barbell Bench Press'),
            ('paused-incline-bench-press', 'Paused Incline Bench Press'),
            ('close-grip-bench-press', 'Close-Grip Bench Press'),
            ('wide-grip-bench-press', 'Wide-Grip Bench Press'),
            ('barbell-curl', 'Barbell Curl'),
            ('barbell-preacher-curl', 'Barbell Preacher Curl'),
            ('bicep-curl', 'Dumbbell Bicep Curl'),
            ('dips', 'Chest Dips'),
            ('weighted-dips', 'Weighted Dips'),
            ('assisted-dips', 'Machine Assisted Dips'),
            ('bench-dips', 'Bench Dips'),
            ('crab-dips', 'Crab Dips'),
            ('lateral-raise', 'Dumbbell Lateral Raise'),
            ('seated-dumbbell-lateral-raise', 'Seated Dumbbell Lateral Raise'),
            ('cable-lateral-raise', 'Cable Lateral Raise'),
            ('leg-curl', 'Lying Leg Curl'),
            ('plate-loaded-abdominal-crunch', 'Plate-Loaded Abdominal Crunch'),
        )
        with db._connection(True) as c:
            c.executemany('INSERT INTO exercises(external_id,name_en) VALUES (?,?)', entries)

    def test_required_queries_select_the_right_variants_first(self):
        queries = {
            'брусья': 'dips',
            'брусья с весом': 'weighted-dips',
            'брусья с дополнительным весом': 'weighted-dips',
            'отжимания на брусьях с отягощением': 'weighted-dips',
            '  ЖИМ   ЛЁЖА  ': 'bench-press',
            'жим лежа': 'bench-press',
            'жим лжеа': 'bench-press',
            'бицепс со штангой': 'barbell-curl',
            'разведения в стороны': 'lateral-raise',
            'МАХИ В СТОРОНЫ': 'lateral-raise',
            'bench press': 'bench-press',
            'barbell curl': 'barbell-curl',
            'incline bench press': 'incline-bench-press',
            'Dumbbell Bench Press': 'db-bench-press',
            'жим гантелей лежа': 'db-bench-press',
            'жим штанги лёжа узким хватом': 'close-grip-bench-press',
            'развед': 'lateral-raise',
        }
        for query, expected in queries.items():
            with self.subTest(query=query):
                items = catalog.search_exercises(query)['items']
                self.assertTrue(items)
                self.assertEqual(items[0]['exercise_id'], expected)
        weighted = catalog.search_exercises('брусья с весом')['items']
        self.assertEqual([item['exercise_id'] for item in weighted], ['weighted-dips'])
        # Searching for biceps must not label leg curls as arm exercises.
        biceps = catalog.search_exercises('бицепс')['items']
        self.assertNotIn('leg-curl', [item['exercise_id'] for item in biceps])

    def test_complete_names_keep_variants_and_never_mix_translated_words(self):
        for external, expected in {
            'db-bench-press': 'Жим гантелей лёжа',
            'close-grip-bench-press': 'Жим штанги лёжа узким хватом',
            'weighted-dips': 'Отжимания на брусьях с дополнительным весом',
            'leg-curl': 'Сгибание ног лёжа',
        }.items():
            with self.subTest(external=external):
                card = catalog.get_exercise(external)
                self.assertEqual(card['name'], expected)
                self.assertTrue(card['has_russian_name'])
        # The checked-in localization covers the existing 602 IDs, without
        # unknown partial word substitutions leaking into display labels.
        self.assertEqual(len(RUSSIAN_NAMES), 602)
        for name in RUSSIAN_NAMES.values():
            self.assertTrue(re.search('[А-Яа-яЁё]', name))
            self.assertFalse(re.search('[A-Za-z\ufffd]', name), name)

    def test_valid_database_name_wins_and_corrupt_or_mixed_names_do_not(self):
        with db._connection(True) as c:
            c.execute("UPDATE exercises SET name_ru='Мой жим лёжа' WHERE external_id='bench-press'")
            c.execute("UPDATE exercises SET name_ru='Скручивания \ufffd\ufffd' WHERE external_id='plate-loaded-abdominal-crunch'")
            c.execute("UPDATE exercises SET name_ru='Отжимания weighted' WHERE external_id='weighted-dips'")
            c.execute("INSERT INTO exercises(external_id,name_en) VALUES ('new-unknown-variant','Rare Novel Exercise')")
        self.assertEqual(catalog.get_exercise('bench-press')['name'], 'Мой жим лёжа')
        self.assertEqual(catalog.search_exercises('жим штанги лёжа')['items'][0]['exercise_id'], 'bench-press')
        self.assertEqual(catalog.get_exercise('plate-loaded-abdominal-crunch')['name'],
                         'Скручивания в тренажёре с дисками')
        self.assertEqual(catalog.get_exercise('weighted-dips')['name'],
                         'Отжимания на брусьях с дополнительным весом')
        unknown = catalog.get_exercise('new-unknown-variant')
        self.assertEqual(unknown['name'], 'Rare Novel Exercise')
        self.assertFalse(unknown['has_russian_name'])
        # A translation entry never fabricates a selectable exercise in SQLite.
        self.assertEqual(catalog.search_exercises('Шавасана')['total'], 0)
        with self.assertRaises(ValueError):
            catalog.get_exercise('savasana')

    def test_search_is_read_only_literal_paginated_and_deterministic(self):
        with db._connection(True) as c:
            c.executemany('INSERT INTO exercises(external_id,name_en,name_ru) VALUES (?,?,?)',
                          [(f'page-{i:02}', f'Page {i}', f'Пагинация {i:02}') for i in range(19)])
        with db._connection() as c:
            before = '\n'.join(c.iterdump())
        first = catalog.search_exercises('пагинация', page=0)
        second = catalog.search_exercises('пагинация', page=1)
        last = catalog.search_exercises('пагинация', page=99)
        self.assertEqual([len(page['items']) for page in (first, second, last)], [8, 8, 3])
        self.assertEqual(last['page'], 2)
        ids = [item['exercise_id'] for page in (first, second, last) for item in page['items']]
        self.assertEqual(ids, [f'page-{i:02}' for i in range(19)])
        self.assertEqual(catalog.search_exercises("%' OR 1=1 --")['total'], 0)
        catalog.get_exercise('bench-press')
        with db._connection() as c:
            self.assertEqual('\n'.join(c.iterdump()), before)
        for kwargs in ({'query': 'x'}, {'query': 'x' * 101}, {'query': 'жим', 'page': -1},
                       {'query': 'жим', 'page_size': True}, {'query': 'жим', 'page_size': 21}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                catalog.search_exercises(**kwargs)

    def test_images_are_distinct_ordered_and_truthfully_labelled(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'data' / 'repdb' / 'images'
            folder.mkdir(parents=True)
            for filename in ('start.webp', 'peak.webp', 'main.webp'):
                (folder / filename).write_bytes(filename.encode())
            with db._connection(True) as c:
                c.execute('ALTER TABLE exercises ADD COLUMN image_url TEXT')
                eid = c.execute("SELECT id FROM exercises WHERE external_id='bench-press'").fetchone()[0]
                # Insertion order intentionally differs from movement order.
                c.executemany('INSERT INTO exercise_images(exercise_id,view,path) VALUES (?,?,?)',
                              [(eid, 'peak', 'images/peak.webp'),
                               (eid, 'main', 'images/main.webp'),
                               (eid, 'start', 'images/start.webp')])
                c.execute("UPDATE exercises SET image_url='images/start.webp' WHERE id=?", (eid,))
                c.execute("UPDATE exercises SET equipment='barbell' WHERE id=?", (eid,))
                c.execute("INSERT INTO exercise_muscles VALUES (?,'pectoralis_major','primary')", (eid,))
            with patch('database.catalog.__file__', str(root / 'database' / 'catalog.py')):
                card = catalog.get_exercise('bench-press')
                self.assertEqual([item['source'] for item in card['images']],
                                 [folder / 'start.webp', folder / 'peak.webp'])
                self.assertEqual([item['caption'] for item in card['images']],
                                 ['Исходное положение', 'Пиковая фаза'])
                self.assertEqual(card['image'], card['images'][0]['source'])
                self.assertEqual(card['muscles'], ['Большая грудная'])
                self.assertEqual(card['equipment_label'], 'Штанга')
                self.assertNotIn('Конечное положение', str(card['images']))
                repeated = catalog.exercise_images([('peak', 'images/start.webp'),
                                                     ('start', 'images/start.webp')],
                                                    'images/start.webp')
                self.assertEqual(len(repeated), 1)
                self.assertEqual(repeated[0]['view'], 'start')
                (folder / 'start.webp').unlink()
                (folder / 'peak.webp').unlink()
                one = catalog.get_exercise('bench-press')
                self.assertEqual(len(one['images']), 1)
                self.assertEqual(one['images'][0]['caption'], 'Изображение упражнения')
                (folder / 'main.webp').unlink()
                empty = catalog.get_exercise('bench-press')
                self.assertEqual(empty['images'], [])
                self.assertIsNone(empty['image'])

    def test_url_only_unknown_views_invalid_paths_and_missing_images(self):
        self.assertEqual(catalog.get_exercise('bench-press')['images'], [])
        url = 'https://example.org/exercise.png'
        self.assertEqual(catalog.exercise_images([], url),
                         [dict(source=url, view='main', caption='Изображение упражнения')])
        unknown = catalog.exercise_images([('side', url)])
        self.assertEqual(unknown[0]['caption'], 'Изображение упражнения')
        for value in ('../../.env', 'https://127.0.0.1/a.png', 'http://localhost/a.png',
                      'https://[broken/a.png', 'https://example.org:invalid/a.png',
                      'https://user:password@example.org/a.png', 'file:///C:/image.png'):
            with self.subTest(value=value):
                self.assertIsNone(catalog.image_source(value))
