from aiogram.fsm.state import State, StatesGroup


class DailyMorningState(StatesGroup):
    sleep_start = State()
    wake_time = State()
    sleep_duration = State()
    sleep_score = State()


class DailyEveningState(StatesGroup):
    productivity = State()
    stress = State()
    mood = State()
    steps = State()
    screen_time = State()

class DailyScheduleState(StatesGroup):
    morning_time = State()
    evening_time = State()