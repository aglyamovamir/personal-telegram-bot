from database import db, programs
from database.import_legacy_program import import_legacy_program
from workouts.program import WORKOUTS


def seed_catalog():
    with db._connection(True) as c:
        for day in WORKOUTS.values():
            for exercise in day:
                c.execute('INSERT OR IGNORE INTO exercises(external_id,name_en) VALUES (?,?)',
                          (exercise['exercise_id'], exercise['name']))


def friend_program(user_id):
    # An explicitly configured personal program, not automatic A/B/C inheritance.
    return programs.create_program(user_id, 'Personal', [dict(name='Day one', exercises=[
        dict(exercise_id='bench-press', name='Bench', working_sets=3, warmup=[]),
        dict(exercise_id='squat', name='Squat', working_sets=3, warmup=[]),
    ])], activate=True)
