from datetime import date, datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database.db import (
    save_morning_metrics,
    save_evening_metrics,
    save_bot_settings,
    get_bot_settings,
    get_daily_metrics,
    update_daily_metric,
)

from google_sheets import sync_daily_metrics
from states.daily import (
    DailyMorningState,
    DailyEveningState,
    DailyScheduleState,
    DailyEditState,
)
from scheduler import reschedule_notifications

router = Router()

def configure_scheduler(bot, dp):
    global _scheduler_bot, _scheduler_dp
    _scheduler_bot = bot
    _scheduler_dp = dp

_scheduler_bot = None
_scheduler_dp = None

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

@router.message(Command("change_time"))
async def change_time_start(message: Message, state: FSMContext):
    settings = get_bot_settings()

    if settings:
        chat_id, morning_time, evening_time = settings
        current = (
            f"Текущее расписание:\n"
            f"🌅 Утро: {morning_time}\n"
            f"🌙 Вечер: {evening_time}\n\n"
        )
    else:
        current = ""

    await state.set_state(DailyScheduleState.morning_time)
    await message.answer(
        f"{current}"
        "Во сколько присылать утренний опрос?\n"
        "Например: 07:30"
    )


@router.message(DailyScheduleState.morning_time)
async def schedule_morning_time(message: Message, state: FSMContext):
    value = message.text.strip()

    try:
        hour, minute = map(int, value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(
            "Не понял время.\n"
            "Введи в формате ЧЧ:ММ, например: 07:30"
        )
        return

    await state.update_data(morning_time=value)
    await state.set_state(DailyScheduleState.evening_time)

    await message.answer(
        "Во сколько присылать вечерний опрос?\n"
        "Например: 22:00"
    )


@router.message(DailyScheduleState.evening_time)
async def schedule_evening_time(message: Message, state: FSMContext):
    value = message.text.strip()

    try:
        hour, minute = map(int, value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(
            "Не понял время.\n"
            "Введи в формате ЧЧ:ММ, например: 22:00"
        )
        return

    data = await state.get_data()

    save_bot_settings(
        chat_id=message.chat.id,
        morning_time=data["morning_time"],
        evening_time=value,
    )
    
    if _scheduler_bot is not None and _scheduler_dp is not None:
        reschedule_notifications(_scheduler_bot, _scheduler_dp)

    await state.clear()

    await message.answer(
        "✅ Расписание сохранено!\n\n"
        f"🌅 Утро: {data['morning_time']}\n"
        f"🌙 Вечер: {value}"
    )


@router.message(Command("edit_day"))
async def edit_day_start(message: Message, state: FSMContext):
    await state.set_state(DailyEditState.date)

    await message.answer(
        "✏️ Редактирование дневных показателей\n\n"
        "Введи дату, данные за которую хочешь изменить.\n"
        "Формат: ДД.ММ.ГГГГ\n"
        "Например: 01.11.2026"
    )


@router.message(DailyEditState.date)
async def edit_day_date(message: Message, state: FSMContext):
    try:
        selected_date = datetime.strptime(
            message.text.strip(), "%d.%m.%Y"
        ).date().isoformat()
    except (ValueError, AttributeError):
        await message.answer(
            "Некорректная дата.\n"
            "Используй формат ДД.ММ.ГГГГ, например: 01.11.2026"
        )
        return

    metrics = get_daily_metrics(selected_date)

    if metrics is None:
        await message.answer(
            "За эту дату нет сохранённых показателей."
        )
        await state.clear()
        return

    await state.update_data(selected_date=selected_date)
    await state.set_state(DailyEditState.field)

    labels = [
        "Время засыпания",
        "Время пробуждения",
        "Продолжительность сна",
        "Показатель сна",
        "Продуктивность",
        "Стресс",
        "Настроение",
        "Количество шагов",
        "Экранное время",
    ]

    values = metrics[1:]

    lines = []
    for i, (label, value) in enumerate(zip(labels, values), start=1):
        if value is None:
            display_value = "Не заполнено"
        elif i in (3, 9):
            display_value = f"{value // 60}:{value % 60:02d}"
        elif i in (4, 5, 6, 7):
            display_value = str(value)
        elif i == 8:
            display_value = str(value)
        else:
            display_value = str(value)

        lines.append(f"{i}. {label}: {display_value}")

    await message.answer(
        f"Данные за {datetime.strptime(selected_date, '%Y-%m-%d').strftime('%d.%m.%Y')}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        "Введи номер показателя, который хочешь изменить (1–9)."
    )


@router.message(DailyEditState.field)
async def edit_day_field(message: Message, state: FSMContext):
    try:
        choice = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer("Введи номер показателя от 1 до 9.")
        return

    fields = {
        1: ("sleep_start", "Время засыпания", "ЧЧ:ММ, например 23:40"),
        2: ("wake_time", "Время пробуждения", "ЧЧ:ММ, например 08:10"),
        3: ("sleep_duration_minutes", "Продолжительность сна", "ЧЧ:ММ, например 7:42"),
        4: ("sleep_score", "Показатель сна", "Число от 0 до 100"),
        5: ("productivity", "Продуктивность", "Число от 1 до 10"),
        6: ("stress", "Стресс", "Число от 1 до 10"),
        7: ("mood", "Настроение", "Число от 1 до 10"),
        8: ("steps", "Количество шагов", "Целое неотрицательное число"),
        9: ("screen_time_minutes", "Экранное время", "ЧЧ:ММ, например 5:12"),
    }

    if choice not in fields:
        await message.answer("Введи номер показателя от 1 до 9.")
        return

    field, label, instruction = fields[choice]

    await state.update_data(edit_field=field)
    await state.set_state(DailyEditState.value)

    await message.answer(
        f"Выбран показатель: {label}\n\n"
        f"Введи новое значение.\n"
        f"Формат: {instruction}"
    )


@router.message(DailyEditState.value)
async def edit_day_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data["edit_field"]
    value = message.text.strip()

    try:
        if field in ("sleep_start", "wake_time"):
            hour, minute = map(int, value.split(":"))

            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError

            new_value = f"{hour:02d}:{minute:02d}"

        elif field in ("sleep_duration_minutes", "screen_time_minutes"):
            hour, minute = map(int, value.split(":"))

            if hour < 0 or not (0 <= minute <= 59):
                raise ValueError

            new_value = hour * 60 + minute

        else:
            new_value = int(value)

            if field == "sleep_score" and not 0 <= new_value <= 100:
                raise ValueError

            if field in ("productivity", "stress", "mood") and not 1 <= new_value <= 10:
                raise ValueError

            if field == "steps" and new_value < 0:
                raise ValueError

    except (ValueError, AttributeError):
        await message.answer(
            "Некорректное значение.\n"
            "Проверь формат и попробуй ещё раз."
        )
        return

    selected_date = data["selected_date"]

    updated = update_daily_metric(
        date=selected_date,
        field=field,
        value=new_value,
    )

    if not updated:
        await message.answer(
            "Не удалось обновить данные: запись не найдена."
        )
        await state.clear()
        return

    await state.clear()

    try:
        sync_daily_metrics(selected_date)
        sync_message = "Данные также синхронизированы с Google Sheets."
    except Exception:
        sync_message = (
            "Данные сохранены в базе, но синхронизация с Google Sheets "
            "не удалась."
        )

    await message.answer(
        "✅ Показатель успешно обновлён!\n\n"
        f"Дата: {datetime.strptime(selected_date, '%Y-%m-%d').strftime('%d.%m.%Y')}\n"
        f"Новое значение: {value}\n\n"
        f"{sync_message}"
    )
