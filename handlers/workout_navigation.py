"""Keep an in-memory workout input while switching to program management."""
from copy import deepcopy


WORKOUT_KEYS = {
    'workout_id', 'exercise_index', 'revision', 'correction_position',
    'correction_token', 'correction_input', 'exercise_input',
    'delete_revision', 'delete_token', 'delete_fingerprint', 'delete_origin',
}


async def park_workout_state(state):
    current = await state.get_state()
    if current and current.startswith('WorkoutState:'):
        data = await state.get_data()
        data['parked_workout'] = dict(state=current, data=deepcopy(
            {key: value for key, value in data.items() if key in WORKOUT_KEYS}))
        await state.set_data(data)
        await state.set_state(None)


async def clear_workout_state(state, workout_id):
    """Remove only caches belonging to this workout, preserving editor/daily data."""
    data = await state.get_data()
    current = await state.get_state()
    if data.get('workout_id') == workout_id:
        for key in WORKOUT_KEYS:
            data.pop(key, None)
        if current and current.startswith('WorkoutState:'):
            await state.set_state(None)
    if data.get('parked_workout', {}).get('data', {}).get('workout_id') == workout_id:
        data.pop('parked_workout', None)
    if data.get('unfinished', {}).get('workout_id') == workout_id:
        data.pop('unfinished', None)
    await state.set_data(data)
