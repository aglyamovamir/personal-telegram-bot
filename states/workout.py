from aiogram.fsm.state import State, StatesGroup


class WorkoutState(StatesGroup):
    waiting_for_exercise_input = State()