from datetime import datetime
from email.mime import message

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from database.db import (
    create_workout,
    save_set,
    finish_workout,
    get_last_workout,
    get_workout_sets,
    get_last_exercise_sets,
)

from keyboards.set_type import (
    set_type_menu,
    working_set_menu,
    exercise_finished_menu,
)
from keyboards.main_menu import main_menu
from keyboards.workout_menu import workout_menu
from states.workout import WorkoutState
from workouts.program import WORKOUTS
from handlers.daily import morning_start, evening_start
from handlers.today import today_handler

router = Router()


@router.message(CommandStart())
async def start_command(message: Message):
    await message.answer(
        "Привет! Что будем делать?",
        reply_markup=main_menu
    )

@router.message(F.text == "🌅 Утро")
async def morning_menu_button(message: Message, state):
    await morning_start(message, state)


@router.message(F.text == "🌙 Вечер")
async def evening_menu_button(message: Message, state):
    await evening_start(message, state)


@router.message(F.text == "📊 Сегодня")
async def today_menu_button(message: Message):
    await today_handler(message)


@router.message(F.text == "🏋️ Тренировка")
async def training_menu_button(message: Message):
    await message.answer(
        "Выберите тренировку:",
        reply_markup=workout_menu
    )


@router.message(F.text == "📚 История")
async def history_menu_button(message: Message):
    workout = get_last_workout()

    if workout is None:
        await message.answer(
            "Завершённых тренировок пока нет."
        )
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

    warmup_number = 0
    working_number = 0

    for exercise, set_number, is_warmup, weight, reps in sets:
        if exercise != current_exercise:
            current_exercise = exercise
            warmup_number = 0
            working_number = 0
            text += f"\n<b>{exercise}</b>\n"

        if is_warmup:
            warmup_number += 1
            text += f"Разминка {warmup_number}: {weight:g} кг × {reps}\n"
        else:
            working_number += 1
            text += f"Рабочий {working_number}: {weight:g} кг × {reps}\n"

    await message.answer(
        text,
        parse_mode="HTML"
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
        program_progress_index=0,
        postponed_exercises=[],
        exercise=first_exercise["name"],
        set_number=1,
        working_set_number=0,
        warmup_count=0,
        is_warmup=None
    )

    previous_sets = get_last_exercise_sets(first_exercise["name"])

    previous_text = ""

    if previous_sets:
        previous_text = "\nПрошлая тренировка:\n"

        for weight, reps in previous_sets:
            previous_text += f"{weight:g} кг × {reps}\n"

    await callback.message.answer(
        f"Выбрана тренировка {workout_name}.\n"
        f"Первое упражнение — {first_exercise['name']}.\n"
        f"{previous_text}\n"
        f"Выберите тип первого подхода:",
        reply_markup=set_type_menu
    )

    await callback.answer()

@router.callback_query(lambda callback: callback.data == "set_warmup")
async def choose_warmup(callback: CallbackQuery, state):
    data = await state.get_data()

    warmup_count = data.get("warmup_count", 0)

    if warmup_count >= 3:
        await callback.answer(
            "Максимум 3 разминочных подхода.",
            show_alert=True
        )
        return

    await state.update_data(
        is_warmup=True,
        warmup_count=warmup_count + 1
    )

    await state.set_state(WorkoutState.choosing_weight)

    await callback.message.answer(
        f"🔥 Разминочный подход №{warmup_count + 1}.\n\n"
        f"Введите вес в кг:"
    )

    await callback.answer()

@router.callback_query(lambda callback: callback.data == "set_working")
async def choose_working(callback: CallbackQuery, state):
    data = await state.get_data()

    working_set_number = data.get("working_set_number", 0) + 1

    await state.update_data(
        is_warmup=False,
        working_set_number=working_set_number
    )

    await state.set_state(WorkoutState.choosing_weight)

    await callback.message.answer(
        f"💪 Рабочий подход №{working_set_number}.\n\n"
        f"Введите вес в кг:"
    )

    await callback.answer()

