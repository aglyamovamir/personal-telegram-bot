from html import escape
from uuid import uuid4
import sqlite3

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import CallbackQuery, Message

from database import db, programs, sessions
from keyboards.main_menu import main_menu
from keyboards.workout_menu import workout_menu, workout_controls, correction_controls, cancel_workout_controls
from states.workout import WorkoutState
from workouts.parser import parse_exercise_input, format_parsed_sets
from handlers.daily import morning_start, evening_start
from handlers.today import today_handler
from google_sheets import safe_sync_workout
from scheduler import reschedule_notifications
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.program_editor_views import day_summary
from handlers import unfinished_workout_views as unfinished_views
from handlers.workout_navigation import park_workout_state, clear_workout_state

router = Router()

@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "<b>Формат ввода подходов</b>\n\n"
        "Каждая строка — вес и количество повторений.\n\n"
        "<code>20 10\n"
        "50 10\n"
        "\n"
        "60 10 3</code>\n\n"
        "Первые две строки — разминочные подходы. "
        "Пустая строка отделяет разминку от рабочих подходов.\n\n"
        "<code>60 10 3</code> — 3 рабочих подхода "
        "по 60 кг на 10 повторений.\n\n"
        "Если не оставить пустую строку, все подходы "
        "будут записаны как рабочие.",
        parse_mode="HTML"
    )

def format_exercise_sets(sets):
    groups = []

    for is_warmup, weight, reps in sets:
        if groups and tuple(groups[-1][:3]) == (is_warmup, weight, reps):
            groups[-1][3] += 1
        else:
            groups.append([is_warmup, weight, reps, 1])

    text = ""

    for is_warmup, weight, reps, count in groups:
        label = "Разминка" if is_warmup else "Рабочий"
        count_text = f" × {count}" if count > 1 else ""
        text += f"{label}: {weight:g} кг × {reps}{count_text}\n"

    return text


async def show_session(message, state, user_id, session):
    progress = session['progress']
    index = progress['current']
    exercise = session['snapshot']['exercises'][index]
    data = await state.get_data()
    data.update(workout_id=session['workout_id'], exercise_index=index,
                revision=session['revision'])
    data.pop('unfinished', None)
    data.pop('parked_workout', None)
    await state.set_data(data)
    await state.set_state(WorkoutState.waiting_for_exercise_input)
    previous = db.get_last_exercise_sets(user_id, exercise['exercise_id'])
    previous_text = '\nПрошлые результаты:\n' + '\n'.join(f'{w:g} кг × {r}' for w, r in previous) if previous else ''
    await message.answer(
        f"Тренировка: {session['snapshot']['day_name']}\n"
        f"Упражнение {index + 1}: {exercise['name']}\n"
        f"Выполнено: {len(progress['completed'])} из {len(session['snapshot']['exercises'])}. Отложено: {len(progress['postponed'])}.\n"
        f"Отправьте ВСЕ выполненные подходы этого упражнения ОДНИМ сообщением. Затем сразу перейдём дальше.\n"
        f"Формат: вес повторения [количество], например 50 8 3.\n"
        f"Разминку отделите от рабочих пустой строкой; без неё все подходы рабочие. /help — примеры.{previous_text}",
        reply_markup=workout_controls(session['workout_id'], index, session['revision'], can_correct=bool(progress['completed'])),
    )


