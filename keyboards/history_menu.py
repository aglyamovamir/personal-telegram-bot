from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


history_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Тренировка A",
                callback_data="history_A"
            )
        ],
        [
            InlineKeyboardButton(
                text="Тренировка B",
                callback_data="history_B"
            )
        ],
        [
            InlineKeyboardButton(
                text="Тренировка C",
                callback_data="history_C"
            )
        ]
    ]
)