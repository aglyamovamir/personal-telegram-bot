import sqlite3
from datetime import datetime


DB_PATH = "database/database.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workout_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workout TEXT NOT NULL,
            exercise TEXT NOT NULL,
            set_number INTEGER NOT NULL,
            weight REAL NOT NULL,
            reps INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    connection.commit()
    connection.close()


def save_set(workout, exercise, set_number, weight, reps):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO workout_sets (
            workout,
            exercise,
            set_number,
            weight,
            reps,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        workout,
        exercise,
        set_number,
        weight,
        reps,
        datetime.now().isoformat(timespec="seconds")
    ))

    connection.commit()
    connection.close()