import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiogram.methods import SendMessage

from database import db, programs, sessions, catalog
from database.migrations_v3 import migrate_v3
from database.migrations_v4 import migrate_v4
from database import migrations_v3
from database.migrations import migrate
from database.migrations_v2 import migrate_v2
from legacy_schema import create_legacy_database
from states.program_editor import ProgramEditor
from states.workout import WorkoutState
from test_handlers import TelegramTestCase
from test_database import TemporaryDatabaseTestCase
from test_programs import document, SETS
from workouts.plan_parser import parse_plan


class EditorTestCase(TelegramTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        with db._connection(True) as c:
            c.execute("UPDATE exercises SET name_en='Bench press',name_ru='Жим лёжа' WHERE external_id='bench-press'")
            c.execute("UPDATE exercises SET name_en='Squat',name_ru='Присед' WHERE external_id='squat'")

    async def press(self, label, telegram_id=111, *, contains=False):
        if label == '＋ Найти упражнение':
            for request in reversed(self.session.requests):
                if isinstance(request, SendMessage) and request.chat_id == telegram_id and request.reply_markup:
                    if any(b.text == 'Добавить ещё' for row in request.reply_markup.inline_keyboard for b in row):
                        label = 'Добавить ещё'
                    break
        # Optional legacy-plan settings are now explicitly behind one screen.
        if label in {'Число рабочих подходов', 'Вес и повторения рабочих', 'Разминочные подходы'}:
            for request in reversed(self.session.requests):
                if isinstance(request, SendMessage) and request.chat_id == telegram_id and request.reply_markup:
                    if any(b.text == 'Необязательный план' for row in request.reply_markup.inline_keyboard for b in row):
                        await self.press('Необязательный план', telegram_id)
                    break
        for request in reversed(self.session.requests):
            if isinstance(request, SendMessage) and request.chat_id == telegram_id and request.reply_markup:
                for row in request.reply_markup.inline_keyboard:
                    for button in row:
                        if (label in button.text if contains else button.text == label):
                            await self.click(telegram_id, button.callback_data)
                            # Existing helper represents selecting AND adding an exercise.
                            editor = (await self.context(telegram_id).get_data()).get('editor', {})
                            if label != 'Добавить в день' and any(a[0] == 'pick' for a in editor.get('actions', [])):
                                await self.press('Добавить в день', telegram_id)
                            return button.callback_data
                break
        self.fail(f'Button not found: {label}')

    async def draft(self, telegram_id=111):
        return (await self.context(telegram_id).get_data())['editor']

    async def new_program(self, name='Мой план', telegram_id=111):
        await self.send(telegram_id, '🗂 Мои программы')
        await self.press('＋ Создать программу', telegram_id)
        await self.send(telegram_id, name)

    async def add_day(self, name):
        await self.press('＋ Добавить день')
        await self.send(111, name)

    async def add_exercise(self, query='Bench', label=None, *, open_details=True):
        before = len((await self.draft())['draft']['days'][-1]['exercises'])
        await self.press('＋ Найти упражнение')
        await self.send(111, query)
        await self.press(label or {'Bench': 'Жим лёжа', 'squat': 'Присед'}.get(query, query), contains=True)
        editor = await self.draft()
        items = editor['draft']['days'][-1]['exercises']
        if open_details and len(items) > before:
            await self.press(f"{len(items)}. {items[-1]['name']}")



class EditorTests(EditorTestCase):
    async def test_build_two_days_weights_warmups_save_activate_and_preview(self):
        before = len(programs.list_programs(self.uid))
        await self.new_program()
        await self.add_day('Верх')
        await self.add_exercise()
        await self.press('Вес и повторения рабочих')
        await self.send(111, '50 8 3')
        await self.press('Разминочные подходы')
        await self.send(111, '20 10\n40 5')
        await self.press('Назад')
        await self.press('Назад')
        await self.add_day('Низ')
        await self.add_exercise('squat')
        await self.press('Число рабочих подходов')
        await self.send(111, '4')
        await self.press('Назад')
        await self.press('Сохранить программу')
        editor = await self.draft()
        pid = editor['program_id']
        self.assertEqual(len(programs.list_programs(self.uid)), before + 1)
        program = programs.get_program(self.uid, pid)
        self.assertEqual([d['name'] for d in program['days']], ['Верх', 'Низ'])
        upper, lower = [d['exercises'][0] for d in program['days']]
        self.assertEqual(upper['working_sets'], 3)
        self.assertEqual(upper['working_plan'][0], dict(weight=50, reps=8))
        self.assertEqual(upper['warmup'], [dict(weight=20, reps=10), dict(weight=40, reps=5)])
        self.assertEqual(lower['working_sets'], 4)
        await self.press('Сделать активной')
        self.assertEqual(programs.get_active_program(self.uid)['id'], pid)
        await self.press('Просмотр состава')
        await self.press('Назад')
        await self.press('Назад')
        await self.press('Отмена')
        await self.click(111, f"previewday:{program['days'][0]['id']}")
        self.assertIsNone(db.get_active_workout(self.uid))
        await self.press('Начать тренировку')
        session = sessions.get_session(self.uid, db.get_active_workout(self.uid)[0])
        self.assertEqual(session['snapshot']['day_name'], 'Верх')
        self.assertEqual(db.get_workout_sets(self.uid, session['workout_id']), [])

    async def test_duplicates_require_confirmation_reorder_and_delete_are_draft_only(self):
        await self.new_program()
        await self.add_day('Один')
        await self.add_exercise()
        await self.press('Назад')
        await self.add_exercise()
        self.assertEqual(len((await self.draft())['draft']['days'][0]['exercises']), 1)
        await self.press('Подтвердить')
        await self.press('2. Жим лёжа')
        await self.press('Моё название')
        await self.send(111, 'Второй жим')
        await self.press('Упражнение ↑')
        draft = (await self.draft())['draft']
        self.assertEqual(draft['days'][0]['exercises'][0]['name'], 'Второй жим')
        await self.press('Удалить упражнение')
        self.assertEqual(len((await self.draft())['draft']['days'][0]['exercises']), 2)
        await self.press('Подтвердить')
        self.assertEqual(len((await self.draft())['draft']['days'][0]['exercises']), 1)
        await self.press('Назад')
        await self.add_day('Два')
        await self.add_exercise('squat')
        await self.press('Назад')
        await self.press('День ↑')
        self.assertEqual([d['name'] for d in (await self.draft())['draft']['days']], ['Два', 'Один'])

    async def test_invalid_inputs_and_incomplete_program_do_not_write(self):
        count = len(programs.list_programs(self.uid))
        await self.send(111, '/programs')
        await self.press('＋ Создать программу')
        await self.send(111, ' ')
        self.assertEqual(await self.context(111).get_state(), ProgramEditor.text.state)
        await self.send(111, 'x' * 121)
        await self.send(111, 'Valid')
        await self.press('Сохранить')
        self.assertEqual(len(programs.list_programs(self.uid)), count)
        await self.add_day('Day')
        await self.add_exercise()
        await self.press('Число рабочих подходов')
        for value in ('0', '-1', '1.5', '101', 'many'):
            await self.send(111, value)
            self.assertEqual((await self.draft())['draft']['days'][0]['exercises'][0]['working_sets'], None)
        await self.send(111, '2')
        await self.press('Вес и повторения рабочих')
        for value in ('nan 8', '50 0', '50 8 9999999', 'text', '50 99999999999999999999999999'):
            await self.send(111, value)
            self.assertEqual(await self.context(111).get_state(), ProgramEditor.text.state)
        await self.send(111, '- - 2')
        self.assertEqual((await self.draft())['draft']['days'][0]['exercises'][0]['working_plan'], [dict(weight=None, reps=None)] * 2)

    async def test_old_button_double_tap_and_cross_user_token_have_no_effect(self):
        await self.send(111, '/programs')
        old = await self.press('＋ Создать программу')
        await self.click(111, old)
        await self.send(111, 'One')
        before = await self.draft()
        await self.click(111, old)
        self.assertEqual(await self.draft(), before)
        await self.send(222, '/programs')
        other_before = await self.draft(222)
        await self.click(222, old)
        self.assertEqual(await self.draft(222), other_before)
        self.assertEqual(len(programs.list_programs(self.friend)), 1)
        await self.click(111, 'pe:forged:999999')
        self.assertEqual(await self.draft(), before)

    async def test_archive_last_program_warns_and_restores_without_history_loss(self):
        wid = db.create_workout(self.uid, 'Old')
        db.save_set(self.uid, wid, 'Old exercise', 1, 20, 10)
        db.finish_workout(self.uid, wid)
        history = db.get_workout_sets(self.uid, wid)
        await self.send(111, '/programs')
        await self.press('A/B/C', contains=True)
        await self.press('В архив')
        self.assertTrue(any('последняя доступная' in (r.text or '') for r in self.session.requests if isinstance(r, SendMessage)))
        self.assertEqual(len(programs.list_programs(self.uid)), 1)
        confirm = await self.press('Подтвердить')
        self.assertEqual(programs.list_programs(self.uid), [])
        self.assertIsNone(programs.get_active_program(self.uid))
        await self.click(111, confirm)
        await self.press('Архив')
        await self.press('A/B/C', contains=True)
        await self.press('Восстановить')
        self.assertEqual(len(programs.list_programs(self.uid)), 1)
        self.assertEqual(db.get_workout_sets(self.uid, wid), history)

    async def test_workout_blocks_editor_and_editor_blocks_workout_fsm(self):
        await self.click(111, 'nextday')
        before = await self.context(111).get_data()
        await self.send(111, '/programs')
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)
        self.assertEqual(await self.context(111).get_data(), before)
        await self.send(111, '/cancel')
        await self.press('Удалить тренировку')
        await self.new_program()
        draft = await self.draft()
        await self.click(111, 'nextday')
        self.assertEqual(await self.draft(), draft)
        self.assertIsNone(db.get_active_workout(self.uid))

    async def test_optimistic_conflict_does_not_overwrite_external_edit(self):
        await self.send(111, '/programs')
        await self.press('A/B/C', contains=True)
        pid = (await self.draft())['program_id']
        await self.press('Переименовать')
        await self.send(111, 'My draft name')
        programs.update_program(self.uid, pid, name='External edit')
        await self.press('Сохранить')
        self.assertEqual(programs.get_program(self.uid, pid)['name'], 'External edit')

    async def test_search_pagination_unicode_and_repeated_save(self):
        with db._connection(True) as c:
            for i in range(15):
                c.execute('INSERT INTO exercises(external_id,name_en,name_ru) VALUES (?,?,?)', (f'press-{i}', f'Press {i}', f'Жим {i}'))
        await self.new_program()
        await self.add_day('Search')
        await self.press('＋ Найти упражнение')
        await self.send(111, 'ЖИМ')
        await self.press('→')
        await self.press('Жим 8')
        old = await self.press('Сохранить программу')
        pid = (await self.draft())['program_id']
        await self.click(111, old)
        self.assertEqual(len([p for p in programs.list_programs(self.uid) if p['id'] == pid]), 1)
        self.assertEqual(programs.get_program(self.uid, pid)['days'][0]['exercises'][0]['exercise_id'], 'press-8')

    async def test_first_user_has_create_screen_without_abc_and_cancel_confirms(self):
        with patch.dict('os.environ', {'ALLOWED_TELEGRAM_IDS': '111,222,333'}):
            await self.send(333, '/programs')
            await self.press('＋ Создать программу', 333)
            await self.send(333, 'Своя первая')
            new_id = db.get_or_create_user(333, 333)['id']
            self.assertEqual(programs.list_programs(new_id), [])
            await self.send(333, '/cancel')
            self.assertTrue((await self.draft(333))['dirty'])
            await self.press('Подтвердить', 333)
            self.assertIsNone((await self.draft(333))['draft'])

    async def test_forged_program_id_still_fails_owner_check(self):
        await self.send(111, '/programs')
        editor = await self.draft()
        other_pid = programs.list_programs(self.friend)[0]['id']
        # Simulate an injected target as well as normal opaque token tampering:
        # even trusted routing is never sufficient to authorize a DB operation.
        editor['actions'] = [['open', other_pid]]
        await self.context(111).update_data(editor=editor)
        await self.click(111, f"pe:{editor['token']}:0")
        self.assertIsNone((await self.draft())['draft'])
        self.assertEqual(programs.get_program(self.friend, other_pid)['name'], 'Personal')

    async def test_rename_day_program_and_warmup_removal(self):
        await self.send(111, '/programs')
        await self.press('A/B/C', contains=True)
        pid = (await self.draft())['program_id']
        await self.press('Переименовать')
        await self.send(111, 'Новое имя')
        await self.press('1. A', contains=True)
        await self.press('Переименовать день')
        await self.send(111, 'Верх тела')
        await self.press('1. Жим', contains=True)
        await self.press('Разминочные подходы')
        await self.send(111, '-')
        await self.press('Назад')
        await self.press('Сохранить программу')
        plan = programs.get_program(self.uid, pid)
        self.assertEqual(plan['name'], 'Новое имя')
        self.assertEqual(plan['days'][0]['name'], 'Верх тела')
        self.assertEqual(plan['days'][0]['exercises'][0]['warmup'], [])
        await self.send(111, '🏋️ Тренировка')
        self.assertIsNone(await self.context(111).get_state())
        self.assertIsNone(db.get_active_workout(self.uid))
        for request in self.session.requests:
            if isinstance(request, SendMessage) and request.reply_markup:
                for row in request.reply_markup.inline_keyboard:
                    for button in row:
                        self.assertLessEqual(len(button.callback_data.encode('utf-8')), 64)