async def show_training(message, state, user_id, *, show_unfinished=True):
    current = await state.get_state()
    if current and current.startswith('ProgramEditor:'):
        await state.set_state(None)
        current = None
    workout_states = {WorkoutState.waiting_for_exercise_input.state, WorkoutState.correcting_previous.state, WorkoutState.confirming_cancel.state}
    if current and current not in workout_states:
        await message.answer('Сначала завершите текущий опрос или отмените его через /cancel.')
        return
    active = db.get_active_workout(user_id)
    if active and show_unfinished:
        await show_unfinished_workout(message, state, user_id, active[0])
        return
    if active:
        await park_workout_state(state)
    elif await state.get_state() in workout_states:
        data = await state.get_data()
        await clear_workout_state(state, data.get('workout_id'))
    data = await state.get_data()
    data.pop('unfinished', None)
    await state.set_data(data)
    choices = programs.list_programs(user_id)
    program = programs.get_active_program(user_id)
    if not choices:
        text = 'Создайте программу: добавьте тренировочные дни и упражнения.'
    elif program:
        text = f"Программа: {program['name']}\nВыберите тренировочный день."
    else:
        text = 'Выберите активную программу в списке программ или создайте новую.'
    if active:
        text += '\nНезавершённая тренировка сохранена. К ней можно вернуться через /resume.'
    await message.answer(text, reply_markup=workout_menu(choices, program))


