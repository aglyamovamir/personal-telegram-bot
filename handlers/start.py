from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from database.db import save_set
from keyboards.main_menu import main_menu
from keyboards.workout_menu import workout_menu
from states.workout import WorkoutState


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
async def workout_selected(callback: CallbackQuery, state):
    workout_name = callback.data.replace("workout_", "")

    await state.update_data(
        workout=workout_name,
        exercise="Жим лежа",
        set_number=1
    )

    await state.set_state(WorkoutState.choosing_weight)

    await callback.message.answer(
        f"Выбрана тренировка {workout_name}.\n"
        f"Первое упражнение — Жим лежа.\n\n"
        f"Введите вес в кг:"
    )

    await callback.answer()


@router.message(WorkoutState.choosing_weight)
async def process_weight(message: Message, state):
    await state.update_data(weight=message.text)
    await state.set_state(WorkoutState.choosing_reps)

    await message.answer(
        "Вес сохранён.\n"
        "Теперь введите количество повторений:"
    )


@router.message(WorkoutState.choosing_reps)
async def process_reps(message: Message, state):
    data = await state.get_data()

    workout = data["workout"]
    exercise = data["exercise"]
    set_number = data["set_number"]
    weight = data["weight"]
    reps = int(message.text)

    save_set(
        workout=workout,
        exercise=exercise,
        set_number=set_number,
        weight=float(weight),
        reps=reps
    )

    await message.answer(
        f"Подход сохранён в базу данных:\n\n"
        f"{exercise}\n"
        f"{weight} кг × {reps}"
    )

    await state.clear()