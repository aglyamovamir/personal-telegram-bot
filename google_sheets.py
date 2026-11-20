import os
import json
import base64
import logging
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from threading import Lock
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

from database.db import get_daily_metrics, has_workout_on_date, get_user, get_initial_owner_telegram_id, get_workout, get_workout_sets

SPREADSHEET_ID = "1zqO0E-zAHgr8pAB0Lqcb-txqEnSbbRNq_x69MATF2dM"
_workout_sync_lock = Lock()


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
            range_name=f"A{target_row}:K{target_row}",
            values=[row]
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
            range_name=f"A2:K{len(data_rows) + 1}",
            values=data_rows
        )



def sync_workout(user_id, workout_id):
    # Amvera runs one process; serialize read/append pairs within that process.
    with _workout_sync_lock:
        return _sync_workout(user_id, workout_id)


def _sync_workout(user_id, workout_id):
    if not export_enabled(user_id):
        return False

    # Проверка принадлежности тренировки пользователю
    workout_id, workout_type, started_at, finished_at = get_workout(
        user_id, workout_id
    )
    sets = get_workout_sets(user_id, workout_id, export=True)

    if finished_at is None:
        print(f"Workout {workout_id} is not finished")
        return False

    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)

    # Поддержка старых и новых форматов времени
    zone = ZoneInfo(get_user(user_id)["timezone"])

    if started.tzinfo is None:
        started = started.replace(tzinfo=zone)

    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=zone)

    started = started.astimezone(zone)
    finished = finished.astimezone(zone)
    if finished.timestamp() < started.timestamp():
        raise ValueError("Workout finishes before it starts")

    duration_min = round(
        (finished.timestamp() - started.timestamp()) / 60
    )

    total_sets = len(sets)
    working_sets = sum(
        1 for row in sets if row[4] == 0
    )
    total_volume = sum(
        row[5] * row[6]
        for row in sets
        if row[4] == 0
    )

    # ==========================================
    # WORKOUTS
    # ==========================================

    workout_row = [
        int(workout_id),
        to_sheets_date(started.isoformat()),
        workout_type,
        "completed",
        to_sheets_datetime(started.isoformat()),
        int(duration_min),
        int(total_sets),
        int(working_sets),
        float(total_volume)
    ]

    workouts_sheet = get_sheet(user_id, "Workouts")

    # Форматирование числовых полей и дат
    workouts_sheet.batch_format([
        {
            "range": "A2:A",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0"
                }
            }
        },
        {
            "range": "B2:B",
            "format": {
                "numberFormat": {
                    "type": "DATE",
                    "pattern": "yyyy-mm-dd"
                }
            }
        },
        {
            "range": "E2:E",
            "format": {
                "numberFormat": {
                    "type": "DATE_TIME",
                    "pattern": "dd.MM.yyyy hh:mm:ss"
                }
            }
        },
        {
            "range": "F2:H",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0"
                }
            }
        },
        {
            "range": "I2:I",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0.00"
                }
            }
        }
    ])

    all_values = workouts_sheet.get_all_values(value_render_option="UNFORMATTED_VALUE")

    target_row = None

    for row_number, sheet_row in enumerate(
        all_values[1:], start=2
    ):
        if sheet_row and _sheet_id(sheet_row[0]) == workout_id:
            target_row = row_number
            break

    if target_row is None:
        workouts_sheet.append_row(
            workout_row,
            value_input_option="RAW"
        )
        print(f"Workout added: {workout_id}")
    else:
        workouts_sheet.update(
            range_name=f"A{target_row}:I{target_row}",
            values=[workout_row],
            value_input_option="RAW"
        )
        print(f"Workout updated: {workout_id}")

    # ==========================================
    # SETS
    # ==========================================

    sets_sheet = get_sheet(user_id, "Sets")

    # Форматирование числовых полей и дат
    sets_sheet.batch_format([
        {
            "range": "A2:A",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0"
                }
            }
        },
        {
            "range": "B2:B",
            "format": {
                "numberFormat": {
                    "type": "DATE",
                    "pattern": "yyyy-mm-dd"
                }
            }
        },
        {
            "range": "F2:H",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0"
                }
            }
        },
        {
            "range": "I2:I",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0.00"
                }
            }
        },
        {
            "range": "J2:J",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0"
                }
            }
        },
        {
            "range": "K2:K",
            "format": {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0.00"
                }
            }
        }
    ])

    existing_sets = sets_sheet.get_all_values(value_render_option="UNFORMATTED_VALUE")
    existing_by_id = {}
    legacy_rows = defaultdict(deque)
    for row_number, row in enumerate(existing_sets[1:], start=2):
        if not row or _sheet_id(row[0]) != workout_id:
            continue
        set_id = _sheet_id(row[5]) if len(row) > 5 else None
        if set_id is not None:
            existing_by_id.setdefault(set_id, row_number)
        elif len(row) >= 8:
            # Consume each legacy row once, including repeated exercise blocks.
            exercise_key = str(row[3]).strip() or str(row[4]).strip()
            legacy_rows[(exercise_key, _sheet_id(row[6]))].append(row_number)

    rows_to_update = []
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

        canonical_exercise_name = exercise_name

        row = [
            int(workout_id),
            to_sheets_date(started.isoformat()),
            workout_type,
            exercise_id or "",
            canonical_exercise_name,
            int(_set_id),
            int(set_number),
            int(is_warmup),
            float(weight),
            int(reps),
            float(volume)
        ]

        target_row = existing_by_id.get(_set_id)
        if target_row is None:
            legacy_key = (str(exercise_id or canonical_exercise_name).strip(), int(set_number))
            if legacy_rows[legacy_key]:
                target_row = legacy_rows[legacy_key].popleft()
        if target_row is None:
            rows_to_add.append(row)
        else:
            # Re-export also repairs text numbers/dates in previously saved rows.
            rows_to_update.append({"range": f"A{target_row}:K{target_row}", "values": [row]})

    if rows_to_update:
        sets_sheet.batch_update(rows_to_update, value_input_option="RAW")

    if rows_to_add:
        sets_sheet.append_rows(
            rows_to_add,
            value_input_option="RAW"
        )

    print(
        f"Sets synced for workout {workout_id}: "
        f"{len(rows_to_add)} new sets"
    )

    return True


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
        result = sync_workout(user_id, workout_id)
        if result:
            workout = get_workout(user_id, workout_id)
            # Refresh the day flag as well, if metrics already exist.
            started = datetime.fromisoformat(workout[2])
            zone = ZoneInfo(get_user(user_id)['timezone'])
            if started.tzinfo is None:
                started = started.replace(tzinfo=zone)
            sync_daily_metrics(user_id, started.astimezone(zone).date().isoformat())
        return result
    except Exception:
        logging.getLogger(__name__).warning('Workout export failed; SQLite data is saved (internal user %s)', user_id)
        return False

def to_sheets_datetime(iso_datetime):
    dt = datetime.fromisoformat(iso_datetime)

    # Сохраняем местное время тренировки, не переводя в UTC
    dt = dt.replace(tzinfo=None)

    sheets_epoch = datetime(1899, 12, 30)

    return (dt - sheets_epoch).total_seconds() / 86400

def to_sheets_date(iso_datetime):
    dt = datetime.fromisoformat(iso_datetime[:10])
    epoch = datetime(1899, 12, 30)
    return (dt - epoch).days


def _sheet_id(value):
    """Read integer IDs from numeric cells or legacy text, independent of locale."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", "."))
        if number.is_finite() and number == number.to_integral_value():
            return int(number)
    except InvalidOperation:
        pass
    return None
