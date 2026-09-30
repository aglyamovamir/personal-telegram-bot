from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

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
    weight = data["weight"]
    reps = message.text

    await message.answer(
        f"Подход сохранён: {weight} кг × {reps} повторений"
    )

    await state.clear()


@router.callback_query(lambda callback: callback.data.startswith("workout_"))
async def workout_selected(callback: CallbackQuery, state):
    workout_name = callback.data.replace("workout_", "")

    await state.set_state(WorkoutState.choosing_weight)

    await callback.message.answer(
        f"Выбрана тренировка {workout_name}.\n"
        f"Первое упражнение — Жим лежа.\n\n"
        f"Введите вес в кг:"
    )

    await callback.answer()