async def show_unfinished_workout(message, state, user_id, workout_id, *, page=None):
    details = sessions.get_unfinished_workout(user_id, workout_id)
    await park_workout_state(state)
    token = uuid4().hex[:12]
    await state.update_data(unfinished=dict(workout_id=workout_id, token=token,
                                            fingerprint=details['fingerprint']))
    button = lambda title, action, value='': InlineKeyboardButton(
        text=title, callback_data=f'uf:{token}:{action}:{value}')
    if page is None:
        text, omitted = unfinished_views.compact_card(details)
        rows = [[button('Посмотреть все подходы', 'sets', 0)]] if omitted or details['set_count'] > 12 else []
    else:
        pages = unfinished_views.all_sets_pages(details)
        page = max(0, min(page, len(pages) - 1))
        text = pages[page] + f'\n\nСтраница {page + 1} из {len(pages)}'
        navigation = []
        if page:
            navigation.append(button('← Подходы', 'sets', page - 1))
        if page + 1 < len(pages):
            navigation.append(button('Подходы →', 'sets', page + 1))
        rows = [navigation] if navigation else []
        rows.append([button('К карточке', 'card')])
    if details['can_resume']:
        rows.append([button('Продолжить тренировку', 'resume')])
    rows += [[button('Завершить с сохранением', 'finish')],
             [button('Удалить тренировку', 'delete')], [button('Назад', 'back')]]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def resume_training(message, state, user_id, *, workout_id=None):
    current = await state.get_state()
    if current and not current.startswith(('WorkoutState:', 'ProgramEditor:')):
        await message.answer('Сначала завершите текущий опрос или отмените его через /cancel.')
        return
    if current == WorkoutState.correcting_previous.state:
        await park_workout_state(state)
    active = db.get_active_workout(user_id)
    if workout_id is not None and (not active or active[0] != workout_id):
        await show_current_unfinished(message, state, user_id, workout_id)
        return
    if not active:
        await show_training(message, state, user_id)
        return
    details = sessions.get_unfinished_workout(user_id, active[0])
    if not details['can_resume']:
        await show_unfinished_workout(message, state, user_id, active[0])
        return
    session = sessions.get_resume_session(user_id, active[0])
    data = await state.get_data()
    parked = data.get('parked_workout', {})
    saved = parked.get('data', {})
    changed_step = saved.get('workout_id') == active[0] and saved.get('revision') != session['revision']
    if saved.get('workout_id') == active[0] and saved.get('revision') == session['revision']:
        data.update(saved)
        data.pop('parked_workout', None)
        data.pop('unfinished', None)
        await state.set_data(data)
        if parked.get('state') == WorkoutState.correcting_previous.state:
            token = uuid4().hex[:12]
            await state.update_data(correction_token=token)
            await state.set_state(WorkoutState.correcting_previous)
            text = 'Возвращаемся к исправлению предыдущего упражнения. Сохранённые подходы не изменены.'
            if saved.get('correction_input'):
                text += '\nВаш предыдущий ввод:\n' + saved['correction_input']
            for offset in range(0, max(0, len(text) - 3900), 3900):
                await message.answer(text[offset:offset + 3900])
            await message.answer(text[((len(text) - 1) // 3900) * 3900:], reply_markup=correction_controls(token))
            return
    await show_session(message, state, user_id, session)
    if changed_step:
        correcting = parked.get('state') == WorkoutState.correcting_previous.state
        prior_input = saved.get('correction_input' if correcting else 'exercise_input')
        if prior_input:
            prior_position = saved.get('correction_position' if correcting else 'exercise_index')
            label = f' упражнения №{prior_position + 1}' if type(prior_position) is int else ' прежнего шага'
            warning = ('Тренировка изменилась. Предыдущий ввод' + label
                       + ' сохранён и не применён к текущему упражнению:\n' + prior_input)
            for offset in range(0, len(warning), 3900):
                await message.answer(warning[offset:offset + 3900])
        return
    cached_input = (await state.get_data()).get('exercise_input')
    if cached_input:
        text = 'Ваш предыдущий ввод ещё не записан в подходы:\n' + cached_input
        for offset in range(0, len(text), 3900):
            await message.answer(text[offset:offset + 3900])


@router.callback_query(F.data.startswith('uf:'))
async def unfinished_workout_action(callback, state, user_id: int):
    data = await state.get_data()
    card = data.get('unfinished', {})
    try:
        _, token, action, value = callback.data.split(':')
        if token != card.get('token'):
            raise ValueError
        workout_id = card['workout_id']
    except (ValueError, KeyError):
        await callback.answer('Карточка устарела. Откройте раздел «Тренировка» заново.', show_alert=True)
        return
    await state.update_data(unfinished={**card, 'token': None})
    try:
        if action == 'sets':
            await show_unfinished_workout(callback.message, state, user_id, workout_id, page=int(value))
        elif action == 'card':
            await show_unfinished_workout(callback.message, state, user_id, workout_id)
        elif action == 'back':
            await show_training(callback.message, state, user_id, show_unfinished=False)
        elif action == 'resume':
            await resume_training(callback.message, state, user_id, workout_id=workout_id)
        elif action == 'delete':
            await ask_delete_workout(callback.message, state, user_id, workout_id, origin='card')
        elif action == 'finish':
            sessions.finish_unfinished_workout(user_id, workout_id, card['fingerprint'])
            await clear_workout_state(state, workout_id)
            safe_sync_workout(user_id, workout_id)
            await callback.message.answer('Тренировка завершена. Все записанные подходы сохранены.')
            await show_training(callback.message, state, user_id)
    except db.WorkoutAccessError as error:
        await callback.message.answer(str(error))
        await show_current_unfinished(callback.message, state, user_id, workout_id)
    except (ValueError, sqlite3.Error):
        await callback.message.answer('Не удалось выполнить действие. Подходы сохранены. Откройте карточку заново.')
    await callback.answer()


async def show_current_unfinished(message, state, user_id, workout_id):
    active = db.get_active_workout(user_id)
    if active and active[0] == workout_id:
        await show_unfinished_workout(message, state, user_id, workout_id)
    else:
        await clear_workout_state(state, workout_id)
        await message.answer('Эта тренировка уже завершена, удалена или недоступна.')
        await show_training(message, state, user_id)


@router.callback_query(F.data == 'legacyprograms')
async def legacy_programs(callback, state, user_id: int):
    from handlers.program_editor import editor_start
    await editor_start(callback.message, state, user_id)
    await callback.answer()


@router.callback_query(F.data.startswith('legacyfinish:'))
async def finish_legacy_workout(callback, state, user_id: int):
    try:
        wid = int(callback.data.split(':')[1])
    except ValueError:
        await callback.answer('Кнопка устарела.')
        return
    db.finish_legacy_workout(user_id, wid)
    await clear_workout_state(state, wid)
    safe_sync_workout(user_id, wid)
    await callback.message.answer('Старая тренировка завершена. Все записанные подходы сохранены.')
    await show_training(callback.message, state, user_id)
    await callback.answer()


@router.message(CommandStart())
async def start_command(message: Message, user_id: int):
    reschedule_notifications(message.bot, user_id)
    await message.answer('Главное меню. Продолжить тренировку: /resume', reply_markup=main_menu)


@router.message(F.text == '🌅 Утро')
async def morning_menu_button(message: Message, state, user_id: int):
    await morning_start(message, state, user_id)


@router.message(F.text == '🌙 Вечер')
async def evening_menu_button(message: Message, state, user_id: int):
    await evening_start(message, state, user_id)


@router.message(F.text == '📊 Сегодня')
async def today_menu_button(message: Message, user_id: int):
    await today_handler(message, user_id)


@router.message(F.text == '🏋️ Тренировка')
async def training_menu_button(message: Message, state, user_id: int):
    await show_training(message, state, user_id)


@router.message(Command('resume'))
async def resume_command(message: Message, state, user_id: int):
    await resume_training(message, state, user_id)


@router.callback_query(F.data == 'training')
async def training_button(callback: CallbackQuery, state, user_id: int):
    await show_training(callback.message, state, user_id)
    await callback.answer()


@router.callback_query(F.data.in_({'trainingprograms', 'trainingnew', 'editactive'}))
async def training_program_controls(callback: CallbackQuery, state, user_id: int):
    from handlers import program_editor
    if callback.data == 'editactive':
        await program_editor.open_active_editor(callback.message, state, user_id)
    elif callback.data == 'trainingnew':
        if await program_editor.guard(callback.message, state, user_id):
            await program_editor.dispatch_action(callback.message, state, user_id,
                                                  program_editor.clean_editor(), 'new', [])
    else:
        await program_editor.editor_start(callback.message, state, user_id)
    await callback.answer()


@router.callback_query(F.data.startswith('trainingpage:'))
async def training_page(callback: CallbackQuery, user_id: int):
    try:
        _, program_page, day_page = callback.data.split(':')
        program_page, day_page = int(program_page), int(day_page)
    except ValueError:
        await callback.answer('Неверная страница.')
        return
    program = programs.get_active_program(user_id)
    title = f"Программа: {program['name']}\nВыберите тренировочный день." if program else 'Выберите активную программу.'
    await callback.message.answer(title, reply_markup=workout_menu(
        programs.list_programs(user_id), program, program_page, day_page))
    await callback.answer()


@router.callback_query(F.data.startswith('program:'))
async def program_selected(callback: CallbackQuery, user_id: int):
    try:
        program_id = int(callback.data.split(':')[1])
    except ValueError:
        await callback.answer('Неверная кнопка.')
        return
    programs.activate_program(user_id, program_id)
    program = programs.get_active_program(user_id)
    await callback.message.answer(f"Программа: {program['name']}\nВыберите тренировочный день.",
                                  reply_markup=workout_menu(programs.list_programs(user_id), program))
    await callback.answer()


@router.callback_query(F.data.startswith('day:') | (F.data == 'nextday'))
async def workout_selected(callback: CallbackQuery, state, user_id: int):
    active = db.get_active_workout(user_id)
    if active:
        await show_unfinished_workout(callback.message, state, user_id, active[0])
        await callback.answer()
        return
    if await state.get_state():
        await callback.answer('Сначала закончите текущий ввод или продолжите тренировку через /resume.', show_alert=True)
        return
    try:
        day_id = None if callback.data == 'nextday' else int(callback.data.split(':')[1])
    except ValueError:
        await callback.answer('Неверный день.')
        return
    session = sessions.start_day(user_id, day_id)
    await show_session(callback.message, state, user_id, session)
    await callback.answer()


@router.callback_query(F.data.startswith('previewday:') | (F.data == 'previewnext'))
async def preview_day(callback: CallbackQuery, user_id: int):
    try:
        day = programs.next_day(user_id) if callback.data == 'previewnext' else programs.get_day(user_id, int(callback.data.split(':')[1]))
    except ValueError as error:
        if isinstance(error, db.WorkoutAccessError):
            raise
        await callback.answer('Неверный день.')
        return
    text = day_summary(day)
    for offset in range(0, len(text), 3900):
        await callback.message.answer(text[offset:offset + 3900])
    await callback.message.answer('Начать этот день? Плановые подходы не считаются выполненными.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Начать тренировку', callback_data=f"day:{day['id']}")],
            [InlineKeyboardButton(text='Назад', callback_data='training')],
        ]))
    await callback.answer()


