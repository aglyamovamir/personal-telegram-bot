"""Frozen pre-v1 schema for temporary migration fixtures only."""
import sqlite3

def create_legacy_database(path):
    connection = sqlite3.connect(path)
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
            exercise_id TEXT,
            FOREIGN KEY (workout_id) REFERENCES workouts(id)
        )
    """)

    connection.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            chat_id INTEGER NOT NULL,
            morning_time TEXT NOT NULL,
            evening_time TEXT NOT NULL
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
