from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from keyboards.main_menu import main_menu


router = Router()


@router.message(CommandStart())
async def start_command(message: Message):
    await message.answer(
        "Привет! Что будем делать?",
        reply_markup=main_menu
    )


@router.callback_query(lambda callback: callback.data == "training")
async def training_button(callback: CallbackQuery):
    await callback.message.answer("Выберите тренировку: A, Б или В")
    await callback.answer()