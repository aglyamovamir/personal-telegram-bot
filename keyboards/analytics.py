"""Small callbacks contain numeric anchors only, never exercise names."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows])


def menu():
    return keyboard([
        [('Разбор последней тренировки', 'an:days:r:0')],
        [('Прогресс по тренировкам', 'an:days:d:0')],
        [('Прогресс по упражнениям', 'an:list:0')],
        [('Главное меню', 'an:home')]])


def periods(prefix, selected):
    return [(('✓ ' if value == selected else '') + label, f'{prefix}:{value}')
            for value, label in ((3, '3 месяца'), (6, '6 месяцев'), (0, 'Вся история'))]


def day_choices(groups, mode, page=0):
    rows = [[(g['title'], f"an:{'report' if mode == 'r' else 'day'}:{g['records'][0]['id']}" + ('' if mode == 'r' else ':0'))]
            for g in groups[page * 8:(page + 1) * 8]]
    nav = []
    if page:
        nav.append(('←', f'an:days:{mode}:{page-1}'))
    if (page + 1) * 8 < len(groups):
        nav.append(('→', f'an:days:{mode}:{page+1}'))
    if nav:
        rows.append(nav)
    rows.append([('Назад', 'an:menu')])
    return keyboard(rows)


def report_controls(current):
    # Exactly two buttons on the detailed report.
    return keyboard([[(f"График тренировок {current['name'][:60]}", f"an:day:{current['id']}:0")],
                     [('Назад', 'an:days:r:0')]])


def exercise_choices(items, page=0, *, filtered=False):
    rows = [[(item['name'], f"an:ex:{item['id']}:0")] for item in items[page * 8:(page + 1) * 8]]
    nav = []
    prefix = 'an:found' if filtered else 'an:list'
    if page:
        nav.append(('←', f'{prefix}:{page-1}'))
    if (page + 1) * 8 < len(items):
        nav.append(('→', f'{prefix}:{page+1}'))
    if nav:
        rows.append(nav)
    rows += [[('Найти упражнение', 'an:search')], [('Назад', 'an:menu')]]
    return keyboard(rows)