async def valid_control(callback, state, user_id):
    data = await state.get_data()
    if 'workout_id' not in data:
        await callback.answer('Продолжите тренировку через /resume.', show_alert=True)
        return None
    session = sessions.get_session(user_id, data['workout_id'])
    suffix = f"{session['workout_id']}:{session['progress']['current']}:{session['revision']}"
    if callback.data.split(':', 1)[-1] != suffix:
        await callback.answer('Эта кнопка устарела. Откройте /resume.', show_alert=True)
        return None
    return session


@router.callback_query(WorkoutState.waiting_for_exercise_input, F.data.startswith('postpone:'))
async def postpone_exercise(callback: CallbackQuery, state, user_id: int):
    session = await valid_control(callback, state, user_id)
    if session is not None:
        session = sessions.postpone_exercise(user_id, session['workout_id'], session['revision'])
        await show_session(callback.message, state, user_id, session)
        await callback.answer()


@router.callback_query(WorkoutState.waiting_for_exercise_input, F.data.startswith('finish:'))
async def finish_workout_button(callback: CallbackQuery, state, user_id: int):
    session = await valid_control(callback, state, user_id)
    if session is not None:
        details = sessions.get_unfinished_workout(user_id, session['workout_id'])
        if details['revision'] != session['revision']:
            await callback.answer('Тренировка изменилась. Откройте /resume.', show_alert=True)
            return
        sessions.finish_unfinished_workout(user_id, session['workout_id'], details['fingerprint'])
        await clear_workout_state(state, session['workout_id'])
        safe_sync_workout(user_id, session['workout_id'])
        await callback.message.answer('Тренировка завершена! 💪')
        await show_training(callback.message, state, user_id)
        await callback.answer()


