from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


set_type_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔥 Разминочный подход",
                callback_data="set_warmup"
            )
        ],
        [
            InlineKeyboardButton(
                text="💪 Рабочий подход",
                callback_data="set_working"
            )
        ],
        [
            InlineKeyboardButton(
                text="⏸️ Отложить упражнение",
                callback_data="postpone_exercise"
            )
        ]
    ]
)


working_set_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💪 Ещё рабочий",
                callback_data="set_working"
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Завершить упражнение",
                callback_data="finish_exercise"
            )
        ]
    ]
)

exercise_finished_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➡️ Следующее упражнение",
                callback_data="next_exercise"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏁 Завершить тренировку",
                callback_data="finish_workout"
            )
        ]
    ]
)