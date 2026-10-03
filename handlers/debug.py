import sqlite3

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.db import DB_PATH

router = Router()


@router.message(Command("dbtest"))
async def dbtest(message: Message):
    try:
        connection = sqlite3.connect(DB_PATH)
        cursor = connection.cursor()

        cursor.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
        """)

        tables = [row[0] for row in cursor.fetchall()]

        result = f"DB_PATH: {DB_PATH}\n"
        result += f"TABLES: {tables}\n\n"

        for workout_type in ("A", "B", "C"):
            try:
                cursor.execute("""
                    SELECT id, workout_type, started_at, finished_at
                    FROM workouts
                    WHERE workout_type = ?
                    ORDER BY started_at DESC
                    LIMIT 1
                """, (workout_type,))

                workout = cursor.fetchone()
                result += f"{workout_type}: {workout}\n"
            except Exception as e:
                result += f"{workout_type}: ERROR {e}\n"

        if "workout_sets" in tables:
            cursor.execute("SELECT COUNT(*) FROM workout_sets")
            result += f"\nWORKOUT_SETS COUNT: {cursor.fetchone()[0]}"
        else:
            result += "\nWORKOUT_SETS: TABLE DOES NOT EXIST"

        connection.close()

        await message.answer(result)

    except Exception as e:
        await message.answer(f"DB ERROR:\n{type(e).__name__}: {e}")