class EditorDatabaseTests(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        from program_fixtures import seed_catalog
        seed_catalog()
        self.other = db.get_or_create_user(222, 222)['id']

    def test_foreign_ids_revision_and_archived_start_are_denied(self):
        pid = programs.create_program(self.user_id, 'Mine', document(1), activate=True)
        program = programs.get_program(self.user_id, pid)
        for operation in (
            lambda: programs.archive_program(self.other, pid, expected_revision=0),
            lambda: programs.restore_program(self.other, pid, expected_revision=0),
            lambda: programs.get_day(self.other, program['days'][0]['id']),
            lambda: programs.update_program(self.other, pid, name='bad', expected_revision=0),
        ):
            with self.assertRaises(programs.ProgramAccessError):
                operation()
        programs.archive_program(self.user_id, pid, expected_revision=0)
        with self.assertRaises(programs.ProgramAccessError):
            programs.activate_program(self.user_id, pid)
        with self.assertRaises(programs.ProgramAccessError):
            sessions.start_day(self.user_id, program['days'][0]['id'])

    def test_archive_and_restore_leave_active_snapshot_and_history_intact(self):
        pid = programs.create_program(self.user_id, 'Mine', document(2, 2), activate=True)
        session = sessions.start_day(self.user_id)
        programs.archive_program(self.user_id, pid, expected_revision=0)
        self.assertEqual(sessions.get_session(self.user_id, session['workout_id']), session)
        next_step = sessions.complete_exercise(self.user_id, session['workout_id'], 0, SETS)
        sessions.complete_exercise(self.user_id, session['workout_id'], next_step['revision'], SETS)
        programs.restore_program(self.user_id, pid, expected_revision=1)
        self.assertEqual(len(db.get_workout_sets(self.user_id, session['workout_id'])), 2)

    def test_search_is_unicode_literal_and_paginated(self):
        previous = catalog.search_exercises('жим')['total']
        with db._connection(True) as c:
            for i in range(19):
                c.execute('INSERT INTO exercises(external_id,name_en,name_ru) VALUES (?,?,?)', (f'u-{i}', f'UNICODE press {i}', f'Жим {i}'))
        self.assertEqual(catalog.search_exercises('жим')['total'], previous + 19)
        self.assertEqual(len(catalog.search_exercises('ЖИМ', 1)['items']), 8)
        self.assertEqual(len(catalog.search_exercises('unicode', 2)['items']), 3)
        self.assertEqual(catalog.search_exercises("%' OR 1=1 --")['total'], 0)
        with self.assertRaises(ValueError):
            catalog.search_exercises('a')

    def test_current_migration_v4_is_idempotent(self):
        with db._connection() as c:
            before = '\n'.join(c.iterdump())
        self.assertFalse(migrate_v4(self.db_path))
        with db._connection() as c:
            self.assertEqual('\n'.join(c.iterdump()), before)


class MigrationV3Tests(unittest.TestCase):
    def test_v3_preserves_v2_and_rolls_back_on_final_check_failure(self):
        with tempfile.TemporaryDirectory(prefix='life-bot-v3-') as directory:
            path = Path(directory) / 'v2.sqlite3'
            create_legacy_database(path)
            migrate(path, 111)
            migrate_v2(path)
            c = sqlite3.connect(path)
            try:
                with c:
                    c.execute("INSERT INTO programs(user_id,name,created_at) VALUES (1,'Existing','before')")
                    c.execute("INSERT INTO workouts(user_id,workout_type,started_at) VALUES (1,'Old','before')")
                before = '\n'.join(c.iterdump())
                check = migrations_v3._check
                count = []

                def fail_final(connection):
                    check(connection)
                    count.append(True)
                    if len(count) == 2:
                        raise ValueError('Simulated validation failure')

                with patch.object(migrations_v3, '_check', side_effect=fail_final):
                    with self.assertRaises(ValueError):
                        migrate_v3(path)
                self.assertEqual('\n'.join(c.iterdump()), before)
                self.assertTrue(migrate_v3(path))
                self.assertFalse(migrate_v3(path))
                self.assertEqual(c.execute('SELECT name,archived_at,revision FROM programs').fetchall(), [('Existing', None, 0)])
                self.assertEqual(c.execute('SELECT workout_type,started_at FROM workouts').fetchall(), [('Old', 'before')])
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
            finally:
                c.close()
