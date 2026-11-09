"""Transactional draft editor. SQL lives in database/programs.py and catalog.py."""
from copy import deepcopy

from aiogram import F, Router
from aiogram.filters import Command

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
    await state.set_data({'editor': editor})
    await state.set_state(ProgramEditor.text if prompt else ProgramEditor.screen)
    # Bound text without splitting HTML: editor screens use plain text.
    await message.answer(text[:3900], reply_markup=markup)


def clean_editor():
    return dict(draft=None, program_id=None, dirty=False, archived=False)


async def show_list(message, state, user_id, editor=None, *, archived=False, page=0):
    editor = clean_editor() if editor is None else editor
    editor['archived'] = archived
    text, rows = views.list_view(programs.list_programs(user_id, archived=archived), page, archived)
    await render(message, state, editor, text, rows)


async def show_program(message, state, editor, page=0):
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


async def confirm(message, state, editor, text, action):
    await render(message, state, editor, text,
                 [[views.button('Подтвердить', *action)], [views.button('Назад', 'program', 0), views.button('Отмена', 'program', 0)]])


async def guard(message, state, user_id):
    current = await state.get_state()
    active = db.get_active_workout(user_id)
    legacy = active and db.get_workout_revision(user_id, active[0]) is None
    if legacy and current and current.startswith('WorkoutState:'):
        await state.clear()
        current = None
    if (active and not legacy) or (current and not current.startswith('ProgramEditor:')):
        await message.answer('Сначала закончите текущий ввод или тренировку. Для тренировки: /resume, /exit или /cancel.')
        return False
    return True


@router.message(F.text.in_({'Мои программы', '🗂 Мои программы'}))
@router.message(Command('programs'))
async def editor_start(message, state, user_id: int):
    if not await guard(message, state, user_id):
        return
    existing = (await state.get_data()).get('editor')
    if existing and existing.get('dirty'):
        await show_program(message, state, existing)
        return
    await show_list(message, state, user_id)


async def ask(message, state, editor, field, *indices):
    prompts = {
        'program_new': 'Введите название новой программы (до 120 символов).',
        'program_name': 'Введите новое название программы.',
        'day_new': 'Введите название нового тренировочного дня.',
        'day_name': 'Введите новое название дня.',
        'exercise_name': 'Введите своё название упражнения.',
        'search': 'Введите часть русского или английского названия упражнения (2–100 символов).',
        'count': 'Сколько рабочих подходов? Введите целое число от 1 до 100.',
        'working': 'Введите рабочие подходы: вес повторения [количество], по строке на вариант.\nПример: 50 8 3\nЗнак - вместо веса или повторений оставляет их незаданными.',
        'warmup': 'Введите разминочные подходы: вес повторения [количество].\nПример: 20 10\nОтдельный знак - убирает разминку.',
    }
    back = ['exercise', *indices] if field in ('count', 'working', 'warmup', 'exercise_name') else (['day', indices[0], 0] if field in ('search', 'day_name') else ['program', 0])
    if field == 'program_new':
        back = ['list', False, 0]
    await render(message, state, editor, prompts[field],
                 [[views.button('Назад', *back), views.button('Отмена', *back)]], prompt=[field, *indices])


async def search_results(message, state, editor, day, query, page=0):
    result = catalog.search_exercises(query, page)
    rows = [[views.button(item['name'], 'pick', day, item['exercise_id'], item['name'])] for item in result['items']]
    views.nav(rows, result['page'], result['total'], 'searchpage', day, query)
    rows += [[views.button('Другой запрос', 'ask', 'search', day)], [views.button('Назад', 'day', day, 0)]]
    await render(message, state, editor, f"Поиск: {query}\nНайдено: {result['total']}. Страница {result['page'] + 1}.", rows)


def append_exercise(editor, day, external_id, name):
    editor['draft']['days'][day]['exercises'].append(dict(
        exercise_id=external_id, name=name, working_sets=None, warmup=[], working_plan=[]))
    editor['dirty'] = True


@router.callback_query(F.data.startswith('pe:'))
async def editor_callback(callback, state, user_id: int):
    if not await guard(callback.message, state, user_id):
        await callback.answer()
        return
    editor = (await state.get_data()).get('editor')
    try:
        _, token, index = callback.data.split(':')
        index = int(index)
        if not editor or token != editor.get('token') or index < 0 or index >= len(editor['actions']):
            raise ValueError
        action, *args = editor['actions'][index]
    except (ValueError, KeyError):
        await callback.answer('Кнопка устарела. Откройте «Мои программы» заново.', show_alert=True)
        return
    # Consume once before performing a write, even if sending the next screen fails.
    editor['token'] = None
    await state.update_data(editor=editor)
    try:
        await dispatch_action(callback.message, state, user_id, editor, action, args)
    except programs.ProgramAccessError as error:
        await callback.message.answer(str(error))
        await show_list(callback.message, state, user_id)
    except (ValueError, KeyError, IndexError) as error:
        await callback.message.answer(str(error) if isinstance(error, ValueError) else 'Экран устарел. Откройте программу заново.')
        if editor.get('draft'):
            await show_program(callback.message, state, editor)
        else:
            await show_list(callback.message, state, user_id)
    await callback.answer()


