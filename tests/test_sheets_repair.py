"""Historical Sheets repair: preserve rows/formulas and verify atomic typed writes."""
import copy
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

import gspread
from google.auth.credentials import AnonymousCredentials
from gspread.http_client import HTTPClient

import fix_google_sheets_formats as repair


def cell(value):
    if value is None:
        return {}
    kind = 'numberValue' if type(value) in (int, float) else 'boolValue' if isinstance(value, bool) else 'stringValue'
    if isinstance(value, str) and value.startswith('='):
        kind = 'formulaValue'
    return {'userEnteredValue': {kind: value}}


def snapshot():
    tables = [
        ('Workouts', [
            ['date', 'workout_id', 'started_at', 'total_volume_kg', 'duration_min', 'note'],
            ['2026-11-19', '1.0', '2026-11-19T12:59:17+03:00', '1 250,5', '60', 'Keep me'],
            ['20.11.2026', 2, '2026-11-19T22:30:00Z', '=SUM(D2)', '45', '62,5'],
        ]),
        ('Sets', [
            ['workout_id', 'date', 'reps', 'weight_kg', 'set_id', 'volume_kg', 'exercise'],
            ['1', '2026-11-19', '10', '62,5', '100', '625', 'Bench'],
            [2, 46346, 8, 20.0, 101, '=D3*C3', 'Keep exercise'],
        ]),
    ]
    return {'spreadsheetId': 'test-only', 'properties': {'title': 'Offline'}, 'sheets': [
        {'properties': {'sheetId': index, 'title': name, 'gridProperties': {'rowCount': 100, 'columnCount': 8}},
         'data': [{'rowData': [{'values': [cell(value) for value in row]} for row in rows]}]}
        for index, (name, rows) in enumerate(tables)
    ]}


class MemoryRepairClient(HTTPClient):
    def __init__(self):
        session = Mock()
        session.request.side_effect = AssertionError('Real Google network forbidden')
        super().__init__(AnonymousCredentials(), session=session)
        self.snapshot = snapshot()
        self.writes = []
        self.read_count = 0
        self.change_on_read = None
        self.lose_response = False

    def fetch_sheet_metadata(self, spreadsheet_id, params=None):
        self.read_count += 1
        if self.read_count == self.change_on_read:
            self.snapshot['sheets'][0]['data'][0]['rowData'][1]['values'][5] = cell('Concurrent change')
        return copy.deepcopy(self.snapshot)

    def batch_update(self, spreadsheet_id, body):
        self.writes.append(copy.deepcopy(body))
        for request in body['requests']:
            if 'repeatCell' in request:
                update = request['repeatCell']
                grid = update['range']
                sheet = next(s for s in self.snapshot['sheets'] if s['properties']['sheetId'] == grid['sheetId'])
                for row in sheet['data'][0]['rowData'][grid['startRowIndex']:]:
                    col = grid['startColumnIndex']
                    while len(row['values']) <= col:
                        row['values'].append({})
                    row['values'][col].setdefault('userEnteredFormat', {}).update(copy.deepcopy(update['cell']['userEnteredFormat']))
            else:
                update = request['updateCells']
                start = update['start']
                sheet = next(s for s in self.snapshot['sheets'] if s['properties']['sheetId'] == start['sheetId'])
                destination = sheet['data'][0]['rowData'][start['rowIndex']]['values'][start['columnIndex']]
                destination['userEnteredValue'] = copy.deepcopy(update['rows'][0]['values'][0]['userEnteredValue'])
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError('Response lost after successful write')
        return {}


