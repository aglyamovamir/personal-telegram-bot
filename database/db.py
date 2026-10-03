import sqlite3
from datetime import datetime


import os

if os.path.exists("/data"):
    DB_PATH = "/data/database.db"
else:
    DB_PATH = "database/database.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workout_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id INTEGER NOT NULL,
            exercise TEXT NOT NULL,
            set_number INTEGER NOT NULL,
            is_warmup INTEGER NOT NULL DEFAULT 0,
            weight REAL NOT NULL,
            reps INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (workout_id) REFERENCES workouts(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,

            sleep_start TEXT,
            wake_time TEXT,
            sleep_duration_minutes INTEGER,
            sleep_score INTEGER,

            productivity REAL,
            stress INTEGER,
            mood INTEGER,
            steps INTEGER,
            screen_time_minutes INTEGER,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL UNIQUE,
            name_en TEXT NOT NULL,
            name_ru TEXT,
            description_en TEXT,
            description_ru TEXT,
            category TEXT,
            force_type TEXT,
            mechanic TEXT,
            difficulty TEXT,
            equipment TEXT,
            body_part TEXT,
            met REAL,
            is_unilateral INTEGER NOT NULL DEFAULT 0,
            is_bodyweight INTEGER NOT NULL DEFAULT 0,
            variation_group TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exercise_muscles (
            exercise_id INTEGER NOT NULL,
            muscle TEXT NOT NULL,
            muscle_type TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            UNIQUE (exercise_id, muscle, muscle_type)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exercise_goals (
            exercise_id INTEGER NOT NULL,
            goal TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            UNIQUE (exercise_id, goal)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exercise_tags (
            exercise_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            UNIQUE (exercise_id, tag)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exercise_instructions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL,
            language TEXT NOT NULL,
            step_number INTEGER NOT NULL,
            instruction TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            UNIQUE (exercise_id, language, step_number)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exercise_tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL,
            language TEXT NOT NULL,
            tip_number INTEGER NOT NULL,
            tip TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            UNIQUE (exercise_id, language, tip_number)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exercise_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL,
            view TEXT NOT NULL,
            path TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            UNIQUE (exercise_id, view)
        )
    """)
    
    connection.commit()
    connection.close()


def create_workout(workout_type):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO workouts (
            workout_type,
            started_at
        )
        VALUES (?, ?)
    """, (
        workout_type,
        datetime.now().isoformat(timespec="seconds")
    ))

    workout_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return workout_id


def save_set(workout_id, exercise, set_number, weight, reps, is_warmup=False):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO workout_sets (
            workout_id,
            exercise,
            set_number,
            is_warmup,
            weight,
            reps,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        workout_id,
        exercise,
        set_number,
        1 if is_warmup else 0,
        weight,
        reps,
        datetime.now().isoformat(timespec="seconds")
    ))

    connection.commit()
    connection.close()


def finish_workout(workout_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE workouts
        SET finished_at = ?
        WHERE id = ?
    """, (
        datetime.now().isoformat(timespec="seconds"),
        workout_id
    ))

    connection.commit()
    connection.close()

def get_last_workout_by_type(workout_type):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, workout_type, started_at, finished_at
        FROM workouts
        WHERE workout_type = ?
          AND finished_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 1
    """, (workout_type,))

    workout = cursor.fetchone()
    connection.close()

    return workout

def get_last_workout():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, workout_type, started_at, finished_at
        FROM workouts
        WHERE finished_at IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
    """)

    workout = cursor.fetchone()

    connection.close()

    return workout


def get_workout_sets(workout_id):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT exercise, set_number, is_warmup, weight, reps
        FROM workout_sets
        WHERE workout_id = ?
        ORDER BY id
    """, (workout_id,))
    sets = cursor.fetchall()
    connection.close()
    return sets

def save_morning_metrics(
    date,
    sleep_start,
    wake_time,
    sleep_duration_minutes,
    sleep_score
):
    connection = get_connection()
    cursor = connection.cursor()

    now = datetime.now().isoformat(timespec="seconds")

    cursor.execute("""
        INSERT INTO daily_metrics (
            date,
            sleep_start,
            wake_time,
            sleep_duration_minutes,
            sleep_score,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            sleep_start = excluded.sleep_start,
            wake_time = excluded.wake_time,
            sleep_duration_minutes = excluded.sleep_duration_minutes,
            sleep_score = excluded.sleep_score,
            updated_at = excluded.updated_at
    """, (
        date,
        sleep_start,
        wake_time,
        sleep_duration_minutes,
        sleep_score,
        now,
        now
    ))

    connection.commit()
    connection.close()


def save_evening_metrics(
    date,
    productivity,
    stress,
    mood,
    steps,
    screen_time_minutes
):
    connection = get_connection()
    cursor = connection.cursor()

    now = datetime.now().isoformat(timespec="seconds")

    cursor.execute("""
        INSERT INTO daily_metrics (
            date,
            productivity,
            stress,
            mood,
            steps,
            screen_time_minutes,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            productivity = excluded.productivity,
            stress = excluded.stress,
            mood = excluded.mood,
            steps = excluded.steps,
            screen_time_minutes = excluded.screen_time_minutes,
            updated_at = excluded.updated_at
    """, (
        date,
        productivity,
        stress,
        mood,
        steps,
        screen_time_minutes,
        now,
        now
    ))

    connection.commit()
    connection.close()

def get_daily_metrics(date):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            date,
            sleep_start,
            wake_time,
            sleep_duration_minutes,
            sleep_score,
            productivity,
            stress,
            mood,
            steps,
            screen_time_minutes
        FROM daily_metrics
        WHERE date = ?
    """, (date,))

    metrics = cursor.fetchone()

    connection.close()

    return metrics

def has_workout_on_date(date):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM workouts
        WHERE DATE(started_at) = ?
        AND finished_at IS NOT NULL
    """, (date,))

    count = cursor.fetchone()[0]

    connection.close()

    return count > 0

def get_last_exercise_sets(exercise):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT ws.weight, ws.reps
        FROM workout_sets ws
        JOIN workouts w ON w.id = ws.workout_id
        WHERE ws.exercise = ?
          AND w.finished_at IS NOT NULL
          AND w.id = (
              SELECT MAX(ws2.workout_id)
              FROM workout_sets ws2
              JOIN workouts w2 ON w2.id = ws2.workout_id
              WHERE ws2.exercise = ?
                AND w2.finished_at IS NOT NULL
          )
        ORDER BY ws.id
    """, (exercise, exercise))

    sets = cursor.fetchall()

    connection.close()
    return sets