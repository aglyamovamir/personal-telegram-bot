from aiogram.fsm.state import State, StatesGroup


class ProgramEditor(StatesGroup):
    screen = State()
    text = State()
