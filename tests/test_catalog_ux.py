"""Search/card UX using temporary databases and fake Telegram transport."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from aiogram.methods import SendMessage, SendPhoto, DeleteMessage
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, Chat, User

from database import db, catalog
from test_editor import EditorTestCase


class CatalogUXTests(EditorTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        with db._connection(True) as c:
            for external, en in [('barbell-curl', 'Barbell Curl'), ('dips', 'Dips'),
                                 ('bench-dips', 'Bench Dips'), ('lateral-raise', 'Dumbbell Lateral Raises'),
                                 ('incline-bench-test', 'Incline Bench Press')]:
                c.execute('INSERT OR IGNORE INTO exercises(external_id,name_en) VALUES (?,?)', (external, en))
            eid = c.execute("SELECT id FROM exercises WHERE external_id='bench-press'").fetchone()[0]
            c.execute("UPDATE exercises SET equipment='barbell' WHERE id=?", (eid,))
            c.execute("INSERT INTO exercise_muscles VALUES (?,'pectoralis_major','primary')", (eid,))

    async def raw_press(self, label, *, contains=False, user=111):
        for request in reversed(self.session.requests):
            if isinstance(request, SendMessage) and request.chat_id == user and request.reply_markup:
                for row in request.reply_markup.inline_keyboard:
                    for button in row:
                        if (label in button.text if contains else label == button.text):
                            await self.click(user, button.callback_data)
                            return button.callback_data
                break
        self.fail('Missing button: ' + label)

    async def prepare_search(self, query='Bench'):
        await self.new_program('Test')
        await self.add_day('Day')
        await self.send(111, query)

    def test_synonyms_normalization_typos_and_ranking(self):
        examples = {'брусья': 'dips', '  ЖИМ   ЛЁЖА ': 'bench-press',
                    'бицепс со штангой': 'barbell-curl', 'разведения в стороны': 'lateral-raise',
                    'жим лежа': 'bench-press', 'barbell curl': 'barbell-curl',
                    'жим лжеа': 'bench-press'}
        for query, expected in examples.items():
            with self.subTest(query=query):
                result = catalog.search_exercises(query)
                if query == 'брусья':
                    self.assertIn(expected, [item['exercise_id'] for item in result['items']])
                else:
                    self.assertEqual(result['items'][0]['exercise_id'], expected)
        self.assertEqual(catalog.search_exercises('bench press')['items'][0]['exercise_id'], 'bench-press')
        self.assertEqual(catalog.score('bench press', ['bench press'])[0], 0)
        self.assertGreater(catalog.score('bench pres', ['bench press'])[0], 0)
        self.assertIsNotNone(catalog.score('брусья', ['Dips', 'Dips', 'брусья']))
        self.assertIsNone(catalog.image_source('../../.env'))
        self.assertIsNone(catalog.image_source('https://127.0.0.1/image.png'))
        self.assertIsNone(catalog.image_source('https://[broken/image.png'))
        self.assertIsNone(catalog.image_source('https://example.org:invalid/image.png'))

    async def test_card_no_image_back_add_and_editing_message(self):
        await self.prepare_search()
        before = deepcopy((await self.draft())['draft'])
        result_context = deepcopy((await self.draft())['search'])
        with patch('database.catalog.image_source', return_value=None):
            await self.raw_press('Жим лёжа', contains=True)
        editor = await self.draft()
        self.assertEqual(editor['draft'], before)
        self.assertEqual(editor['search'], result_context)
        text = next(r.text for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        for value in ('Жим лёжа', 'Bench press', 'Большая грудная', 'Штанга', 'Изображение отсутствует'):
            self.assertIn(value, text)
        self.assertFalse(any(isinstance(r, SendPhoto) for r in self.session.requests))
        token = await self.raw_press('Назад к результатам')
        self.assertEqual((await self.draft())['search'], result_context)
        await self.click(222, token)
        self.assertEqual((await self.draft())['draft'], before)
        await self.raw_press('Жим лёжа', contains=True)
        await self.raw_press('Добавить в день')
        self.assertEqual(len((await self.draft())['draft']['days'][0]['exercises']), 1)
        self.assertTrue(self.session.edits)
        await self.raw_press('Добавить упражнение')
        self.assertEqual((await self.draft())['draft']['days'][0]['exercises'][0]['exercise_id'], 'bench-press')

    async def test_image_request_failure_and_pagination_preserve_draft(self):
        with db._connection(True) as c:
            for i in range(12):
                c.execute('INSERT INTO exercises(external_id,name_en,name_ru) VALUES (?,?,?)',
                          (f'page-{i}', f'Page bench {i}', f'Жим страница {i}'))
        await self.prepare_search('страница')
        await self.raw_press('→')
        context = deepcopy((await self.draft())['search'])
        self.assertEqual(context['page'], 1)
        before = deepcopy((await self.draft())['draft'])
        with patch('database.catalog.image_source', return_value='https://example.org/exercise.jpg'):
            await self.raw_press('Жим страница', contains=True)
            self.assertTrue(any(isinstance(r, SendPhoto) for r in self.session.requests))
            await self.raw_press('Назад к результатам')
            original = self.session.make_request

            async def fail_photo(bot, method, timeout=None):
                if isinstance(method, SendPhoto):
                    raise TelegramBadRequest(method=method, message='Photo unavailable')
                return await original(bot, method, timeout)

            with patch.object(self.session, 'make_request', side_effect=fail_photo):
                await self.raw_press('Жим страница', contains=True)
            self.assertEqual((await self.draft())['draft'], before)
            self.assertTrue(any('сейчас недоступно' in r.text for r in self.session.requests if isinstance(r, SendMessage)))
            await self.raw_press('Назад к результатам')
            self.assertEqual((await self.draft())['search'], context)

    async def test_card_add_token_cannot_add_to_another_users_draft(self):
        await self.prepare_search()
        await self.raw_press('Жим лёжа', contains=True)
        editor = await self.draft()
        index = next(i for i, action in enumerate(editor['actions']) if action[0] == 'pick')
        forged = f"pe:{editor['token']}:{index}"
        await self.new_program('Friend', telegram_id=222)
        before = deepcopy((await self.draft(222))['draft'])
        await self.click(222, forged)
        self.assertEqual((await self.draft(222))['draft'], before)
        await self.click(111, forged)
        self.assertEqual(len((await self.draft())['draft']['days'][0]['exercises']), 1)
        await self.click(111, forged)
        self.assertEqual(len((await self.draft())['draft']['days'][0]['exercises']), 1)

    async def test_repeated_queries_work_directly_for_empty_and_nonempty_results(self):
        await self.prepare_search('zzzznothing')
        text = next(r.text for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        self.assertIn('ничего не найдено', text)
        self.assertIn('Введите другое название', text)
        await self.send(111, 'Bench')
        self.assertIn('bench-press', (await self.draft())['search_ids'])
        await self.send(111, 'бицепс со штангой')
        self.assertIn('barbell-curl', (await self.draft())['search_ids'])

    async def test_two_images_are_shown_in_order_and_only_bot_media_is_removed(self):
        await self.prepare_search()
        item = catalog.get_exercise('bench-press')
        item['images'] = [dict(source='https://example.org/start.jpg', caption='Исходное положение'),
                          dict(source='https://example.org/end.jpg', caption='Конечное положение')]
        item['image'] = item['images'][0]['source']
        original = self.session.make_request
        sent_ids = []

        async def media_transport(bot, method, timeout=None):
            if isinstance(method, SendPhoto):
                self.session.requests.append(method)
                sent_ids.append(7000 + len(sent_ids) + 1)
                return Message(message_id=sent_ids[-1], date=datetime.now(timezone.utc),
                               chat=Chat(id=111, type='private'),
                               from_user=User(id=bot.id, is_bot=True, first_name='Bot'))
            return await original(bot, method, timeout)

        with patch('database.catalog.get_exercise', return_value=item), patch.object(self.session, 'make_request', side_effect=media_transport):
            await self.raw_press('Жим лёжа', contains=True)
            photos = [request for request in self.session.requests if isinstance(request, SendPhoto)]
            self.assertEqual([request.caption for request in photos], ['Исходное положение', 'Конечное положение'])
            await self.raw_press('Назад к результатам')
            deleted = [request.message_id for request in self.session.requests if isinstance(request, DeleteMessage)]
            self.assertEqual(deleted, [7001, 7002])
            await self.raw_press('Жим лёжа', contains=True)
            await self.raw_press('Закрыть редактор')
            deleted = [request.message_id for request in self.session.requests if isinstance(request, DeleteMessage)]
            self.assertEqual(deleted, sent_ids)

    def test_optional_image_url_and_related_local_file_fallback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / 'data' / 'repdb' / 'images'
            images.mkdir(parents=True)
            image = images / 'bench.png'
            image.write_bytes(b'fixture')
            with db._connection(True) as c:
                c.execute('ALTER TABLE exercises ADD COLUMN image_url TEXT')
                c.execute("UPDATE exercises SET image_url='../outside.png' WHERE external_id='bench-press'")
                eid = c.execute("SELECT id FROM exercises WHERE external_id='bench-press'").fetchone()[0]
                c.execute('INSERT INTO exercise_images(exercise_id,view,path) VALUES (?,?,?)',
                          (eid, 'start', 'images/bench.png'))
            with patch('database.catalog.__file__', str(root / 'database' / 'catalog.py')):
                self.assertEqual(catalog.get_exercise('bench-press')['image'], image)
                self.assertIsNone(catalog.image_source('https://user:password@example.org/photo.png'))
                image.unlink()
                self.assertIsNone(catalog.get_exercise('bench-press')['image'])