@router.callback_query(lambda callback: callback.data == "postpone_exercise")
async def postpone_exercise(callback: CallbackQuery, state):
    data = await state.get_data()

    workout = data["workout"]
    exercise_index = data["exercise_index"]

    postponed_exercises = data.get("postponed_exercises", [])

    if exercise_index not in postponed_exercises:
        postponed_exercises.append(exercise_index)

    next_exercise_index = exercise_index + 1

    await state.update_data(
        program_progress_index=next_exercise_index
    )

    await state.update_data(
        postponed_exercises=postponed_exercises
    )

    if next_exercise_index >= len(WORKOUTS[workout]):
        await callback.message.answer(
            "Больше упражнений в программе нет.\n"
            "Переходим к отложенным упражнениям."
        )

        next_exercise_index = postponed_exercises.pop(0)

        await state.update_data(
            postponed_exercises=postponed_exercises
        )

    next_exercise = WORKOUTS[workout][next_exercise_index]

    await state.update_data(
        exercise_index=next_exercise_index,
        exercise=next_exercise["name"],
        set_number=1,
        warmup_count=0,
        working_set_number=0,
        is_warmup=None
    )

    previous_sets = get_last_exercise_sets(next_exercise["name"])

    previous_text = ""

    if previous_sets:
        previous_text = "\nПрошлая тренировка:\n"

        for weight, reps in previous_sets:
            previous_text += f"{weight:g} кг × {reps}\n"

    await callback.message.answer(
        f"Упражнение отложено.\n\n"
        f"Следующее упражнение — {next_exercise['name']}.\n"
        f"{previous_text}\n"
        f"Выберите тип первого подхода:",
        reply_markup=set_type_menu
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

    is_warmup = data.get("is_warmup", False)
    warmup_count = data.get("warmup_count", 0)
    working_set_number = data.get("working_set_number", 0)

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
        reps=reps,
        is_warmup=is_warmup
    )

    if is_warmup:
        await message.answer(
            f"🔥 Разминочный подход №{warmup_count} сохранён:\n\n"
            f"{exercise}\n"
            f"{weight:g} кг × {reps}\n\n"
            f"Что дальше?",
            reply_markup=set_type_menu
        )

        await state.update_data(
            set_number=set_number + 1
        )

        return

    await message.answer(
        f"💪 Рабочий подход №{working_set_number} сохранён:\n\n"
        f"{exercise}\n"
        f"{weight:g} кг × {reps}"
    )

    await state.update_data(
        set_number=set_number + 1
    )

    await message.answer(
        "Что дальше?",
        reply_markup=working_set_menu
    )

@router.callback_query(lambda callback: callback.data == "finish_exercise")
async def finish_exercise(callback: CallbackQuery, state):
    await callback.message.answer(
        "Упражнение завершено.\n\n"
        "Что дальше?",
        reply_markup=exercise_finished_menu
    )

    await callback.answer()

@router.callback_query(lambda callback: callback.data == "next_exercise")
async def next_exercise(callback: CallbackQuery, state):
    data = await state.get_data()

    workout = data["workout"]
    postponed_exercises = data.get("postponed_exercises", [])
    program_progress_index = data.get("program_progress_index", 0)

    # Сначала возвращаемся к отложенному упражнению
    if postponed_exercises:
        next_exercise_index = postponed_exercises.pop(0)

        await state.update_data(
            postponed_exercises=postponed_exercises
        )

    else:
        # Иначе двигаемся дальше по основной программе
        next_exercise_index = program_progress_index + 1

        if next_exercise_index >= len(WORKOUTS[workout]):
            workout_id = data["workout_id"]

            finish_workout(workout_id)

            await callback.message.answer(
                f"Тренировка {workout} завершена! 💪"
            )

            await state.clear()
            await callback.answer()
            return

        await state.update_data(
            program_progress_index=next_exercise_index
        )

    next_exercise = WORKOUTS[workout][next_exercise_index]

    await state.update_data(
        exercise_index=next_exercise_index,
        exercise=next_exercise["name"],
        set_number=1,
        warmup_count=0,
        working_set_number=0,
        is_warmup=None
    )

    previous_sets = get_last_exercise_sets(next_exercise["name"])

    previous_text = ""

    if previous_sets:
        previous_text = "\nПрошлая тренировка:\n"

        for weight, reps in previous_sets:
            previous_text += f"{weight:g} кг × {reps}\n"

    await callback.message.answer(
        f"Следующее упражнение — {next_exercise['name']}.\n"
        f"{previous_text}\n"
        f"Выберите тип первого подхода:",
        reply_markup=set_type_menu
    )

    await callback.answer()

@router.callback_query(lambda callback: callback.data == "finish_workout")
async def finish_workout_button(callback: CallbackQuery, state):
    data = await state.get_data()

    workout_id = data["workout_id"]
    workout = data["workout"]

    finish_workout(workout_id)

    await callback.message.answer(
        f"Тренировка {workout} завершена! 💪"
    )

    await state.clear()
    await callback.answer()