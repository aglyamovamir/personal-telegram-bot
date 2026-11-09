from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def workout_menu(programs, active, program_page=0, day_page=0):
    days = active['days'] if active else []
    day_page = max(0, min(day_page, max(0, (len(days) - 1) // 12)))
    rows = []
    if active:
        rows.append([InlineKeyboardButton(text="Следующий день по порядку", callback_data='nextday')])
        rows.extend([[InlineKeyboardButton(text=d['name'], callback_data=f"day:{d['id']}")]
                     for d in days[day_page * 12:(day_page + 1) * 12]])
        if day_page:
            rows.append([InlineKeyboardButton(text='← Дни', callback_data=f'trainingpage:{program_page}:{day_page - 1}')])
        if (day_page + 1) * 12 < len(days):
            rows.append([InlineKeyboardButton(text='Дни →', callback_data=f'trainingpage:{program_page}:{day_page + 1}')])
        rows.append([InlineKeyboardButton(text='Редактировать программу', callback_data='editactive')])
    else:
        rows.append([InlineKeyboardButton(text='Создать программу', callback_data='trainingnew')])
    rows.append([InlineKeyboardButton(text='Список программ', callback_data='trainingprograms')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def workout_controls(workout_id, exercise_index, token, *, can_correct=False):
    suffix = f'{workout_id}:{exercise_index}:{token}'
    actions = [InlineKeyboardButton(text="Отложить упражнение", callback_data=f'postpone:{suffix}')]
    if can_correct:
        actions.append(InlineKeyboardButton(text='Исправить предыдущее', callback_data=f'fix:{suffix}'))
    return InlineKeyboardMarkup(inline_keyboard=[
        actions,
        [InlineKeyboardButton(text="Завершить тренировку", callback_data=f'finish:{suffix}')],
    ])


def correction_controls(token):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Отмена исправления', callback_data=f'endfix:{token}')
    ]])


def cancel_workout_controls(token):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Да, удалить', callback_data=f'deleteworkout:{token}')],
        [InlineKeyboardButton(text='Назад', callback_data=f'keepworkout:{token}')],
    ])
