import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from database.db import (
    init_db,
    DB_PATH,
    get_last_workout_by_type,
    get_workout_sets,
)
from handlers.debug import router as debug_router
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
dp.include_router(debug_router)

async def main():
    init_db()

    print("DB_PATH:", DB_PATH)

    for workout_type in ("A", "B", "C"):
        workout = get_last_workout_by_type(workout_type)
        print(f"HISTORY {workout_type}:", workout)

        if workout:
            print(f"SETS {workout_type}:", get_workout_sets(workout[0]))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())