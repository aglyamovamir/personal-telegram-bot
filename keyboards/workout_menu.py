from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def workout_menu(programs, active, program_page=0, day_page=0):
    program_page = max(0, min(program_page, max(0, (len(programs) - 1) // 8)))
    days = active['days'] if active else []
    day_page = max(0, min(day_page, max(0, (len(days) - 1) // 12)))
    rows = [[InlineKeyboardButton(text=p['name'], callback_data=f"program:{p['id']}")]
            for p in programs[program_page * 8:(program_page + 1) * 8]]
    if program_page:
        rows.append([InlineKeyboardButton(text='← Программы', callback_data=f'trainingpage:{program_page - 1}:{day_page}')])
    if (program_page + 1) * 8 < len(programs):
        rows.append([InlineKeyboardButton(text='Программы →', callback_data=f'trainingpage:{program_page + 1}:{day_page}')])
    if active:
        rows.append([InlineKeyboardButton(text="Следующий день по порядку", callback_data='previewnext')])
        rows.extend([[InlineKeyboardButton(text=d['name'], callback_data=f"previewday:{d['id']}")]
                     for d in days[day_page * 12:(day_page + 1) * 12]])
        if day_page:
            rows.append([InlineKeyboardButton(text='← Дни', callback_data=f'trainingpage:{program_page}:{day_page - 1}')])
        if (day_page + 1) * 12 < len(days):
            rows.append([InlineKeyboardButton(text='Дни →', callback_data=f'trainingpage:{program_page}:{day_page + 1}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def workout_controls(workout_id, exercise_index, token, *, can_correct=False):
    suffix = f'{workout_id}:{exercise_index}:{token}'
    actions = [InlineKeyboardButton(text="⏭ Отложить упражнение", callback_data=f'postpone:{suffix}')]
    if can_correct:
        actions.append(InlineKeyboardButton(text='✏️ Исправить предыдущее', callback_data=f'fix:{suffix}'))
    return InlineKeyboardMarkup(inline_keyboard=[
        actions,
        [InlineKeyboardButton(text="🏁 Завершить тренировку", callback_data=f'finish:{suffix}')],
    ])


def correction_controls(token):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Отмена исправления', callback_data=f'endfix:{token}')
    ]])


def cancel_workout_controls(token):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Удалить тренировку', callback_data=f'deleteworkout:{token}')],
        [InlineKeyboardButton(text='Вернуться к тренировке', callback_data=f'keepworkout:{token}')],
    ])
