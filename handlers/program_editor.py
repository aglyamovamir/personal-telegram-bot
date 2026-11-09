"""Autosaved owned program editor; transient search/new names live in the FSM."""
from copy import deepcopy
from pathlib import Path
import sqlite3

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import FSInputFile

from database import db, programs, catalog
from handlers import program_editor_views as views
from keyboards.program_editor import editor_keyboard
from states.program_editor import ProgramEditor
from workouts.plan_parser import parse_plan

router = Router()


async def render(message, state, editor, text, rows, *, prompt=None):
    markup, token, actions = editor_keyboard(rows)
    editor['token'], editor['actions'] = token, actions
    editor['prompt'] = prompt
    # Keep parked workout input/progress while editing a program.
    await state.update_data(editor=editor)
    await state.set_state(ProgramEditor.text if prompt else ProgramEditor.screen)
    if message.from_user and message.from_user.is_bot:
        try:
            await message.edit_text(text[:3900], reply_markup=markup, parse_mode=None)
            return
        except TelegramBadRequest:
            pass
    await message.answer(text[:3900], reply_markup=markup, parse_mode=None)


def clean_editor():
    return dict(draft=None, program_id=None, dirty=False, archived=False)


def loaded_editor(program):
    return dict(draft=deepcopy(program), program_id=program['id'], dirty=False,
                revision=program['revision'], original_days=deepcopy(program['days']))


def drop_empty_days(editor):
    if editor.get('draft'):
        editor['draft']['days'] = [day for day in editor['draft']['days'] if day['exercises']]


async def cleanup_media(message, editor):
    for message_id in editor.pop('media_messages', []):
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
        except (TelegramBadRequest, TelegramNetworkError):
            pass


async def show_list(message, state, user_id, editor=None, *, archived=False, page=0):
    previous = (await state.get_data()).get('editor', {})
    if previous.get('dirty') and previous.get('draft'):
        await state.update_data(unsaved_program_change=deepcopy(previous['draft']))
    await cleanup_media(message, previous)
    editor = clean_editor() if editor is None else editor
    editor['archived'] = archived
    text, rows = views.list_view(programs.list_programs(user_id, archived=archived), page, archived)
    pending = (await state.get_data()).get('unsaved_program_change')
    if pending:
        editor['rejected_change'] = pending
        rows.insert(-2, [views.button('Посмотреть мой ввод', 'rejected_preview', 0)])
    await render(message, state, editor, text, rows)


async def show_program(message, state, editor, page=0):
    drop_empty_days(editor)
    if not editor.get('draft'):
        raise ValueError('Сначала выберите программу.')
    text, rows = views.program_view(editor, page)
    await render(message, state, editor, text, rows)


async def show_day(message, state, editor, index, page=0):
    text, rows = views.day_view(editor, index, page)
    await render(message, state, editor, text, rows)


async def show_exercise(message, state, editor, day, index):
    text, rows = views.exercise_view(editor, day, index)
    await render(message, state, editor, text, rows)


async def show_optional_plan(message, state, editor, day, index):
    text, rows = views.optional_plan_view(editor, day, index)
    await render(message, state, editor, text, rows)


async def confirm(message, state, editor, text, action, *, back=None):
    back = back or ['program', 0]
    await render(message, state, editor, text,
                 [[views.button('Да', *action)]] + views.exits(*back))


async def guard(message, state, user_id):
    current = await state.get_state()
    if current and current.startswith('WorkoutState:'):
        from handlers.workout_navigation import park_workout_state
        await park_workout_state(state)
        return True
    if current and not current.startswith('ProgramEditor:'):
        await message.answer('Сначала закончите текущий ввод показателей.')
        return False
    return True


@router.message(F.text.in_({'Мои программы', '🗂 Мои программы'}))
@router.message(Command('programs'))
async def editor_start(message, state, user_id: int):
    if await guard(message, state, user_id):
        await show_list(message, state, user_id)


async def open_program_editor(message, state, user_id, program_id):
    if await guard(message, state, user_id):
        previous = (await state.get_data()).get('editor', {})
        if previous.get('dirty') and previous.get('draft'):
            await state.update_data(unsaved_program_change=deepcopy(previous['draft']))
        await show_program(message, state, loaded_editor(programs.get_program(user_id, program_id)))


async def open_active_editor(message, state, user_id):
    program = programs.get_active_program(user_id)
    if program:
        await open_program_editor(message, state, user_id, program['id'])
    else:
        await editor_start(message, state, user_id)


