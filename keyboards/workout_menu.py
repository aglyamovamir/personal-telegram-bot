from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


workout_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Тренировка A",
                callback_data="workout_A"
            )
        ],
        [
            InlineKeyboardButton(
                text="Тренировка Б",
                callback_data="workout_B"
            )
        ],
        [
            InlineKeyboardButton(
                text="Тренировка В",
                callback_data="workout_C"
            )
        ]
    ]
)