@router.message(Command('cancel'))
async def cancel_current_workout(message: Message, state, user_id: int):
    data = await state.get_data()
    current = await state.get_state()
    if current == WorkoutState.correcting_previous.state:
        await message.answer('Исправление отменено. Прежние подходы сохранены.')
        await return_to_workout(message, state, user_id, data['workout_id'])
        return
    if current and not current.startswith('WorkoutState:'):
        if current.startswith('ProgramEditor:'):
            from handlers.program_editor import close_editor
            await close_editor(message, state, user_id, data.get('editor', {}))
            return
        await state.clear()
        await message.answer('Текущий ввод отменён.', reply_markup=main_menu)
        return
    active = db.get_active_workout(user_id)
    if active:
        await ask_delete_workout(message, state, user_id, active[0], origin='command')
        return
    await state.clear()
    await message.answer('Текущий ввод отменён. Активной тренировки нет.', reply_markup=main_menu)


async def ask_delete_workout(message, state, user_id, workout_id, *, origin):
    details = sessions.get_unfinished_workout(user_id, workout_id)
    token = uuid4().hex[:12]
    await state.update_data(workout_id=workout_id, delete_revision=details['revision'],
                            delete_fingerprint=details['fingerprint'], delete_token=token,
                            delete_origin=origin)
    await state.set_state(WorkoutState.confirming_cancel)
    await message.answer(unfinished_views.deletion_warning(details), reply_markup=cancel_workout_controls(token))


