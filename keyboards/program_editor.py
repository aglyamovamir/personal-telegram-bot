"""Compact opaque callbacks; actual actions stay in the user's FSM."""
from uuid import uuid4

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def editor_keyboard(rows):
    token = uuid4().hex[:12]
    actions = []
    keyboard = []
    for row in rows:
        buttons = []
        for label, action in row:
            callback = f'pe:{token}:{len(actions)}'
            if len(callback.encode('utf-8')) > 64:
                raise ValueError('Callback is too long')
            actions.append(action)
            buttons.append(InlineKeyboardButton(text=label[:120], callback_data=callback))
        keyboard.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=keyboard), token, actions
