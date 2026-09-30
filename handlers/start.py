from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from keyboards.main_menu import main_menu
from keyboards.workout_menu import workout_menu
from workouts.program import WORKOUTS


router = Router()


@router.message(CommandStart())
async def start_command(message: Message):
    await message.answer(
        "Привет! Что будем делать?",
        reply_markup=main_menu
    )


@router.callback_query(lambda callback: callback.data == "training")
async def training_button(callback: CallbackQuery):
    await callback.message.answer(
        "Выберите тренировку:",
        reply_markup=workout_menu
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data.startswith("workout_"))
async def workout_selected(callback: CallbackQuery):
    workout_name = callback.data.replace("workout_", "")
    exercises = WORKOUTS[workout_name]

    text = f"Тренировка {workout_name}:\n\n"

    for number, exercise in enumerate(exercises, start=1):
        text += f"{number}. {exercise}\n"

    await callback.message.answer(text)
    await callback.answer()