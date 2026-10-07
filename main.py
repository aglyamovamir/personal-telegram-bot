import asyncio
import os

from scheduler import scheduler, schedule_morning, schedule_evening
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from database.db import init_db

from handlers.start import router as start_router
from handlers.daily import router as daily_router
from handlers.today import router as today_router


load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

dp.include_router(start_router)
dp.include_router(daily_router)
dp.include_router(today_router)


async def main():
    init_db()
    scheduler.start()
    schedule_morning(bot, dp)
    schedule_evening(bot, dp)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())