class HistoricalSheetsRepairTests(unittest.TestCase):
    def setUp(self):
        self.client = MemoryRepairClient()
        self.book = gspread.Spreadsheet(self.client, {'id': 'test-only'})
        directory = TemporaryDirectory(prefix='sheets-repair-test-')
        self.addCleanup(directory.cleanup)
        self.backups = Path(directory.name)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def run_repair(self, apply=True):
        return repair.repair(self.book, 'Europe/Moscow', apply=apply, backup_directory=self.backups)

    def test_dry_run_never_writes_or_creates_backups(self):
        plan = self.run_repair(apply=False)
        self.assertGreater(len(plan['edits']), 10)
        self.assertFalse(self.client.writes)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_apply_uses_one_atomic_batch_and_preserves_headers_formulas_and_notes(self):
        before = copy.deepcopy(self.client.snapshot)
        plan = self.run_repair()
        self.assertEqual(len(self.client.writes), 1)
        repair.verify(before, self.client.snapshot, plan)
        values = repair.entered_values(self.client.snapshot)
        self.assertEqual(values[('Sets', 1, 3)], {'numberValue': 62.5})
        self.assertEqual(values[('Workouts', 1, 3)], {'numberValue': 1250.5})
        self.assertEqual(values[('Workouts', 2, 3)], {'formulaValue': '=SUM(D2)'})
        self.assertEqual(values[('Workouts', 2, 5)], {'stringValue': '62,5'})
        self.assertEqual([len(s['data'][0]['rowData']) for s in self.client.snapshot['sheets']], [3, 3])
        backups = list(self.backups.glob('*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8')), before)

    def test_repeat_converts_zero_cells(self):
        self.run_repair()
        self.assertEqual(self.run_repair()['edits'], [])

    def test_lost_response_is_resolved_by_readback_without_second_write(self):
        self.client.lose_response = True
        self.run_repair()
        self.assertEqual(len(self.client.writes), 1)

    def test_invalid_cell_blocks_all_changes(self):
        self.client.snapshot['sheets'][1]['data'][0]['rowData'][1]['values'][2] = cell('eight')
        with self.assertRaisesRegex(ValueError, 'no changes written'):
            self.run_repair()
        self.assertFalse(self.client.writes)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_duplicate_or_missing_headers_block_writes(self):
        for headers in (['workout_id', 'date', 'workout_id'], ['weight_kg', 'date']):
            with self.subTest(headers=headers):
                self.client.snapshot = snapshot()
                self.client.snapshot['sheets'][0]['data'][0]['rowData'][0]['values'] = [cell(v) for v in headers]
                with self.assertRaises(ValueError):
                    self.run_repair()
                self.assertFalse(self.client.writes)

    def test_concurrent_edit_aborts_before_write_and_keeps_backup(self):
        self.client.change_on_read = self.client.read_count + 2
        with self.assertRaisesRegex(RuntimeError, 'changed during preparation'):
            self.run_repair()
        self.assertFalse(self.client.writes)
        self.assertEqual(len(list(self.backups.glob('*.json'))), 1)

    def test_timezone_aware_naive_and_local_text_dates(self):
        expected = (datetime(2026, 11, 20, 1, 30) - repair.EPOCH).total_seconds() / 86400
        for text in ('2026-11-19T22:30:00Z', '2026-11-20T01:30:00+03:00', '20.11.2026 01:30', '2026-11-20T01:30:00'):
            with self.subTest(text=text):
                self.assertEqual(repair.serial_value(text, 'datetime', 'Europe/Moscow'), expected)
        self.assertEqual(repair.serial_value('2026-11-19T22:30:00Z', 'date', 'Europe/Moscow'), int(expected))

    def test_numeric_strings_and_bad_numeric_values(self):
        self.assertEqual(repair.numeric_value("'62,5", 'number'), 62.5)
        self.assertEqual(repair.numeric_value('1\u202f250,5', 'number'), 1250.5)
        self.assertEqual(repair.numeric_value('10.0', 'integer'), 10)
        for value in ('NaN', 'inf', True, '1,234.5', '2.5', str(2**53)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                repair.numeric_value(value, 'integer')

    def test_grid_offsets_preserve_coordinates(self):
        sheet = {'data': [{'startRow': 4, 'startColumn': 2, 'rowData': [{'values': [cell('10')]}]}]}
        self.assertEqual(repair.cells(sheet), {(4, 2): cell('10')})
