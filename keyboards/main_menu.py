from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🏋️ Тренировка",
                callback_data="training"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 История",
                callback_data="history"
            )
        ]
    ]
)