@router.message(Command('exit'))
async def exit_current_workout(message: Message, state, user_id: int):
    active = db.get_active_workout(user_id)
    if not active:
        await message.answer('Нет активной тренировки.')
        return
    details = sessions.get_unfinished_workout(user_id, active[0])
    sessions.finish_unfinished_workout(user_id, active[0], details['fingerprint'])
    await clear_workout_state(state, active[0])
    safe_sync_workout(user_id, active[0])
    await message.answer('Тренировка завершена. Введённые подходы сохранены.', reply_markup=main_menu)
    await show_training(message, state, user_id)


@router.message(F.text == '📚 История')
async def history_menu_button(message: Message, user_id: int):
    await show_history_days(message, user_id)


async def show_history_days(message, user_id):
    groups = db.get_history_days(user_id)
    await message.answer('Какую тренировку посмотреть?' if groups else 'Завершённых тренировок пока нет.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=g['title'], callback_data='hday:' + g['key'])] for g in groups
        ]) if groups else None)


@router.callback_query(F.data == 'historydays')
async def history_days_back(callback, user_id: int):
    await show_history_days(callback.message, user_id)
    await callback.answer()


@router.callback_query(F.data.startswith('hday:'))
async def history_day_selected(callback, user_id: int):
    parts = callback.data.split(':')
    key = ':'.join(parts[1:3])
    try:
        page = int(parts[3]) if len(parts) == 4 else 0
        if page < 0:
            raise ValueError
    except ValueError:
        await callback.answer('Неверная страница.')
        return
    group = next((g for g in db.get_history_days(user_id) if g['key'] == key), None)
    if group is None:
        await callback.answer('День недоступен. Откройте историю заново.')
        return
    if not group['workouts']:
        await callback.message.answer('Завершённых тренировок этого дня пока нет.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Назад к выбору дней', callback_data='historydays')]]))
        await callback.answer()
        return
    await show_history_workout(callback, user_id, group['workouts'][0])


@router.callback_query(F.data.startswith('history:') | F.data.startswith('history_'))
async def history_selected(callback: CallbackQuery, user_id: int):
    if callback.data.startswith('history_'):  # Preserve old A/B/C history buttons.
        workout = db.get_last_workout_by_type(user_id, callback.data.removeprefix('history_'))
        if workout is None:
            await callback.answer('Завершённых тренировок нет.')
            return
    else:
        try:
            workout = db.get_workout(user_id, int(callback.data.split(':')[1]))
        except ValueError as error:
            if isinstance(error, db.WorkoutAccessError):
                raise
            await callback.answer('Неверная кнопка.')
            return
    await show_history_workout(callback, user_id, workout)