async def close_editor(message, state, user_id, editor):
    if editor.get('dirty') and editor.get('draft'):
        await state.update_data(unsaved_program_change=deepcopy(editor['draft']))
    await cleanup_media(message, editor)
    data = await state.get_data()
    data.pop('editor', None)
    await state.set_data(data)
    await state.set_state(None)
    from handlers.start import show_training
    await show_training(message, state, user_id)


async def ask(message, state, editor, field, *indices):
    prompts = {
        'program_new': 'Введите название новой программы (до 120 символов).',
        'program_name': 'Введите новое название программы.',
        'day_new': 'Введите название первого тренировочного дня.' if not editor.get('program_id') else 'Введите название нового тренировочного дня.',
        'day_name': 'Введите новое название дня.',
        'exercise_name': 'Введите своё название упражнения.',
        'search': 'Введите название упражнения на русском или английском (2–100 символов).',
        'count': 'Сколько рабочих подходов? Введите целое число от 1 до 100.',
        'working': 'Введите рабочие подходы: вес повторения [количество], по строке на вариант.\nПример: 50 8 3\nЗнак - вместо веса или повторений оставляет их незаданными.',
        'warmup': 'Введите разминочные подходы: вес повторения [количество].\nПример: 20 10\nОтдельный знак - убирает разминку.',
    }
    if field in ('count', 'working', 'warmup'):
        back = ['optional_plan', *indices]
    elif field == 'exercise_name':
        back = ['exercise', *indices]
    elif field in ('search', 'day_name'):
        back = ['day', indices[0], 0]
    elif field == 'program_new' or (field == 'day_new' and not editor.get('program_id')):
        back = ['list', False, 0]
    else:
        back = ['program', 0]
    if field == 'day_new':
        drop_empty_days(editor)
    await render(message, state, editor, prompts[field], views.exits(*back), prompt=[field, *indices])


async def search_results(message, state, editor, day, query, page=0):
    result = catalog.search_exercises(query, page)
    editor['search'] = dict(day=day, query=query, page=result['page'])
    editor['search_ids'] = [item['exercise_id'] for item in result['items']]
    rows = [[views.button(item['name'], 'catalog_card', item['exercise_id'])] for item in result['items']]
    views.nav(rows, result['page'], result['total'], 'searchpage', day, query)
    rows += views.exits('day', day, 0)
    if result['items']:
        text = f"Поиск: {query}\nНайдено: {result['total']}. Страница {result['page'] + 1}.\nВыберите упражнение или введите другое название."
        text += '\n\n' + '\n'.join(f"{i + 1 + result['page'] * 8}. {item['name']}" for i, item in enumerate(result['items']))
    else:
        text = f'По запросу «{query}» ничего не найдено.\nВведите другое название'
    await render(message, state, editor, text, rows, prompt=['search', day])


async def show_catalog_card(message, state, editor, external, *, show_image=False):
    if external not in editor.get('search_ids', []):
        raise ValueError('Выбор устарел. Повторите поиск.')
    await cleanup_media(message, editor)
    item = catalog.get_exercise(external)
    search = editor['search']
    text = views.catalog_card(item)
    rows = [[views.button('Добавить в день', 'pick', search['day'], external)],
            [views.button('Назад к результатам', 'search_back')], [views.button('Закрыть редактор', 'close')]]
    images = item.get('images', [])
    if not images and item.get('image'):
        images = [dict(source=item['image'], caption='Иллюстрация упражнения')]
    editor['media_messages'] = []
    for image in images[:2]:
        try:
            source = image['source']
            sent = await message.answer_photo(FSInputFile(source) if isinstance(source, Path) else source,
                                              caption=image.get('caption'), parse_mode=None)
            if getattr(sent, 'message_id', None):
                editor['media_messages'].append(sent.message_id)
        except (TelegramBadRequest, TelegramNetworkError, OSError):
            text += '\nИзображение сейчас недоступно. Упражнение можно добавить без него.'
    await render(message, state, editor, text, rows)


def append_exercise(editor, day, external_id, name):
    editor['draft']['days'][day]['exercises'].append(dict(
        exercise_id=external_id, name=name, working_sets=None, warmup=[], working_plan=[]))


