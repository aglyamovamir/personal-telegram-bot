import sqlite3
from datetime import datetime


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


def save_set(workout_id, exercise, set_number, weight, reps):
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
        0,
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
        SELECT exercise, set_number, weight, reps
        FROM workout_sets
        WHERE workout_id = ?
        ORDER BY id
    """, (workout_id,))

    sets = cursor.fetchall()

    connection.close()

    return sets