from datetime import datetime

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from database.db import (
    create_workout,
    save_set,
    finish_workout,
    get_last_workout,
    get_workout_sets,
)
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

@router.callback_query(lambda callback: callback.data == "history")
async def history_button(callback: CallbackQuery):
    workout = get_last_workout()

    if workout is None:
        await callback.message.answer(
            "Завершённых тренировок пока нет."
        )
        await callback.answer()
        return

    workout_id, workout_type, started_at, finished_at = workout

    sets = get_workout_sets(workout_id)

    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)

    text = (
        f"📊 Последняя тренировка — {workout_type}\n\n"
        f"Начало: {started.strftime('%d.%m.%Y %H:%M')}\n"
        f"Окончание: {finished.strftime('%d.%m.%Y %H:%M')}\n\n"
    )

    current_exercise = None

    for exercise, set_number, weight, reps in sets:
        if exercise != current_exercise:
            current_exercise = exercise
            text += f"\n<b>{exercise}</b>\n"

        text += f"Подход {set_number}: {weight:g} кг × {reps}\n"

    await callback.message.answer(
        text,
        parse_mode="HTML"
    )

    await callback.answer()

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

    warmup_text = ""

    if first_exercise["warmup"]:
        warmup_text = "\n".join(
            f"Разминка: {item['weight']} кг × {item['reps']}"
            for item in first_exercise["warmup"]
        )

    await callback.message.answer(
        f"Выбрана тренировка {workout_name}.\n"
        f"Первое упражнение — {first_exercise['name']}.\n"
        f"{warmup_text}\n\n"
        f"Подход №1.\n"
        f"Введите вес в кг:"
    )

    await callback.answer()



@router.message(WorkoutState.choosing_weight)
async def process_weight(message: Message, state):
    try:
        weight = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer(
            "Не понял вес.\n"
            "Введите только число, например: 52 или 52.5"
        )
        return

    if weight < 0:
        await message.answer(
            "Вес не может быть отрицательным.\n"
            "Введите вес ещё раз:"
        )
        return

    await state.update_data(weight=weight)
    await state.set_state(WorkoutState.choosing_reps)

    await message.answer(
        f"Вес сохранён: {weight} кг.\n"
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
    try:
        reps = int(message.text)
    except ValueError:
        await message.answer(
            "Не понял количество повторений.\n"
            "Введите целое число, например: 8 или 12"
        )
        return

    if reps <= 0:
        await message.answer(
            "Количество повторений должно быть больше нуля.\n"
            "Введите ещё раз:"
        )
        return

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

            warmup_text = ""

            if next_exercise["warmup"]:
                warmup_text = "\n".join(
                    f"Разминка: {item['weight']} кг × {item['reps']}"
                    for item in next_exercise["warmup"]
                )

            await message.answer(
                f"Упражнение завершено.\n\n"
                f"Следующее упражнение — {next_exercise['name']}.\n"
                f"{warmup_text}\n\n"
                f"Подход №1.\n"
                f"Введите вес в кг:"
            )

        else:
            finish_workout(workout_id)

            await message.answer(
                f"Тренировка {workout} завершена! 💪"
            )

            await state.clear()