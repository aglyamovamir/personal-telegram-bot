import json
import sqlite3


JSON_PATH = "data/repdb/exercises.json"
DB_PATH = "database/database.db"


def main():
    with open(JSON_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)

    exercises = data["exercises"]

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    try:
        for exercise in exercises:
            cursor.execute("""
                INSERT INTO exercises (
                    external_id,
                    name_en,
                    name_ru,
                    description_en,
                    description_ru,
                    category,
                    force_type,
                    mechanic,
                    difficulty,
                    equipment,
                    body_part,
                    met,
                    is_unilateral,
                    is_bodyweight,
                    variation_group
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                exercise["id"],
                exercise["name_en"],
                None,
                exercise.get("description_en"),
                None,
                exercise.get("category"),
                exercise.get("force_type"),
                exercise.get("mechanic"),
                exercise.get("difficulty"),
                exercise.get("equipment"),
                exercise.get("body_part"),
                exercise.get("met"),
                1 if exercise.get("is_unilateral") else 0,
                1 if exercise.get("is_bodyweight") else 0,
                exercise.get("variation_group")
            ))

            exercise_id = cursor.lastrowid

            for muscle in exercise.get("primary_muscles", []):
                cursor.execute("""
                    INSERT INTO exercise_muscles (
                        exercise_id,
                        muscle,
                        muscle_type
                    )
                    VALUES (?, ?, ?)
                """, (
                    exercise_id,
                    muscle,
                    "primary"
                ))

            for muscle in exercise.get("secondary_muscles", []):
                cursor.execute("""
                    INSERT INTO exercise_muscles (
                        exercise_id,
                        muscle,
                        muscle_type
                    )
                    VALUES (?, ?, ?)
                """, (
                    exercise_id,
                    muscle,
                    "secondary"
                ))

            for goal in exercise.get("goals", []):
                cursor.execute("""
                    INSERT INTO exercise_goals (
                        exercise_id,
                        goal
                    )
                    VALUES (?, ?)
                """, (
                    exercise_id,
                    goal
                ))

            for tag in exercise.get("tags", []):
                cursor.execute("""
                    INSERT INTO exercise_tags (
                        exercise_id,
                        tag
                    )
                    VALUES (?, ?)
                """, (
                    exercise_id,
                    tag
                ))

            for language in ("en", "de", "es"):
                instructions = exercise.get(f"instructions_{language}", [])

                for step_number, instruction in enumerate(instructions, start=1):
                    cursor.execute("""
                        INSERT INTO exercise_instructions (
                            exercise_id,
                            language,
                            step_number,
                            instruction
                        )
                        VALUES (?, ?, ?, ?)
                    """, (
                        exercise_id,
                        language,
                        step_number,
                        instruction
                    ))

                tips = exercise.get(f"tips_{language}", [])

                for tip_number, tip in enumerate(tips, start=1):
                    cursor.execute("""
                        INSERT INTO exercise_tips (
                            exercise_id,
                            language,
                            tip_number,
                            tip
                        )
                        VALUES (?, ?, ?, ?)
                    """, (
                        exercise_id,
                        language,
                        tip_number,
                        tip
                    ))

            images = exercise.get("images", {}).get("flat", {})

            for view, path in images.items():
                cursor.execute("""
                    INSERT INTO exercise_images (
                        exercise_id,
                        view,
                        path
                    )
                    VALUES (?, ?, ?)
                """, (
                    exercise_id,
                    view,
                    path
                ))

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    print(f"Imported exercises: {len(exercises)}")


if __name__ == "__main__":
    main()
