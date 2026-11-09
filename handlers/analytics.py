"""Read-only analytics navigation. Existing workout/editor FSM is preserved."""
import asyncio
import logging
import sqlite3
from html import escape

from aiogram import Router, F
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile

from database import analytics as facts, db
from keyboards import analytics as buttons
from keyboards.main_menu import main_menu
from workouts import analytics_presentation as view, presentation
from workouts.charts import render_chart

router = Router()
logger = logging.getLogger(__name__)


class AnalyticsState(StatesGroup):
    search = State()


async def show_menu(message, user_id, state=None):
    if state and await state.get_state() == AnalyticsState.search.state:
        await state.set_state(None)
    await message.answer('📊 Аналитика\nВыберите, что посмотреть.', reply_markup=buttons.menu())


async def send_text(message, text, markup=None):
    chunks = presentation.html_chunks(text)
    for i, chunk in enumerate(chunks):
        await message.answer(chunk, parse_mode='HTML', reply_markup=markup if i == len(chunks) - 1 else None)


async def send_chart(message, records, title, *, exercise_label=None, selected_id=None, caption=None, markup=None):
    try:
        picture = await asyncio.to_thread(render_chart, records, title, exercise_label=exercise_label, selected_id=selected_id)
        if picture:
            # A report can be long: never split HTML inside a Telegram caption.
            if caption and len(caption.encode('utf-16-le')) // 2 <= 1000:
                await message.answer_photo(BufferedInputFile(picture, filename='analytics.png'),
                                           caption=caption, parse_mode='HTML', reply_markup=markup)
                return True
            await message.answer_photo(BufferedInputFile(picture, filename='analytics.png'))
        elif not caption:
            await message.answer('Нет записей с достоверными датами для графика.')
    except Exception:
        # Graph/network failure must never suggest that committed facts were lost.
        logger.warning('Analytics chart could not be delivered', exc_info=False)
        if not caption:
            await message.answer('График сейчас недоступен. Числовые результаты сохранены и доступны ниже.')
    if caption:
        await send_text(message, caption, markup)
    return False


async def send_completion_report(message, user_id, workout_id):
    """Called only AFTER successful completion; never changes database or FSM."""
    try:
        data = facts.load(user_id)
        current, previous = facts.breakdown(data, workout_id)
        group = facts.group_for(data, workout_id)
        text = view.summary(current, previous, completed=True)
        changes = view.comparison(current, previous, short=True)
        # Keep automatic reports short. Full report retains every exercise.
        if changes:
            selected = presentation.html_chunks(changes, limit=900)[0]
            text += '\n' + selected
            if selected != changes:
                text += '\nПолное сравнение — в подробном разборе.'
        controls = buttons.keyboard([[('Подробный разбор', f'an:report:{workout_id}')]])
        await send_chart(message, group['records'], group['title'] + ' — объём',
                         selected_id=workout_id, caption=text, markup=controls)
    except Exception:
        logger.warning('Post-workout report unavailable (workout %s)', workout_id, exc_info=False)
        await message.answer('Тренировка завершена, подходы сохранены. Аналитика сейчас недоступна — откройте её позже.')


