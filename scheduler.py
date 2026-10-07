from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from database.db import get_bot_settings
from states.daily import DailyMorningState, DailyEveningState


scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


async def send_morning_message(bot: Bot, dp: Dispatcher):
    settings = get_bot_settings()

    if not settings:
        return

    chat_id, morning_time, evening_time = settings

    context = FSMContext(
        storage=dp.storage,
        key=StorageKey(
            bot_id=bot.id,
            chat_id=chat_id,
            user_id=chat_id,
        ),
    )

    await context.set_state(DailyMorningState.sleep_start)

    await bot.send_message(
        chat_id,
        "🌅 Доброе утро!\n\n"
        "Во сколько ты лёг спать?\n"
        "Например: 23:40"
    )


def schedule_morning(bot: Bot, dp: Dispatcher):
    settings = get_bot_settings()

    if not settings:
        return

    chat_id, morning_time, evening_time = settings

    hour, minute = map(int, morning_time.split(":"))

    scheduler.add_job(
        send_morning_message,
        "cron",
        hour=hour,
        minute=minute,
        args=[bot, dp],
        id="morning_message",
        replace_existing=True,
    )

async def send_evening_message(bot: Bot, dp: Dispatcher):
    settings = get_bot_settings()

    if not settings:
        return

    chat_id, morning_time, evening_time = settings

    context = FSMContext(
        storage=dp.storage,
        key=StorageKey(
            bot_id=bot.id,
            chat_id=chat_id,
            user_id=chat_id,
        ),
    )

    await context.set_state(DailyEveningState.productivity)

    await bot.send_message(
        chat_id,
        "🌙 Вечерний опрос\n\n"
        "Оцени продуктивность сегодня от 1 до 10."
    )


def schedule_evening(bot: Bot, dp: Dispatcher):
    settings = get_bot_settings()

    if not settings:
        return

    chat_id, morning_time, evening_time = settings

    hour, minute = map(int, evening_time.split(":"))

    scheduler.add_job(
        send_evening_message,
        "cron",
        hour=hour,
        minute=minute,
        args=[bot, dp],
        id="evening_message",
        replace_existing=True,
    )