import sqlite3
import tempfile
import unittest
from copy import deepcopy
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
        # Optional existing plan settings remain behind their own screen.
        if label in {'Число рабочих подходов', 'Вес и повторения рабочих', 'Разминочные подходы'}:
            editor = (await self.context(telegram_id).get_data()).get('editor', {})
            if any(a[0] == 'optional_plan' for a in editor.get('actions', [])):
                await self.press('Необязательный план', telegram_id)
        for request in reversed(self.session.requests):
            if isinstance(request, SendMessage) and request.chat_id == telegram_id and request.reply_markup:
                for row in request.reply_markup.inline_keyboard:
                    for button in row:
                        if (label in button.text if contains else button.text == label):
                            await self.click(telegram_id, button.callback_data)
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
        await self.press('Создать программу', telegram_id)
        await self.send(telegram_id, name)

    async def add_day(self, name, telegram_id=111):
        editor = await self.draft(telegram_id)
        if editor.get('prompt') != ['day_new']:
            await self.press('Добавить день', telegram_id)
        await self.send(telegram_id, name)

    async def add_exercise(self, query='Bench', label=None, *, open_details=True, telegram_id=111):
        editor = await self.draft(telegram_id)
        day = editor['prompt'][1] if (editor.get('prompt') or [None])[0:1] == ['search'] else len(editor['draft']['days']) - 1
        before = len(editor['draft']['days'][day]['exercises'])
        if not editor.get('prompt') or editor['prompt'][0] != 'search':
            await self.press('Добавить упражнение', telegram_id)
        await self.send(telegram_id, query)
        await self.press(label or {'Bench': 'Жим лёжа', 'squat': 'Присед'}.get(query, query), telegram_id, contains=True)
        editor = await self.draft(telegram_id)
        items = editor['draft']['days'][day]['exercises']
        if open_details and len(items) > before:
            await self.press(f"{len(items)}. {items[-1]['name']}", telegram_id)


