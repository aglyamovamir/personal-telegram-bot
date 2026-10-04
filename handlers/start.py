from datetime import datetime
from email.mime import message

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import CallbackQuery, Message

from database.db import (
    create_workout,
    save_set,
    finish_workout,
    cancel_workout,
    get_last_workout_by_type,
    get_last_workout,
    get_workout_sets,
    get_last_exercise_sets,
)

from keyboards.main_menu import main_menu
from keyboards.workout_menu import workout_menu
from states.workout import WorkoutState
from workouts.program import WORKOUTS
from workouts.parser import parse_exercise_input, format_parsed_sets
from handlers.daily import morning_start, evening_start
from handlers.today import today_handler
from keyboards.history_menu import history_menu

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
    await message.answer(
        "Какую тренировку посмотреть?",
        reply_markup=history_menu
    )

@router.callback_query(
    lambda callback: callback.data.startswith("history_")
)
async def history_selected(callback: CallbackQuery):
    workout_type = callback.data.replace("history_", "")

    workout = get_last_workout_by_type(workout_type)

    if workout is None:
        await callback.message.answer(
            f"Завершённых тренировок типа {workout_type} пока нет."
        )
        await callback.answer()
        return

    workout_id, workout_type, started_at, finished_at = workout

    sets = get_workout_sets(workout_id)

    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)

    text = (
        f"📊 Последняя тренировка {workout_type}\n\n"
        f"Начало: {started.strftime('%d.%m.%Y %H:%M')}\n"
        f"Окончание: {finished.strftime('%d.%m.%Y %H:%M')}\n"
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
            text += (
                f"Разминка {warmup_number}: "
                f"{weight:g} кг × {reps}\n"
            )
        else:
            working_number += 1
            text += (
                f"Рабочий {working_number}: "
                f"{weight:g} кг × {reps}\n"
            )

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

@router.message(Command("cancel"))
async def cancel_current_workout(message: Message, state):
    data = await state.get_data()

    workout_id = data.get("workout_id")

    if workout_id is None:
        await message.answer(
            "Сейчас нет активной тренировки."
        )
        return

    cancel_workout(workout_id)
    await state.clear()

    await message.answer(
        "❌ Тренировка отменена.\n"
        "Все введённые подходы удалены."
    )

    await message.answer(
        "Главное меню:",
        reply_markup=main_menu
    )


@router.message(Command("exit"))
async def exit_current_workout(message: Message, state):
    data = await state.get_data()

    workout_id = data.get("workout_id")

    if workout_id is None:
        await message.answer(
            "Сейчас нет активной тренировки."
        )
        return

    finish_workout(workout_id)
    await state.clear()

    await message.answer(
        "🚪 Вышли из тренировки.\n"
        "Введённые подходы сохранены."
    )

    await message.answer(
        "Главное меню:",
        reply_markup=main_menu
    )

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
    )

    await state.set_state(WorkoutState.waiting_for_exercise_input)

    previous_sets = get_last_exercise_sets(first_exercise["name"])

    previous_text = ""

    if previous_sets:
        previous_text = "\nПрошлая тренировка:\n"

        for weight, reps in previous_sets:
            previous_text += f"{weight:g} кг × {reps}\n"

    await callback.message.answer(
        f"Выбрана тренировка {workout_name}.\n\n"
        f"Упражнение — {first_exercise['name']}.\n"
        f"{previous_text}\n"
        f"Введите все подходы одним сообщением.\n\n"
        f"Пример:\n"
        f"20 10\n"
        f"50 10\n\n"
        f"60 9 3"
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

    await state.update_data(
        postponed_exercises=postponed_exercises
    )

    await callback.message.answer(
        f"Упражнение отложено.\n\n"
        f"Идём дальше по программе."
    )

    await move_to_next_exercise(
        callback.message,
        state,
        prefer_postponed=False
    )

    await callback.answer()

@router.message(WorkoutState.waiting_for_exercise_input)
async def process_exercise_input(message: Message, state):
    try:
        sets = parse_exercise_input(message.text)
    except ValueError as e:
        await message.answer(
            f"❌ Не удалось распознать подходы.\n\n"
            f"{e}\n\n"
            f"Пример правильного ввода:\n"
            f"20 10\n"
            f"50 10\n\n"
            f"60 9 3"
        )
        return

    data = await state.get_data()

    workout_id = data["workout_id"]
    exercise = data["exercise"]

    set_number = 1

    for item in sets:
        save_set(
            workout_id=workout_id,
            exercise=exercise,
            set_number=set_number,
            weight=item["weight"],
            reps=item["reps"],
            is_warmup=item["is_warmup"],
        )

        set_number += 1

    await message.answer(
        f"✅ {exercise} сохранено.\n\n"
        f"{format_parsed_sets(sets)}"
    )

    await state.update_data(
        set_number=set_number
    )

    await move_to_next_exercise(message, state)

async def move_to_next_exercise(message: Message, state, prefer_postponed=True):
    data = await state.get_data()

    workout = data["workout"]
    workout_id = data["workout_id"]

    postponed_exercises = data.get("postponed_exercises", [])
    program_progress_index = data.get("program_progress_index", 0)

    next_exercise_index = None

    # При обычном переходе после выполнения:
    # сначала возвращаем отложенные упражнения.
    if prefer_postponed and postponed_exercises:
        next_exercise_index = postponed_exercises.pop(0)

        await state.update_data(
            postponed_exercises=postponed_exercises
        )

    else:
        # Иначе продолжаем основную программу.
        next_exercise_index = program_progress_index + 1

        if next_exercise_index < len(WORKOUTS[workout]):
            await state.update_data(
                program_progress_index=next_exercise_index
            )

        # Основная программа закончилась.
        else:
            # Если остались отложенные — возвращаемся к ним.
            if postponed_exercises:
                next_exercise_index = postponed_exercises.pop(0)

                await state.update_data(
                    postponed_exercises=postponed_exercises
                )
            else:
                finish_workout(workout_id)

                await message.answer(
                    f"Тренировка {workout} завершена! 💪"
                )

                await state.clear()
                return

    next_exercise = WORKOUTS[workout][next_exercise_index]

    await state.update_data(
        exercise_index=next_exercise_index,
        exercise=next_exercise["name"],
    )

    previous_sets = get_last_exercise_sets(next_exercise["name"])

    previous_text = ""

    if previous_sets:
        previous_text = "\nПрошлая тренировка:\n"

        for weight, reps in previous_sets:
            previous_text += f"{weight:g} кг × {reps}\n"

    await state.set_state(WorkoutState.waiting_for_exercise_input)

    await message.answer(
        f"Следующее упражнение — {next_exercise['name']}.\n"
        f"{previous_text}\n"
        f"Введите все подходы одним сообщением.\n\n"
        f"Пример:\n"
        f"20 10\n"
        f"50 10\n\n"
        f"60 9 3"
    )

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
