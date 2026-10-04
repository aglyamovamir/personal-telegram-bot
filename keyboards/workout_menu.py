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
                text="Тренировка B",
                callback_data="workout_B"
            )
        ],
        [
            InlineKeyboardButton(
                text="Тренировка C",
                callback_data="workout_C"
            )
        ]
    ]
)


postpone_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⏭ Отложить упражнение",
                callback_data="postpone_exercise"
            )
        ]
    ]
)