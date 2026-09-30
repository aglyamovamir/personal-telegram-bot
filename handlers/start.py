from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from database.db import create_workout, save_set, finish_workout
from keyboards.main_menu import main_menu
from keyboards.workout_menu import workout_menu
from states.workout import WorkoutState
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
async def workout_selected(callback: CallbackQuery, state):
    workout_name = callback.data.replace("workout_", "")

    workout_id = create_workout(workout_name)

    first_exercise = WORKOUTS[workout_name][0]

    await state.update_data(
    workout_id=workout_id,
    workout=workout_name,
    exercise_index=0,
    exercise=first_exercise["name"],
    set_number=1
    )

    await state.set_state(WorkoutState.choosing_weight)

    await callback.message.answer(
    f"Выбрана тренировка {workout_name}.\n"
    f"Первое упражнение — {first_exercise['name']}.\n\n"
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

    workout_id = data["workout_id"]
    workout = data["workout"]
    exercise = data["exercise"]
    exercise_index = data["exercise_index"]
    set_number = data["set_number"]
    weight = data["weight"]
    reps = int(message.text)

    save_set(
        workout_id=workout_id,
        exercise=exercise,
        set_number=set_number,
        weight=float(weight),
        reps=reps
    )

    await message.answer(
        f"Подход сохранён в базу данных:\n\n"
        f"{exercise}\n"
        f"Подход №{set_number}: {weight} кг × {reps}"
    )

    current_exercise = WORKOUTS[workout][exercise_index]

    if set_number < current_exercise["working_sets"]:
        next_set_number = set_number + 1

        await state.update_data(
            set_number=next_set_number
        )

        await state.set_state(WorkoutState.choosing_weight)

        await message.answer(
            f"{exercise}\n"
            f"Подход №{next_set_number}.\n\n"
            f"Введите вес в кг:"
        )

    else:
        next_exercise_index = exercise_index + 1

        if next_exercise_index < len(WORKOUTS[workout]):
            next_exercise = WORKOUTS[workout][next_exercise_index]

            await state.update_data(
                exercise_index=next_exercise_index,
                exercise=next_exercise["name"],
                set_number=1
            )

            await state.set_state(WorkoutState.choosing_weight)

            await message.answer(
                f"Упражнение завершено.\n\n"
                f"Следующее упражнение — {next_exercise['name']}.\n"
                f"Подход №1.\n\n"
                f"Введите вес в кг:"
            )

        else:
            finish_workout(workout_id)

            await message.answer(
                f"Тренировка {workout} завершена! 💪"
            )

            await state.clear()