class EditorTests(EditorTestCase):
    async def test_two_days_autosave_optional_plans_activation_preview_and_restart(self):
        active = programs.get_active_program(self.uid)['id']
        await self.new_program()
        self.assertEqual((await self.draft())['prompt'], ['day_new'])
        await self.add_day('Верх')
        self.assertEqual((await self.draft())['prompt'], ['search', 0])
        await self.add_exercise()
        pid = (await self.draft())['program_id']
        self.assertEqual(programs.get_active_program(self.uid)['id'], active)
        await self.press('Вес и повторения рабочих')
        await self.send(111, '50 8 3')
        await self.press('Разминочные подходы')
        await self.send(111, '20 10\n40 5')
        await self.press('Назад')  # exercise
        await self.press('Назад')  # day
        await self.press('Назад')  # program
        await self.add_day('Низ')
        await self.add_exercise('squat')
        await self.press('Число рабочих подходов')
        await self.send(111, '4')
        program = programs.get_program(self.uid, pid)
        self.assertEqual([d['name'] for d in program['days']], ['Верх', 'Низ'])
        upper, lower = [d['exercises'][0] for d in program['days']]
        self.assertEqual(upper['working_sets'], 3)
        self.assertEqual(upper['working_plan'][0], dict(weight=50, reps=8))
        self.assertEqual(upper['warmup'], [dict(weight=20, reps=10), dict(weight=40, reps=5)])
        self.assertEqual(lower['working_sets'], 4)
        await self.press('Назад')
        await self.press('Назад')
        await self.press('Назад')
        await self.press('Сделать активной')
        await self.press('Просмотреть программу')
        preview = self.session.requests[-2].text if not isinstance(self.session.requests[-1], SendMessage) else self.session.requests[-1].text
        self.assertIn('Верх', preview)
        self.assertIn('Низ', preview)
        await self.press('Закрыть редактор')
        await self.context(111).clear()  # MemoryStorage loss does not undo data.
        self.assertEqual(programs.get_program(self.uid, pid), program | {'is_active': 1})

    async def test_duplicates_have_distinct_ids_button_reorder_and_saved_deletion(self):
        await self.new_program()
        await self.add_day('Один')
        await self.add_exercise(open_details=False)
        pid = (await self.draft())['program_id']
        await self.add_exercise(open_details=False)
        self.assertEqual(len(programs.get_program(self.uid, pid)['days'][0]['exercises']), 1)
        await self.press('Да')
        plan = programs.get_program(self.uid, pid)
        ids = [e['id'] for e in plan['days'][0]['exercises']]
        self.assertEqual(len(set(ids)), 2)
        await self.press('2. Жим лёжа')
        await self.press('Моё название')
        await self.send(111, 'Второй жим')
        await self.press('Назад')
        await self.press('Изменить порядок')
        await self.press('2. Второй жим')
        await self.press('1')
        items = programs.get_program(self.uid, pid)['days'][0]['exercises']
        self.assertEqual([e['id'] for e in items], list(reversed(ids)))
        await self.press('1. Второй жим')
        await self.press('Удалить упражнение')
        await self.press('Назад')
        self.assertEqual(len(programs.get_program(self.uid, pid)['days'][0]['exercises']), 2)
        await self.press('Удалить упражнение')
        await self.press('Да')
        self.assertEqual(len(programs.get_program(self.uid, pid)['days'][0]['exercises']), 1)

    async def test_last_exercise_then_last_day_confirmation_preserves_history(self):
        await self.new_program()
        await self.add_day('Один')
        await self.add_exercise()
        pid = (await self.draft())['program_id']
        wid = db.create_workout(self.uid, 'History')
        db.save_set(self.uid, wid, 'Actual', 1, 20, 10)
        db.finish_workout(self.uid, wid)
        rows = db.get_workout_sets(self.uid, wid)
        await self.press('Удалить упражнение')
        await self.press('Да')
        self.assertEqual(len(programs.get_program(self.uid, pid)['days']), 1)
        await self.press('Назад')
        self.assertEqual(len(programs.get_program(self.uid, pid)['days']), 1)
        await self.press('Удалить день')
        await self.press('Да')
        self.assertNotIn(pid, [p['id'] for p in programs.list_programs(self.uid)])
        self.assertEqual(db.get_workout_sets(self.uid, wid), rows)

    async def test_invalid_and_empty_input_never_creates_empty_objects(self):
        before = programs.list_programs(self.uid)
        await self.send(111, '/programs')
        await self.press('Создать программу')
        for value in (' ', 'x' * 121):
            await self.send(111, value)
            self.assertEqual(await self.context(111).get_state(), ProgramEditor.text.state)
        await self.send(111, 'Valid')
        await self.add_day('Day')
        await self.press('Закрыть редактор')
        self.assertEqual(programs.list_programs(self.uid), before)
        await self.new_program()
        await self.add_day('With exercise')
        await self.add_exercise()
        await self.press('Число рабочих подходов')
        for value in ('0', '-1', '1.5', '101', 'many'):
            await self.send(111, value)
            self.assertIsNone((await self.draft())['draft']['days'][0]['exercises'][0]['working_sets'])
        await self.send(111, '2')
        await self.press('Вес и повторения рабочих')
        for value in ('nan 8', '50 0', '50 8 9999999', 'text'):
            await self.send(111, value)
            self.assertEqual(await self.context(111).get_state(), ProgramEditor.text.state)
        await self.send(111, '- - 2')
        self.assertEqual((await self.draft())['draft']['days'][0]['exercises'][0]['working_plan'], [dict(weight=None, reps=None)] * 2)

    async def test_old_button_double_tap_cross_user_and_workout_token_have_no_effect(self):
        await self.send(111, '/programs')
        old = await self.press('Создать программу')
        await self.send(111, 'One')
        before = await self.draft()
        await self.click(111, old)
        self.assertEqual(await self.draft(), before)
        await self.send(222, '/programs')
        other = await self.draft(222)
        await self.click(222, old)
        self.assertEqual(await self.draft(222), other)
        await self.press('Закрыть редактор')
        await self.click(111, 'nextday')
        state_before = await self.context(111).get_data()
        await self.click(111, old)
        self.assertEqual(await self.context(111).get_data(), state_before)
        self.assertEqual(await self.context(111).get_state(), WorkoutState.waiting_for_exercise_input.state)

    async def test_archive_restore_and_delete_keep_recorded_history(self):
        wid = db.create_workout(self.uid, 'Old')
        db.save_set(self.uid, wid, 'Old exercise', 1, 20, 10)
        db.finish_workout(self.uid, wid)
        history = db.get_workout_sets(self.uid, wid)
        await self.send(111, '/programs')
        await self.press('A/B/C', contains=True)
        await self.press('В архив')
        self.assertTrue(any('последняя доступная' in (r.text or '') for r in self.session.requests if isinstance(r, SendMessage)))
        await self.press('Да')
        self.assertEqual(programs.list_programs(self.uid), [])
        self.assertIsNone(programs.get_active_program(self.uid))
        await self.press('Архив')
        await self.press('A/B/C', contains=True)
        await self.press('Восстановить')
        self.assertIsNone(programs.get_active_program(self.uid))
        self.assertEqual(db.get_workout_sets(self.uid, wid), history)

    async def test_optimistic_conflict_keeps_entered_text_and_actual_version(self):
        await self.send(111, '/programs')
        await self.press('A/B/C', contains=True)
        pid = (await self.draft())['program_id']
        await self.press('Переименовать программу')
        programs.update_program(self.uid, pid, name='External edit')
        await self.send(111, 'My entered name')
        self.assertEqual(programs.get_program(self.uid, pid)['name'], 'External edit')
        self.assertEqual((await self.draft())['draft']['name'], 'My entered name')
        self.assertTrue((await self.draft())['dirty'])
        await self.press('Открыть актуальную версию')
        self.assertEqual((await self.draft())['draft']['name'], 'External edit')
        self.assertEqual((await self.draft())['rejected_change']['name'], 'My entered name')
        await self.press('Посмотреть мой ввод')
        self.assertTrue(any('My entered name' in (r.text or '') for r in self.session.requests if isinstance(r, SendMessage)))

    async def test_failed_sql_write_retains_version_and_retry_commits_once(self):
        await self.new_program()
        await self.add_day('Day')
        await self.add_exercise(open_details=False)
        pid = (await self.draft())['program_id']
        before = programs.get_program(self.uid, pid)
        await self.press('Назад')
        await self.press('Переименовать программу')
        with patch.object(programs, 'update_program', side_effect=sqlite3.OperationalError('database is locked')):
            await self.send(111, 'Retry name')
        self.assertEqual(programs.get_program(self.uid, pid), before)
        self.assertEqual((await self.draft())['draft']['name'], 'Retry name')
        await self.press('Посмотреть мой ввод')
        await self.press('Назад')
        self.assertEqual(programs.get_program(self.uid, pid), before)
        old = await self.press('Повторить')
        self.assertEqual(programs.get_program(self.uid, pid)['name'], 'Retry name')
        revision = programs.get_program(self.uid, pid)['revision']
        await self.click(111, old)
        self.assertEqual(programs.get_program(self.uid, pid)['revision'], revision)

    async def test_first_program_activation_only_after_first_exercise(self):
        with patch.dict('os.environ', {'ALLOWED_TELEGRAM_IDS': '111,222,333'}):
            await self.new_program('First', telegram_id=333)
            uid = db.get_or_create_user(333, 333)['id']
            self.assertEqual(programs.list_programs(uid), [])
            await self.add_day('First day', telegram_id=333)
            self.assertIsNone(programs.get_active_program(uid))
            await self.add_exercise(open_details=False, telegram_id=333)
            active = programs.get_active_program(uid)
            self.assertIsNotNone(active)
            await self.press('Закрыть редактор', 333)
            await self.context(333).clear()
            self.assertEqual(programs.get_active_program(uid), active)
            await self.new_program('Second', telegram_id=333)
            await self.add_day('Second day', telegram_id=333)
            await self.add_exercise(open_details=False, telegram_id=333)
            self.assertEqual(programs.get_active_program(uid)['id'], active['id'])

    async def test_cancel_after_failed_edit_keeps_confirmed_input_accessible(self):
        await self.send(111, '/programs')
        await self.press('A/B/C', contains=True)
        pid = (await self.draft())['program_id']
        await self.press('Переименовать программу')
        with patch.object(programs, 'update_program', side_effect=sqlite3.OperationalError('database is locked')):
            await self.send(111, 'Kept after cancel')
        await self.send(111, '/cancel')
        self.assertEqual((await self.context(111).get_data())['unsaved_program_change']['name'], 'Kept after cancel')
        self.assertNotEqual(programs.get_program(self.uid, pid)['name'], 'Kept after cancel')
        await self.send(111, '/programs')
        await self.press('Посмотреть мой ввод')
        screen = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        self.assertIn('Kept after cancel', screen.text)

    async def test_forged_owned_routing_still_checks_program_owner(self):
        await self.send(111, '/programs')
        editor = await self.draft()
        other_pid = programs.list_programs(self.friend)[0]['id']
        editor['actions'] = [['open', other_pid]]
        await self.context(111).update_data(editor=editor)
        await self.click(111, f"pe:{editor['token']}:0")
        self.assertIsNone((await self.draft())['draft'])
        self.assertEqual(programs.get_program(self.friend, other_pid)['name'], 'Personal')

    async def test_rename_and_reorder_preserve_cycle_and_snapshot(self):
        session = sessions.start_day(self.uid)
        await self.send(111, '/programs')
        await self.press('A/B/C', contains=True)
        pid = (await self.draft())['program_id']
        next_id = programs.next_day(self.uid)['id']
        await self.press('Переименовать программу')
        await self.send(111, 'Новое имя')
        await self.press('1. A', contains=True)
        await self.press('Переименовать день')
        await self.send(111, 'Верх тела')
        await self.press('Назад')
        await self.press('Изменить порядок дней')
        await self.press('1. Верх тела', contains=True)
        await self.press('3')
        self.assertEqual(programs.next_day(self.uid)['id'], next_id)
        self.assertEqual(sessions.get_session(self.uid, session['workout_id']), session)
        self.assertEqual(programs.get_program(self.uid, pid)['days'][2]['name'], 'Верх тела')

    async def test_long_preview_keeps_all_text_and_return_navigation(self):
        days = [dict(name=f'Day {i}', exercises=[dict(exercise_id='bench-press', name=f'Item {i}-{j} ' + 'x' * 100)
                                                for j in range(10)]) for i in range(5)]
        pid = programs.create_program(self.uid, 'Long program', days)
        await self.send(111, '/programs')
        await self.press('Long program')
        await self.press('Просмотреть программу')
        parts = []
        while True:
            screen = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage))
            self.assertLessEqual(len(screen.text), 3900)
            parts.append(screen.text.rsplit('\n\nСтраница ', 1)[0])
            if not any(button.text == '→' for row in screen.reply_markup.inline_keyboard for button in row):
                break
            await self.press('→')
        from handlers.program_editor_views import day_summary
        expected = 'Long program\n\n' + '\n\n'.join(day_summary(day) for day in programs.get_program(self.uid, pid)['days'])
        self.assertEqual(''.join(parts), expected)
        await self.press('Назад')
        self.assertEqual((await self.draft())['program_id'], pid)

    async def test_large_reorder_uses_paged_position_grid_and_stable_ids(self):
        days = [dict(name='Large day', exercises=[dict(exercise_id='bench-press', name=f'Exercise {i}') for i in range(30)])]
        pid = programs.create_program(self.uid, 'Large program', days)
        original = programs.get_program(self.uid, pid)['days'][0]['exercises']
        await self.send(111, '/programs')
        await self.press('Large program')
        await self.press('1. Large day', contains=True)
        await self.press('Изменить порядок')
        await self.press('1. Exercise 0')
        screen = next(r for r in reversed(self.session.requests) if isinstance(r, SendMessage))
        self.assertTrue(all(len(row) <= 4 for row in screen.reply_markup.inline_keyboard))
        await self.press('→')
        await self.press('30')
        actual = programs.get_program(self.uid, pid)['days'][0]['exercises']
        self.assertEqual([item['id'] for item in actual], [item['id'] for item in original[1:] + original[:1]])


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

    def test_foreign_or_repeated_occurrence_ids_are_rejected_atomically(self):
        own = programs.create_program(self.user_id, 'Mine', document(2, 2))
        other = programs.create_program(self.other, 'Other', document(1))
        before = programs.get_program(self.user_id, own)
        edited = deepcopy(before['days'])
        edited[0]['exercises'][0]['id'] = programs.get_program(self.other, other)['days'][0]['exercises'][0]['id']
        with self.assertRaises(programs.ProgramAccessError):
            programs.update_program(self.user_id, own, days=edited, expected_revision=0)
        self.assertEqual(programs.get_program(self.user_id, own), before)
        edited = deepcopy(before['days'])
        edited[0]['exercises'][1]['id'] = edited[0]['exercises'][0]['id']
        with self.assertRaises(programs.ProgramAccessError):
            programs.update_program(self.user_id, own, days=edited, expected_revision=0)
        self.assertEqual(programs.get_program(self.user_id, own), before)

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
