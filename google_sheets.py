import os
import json
import base64

import gspread
from google.oauth2.service_account import Credentials

from database.db import get_daily_metrics, has_workout_on_date, get_connection


SPREADSHEET_ID = "1zqO0E-zAHgr8pAB0Lqcb-txqEnSbbRNq_x69MATF2dM"


def get_sheet(sheet_name=None):
    credentials_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")

    if credentials_b64:
        credentials_json = base64.b64decode(credentials_b64).decode("utf-8")

        creds = Credentials.from_service_account_info(
            json.loads(credentials_json),
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    else:
        creds = Credentials.from_service_account_file(
            "credentials/life-bot-510311-28f4df3ef878.json",
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    if sheet_name is None:
        return spreadsheet.sheet1

    return spreadsheet.worksheet(sheet_name)


def minutes_to_time(minutes):
    if minutes is None:
        return ""

    hours = minutes // 60
    remaining_minutes = minutes % 60

    return f"{hours}:{remaining_minutes:02d}"


def format_daily_row(metrics):
    (
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
    ) = metrics

    date_formatted = ".".join(reversed(date.split("-")))
    workout = "Да" if has_workout_on_date(date) else "Нет"

    return [
        date_formatted,
        minutes_to_time(sleep_duration_minutes),
        sleep_score if sleep_score is not None else "",
        sleep_start or "",
        wake_time or "",
        steps if steps is not None else "",
        minutes_to_time(screen_time_minutes),
        workout,
        productivity if productivity is not None else "",
        stress if stress is not None else "",
        mood if mood is not None else "",
    ]


def sync_daily_metrics(date):
    metrics = get_daily_metrics(date)

    if metrics is None:
        print(f"No daily metrics found for {date}")
        return

    sheet = get_sheet()
    row = format_daily_row(metrics)

    all_values = sheet.get_all_values()

    target_row = None

    for row_number, sheet_row in enumerate(all_values[1:], start=2):
        if sheet_row and sheet_row[0] == row[0]:
            target_row = row_number
            break

    if target_row is None:
        sheet.append_row(row)
        print(f"Daily metrics added: {date}")
    else:
        sheet.update(
            f"A{target_row}:K{target_row}",
            [row]
        )
        print(f"Daily metrics updated: {date}")

def sync_workout(workout_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, workout_type, started_at, finished_at
        FROM workouts
        WHERE id = ?
    """, (workout_id,))

    workout = cursor.fetchone()

    if workout is None:
        connection.close()
        print(f"Workout not found: {workout_id}")
        return

    workout_id, workout_type, started_at, finished_at = workout

    cursor.execute("""
        SELECT
            id,
            exercise_id,
            exercise,
            set_number,
            is_warmup,
            weight,
            reps
        FROM workout_sets
        WHERE workout_id = ?
        ORDER BY id
    """, (workout_id,))

    sets = cursor.fetchall()
    connection.close()

    if finished_at is None:
        print(f"Workout {workout_id} is not finished")
        return

    # ---------- Workouts ----------

    from datetime import datetime

    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)

    duration_min = round((finished - started).total_seconds() / 60)

    total_sets = len(sets)
    working_sets = sum(1 for row in sets if row[4] == 0)
    total_volume = sum(
        row[5] * row[6]
        for row in sets
        if row[4] == 0
    )

    workout_row = [
        workout_id,
        started_at[:10],
        workout_type,
        "completed",
        started_at,
        finished_at,
        duration_min,
        total_sets,
        working_sets,
        total_volume
    ]

    workouts_sheet = get_sheet("Workouts")

    all_values = workouts_sheet.get_all_values()

    target_row = None

    for row_number, sheet_row in enumerate(all_values[1:], start=2):
        if sheet_row and str(sheet_row[0]) == str(workout_id):
            target_row = row_number
            break

    if target_row is None:
        workouts_sheet.append_row(workout_row)
        print(f"Workout added: {workout_id}")
    else:
        workouts_sheet.update(
            f"A{target_row}:J{target_row}",
            [workout_row]
        )
        print(f"Workout updated: {workout_id}")

    # ---------- Sets ----------


    sets_sheet = get_sheet("Sets")

    existing_sets = sets_sheet.get_all_values()

    existing_set_keys = set()

    for row in existing_sets[1:]:
        if len(row) >= 8 and row[0] and row[3] and row[6]:
            key = (
                str(row[0]),
                str(row[3]),
                str(float(row[6]))
            )
            existing_set_keys.add(key)

    rows_to_add = []

    for set_row in sets:
        (
            set_id,
            exercise_id,
            exercise_name,
            set_number,
            is_warmup,
            weight,
            reps
        ) = set_row

        volume = weight * reps

        key = (
            str(workout_id),
            str(exercise_id),
            str(float(set_number))
        )

        row = [
            workout_id,
            started_at[:10],
            workout_type,
            exercise_id or "",
            exercise_name,
            "",
            set_number,
            is_warmup,
            weight,
            reps,
            volume
        ]

        if key not in existing_set_keys:
            rows_to_add.append(row)

    if rows_to_add:
        sets_sheet.append_rows(rows_to_add)

    print(
        f"Sets synced for workout {workout_id}: "
        f"{len(rows_to_add)} new sets"
    )