def page_number(value, count):
    page = int(value)
    if page < 0:
        raise ValueError('Неверная страница.')
    return min(page, max(0, (count - 1) // 8))


async def show_exercises(message, state, user_id, page=0, *, filtered=False):
    query = (await state.get_data()).get('analytics_query', '') if filtered else ''
    data = facts.load(user_id)
    items = facts.available_exercises(data, query)
    page = page_number(page, len(items))
    text = 'Выберите упражнение из вашей истории.'
    if not items:
        text = 'Подходящих упражнений с записанными рабочими подходами нет.'
    if filtered:
        text += '\nПоиск: ' + query
    if any(not o['exercise_id'] and facts.measures([o])['sets']
           for record in data['records'].values() for o in record['occurrences']):
        text += '\nСтарые подходы без ID упражнения учитываются в объёме дня и доступны в истории, но не сопоставляются с каталогом по догадке.'
    await message.answer(text, reply_markup=buttons.exercise_choices(items, page, filtered=filtered))


@router.callback_query(F.data.startswith('an:'))
async def analytics_callback(callback, state, user_id: int):
    parts = callback.data.split(':')
    action = parts[1]
    await callback.answer()
    try:
        if action != 'search' and await state.get_state() == AnalyticsState.search.state:
            await state.set_state(None)
        if action == 'menu':
            await show_menu(callback.message, user_id, state)
        elif action == 'home':
            await callback.message.answer('Главное меню', reply_markup=main_menu)
        elif action in ('list', 'found'):
            await show_exercises(callback.message, state, user_id, parts[2], filtered=action == 'found')
        elif action == 'search':
            if await state.get_state() not in (None, AnalyticsState.search.state):
                await callback.message.answer('Сейчас идёт другой ввод. Выберите упражнение кнопкой из списка или закончите текущий ввод.')
                return
            await state.set_state(AnalyticsState.search)
            await callback.message.answer('Введите название упражнения из вашей истории. /analytics — вернуться в аналитику.')
        elif action == 'days':
            mode = parts[2]
            if mode not in ('r', 'd'):
                raise ValueError('Неверный раздел.')
            groups = [g for g in facts.load(user_id)['groups'] if g['records']]
            page = page_number(parts[3], len(groups))
            await callback.message.answer('Выберите тренировочный день.' if groups else 'Завершённых тренировок пока нет.',
                                          reply_markup=buttons.day_choices(groups, mode, page))
        elif action == 'report':
            data = facts.load(user_id)
            current, previous = facts.breakdown(data, int(parts[2]))
            text = view.summary(current, previous) + '\n' + view.comparison(current, previous)
            await send_text(callback.message, text, buttons.report_controls(current))
        elif action == 'day':
            data = facts.load(user_id)
            group = facts.group_for(data, int(parts[2]))
            period = int(parts[3])
            records = facts.period_records(group['records'], period, timezone_name=data['timezone'])
            await send_chart(callback.message, records, group['title'] + ' — объём')
            text = f"<b>{escape(group['title'])}</b>\nТренировок за период: {len(records)}"
            dated = [r for r in records if r['date']]
            if dated:
                first, last = dated[-1]['metrics']['volume'], dated[0]['metrics']['volume']
                text += f'\nОбъём первой → последней: {presentation.format_number(first)} → {presentation.format_number(last)} ({view.delta(first, last)})'
                if any(facts.composition(r) != facts.composition(records[0]) for r in records):
                    text += '\nСостав упражнений менялся. Рост объёма сам по себе не означает рост силы.'
            if len(dated) != len(records):
                text += '\nЗаписи без достоверных дат не нанесены на график.'
            text += '\nОбъём — по введённому весу; разминка исключена.'
            await send_text(callback.message, text, buttons.keyboard([
                buttons.periods(f"an:day:{parts[2]}", period), [('Назад', 'an:days:d:0')]]))
        elif action == 'ex':
            data = facts.load(user_id)
            exercise = facts.exercise_for(data, int(parts[2]))
            period = int(parts[3])
            records = facts.period_records(facts.exercise_records(data, exercise), period, timezone_name=data['timezone'])
            await send_chart(callback.message, records, exercise['name'], exercise_label=facts.weight_label(exercise))
            rows = [buttons.periods(f"an:ex:{exercise['id']}", period)]
            if records:
                rows.append([('Все подходы', f"an:sets:{exercise['id']}:{period}:0")])
            rows.append([('Назад', 'an:list:0')])
            await send_text(callback.message, view.exercise_summary(exercise, records), buttons.keyboard(rows))
        elif action == 'sets':
            data = facts.load(user_id)
            exercise = facts.exercise_for(data, int(parts[2]))
            period, page = int(parts[3]), int(parts[4])
            records = facts.period_records(facts.exercise_records(data, exercise), period, timezone_name=data['timezone'])
            if page < 0 or page >= len(records):
                raise ValueError('Запись недоступна. Откройте упражнение заново.')
            record = records[page]
            text = f"<b>{escape(exercise['name'])}</b> · {escape(record['name'])} · {presentation.format_date(record['started_at'], record['timezone'])}"
            for i, occurrence in enumerate(record['occurrences'], 1):
                text += f'\n\nВключение {i}' if len(record['occurrences']) > 1 else '\n'
                text += '\n' + '\n'.join(escape(line) for line in presentation.grouped_set_lines(occurrence['sets']))
            nav = []
            if page:
                nav.append(('←', f'an:sets:{exercise["id"]}:{period}:{page-1}'))
            if page + 1 < len(records):
                nav.append(('→', f'an:sets:{exercise["id"]}:{period}:{page+1}'))
            await send_text(callback.message, text, buttons.keyboard(([nav] if nav else []) + [
                [('Назад', f'an:ex:{exercise["id"]}:{period}')]]))
        else:
            raise ValueError('Кнопка устарела. Откройте аналитику заново.')
    except (ValueError, IndexError, KeyError, sqlite3.Error) as error:
        text = str(error) if isinstance(error, ValueError) else 'Данные сейчас недоступны. Откройте аналитику заново.'
        await callback.message.answer(text)


@router.message(AnalyticsState.search, F.text.in_({'🏋️ Тренировка', '📚 История', '📊 Аналитика', '📊 Сегодня'}))
async def leave_analytics_search(message, state):
    # Leave only our own input; let the normal menu handler process this message.
    await state.set_state(None)
    return UNHANDLED


@router.message(AnalyticsState.search, F.text, ~F.text.startswith('/'), ~F.text.in_(
    {'🏋️ Тренировка', '📚 История', '📊 Аналитика', '📊 Сегодня', '🌅 Утро', '🌙 Вечер', 'Мои программы', '🗂 Мои программы'}))
async def analytics_search(message, state, user_id: int):
    query = message.text.strip()
    if not 2 <= len(query) <= 100:
        await message.answer('Введите от 2 до 100 символов названия.')
        return
    await state.update_data(analytics_query=query)
    await state.set_state(None)
    await show_exercises(message, state, user_id, filtered=True)
