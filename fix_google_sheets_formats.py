"""One-time, in-place repair of historical Workouts/Sets values. Default: dry run."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

EPOCH = datetime(1899, 12, 30)
SCHEMAS = {
    'Workouts': {
        'workout_id': ('integer', '0'), 'date': ('date', 'yyyy-mm-dd'),
        'started_at': ('datetime', 'dd.MM.yyyy hh:mm:ss'),
        'duration_min': ('integer', '0'), 'total_sets': ('integer', '0'),
        'working_sets': ('integer', '0'), 'total_volume_kg': ('number', '0.00'),
    },
    'Sets': {
        'workout_id': ('integer', '0'), 'date': ('date', 'yyyy-mm-dd'),
        'exercise_order': ('integer', '0'), 'set_id': ('integer', '0'),
        'set_number': ('integer', '0'), 'is_warmup': ('integer', '0'),
        'weight_kg': ('number', '0.00'), 'reps': ('integer', '0'),
        'volume_kg': ('number', '0.00'),
    },
}


def numeric_value(value, kind):
    if isinstance(value, bool):
        raise ValueError('boolean in numeric column')
    text = str(value).strip().removeprefix("'")
    text = re.sub(r'[\s\u00a0\u202f]', '', text).replace(',', '.')
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
        raise ValueError('unrecognized number')
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('unrecognized number') from exc
    if not number.is_finite() or not math.isfinite(float(number)):
        raise ValueError('non-finite number')
    if kind == 'integer':
        if number != number.to_integral_value() or abs(number) > 2**53 - 1:
            raise ValueError('invalid or unsafe integer')
        return int(number)
    return float(number)


def serial_value(value, kind, timezone_name):
    text = str(value).strip().removeprefix("'")
    # Some old exports contain the serial itself as text.
    if re.fullmatch(r'\d+(?:[.,]\d+)?', text):
        serial = numeric_value(text, 'number')
        if not 1 <= serial < 109575:  # 1900 through 2199; reject ambiguous bare years.
            raise ValueError('date serial out of range')
        if len(text) == 4 and text.isdigit():
            raise ValueError('ambiguous date/year')
        return serial
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for pattern in ('%d.%m.%Y', '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M'):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError('unrecognized date/time')
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(timezone_name))
    parsed = parsed.replace(tzinfo=None)
    if kind == 'date':
        parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return (parsed - EPOCH).total_seconds() / 86400


def cells(sheet):
    result = {}
    for block in sheet.get('data', []):
        for row_offset, row in enumerate(block.get('rowData', [])):
            for col_offset, cell in enumerate(row.get('values', [])):
                result[(block.get('startRow', 0) + row_offset,
                        block.get('startColumn', 0) + col_offset)] = cell
    return result


def make_plan(snapshot, timezone_name):
    requests, edits, errors, formats = [], [], [], []
    by_title = {sheet['properties']['title']: sheet for sheet in snapshot.get('sheets', [])}
    for title, schema in SCHEMAS.items():
        if title not in by_title:
            raise ValueError(f'Missing worksheet: {title}')
        sheet = by_title[title]
        sheet_id = sheet['properties']['sheetId']
        grid = cells(sheet)
        headers = {}
        for (row, col), cell in grid.items():
            name = cell.get('userEnteredValue', {}).get('stringValue', '').strip()
            if row == 0 and name in schema:
                if name in headers:
                    raise ValueError(f'Duplicate header: {title}.{name}')
                headers[name] = col
        if not {'workout_id', 'date'} <= headers.keys():
            raise ValueError(f'Unrecognized headers: {title}')
        for name, col in headers.items():
            kind, pattern = schema[name]
            number_format = {'type': {'integer': 'NUMBER', 'number': 'NUMBER',
                                      'date': 'DATE', 'datetime': 'DATE_TIME'}[kind],
                             'pattern': pattern}
            formats.append((title, col, number_format))
            requests.append({'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': 1,
                          'startColumnIndex': col, 'endColumnIndex': col + 1},
                'cell': {'userEnteredFormat': {'numberFormat': number_format}},
                'fields': 'userEnteredFormat.numberFormat'}})
            for (row, column), cell in grid.items():
                if row == 0 or column != col:
                    continue
                entered = cell.get('userEnteredValue', {})
                if not entered or 'formulaValue' in entered:
                    continue
                value = entered.get('stringValue', entered.get('numberValue', entered.get('boolValue')))
                if value == '' or value is None:
                    continue
                ref = rowcol_to_a1(row + 1, col + 1)
                try:
                    if 'numberValue' in entered:
                        # Check integer columns too, without rewriting good native values.
                        numeric_value(value, kind if kind == 'integer' else 'number')
                        continue
                    converted = (serial_value(value, kind, timezone_name)
                                 if kind in ('date', 'datetime') else numeric_value(value, kind))
                except (ValueError, OverflowError) as exc:
                    errors.append((title, ref, str(exc)))
                    continue
                edits.append((title, row, col, converted))
                requests.append({'updateCells': {
                    'start': {'sheetId': sheet_id, 'rowIndex': row, 'columnIndex': col},
                    'rows': [{'values': [{'userEnteredValue': {'numberValue': converted}}]}],
                    'fields': 'userEnteredValue'}})
    return {'requests': requests, 'edits': edits, 'errors': errors, 'formats': formats}


def read_snapshot(book):
    return book.fetch_sheet_metadata(params={
        'includeGridData': True, 'ranges': list(SCHEMAS),
        'fields': 'spreadsheetId,sheets(properties(sheetId,title,gridProperties),'
                  'data(startRow,startColumn,rowData(values(userEnteredValue,userEnteredFormat(numberFormat)))))',
    })


def entered_values(snapshot):
    return {(sheet['properties']['title'], row, col): cell['userEnteredValue']
            for sheet in snapshot['sheets'] for (row, col), cell in cells(sheet).items()
            if cell.get('userEnteredValue')}


def verify(before, after, plan):
    expected = entered_values(before)
    expected.update({(title, row, col): {'numberValue': value}
                     for title, row, col, value in plan['edits']})
    if entered_values(after) != expected:
        raise RuntimeError('Values changed unexpectedly; retain the backup and inspect the sheet')
    grids = {sheet['properties']['title']: cells(sheet) for sheet in after['sheets']}
    for title, col, wanted in plan['formats']:
        for (row, column), cell in grids[title].items():
            if row > 0 and column == col and cell.get('userEnteredValue'):
                if cell.get('userEnteredFormat', {}).get('numberFormat') != wanted:
                    raise RuntimeError(f'Number format not confirmed: {title} column {col + 1}')


def repair(book, timezone_name, *, apply=False, backup_directory=Path('backups')):
    before = read_snapshot(book)
    plan = make_plan(before, timezone_name)
    print(f"Planned conversions: {len(plan['edits'])}; invalid cells: {len(plan['errors'])}")
    for title, ref, reason in plan['errors'][:20]:
        print(f'Cannot convert {title}!{ref}: {reason}')
    if plan['errors']:
        raise ValueError('Unrecognized cells found; no changes written')
    body = {'requests': plan['requests']}
    if len(json.dumps(body).encode()) > 1_800_000:
        raise ValueError('Repair exceeds the safe single-request size; no changes written')
    if not apply:
        print('Dry run only. Add --apply to repair the existing cells.')
        return plan
    backup_directory = Path(backup_directory)
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = backup_directory / ('google-sheets-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with backup.open('x', encoding='utf-8') as stream:
        json.dump(before, stream, ensure_ascii=False)
    print(f'Backup saved: {backup}')
    if read_snapshot(book) != before:
        raise RuntimeError('Spreadsheet changed during preparation; no changes written, run again')
    try:
        book.batch_update(body)
    except Exception:
        # A lost response does not mean the atomic update was not saved.
        verify(before, read_snapshot(book), plan)
    else:
        verify(before, read_snapshot(book), plan)
    print(f"Verified: {len(plan['edits'])} cells converted; rows and formulas preserved.")
    return plan


def open_owner_spreadsheet():
    """Explicit one-time operation for the migration owner, without changing .env or SQLite."""
    from database import db
    from google_sheets import SPREADSHEET_ID

    db.init_db()
    owner = db.get_initial_owner_telegram_id()
    configured = os.getenv('GOOGLE_SHEETS_OWNER_TELEGRAM_ID', '')
    if configured and configured != str(owner):
        raise PermissionError('Configured Sheets owner does not match the database owner')
    with closing(sqlite3.connect(Path(db.DB_PATH).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        row = connection.execute('SELECT timezone FROM users WHERE telegram_user_id=?', (owner,)).fetchone()
    if row is None:
        raise PermissionError('Initial owner not found')
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    encoded = os.getenv('GOOGLE_CREDENTIALS_B64')
    if encoded:
        credentials = Credentials.from_service_account_info(json.loads(base64.b64decode(encoded)), scopes=scopes)
    else:
        credentials = Credentials.from_service_account_file(
            'credentials/life-bot-510311-28f4df3ef878.json', scopes=scopes)
    client = gspread.authorize(credentials)
    client.set_timeout((5, 20))
    return client.open_by_key(SPREADSHEET_ID), row[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Back up and repair existing cells')
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
    try:
        book, zone = open_owner_spreadsheet()
        repair(book, zone, apply=args.apply)
    except Exception as exc:
        # Keep key material, tokens, and response bodies out of console logs.
        print(f'Repair did not complete ({type(exc).__name__}). Check network/access and the backup, if created.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