async def dispatch_action(message, state, user_id, editor, action, args):
    if action == 'list':
        await show_list(message, state, user_id, archived=args[0], page=args[1])
    elif action == 'close':
        await state.clear()
        await message.answer('Редактор закрыт.')
    elif action == 'new':
        await ask(message, state, clean_editor(), 'program_new')
    elif action == 'open':
        program = programs.get_program(user_id, args[0])
        editor = dict(draft=deepcopy(program), program_id=program['id'], dirty=False,
                      revision=program['revision'], original_days=deepcopy(program['days']))
        await show_program(message, state, editor)
    elif action == 'leave':
        if editor['dirty']:
            await confirm(message, state, editor, 'Отменить все несохранённые изменения?', ['discard'])
        else:
            await show_list(message, state, user_id)
    elif action == 'discard':
        await show_list(message, state, user_id)
    elif action == 'program':
        await show_program(message, state, editor, *args)
    elif action == 'preview':
        text = editor['draft']['name'] + '\n\n' + '\n\n'.join(views.day_summary(d) for d in editor['draft']['days'])
        for offset in range(0, max(0, len(text) - 3900), 3900):
            await message.answer(text[offset:offset + 3900])
        last = ((len(text) - 1) // 3900) * 3900
        await render(message, state, editor, text[last:], [[views.button('Назад', 'program', 0)]])
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
    elif action in ('pick', 'duplicate'):
        day, external, name = args
        if action == 'pick' and any(e['exercise_id'] == external for e in editor['draft']['days'][day]['exercises']):
            await confirm(message, state, editor, 'Это упражнение уже есть в дне. Добавить ещё одно отдельное включение?', ['duplicate', *args])
        else:
            append_exercise(editor, day, external, name)
            # Adding is complete: remain at the day, ready to add the next item.
            await show_day(message, state, editor, day,
                           (len(editor['draft']['days'][day]['exercises']) - 1) // 8)
    elif action == 'save':
        if editor['program_id'] is None:
            pid = programs.create_program(user_id, editor['draft']['name'], editor['draft']['days'],
                                           activate=programs.get_active_program(user_id) is None)
        else:
            pid = editor['program_id']
            days = editor['draft']['days'] if editor['draft']['days'] != editor['original_days'] else None
            programs.update_program(user_id, pid, name=editor['draft']['name'], days=days, expected_revision=editor['revision'])
        await dispatch_action(message, state, user_id, editor, 'open', [pid])
    elif action == 'activate':
        if not editor['program_id'] or editor['dirty']:
            raise ValueError('Сначала сохраните изменения программы.')
        programs.activate_program(user_id, editor['program_id'])
        await dispatch_action(message, state, user_id, editor, 'open', [editor['program_id']])
    elif action == 'archive':
        if editor['dirty']:
            raise ValueError('Сначала сохраните изменения или отмените черновик.')
        warning = 'Это последняя доступная программа. После архивирования начать новую тренировку будет нельзя, пока вы её не восстановите. ' if len(programs.list_programs(user_id)) <= 1 else ''
        await confirm(message, state, editor, warning + 'Архивировать программу? История тренировок сохранится.', ['archive_yes'])
    elif action == 'archive_yes':
        programs.archive_program(user_id, editor['program_id'], expected_revision=editor['revision'])
        await show_list(message, state, user_id)
    elif action == 'restore':
        programs.restore_program(user_id, editor['program_id'], expected_revision=editor['revision'])
        await dispatch_action(message, state, user_id, editor, 'open', [editor['program_id']])
    elif action in ('remove_day', 'remove_exercise'):
        await confirm(message, state, editor, 'Удалить из черновика? Применится только после «Сохранить».', [action + '_yes', *args])
    elif action == 'remove_day_yes':
        editor['draft']['days'].pop(args[0])
        editor['dirty'] = True
        await show_program(message, state, editor)
    elif action == 'remove_exercise_yes':
        editor['draft']['days'][args[0]]['exercises'].pop(args[1])
        editor['dirty'] = True
        await show_day(message, state, editor, args[0])
    elif action in ('move_day', 'move_exercise'):
        if action == 'move_day':
            items, index, offset = editor['draft']['days'], args[0], args[1]
        else:
            items, index, offset = editor['draft']['days'][args[0]]['exercises'], args[1], args[2]
        if 0 <= index + offset < len(items):
            items[index], items[index + offset] = items[index + offset], items[index]
            editor['dirty'] = True
            index += offset
        if action == 'move_day':
            await show_day(message, state, editor, index)
        else:
            await show_exercise(message, state, editor, args[0], index)


@router.message(ProgramEditor.text)
async def editor_text(message, state, user_id: int):
    if not await guard(message, state, user_id):
        return
    editor = (await state.get_data()).get('editor')
    if not editor or not editor.get('prompt'):
        await message.answer('Откройте «Мои программы» заново.')
        return
    field, *indices = editor['prompt']
    value = (message.text or '').strip()
    try:
        if field == 'search':
            await search_results(message, state, editor, indices[0], value)
            return
        if field == 'program_new':
            editor = dict(draft=dict(name=programs._name(value), days=[]), program_id=None,
                          dirty=True, revision=None, original_days=[])
        elif field == 'program_name':
            editor['draft']['name'] = programs._name(value)
        elif field == 'day_new':
            editor['draft']['days'].append(dict(name=programs._name(value), exercises=[]))
        elif field == 'day_name':
            editor['draft']['days'][indices[0]]['name'] = programs._name(value)
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
        editor['dirty'] = True
    except ValueError as error:
        await message.answer(str(error))
        return
    if field == 'day_new':
        await show_day(message, state, editor, len(editor['draft']['days']) - 1)
    elif field == 'day_name':
        await show_day(message, state, editor, indices[0])
    elif field in ('count', 'working', 'warmup', 'exercise_name'):
        await show_exercise(message, state, editor, *indices)
    else:
        await show_program(message, state, editor)


@router.message(ProgramEditor.screen)
async def editor_screen_text(message):
    await message.answer('Выберите действие кнопкой. Для выхода без сохранения используйте «Отмена» или /cancel.')
