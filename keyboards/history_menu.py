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
                text="Тренировка Б",
                callback_data="history_B"
            )
        ],
        [
            InlineKeyboardButton(
                text="Тренировка В",
                callback_data="history_C"
            )
        ]
    ]
)