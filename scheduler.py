"""Personal reminder jobs. Notifications never change FSM state."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.exceptions import TelegramForbiddenError

from access import allowed_telegram_ids
from database.db import get_user, get_user_settings, notification_users

scheduler = AsyncIOScheduler(timezone='Europe/Moscow')
logger = logging.getLogger(__name__)


async def send_reminder(bot, user_id, period):
    user = get_user(user_id)
    settings = get_user_settings(user_id)
    offset = 0 if period == 'morning' else 1
    if (not settings or not settings[offset + 2]
            or user['telegram_user_id'] not in allowed_telegram_ids()
            or user['private_chat_id'] != user['telegram_user_id']):
        return
    text = ('🌅 Доброе утро! Начать опрос: /morning'
            if period == 'morning' else '🌙 Вечерний опрос: /evening')
    try:
        await bot.send_message(user['private_chat_id'], text)
    except TelegramForbiddenError:
        logger.warning('Reminder recipient blocked bot (internal user %s)', user_id)


def reschedule_notifications(bot, user_id):
    user = get_user(user_id)
    settings = get_user_settings(user_id)
    for offset, period in enumerate(('morning', 'evening')):
        job_id = f'{period}:{user_id}'
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        if (not settings or not settings[offset + 2]
                or user['telegram_user_id'] not in allowed_telegram_ids()
                or user['private_chat_id'] != user['telegram_user_id']):
            continue
        hour, minute = map(int, settings[offset].split(':'))
        scheduler.add_job(send_reminder, 'cron', hour=hour, minute=minute,
                          timezone=user['timezone'], args=[bot, user_id, period],
                          id=job_id, replace_existing=True, coalesce=True, max_instances=1)


def schedule_all_notifications(bot):
    for (user_id,) in notification_users():
        reschedule_notifications(bot, user_id)
