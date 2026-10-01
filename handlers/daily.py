from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database.db import save_morning_metrics, save_evening_metrics
from google_sheets import sync_daily_metrics
from states.daily import DailyMorningState, DailyEveningState


router = Router()


@router.message(Command("morning"))
async def morning_start(message: Message, state: FSMContext):
    await state.set_state(DailyMorningState.sleep_start)

    await message.answer(
        "🌅 Доброе утро!\n\n"
        "Во сколько ты уснул?\n"
        "Например: 23:40"
    )


@router.message(DailyMorningState.sleep_start)
async def morning_sleep_start(message: Message, state: FSMContext):
    value = message.text.strip()

    try:
        hour, minute = map(int, value.split(":"))

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError

    except (ValueError, AttributeError):
        await message.answer(
            "Не понял время.\n"
            "Введи в формате ЧЧ:ММ, например: 23:40"
        )
        return

    await state.update_data(sleep_start=value)
    await state.set_state(DailyMorningState.wake_time)

    await message.answer(
        "Во сколько ты проснулся?\n"
        "Например: 08:10"
    )


@router.message(DailyMorningState.wake_time)
async def morning_wake_time(message: Message, state: FSMContext):
    value = message.text.strip()

    try:
        hour, minute = map(int, value.split(":"))

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError

    except (ValueError, AttributeError):
        await message.answer(
            "Не понял время.\n"
            "Введи в формате ЧЧ:ММ, например: 08:10"
        )
        return

    await state.update_data(wake_time=value)
    await state.set_state(DailyMorningState.sleep_duration)

    await message.answer(
        "Сколько сна показали часы?\n"
        "Введи в формате ЧЧ:ММ, например: 7:42"
    )


@router.message(DailyMorningState.sleep_duration)
async def morning_sleep_duration(message: Message, state: FSMContext):
    value = message.text.strip()

    try:
        hour, minute = map(int, value.split(":"))

        if hour < 0 or minute < 0 or minute > 59:
            raise ValueError

    except (ValueError, AttributeError):
        await message.answer(
            "Не понял продолжительность сна.\n"
            "Введи в формате ЧЧ:ММ, например: 7:42"
        )
        return

    sleep_duration_minutes = hour * 60 + minute

    await state.update_data(
        sleep_duration_minutes=sleep_duration_minutes
    )

    await state.set_state(DailyMorningState.sleep_score)

    await message.answer(
        "Какой показатель сна показали часы?\n"
        "Введи число от 0 до 100."
    )


@router.message(DailyMorningState.sleep_score)
async def morning_sleep_score(message: Message, state: FSMContext):
    try:
        score = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer(
            "Введи целое число от 0 до 100."
        )
        return

    if not 0 <= score <= 100:
        await message.answer(
            "Показатель должен быть от 0 до 100."
        )
        return

    data = await state.get_data()

    save_morning_metrics(
        date=date.today().isoformat(),
        sleep_start=data["sleep_start"],
        wake_time=data["wake_time"],
        sleep_duration_minutes=data["sleep_duration_minutes"],
        sleep_score=score
    )

    sync_daily_metrics(date.today().isoformat())

    hours = data["sleep_duration_minutes"] // 60
    minutes = data["sleep_duration_minutes"] % 60

    await message.answer(
        "✅ Утренние данные сохранены!\n\n"
        f"Заснул: {data['sleep_start']}\n"
        f"Проснулся: {data['wake_time']}\n"
        f"Сон по часам: {hours} ч {minutes} мин\n"
        f"Показатель сна: {score}/100"
    )

    await state.clear()

@router.message(Command("evening"))
async def evening_start(message: Message, state: FSMContext):
    await state.set_state(DailyEveningState.productivity)

    await message.answer(
        "🌙 Итоги дня\n\n"
        "Как оцениваешь свою продуктивность сегодня?\n"
        "Введи число от 1 до 10."
    )


@router.message(DailyEveningState.productivity)
async def evening_productivity(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer(
            "Введи целое число от 1 до 10."
        )
        return

    if not 1 <= value <= 10:
        await message.answer(
            "Продуктивность должна быть от 1 до 10."
        )
        return

    await state.update_data(productivity=value)
    await state.set_state(DailyEveningState.stress)

    await message.answer(
        "Какой уровень стресса сегодня?\n"
        "1 — минимальный, 10 — максимальный.\n\n"
        "Введи число от 1 до 10."
    )


@router.message(DailyEveningState.stress)
async def evening_stress(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer(
            "Введи целое число от 1 до 10."
        )
        return

    if not 1 <= value <= 10:
        await message.answer(
            "Стресс должен быть от 1 до 10."
        )
        return

    await state.update_data(stress=value)
    await state.set_state(DailyEveningState.mood)

    await message.answer(
        "Как оцениваешь своё настроение сегодня?\n"
        "Введи число от 1 до 10."
    )


@router.message(DailyEveningState.mood)
async def evening_mood(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer(
            "Введи целое число от 1 до 10."
        )
        return

    if not 1 <= value <= 10:
        await message.answer(
            "Настроение должно быть от 1 до 10."
        )
        return

    await state.update_data(mood=value)
    await state.set_state(DailyEveningState.steps)

    await message.answer(
        "Сколько шагов сегодня?\n"
        "Введи количество шагов, например: 9240"
    )


@router.message(DailyEveningState.steps)
async def evening_steps(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer(
            "Введи целое число, например: 9240"
        )
        return

    if value < 0:
        await message.answer(
            "Количество шагов не может быть отрицательным."
        )
        return

    await state.update_data(steps=value)
    await state.set_state(DailyEveningState.screen_time)

    await message.answer(
        "Какое экранное время сегодня?\n"
        "Введи в формате ЧЧ:ММ, например: 5:12"
    )


@router.message(DailyEveningState.screen_time)
async def evening_screen_time(message: Message, state: FSMContext):
    value = message.text.strip()

    try:
        hour, minute = map(int, value.split(":"))

        if hour < 0 or minute < 0 or minute > 59:
            raise ValueError

    except (ValueError, AttributeError):
        await message.answer(
            "Не понял экранное время.\n"
            "Введи в формате ЧЧ:ММ, например: 5:12"
        )
        return

    screen_time_minutes = hour * 60 + minute

    data = await state.get_data()

    save_evening_metrics(
        date=date.today().isoformat(),
        productivity=data["productivity"],
        stress=data["stress"],
        mood=data["mood"],
        steps=data["steps"],
        screen_time_minutes=screen_time_minutes
    )

    sync_daily_metrics(date.today().isoformat())

    hours = screen_time_minutes // 60
    minutes = screen_time_minutes % 60

    await message.answer(
        "✅ Вечерние данные сохранены!\n\n"
        f"Продуктивность: {data['productivity']}/10\n"
        f"Стресс: {data['stress']}/10\n"
        f"Настроение: {data['mood']}/10\n"
        f"Шаги: {data['steps']}\n"
        f"Экранное время: {hours} ч {minutes} мин"
    )

    await state.clear()