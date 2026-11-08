import os
import json
import base64
import logging

import gspread
from google.oauth2.service_account import Credentials

from database.db import get_daily_metrics, has_workout_on_date, get_user, get_initial_owner_telegram_id, get_workout, get_workout_sets

SPREADSHEET_ID = "1zqO0E-zAHgr8pAB0Lqcb-txqEnSbbRNq_x69MATF2dM"


def get_sheet(user_id, sheet_name=None):
    if not export_enabled(user_id):
        raise PermissionError("Google Sheets export is disabled for this user")
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


def format_daily_row(user_id, metrics):
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
    workout = "Да" if has_workout_on_date(user_id, date) else "Нет"

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



def sync_daily_metrics(user_id, date):
    if not export_enabled(user_id):
        return False
    metrics = get_daily_metrics(user_id, date)

    if metrics is None:
        print(f"No daily metrics found for {date}")
        return

    sheet = get_sheet(user_id)
    row = format_daily_row(user_id, metrics)

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

    # Сортировка по дате в формате YYYY-MM-DD
    all_values = sheet.get_all_values()

    data_rows = all_values[1:]

    data_rows.sort(
        key=lambda row: (
            row[0][6:10] + "-" + row[0][3:5] + "-" + row[0][0:2]
            if row and len(row[0]) == 10
            else ""
        )
    )

    if data_rows:
        sheet.update(
            f"A2:K{len(data_rows) + 1}",
            data_rows
        )


def sync_workout(user_id, workout_id):
    if not export_enabled(user_id):
        return False
    # Ownership is checked before opening any Google connection.
    workout_id, workout_type, started_at, finished_at = get_workout(user_id, workout_id)
    sets = get_workout_sets(user_id, workout_id, export=True)

    if finished_at is None:
        print(f"Workout {workout_id} is not finished")
        return

    # ---------- Workouts ----------

    from datetime import datetime

    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)

    # Legacy starts are naive, while new completion timestamps include offset.
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(get_user(user_id)['timezone'])
    if started.tzinfo is None:
        started = started.replace(tzinfo=zone)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=zone)
    duration_min = round((finished.timestamp() - started.timestamp()) / 60)

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
        duration_min,
        total_sets,
        working_sets,
        total_volume
    ]

    workouts_sheet = get_sheet(user_id, "Workouts")

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
            f"A{target_row}:I{target_row}",
            [workout_row]
        )
        print(f"Workout updated: {workout_id}")

    # ---------- Sets ----------


    sets_sheet = get_sheet(user_id, "Sets")

    existing_sets = sets_sheet.get_all_values()

    existing_set_keys = set()

    for row in existing_sets[1:]:
        if len(row) > 5 and row[5]:
            existing_set_keys.add(('set_id', str(row[5])))
            continue
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
            _set_id,
            exercise_id,
            exercise_name,
            set_number,
            is_warmup,
            weight,
            reps
        ) = set_row

        volume = weight * reps
        legacy_key = (
            str(workout_id),
            str(exercise_id),
            str(float(set_number))
        )
        key = ('set_id', str(_set_id))

        canonical_exercise_name = exercise_name

        row = [
            workout_id,
            started_at[:10],
            workout_type,
            exercise_id or "",
            canonical_exercise_name,
            _set_id,
            set_number,
            is_warmup,
            weight,
            reps,
            volume
        ]

        if key not in existing_set_keys and legacy_key not in existing_set_keys:
            rows_to_add.append(row)
            existing_set_keys.add(key)

    if rows_to_add:
        sets_sheet.append_rows(rows_to_add)

    print(
        f"Sets synced for workout {workout_id}: "
        f"{len(rows_to_add)} new sets"
    )

def export_enabled(user_id):
    configured = os.getenv('GOOGLE_SHEETS_OWNER_TELEGRAM_ID', '')
    if not configured.isdecimal() or int(configured) <= 0:
        return False
    owner = int(configured)
    return owner == get_initial_owner_telegram_id() and get_user(user_id)['telegram_user_id'] == owner


def safe_sync_daily_metrics(user_id, date):
    """Export failure must not lose a saved response or leave FSM unfinished."""
    try:
        return sync_daily_metrics(user_id, date)
    except Exception:
        logging.getLogger(__name__).warning('Daily export failed; SQLite data is saved (internal user %s)', user_id)
        return False


def safe_sync_workout(user_id, workout_id):
    try:
        sync_workout(user_id, workout_id)
        if export_enabled(user_id):
            workout = get_workout(user_id, workout_id)
            # Refresh the day flag as well, if metrics already exist.
            sync_daily_metrics(user_id, workout[2][:10])
    except Exception:
        logging.getLogger(__name__).warning('Workout export failed; SQLite data is saved (internal user %s)', user_id)
        return False
