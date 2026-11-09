import asyncio
import os

from dotenv import load_dotenv

# Configuration must be loaded before importing modules that read DB_PATH.
load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from access import PrivateAccessMiddleware, allowed_telegram_ids
from database.db import init_db
from handlers.start import router as start_router
from handlers.daily import router as daily_router, configure_scheduler
from handlers.today import router as today_router
from handlers.analytics import router as analytics_router
from handlers.program_editor import router as program_editor_router
from scheduler import scheduler, schedule_all_notifications


async def main():
    init_db()  # Validate only; legacy schemas require the explicit offline tool.
    allowed_telegram_ids()  # Refuse startup with missing/invalid access policy.
    bot = Bot(token=os.environ['BOT_TOKEN'])
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dp.message.outer_middleware(PrivateAccessMiddleware())
    dp.callback_query.outer_middleware(PrivateAccessMiddleware())
    dp.include_router(analytics_router)
    dp.include_router(start_router)
    dp.include_router(program_editor_router)
    dp.include_router(daily_router)
    dp.include_router(today_router)
    configure_scheduler(bot)
    scheduler.start()
    try:
        schedule_all_notifications(bot)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await dp.storage.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
