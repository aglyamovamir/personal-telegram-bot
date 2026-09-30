from aiogram.fsm.state import State, StatesGroup


class WorkoutState(StatesGroup):
    choosing_weight = State()
    choosing_reps = State()