async def persist(message, state, user_id, editor, after):
    """Commit the user's change before displaying its successful result."""
    editor['dirty'] = True
    editor['after_save'] = after
    try:
        if editor['program_id'] is None:
            pid = programs.create_program(user_id, editor['draft']['name'], editor['draft']['days'], activate_first=True)
            editor['program_id'] = pid
            editor['revision'] = 0
        else:
            pid = editor['program_id']
            days = editor['draft']['days'] if editor['draft']['days'] != editor['original_days'] else None
            programs.update_program(user_id, pid, name=editor['draft']['name'], days=days, expected_revision=editor['revision'])
        updated = loaded_editor(programs.get_program(user_id, pid))
    except (sqlite3.Error, ValueError) as error:
        editor['last_save_error'] = str(error)
        await show_save_error(message, state, editor)
        return False
    await dispatch_action(message, state, user_id, updated, after[0], after[1:])
    return True


async def show_save_error(message, state, editor):
    text = 'Изменения пока не сохранены. ' + editor.get('last_save_error', '') + '\nВаш ввод сохранён на этом экране. Можно повторить попытку или открыть актуальную версию.'
    rows = [[views.button('Повторить', 'retry_save')]]
    if editor.get('program_id'):
        rows.append([views.button('Открыть актуальную версию', 'saved_version')])
    rows += [[views.button('Посмотреть мой ввод', 'pending_preview', 0)], [views.button('Закрыть редактор', 'close')]]
    rows.insert(-1, [views.button('Назад', 'saved_version')] if editor.get('program_id') else [views.button('Назад', 'list', False, 0)])
    await render(message, state, editor, text, rows)


async def preview(message, state, editor, document, page=0, *, action='preview'):
    text = document['name'] + '\n\n' + '\n\n'.join(views.day_summary(day) for day in document['days'])
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)] or ['Программа пока пуста.']
    page = max(0, min(page, len(chunks) - 1))
    rows = []
    nav = []
    if page:
        nav.append(views.button('←', action, page - 1))
    if page + 1 < len(chunks):
        nav.append(views.button('→', action, page + 1))
    if nav:
        rows.append(nav)
    back = ['save_error'] if action == 'pending_preview' else (['program', 0] if editor.get('draft') else ['list', False, 0])
    rows += views.exits(*back)
    await render(message, state, editor, chunks[page] + f'\n\nСтраница {page + 1} из {len(chunks)}.', rows)


@router.callback_query(F.data.startswith('pe:'))
async def editor_callback(callback, state, user_id: int):
    # Old editor buttons must not park or otherwise change live workout input.
    if not (await state.get_state() or '').startswith('ProgramEditor:'):
        await callback.answer('Кнопка устарела. Откройте раздел «Тренировка» заново.', show_alert=True)
        return
    editor = (await state.get_data()).get('editor')
    try:
        _, token, index = callback.data.split(':')
        index = int(index)
        if not editor or token != editor.get('token') or index < 0 or index >= len(editor['actions']):
            raise ValueError
        action, *args = editor['actions'][index]
    except (ValueError, KeyError):
        await callback.answer('Кнопка устарела. Откройте раздел «Тренировка» заново.', show_alert=True)
        return
    editor = deepcopy(editor)
    editor['token'] = None
    await state.update_data(editor=editor)
    try:
        if action not in ('catalog_card', 'catalog_image'):
            await cleanup_media(callback.message, editor)
        await dispatch_action(callback.message, state, user_id, editor, action, args)
    except programs.ProgramAccessError as error:
        await callback.message.answer(str(error))
        await show_list(callback.message, state, user_id)
    except sqlite3.Error:
        await callback.message.answer('Не удалось изменить данные. Откройте программу и повторите действие.')
        await show_list(callback.message, state, user_id)
    except (ValueError, KeyError, IndexError) as error:
        await callback.message.answer(str(error) if isinstance(error, ValueError) else 'Экран устарел. Откройте программу заново.')
        if editor.get('draft'):
            await show_program(callback.message, state, editor)
        else:
            await show_list(callback.message, state, user_id)
    await callback.answer()


