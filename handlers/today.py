from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.db import get_daily_metrics, has_workout_on_date


router = Router()


@router.message(Command("today"))
async def today_handler(message: Message):
    today = date.today().isoformat()

    metrics = get_daily_metrics(today)
    workout_exists = has_workout_on_date(today)

    lines = [
        f"📊 Сегодня — {date.today().strftime('%d.%m.%Y')}",
        ""
    ]

    if metrics:
        (
            _date,
            sleep_start,
            wake_time,
            sleep_duration_minutes,
            sleep_score,
            productivity,
            stress,
            mood,
            steps,
            screen_time_minutes
        ) = metrics

        lines.append("🌅 Сон")

        if sleep_start:
            lines.append(f"Заснул: {sleep_start}")

        if wake_time:
            lines.append(f"Проснулся: {wake_time}")

        if sleep_duration_minutes is not None:
            hours = sleep_duration_minutes // 60
            minutes = sleep_duration_minutes % 60
            lines.append(f"Сон: {hours} ч {minutes} мин")

        if sleep_score is not None:
            lines.append(f"Sleep Score: {sleep_score}/100")

        lines.append("")

        if any(
            value is not None
            for value in (
                productivity,
                stress,
                mood,
                steps,
                screen_time_minutes
            )
        ):
            lines.append("🌙 Итоги дня")

            if productivity is not None:
                lines.append(f"Продуктивность: {productivity}/10")

            if stress is not None:
                lines.append(f"Стресс: {stress}/10")

            if mood is not None:
                lines.append(f"Настроение: {mood}/10")

            if steps is not None:
                lines.append(f"Шаги: {steps}")

            if screen_time_minutes is not None:
                hours = screen_time_minutes // 60
                minutes = screen_time_minutes % 60
                lines.append(
                    f"Экранное время: {hours} ч {minutes} мин"
                )

            lines.append("")
    else:
        lines.append("🌅 Сон")
        lines.append("⏳ Утренние данные ещё не заполнены.")
        lines.append("")

    lines.append("🏋️ Тренировка")

    if workout_exists:
        lines.append("✅ Сегодня была тренировка.")
    else:
        lines.append("⏳ Сегодня тренировки ещё не было.")

    await message.answer("\n".join(lines))