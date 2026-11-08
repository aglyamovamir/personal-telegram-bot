from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def history_menu(workouts):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{label} ({started[:10]})", callback_data=f'history:{wid}')]
        for wid, label, started, finished in workouts
    ])