async def dispatch_action(message, state, user_id, editor, action, args):
    if action == 'list' or action in ('leave', 'discard'):
        await show_list(message, state, user_id, archived=args[0] if action == 'list' else False,
                        page=args[1] if action == 'list' else 0)
    elif action == 'close':
        await close_editor(message, state, user_id, editor)
    elif action == 'new':
        await ask(message, state, clean_editor(), 'program_new')
    elif action == 'open':
        await show_program(message, state, loaded_editor(programs.get_program(user_id, args[0])))
    elif action == 'program':
        await show_program(message, state, editor, *args)
    elif action == 'preview':
        await preview(message, state, editor, editor['draft'], args[0] if args else 0)
    elif action in ('pending_preview', 'rejected_preview'):
        document = editor['draft'] if action == 'pending_preview' else editor['rejected_change']
        await preview(message, state, editor, document, args[0] if args else 0, action=action)
    elif action == 'retry_save':
        await persist(message, state, user_id, editor, editor['after_save'])
    elif action == 'save_error':
        await show_save_error(message, state, editor)
    elif action == 'saved_version':
        pending = deepcopy(editor['draft'])
        updated = loaded_editor(programs.get_program(user_id, editor['program_id']))
        updated['rejected_change'] = pending
        await state.update_data(unsaved_program_change=pending)
        await show_program(message, state, updated)
    elif action == 'day':
        await show_day(message, state, editor, *args)
    elif action == 'exercise':
        await show_exercise(message, state, editor, *args)
    elif action == 'optional_plan':
        await show_optional_plan(message, state, editor, *args)
    elif action == 'ask':
        await ask(message, state, editor, *args)
    elif action == 'searchpage':
        await search_results(message, state, editor, *args)
    elif action == 'search_back':
        search = editor['search']
        await search_results(message, state, editor, search['day'], search['query'], search['page'])
    elif action in ('catalog_card', 'catalog_image'):
        await show_catalog_card(message, state, editor, args[0])
    elif action in ('pick', 'duplicate'):
        day, external = args[:2]
        if external not in editor.get('search_ids', []):
            raise ValueError('Выбор устарел. Повторите поиск.')
        name = catalog.get_exercise(external)['name']
        if action == 'pick' and any(e['exercise_id'] == external for e in editor['draft']['days'][day]['exercises']):
            await confirm(message, state, editor, 'Это упражнение уже есть в этом дне. Добавить ещё раз?', ['duplicate', day, external], back=['search_back'])
        else:
            append_exercise(editor, day, external, name)
            await persist(message, state, user_id, editor, ['day', day, (len(editor['draft']['days'][day]['exercises']) - 1) // 8])
    elif action == 'activate':
        programs.activate_program(user_id, editor['program_id'], expected_revision=editor['revision'])
        await dispatch_action(message, state, user_id, editor, 'open', [editor['program_id']])
    elif action == 'archive':
        warning = 'Это последняя доступная программа. ' if len(programs.list_programs(user_id)) <= 1 else ''
        await confirm(message, state, editor, warning + 'Отправить программу в архив? История тренировок сохранится.', ['archive_yes'])
    elif action == 'archive_yes':
        programs.archive_program(user_id, editor['program_id'], expected_revision=editor['revision'])
        await show_list(message, state, user_id)
    elif action == 'restore':
        programs.restore_program(user_id, editor['program_id'], expected_revision=editor['revision'])
        await dispatch_action(message, state, user_id, editor, 'open', [editor['program_id']])
    elif action == 'delete_program':
        await confirm(message, state, editor, 'Удалить программу целиком? История тренировок и подходы сохранятся. Текущая тренировка продолжит использовать свой снимок.', ['delete_program_yes'])
    elif action == 'delete_program_yes':
        programs.delete_program(user_id, editor['program_id'], expected_revision=editor['revision'])
        await show_list(message, state, user_id)
    elif action == 'remove_day':
        day = args[0]
        if not editor['draft']['days'][day]['exercises']:
            editor['draft']['days'].pop(day)
            await show_program(message, state, editor)
        elif len(editor['draft']['days']) == 1:
            await confirm(message, state, editor, 'Это последний день. Удалить программу целиком? История тренировок сохранится.', ['delete_program_yes'], back=['day', day, 0])
        else:
            await confirm(message, state, editor, 'Удалить день из программы? История тренировок сохранится.', ['remove_day_yes', day], back=['day', day, 0])
    elif action == 'remove_day_yes':
        editor['draft']['days'].pop(args[0])
        await persist(message, state, user_id, editor, ['program', 0])
    elif action == 'remove_exercise':
        day, index = args
        if len(editor['draft']['days'][day]['exercises']) == 1:
            await confirm(message, state, editor, 'Это последнее упражнение. Удалить его вместе с днём?', ['remove_last_exercise_yes', day], back=['exercise', day, index])
        else:
            await confirm(message, state, editor, 'Удалить упражнение из дня? История тренировок сохранится.', ['remove_exercise_yes', day, index], back=['exercise', day, index])
    elif action == 'remove_last_exercise_yes':
        if len(editor['draft']['days']) == 1:
            await confirm(message, state, editor, 'Это последний день. Удалить программу целиком? История тренировок сохранится.', ['delete_program_yes'], back=['day', args[0], 0])
        else:
            editor['draft']['days'].pop(args[0])
            await persist(message, state, user_id, editor, ['program', 0])
    elif action == 'remove_exercise_yes':
        day, index = args
        editor['draft']['days'][day]['exercises'].pop(index)
        await persist(message, state, user_id, editor, ['day', day, 0])
    elif action == 'reorder_days':
        text, rows = views.reorder_view(editor, 'day', page=args[0])
        await render(message, state, editor, text, rows)
    elif action == 'reorder_exercises':
        text, rows = views.reorder_view(editor, 'exercise', day=args[0], page=args[1])
        await render(message, state, editor, text, rows)
    elif action == 'position_day':
        text, rows = views.position_view(editor, 'day', args[0], page=args[1])
        await render(message, state, editor, text, rows)
    elif action == 'position_exercise':
        text, rows = views.position_view(editor, 'exercise', args[1], day=args[0], page=args[2])
        await render(message, state, editor, text, rows)
    elif action in ('place_day', 'place_exercise'):
        if action == 'place_day':
            source, target = args
            items = editor['draft']['days']
            after = ['program', target // 8]
        else:
            day, source, target = args
            items = editor['draft']['days'][day]['exercises']
            after = ['day', day, target // 8]
        if not (0 <= source < len(items) and 0 <= target < len(items)):
            raise ValueError('Позиция устарела. Откройте список заново.')
        items.insert(target, items.pop(source))
        await persist(message, state, user_id, editor, after)
    else:
        raise ValueError('Кнопка устарела. Откройте программу заново.')


@router.message(ProgramEditor.text)
async def editor_text(message, state, user_id: int):
    if not await guard(message, state, user_id):
        return
    editor = deepcopy((await state.get_data()).get('editor'))
    if not editor or not editor.get('prompt'):
        await message.answer('Откройте раздел «Тренировка» заново.')
        return
    field, *indices = editor['prompt']
    value = (message.text or '').strip()
    try:
        if field == 'search':
            await search_results(message, state, editor, indices[0], value)
            return
        if field == 'program_new':
            editor = dict(draft=dict(name=programs._name(value), days=[]), program_id=None,
                          dirty=False, revision=None, original_days=[])
            await ask(message, state, editor, 'day_new')
            return
        if field == 'day_new':
            editor['draft']['days'].append(dict(name=programs._name(value), exercises=[]))
            await ask(message, state, editor, 'search', len(editor['draft']['days']) - 1)
            return
        if field == 'program_name':
            editor['draft']['name'] = programs._name(value)
            after = ['program', 0]
        elif field == 'day_name':
            editor['draft']['days'][indices[0]]['name'] = programs._name(value)
            after = ['day', indices[0], 0]
        else:
            exercise = editor['draft']['days'][indices[0]]['exercises'][indices[1]]
            if field == 'exercise_name':
                exercise['name'] = programs._name(value)
            elif field == 'count':
                try:
                    count = int(value)
                except ValueError as error:
                    raise ValueError('Введите целое число от 1 до 100') from error
                if not 1 <= count <= 100:
                    raise ValueError('Введите целое число от 1 до 100')
                exercise['working_plan'] = (exercise['working_plan'] + [dict(weight=None, reps=None) for _ in range(count)])[:count]
                exercise['working_sets'] = count
            elif field in ('working', 'warmup'):
                plan = parse_plan(value, allow_empty=field == 'warmup')
                exercise['warmup' if field == 'warmup' else 'working_plan'] = plan
                if field == 'working':
                    exercise['working_sets'] = len(plan)
            after = ['exercise', *indices] if field == 'exercise_name' else ['optional_plan', *indices]
        if any(not day['exercises'] for day in editor['draft']['days']):
            await dispatch_action(message, state, user_id, editor, after[0], after[1:])
        else:
            await persist(message, state, user_id, editor, after)
    except ValueError as error:
        await message.answer(str(error))


@router.message(ProgramEditor.screen)
async def editor_screen_text(message):
    await message.answer('Выберите действие кнопкой. «Назад» возвращает на предыдущий экран, «Закрыть редактор» открывает раздел «Тренировка».')
