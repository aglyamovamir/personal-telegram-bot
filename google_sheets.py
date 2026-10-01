import os

import gspread
from google.oauth2.service_account import Credentials

from database.db import get_daily_metrics, has_workout_on_date


SPREADSHEET_ID = "1zqO0E-zAHgr8pAB0Lqcb-txqEnSbbRNq_x69MATF2dM"


def get_sheet():
    credentials_file = os.path.join(
        "credentials",
        os.listdir("credentials")[0]
    )

    creds = Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

    client = gspread.authorize(creds)

    return client.open_by_key(SPREADSHEET_ID).sheet1


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