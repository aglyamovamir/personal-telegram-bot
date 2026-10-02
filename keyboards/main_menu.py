from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🌅 Утро"),
            KeyboardButton(text="🌙 Вечер")
        ],
        [
            KeyboardButton(text="🏋️ Тренировка"),
            KeyboardButton(text="📊 Сегодня")
        ],
        [
            KeyboardButton(text="📚 История")
        ]
    ],
    resize_keyboard=True,
    is_persistent=True
)