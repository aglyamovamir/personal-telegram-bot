"""Private-chat access with explicitly enabled public registration."""
import os

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from database.db import WorkoutAccessError, get_or_create_user, get_active_workout


def open_registration_enabled():
    value = os.getenv('OPEN_REGISTRATION', 'false').strip().lower()
    if value not in {'true', 'false', '1', '0'}:
        raise RuntimeError('OPEN_REGISTRATION: используйте true или false')
    return value in {'true', '1'}


def allowed_telegram_ids():
    opened = open_registration_enabled()
    raw = os.getenv('ALLOWED_TELEGRAM_IDS', '')
    try:
        result = {int(value.strip()) for value in raw.split(',') if value.strip()}
    except ValueError as error:
        raise RuntimeError('ALLOWED_TELEGRAM_IDS: нужны Telegram ID через запятую') from error
    if (not result and not opened) or any(value <= 0 for value in result):
        raise RuntimeError('Задайте непустой ALLOWED_TELEGRAM_IDS; публичная регистрация отключена')
    return result


class PrivateAccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        callback = isinstance(event, CallbackQuery)
        message = event.message if callback else event
        sender = event.from_user  # Never callback.message.from_user (the bot).
        if (not isinstance(message, Message) or not sender or sender.is_bot
                or message.chat.type != 'private' or message.chat.id != sender.id):
            if callback:
                await event.answer('Доступно только в личном чате с ботом.', show_alert=True)
            elif isinstance(message, Message):
                await message.answer('Доступно только в личном чате с ботом.')
            return
        if not open_registration_enabled() and sender.id not in allowed_telegram_ids():
            await event.answer('Доступ пока закрыт.')
            return
        user = get_or_create_user(sender.id, message.chat.id)
        data['user_id'] = user['id']
        data['app_user'] = user
        state = data.get('state')
        current = await state.get_state() if state else None
        # Starting another dialog must not overwrite any active FSM flow.
        text = message.text or ''
        starts_dialog = (text.split('@')[0].split(' ')[0] in {
            '/morning', '/evening', '/change_time', '/edit_day'
        } or text in {'🌅 Утро', '🌙 Вечер'})
        if not callback and starts_dialog and (current or get_active_workout(user['id'])):
            await message.answer('Сначала закончите текущий ввод. Для тренировки: /exit или /cancel.')
            return
        # Unknown commands and menu texts should never be parsed as numeric input.
        known_commands = {'/start', '/help', '/today', '/analytics', '/cancel', '/exit', '/resume', '/programs'}
        if current and not callback and text.startswith('/') and not starts_dialog:
            command = text.split()[0].split('@')[0]
            if command not in known_commands:
                await message.answer('Команда недоступна во время текущего ввода.')
                return
        try:
            return await handler(event, data)
        except WorkoutAccessError as error:
            # Identical error for nonexistent, foreign, and closed workouts.
            await event.answer(str(error))
