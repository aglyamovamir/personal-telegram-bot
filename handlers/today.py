"""Compatibility alias: /today now opens workout analytics."""
from aiogram import Router
from aiogram.filters import Command
from handlers.analytics import show_menu

router = Router()


@router.message(Command('today'))
async def today_handler(message, user_id: int, state=None):
    await show_menu(message, user_id, state)