async def show_history_workout(callback, user_id, workout):
    wid, label, started, finished = workout
    if finished is None:
        await callback.answer('Тренировка ещё не завершена.')
        return
    text = f"📊 Тренировка {escape(label)}\nНачало: {escape(started)}\nОкончание: {escape(finished)}\n"
    previous, grouped = object(), []
    for exercise, exercise_id, number, warmup, weight, reps, occurrence in db.get_history_sets(user_id, wid):
        key = occurrence if occurrence is not None else (exercise_id, exercise)
        if key != previous or number == 1:
            if grouped:
                text += format_exercise_sets(grouped)
            text += f'\n<b>{escape(exercise)}</b>\n'
            grouped = []
            previous = key
        grouped.append((warmup, weight, reps))
    text += format_exercise_sets(grouped)
    # Telegram message limit; each chunk retains valid HTML by grouping lines.
    if len(text) > 4000:
        # Long history is sent as plain text to avoid splitting HTML entities/tags.
        import re
        from html import unescape
        plain = unescape(re.sub(r'</?b>', '', text))
        for offset in range(0, len(plain), 3900):
            await callback.message.answer(plain[offset:offset + 3900])
    else:
        await callback.message.answer(text, parse_mode='HTML')
    buttons = [[InlineKeyboardButton(text='Назад к выбору дней', callback_data='historydays')]]
    await callback.message.answer('История тренировок', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.message(WorkoutState.waiting_for_exercise_input, F.text, ~F.text.startswith('/'), ~F.text.in_({'Мои программы', '🗂 Мои программы'}))
async def process_exercise_input(message: Message, state, user_id: int):
    await state.update_data(exercise_input=message.text)
    try:
        sets = parse_exercise_input(message.text)
    except ValueError as error:
        await message.answer(f'Не удалось распознать подходы: {error}')
        return
    data = await state.get_data()
    result = sessions.complete_exercise(user_id, data['workout_id'], data['revision'], sets)
    await state.update_data(exercise_input=None)
    await message.answer(f'Подходы сохранены:\n{format_parsed_sets(sets)}')
    if result is None:
        await clear_workout_state(state, data['workout_id'])
        safe_sync_workout(user_id, data['workout_id'])
        await message.answer('Тренировка завершена! 💪')
    else:
        await show_session(message, state, user_id, result)


async def return_to_workout(message, state, user_id, workout_id):
    """Rebuild only the cache; the persisted progress is the source of truth."""
    try:
        session = sessions.get_session(user_id, workout_id)
    except db.WorkoutAccessError:
        await show_current_unfinished(message, state, user_id, workout_id)
        return
    except sqlite3.Error:
        await message.answer('Не удалось прочитать текущую тренировку. Эта операция ничего не удаляет. Повторите /resume позже.')
        return
    await show_session(message, state, user_id, session)


@router.callback_query(WorkoutState.waiting_for_exercise_input, F.data.startswith('fix:'))
async def correct_previous(callback: CallbackQuery, state, user_id: int):
    try:
        session = await valid_control(callback, state, user_id)
        if session is None:
            return
        previous = sessions.previous_exercise(user_id, session['workout_id'], session['revision'])
    except db.WorkoutAccessError as error:
        await callback.message.answer(str(error))
        await callback.answer()
        return
    except (sqlite3.Error, ValueError, KeyError, IndexError):
        await callback.message.answer('Не удалось безопасно открыть исправление. Подходы не изменены. Повторите позже или откройте /resume.')
        await callback.answer()
        return
    token = uuid4().hex[:12]
    await state.update_data(workout_id=session['workout_id'], revision=session['revision'],
                            correction_position=previous['position'], correction_token=token)
    await state.set_state(WorkoutState.correcting_previous)
    text = (f"Исправление: {previous['exercise']['name']} (включение №{previous['position'] + 1})\n"
            f"Сейчас сохранено:\n{format_exercise_sets(previous['sets'])}\n"
            'Отправьте ВСЕ исправленные подходы одним сообщением в обычном формате. '
            'Они заменят прежние подходы только этого включения. '
            'Разминку отделите от рабочих пустой строкой; /help — примеры. '
            'После записи вернёмся к текущему упражнению.')
    for offset in range(0, max(0, len(text) - 3900), 3900):
        await callback.message.answer(text[offset:offset + 3900])
    await callback.message.answer(text[((len(text) - 1) // 3900) * 3900:], reply_markup=correction_controls(token))
    await callback.answer()


@router.callback_query(WorkoutState.correcting_previous, F.data.startswith('endfix:'))
async def cancel_correction(callback: CallbackQuery, state, user_id: int):
    data = await state.get_data()
    if callback.data.split(':', 1)[-1] != data.get('correction_token'):
        await callback.answer('Кнопка устарела. Откройте /resume.', show_alert=True)
        return
    await state.update_data(correction_token=None)
    await return_to_workout(callback.message, state, user_id, data['workout_id'])
    await callback.answer('Исправление отменено. Данные не изменились.')


@router.message(WorkoutState.correcting_previous, F.text, ~F.text.startswith('/'), ~F.text.in_({'Мои программы', '🗂 Мои программы'}))
async def process_correction(message: Message, state, user_id: int):
    await state.update_data(correction_input=message.text)
    try:
        sets = parse_exercise_input(message.text)
    except ValueError as error:
        await message.answer(f'Не удалось распознать подходы: {error}. Прежние данные сохранены. Повторите ввод или нажмите «Отмена исправления».')
        return
    data = await state.get_data()
    try:
        result = sessions.replace_previous_sets(user_id, data['workout_id'], data['correction_position'], data['revision'], sets)
    except db.WorkoutAccessError as error:
        await message.answer(f'Исправление не выполнено: {error}')
        await return_to_workout(message, state, user_id, data['workout_id'])
        return
    except (ValueError, sqlite3.Error, OverflowError):
        await message.answer('Не удалось сохранить исправление. Прежние подходы не изменились. Проверьте ввод и повторите или нажмите «Отмена исправления».')
        return
    await message.answer('Предыдущие подходы заменены. Возвращаемся к текущему упражнению.')
    await state.update_data(correction_input=None)
    await show_session(message, state, user_id, result)


@router.callback_query(WorkoutState.confirming_cancel, F.data.startswith('deleteworkout:') | F.data.startswith('keepworkout:'))
async def confirm_workout_cancel(callback: CallbackQuery, state, user_id: int):
    data = await state.get_data()
    if callback.data.split(':', 1)[-1] != data.get('delete_token'):
        await callback.answer('Подтверждение устарело. Откройте карточку тренировки заново.', show_alert=True)
        return
    await state.update_data(delete_token=None)  # Consume before any write.
    if callback.data.startswith('keepworkout:'):
        if data.get('delete_origin') == 'card':
            await state.set_state(None)
            await show_current_unfinished(callback.message, state, user_id, data['workout_id'])
        else:
            await return_to_workout(callback.message, state, user_id, data['workout_id'])
        await callback.answer('Удаление отменено.')
        return
    try:
        sessions.delete_unfinished_workout(user_id, data['workout_id'], data['delete_fingerprint'])
    except db.WorkoutAccessError as error:
        await callback.message.answer(f'Тренировка не удалена: {error}')
        if data.get('delete_origin') == 'card':
            await state.set_state(None)
            await show_current_unfinished(callback.message, state, user_id, data['workout_id'])
        else:
            await return_to_workout(callback.message, state, user_id, data['workout_id'])
    except sqlite3.Error:
        await callback.message.answer('Удаление не выполнено. Данные сохранены; откройте /resume и при необходимости повторите /cancel.')
    else:
        await clear_workout_state(state, data['workout_id'])
        await callback.message.answer('Тренировка и все её подходы удалены.', reply_markup=main_menu)
        await show_training(callback.message, state, user_id)
    await callback.answer()


@router.message(WorkoutState.confirming_cancel, ~F.text.startswith('/'), ~F.text.in_({'Мои программы', '🗂 Мои программы'}))
async def deletion_confirmation_text(message: Message):
    await message.answer('Удаление ещё не подтверждено. Нажмите «Да, удалить» или «Назад»; /resume возвращает к записи.')


@router.callback_query(F.data.startswith('postpone:') | F.data.startswith('finish:')
                       | F.data.startswith('fix:') | F.data.startswith('endfix:')
                       | F.data.startswith('deleteworkout:') | F.data.startswith('keepworkout:')
                       | F.data.startswith('correct:') | F.data.startswith('cancelcorrect:')
                       | F.data.startswith('workout_') | F.data.in_({'postpone_exercise', 'finish_workout'}))
async def expired_workout_button(callback: CallbackQuery):
    await callback.answer('Эта кнопка устарела. Откройте тренировку заново или вызовите /resume.', show_alert=True)
