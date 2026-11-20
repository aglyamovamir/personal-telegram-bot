"""Exercise real gspread Worksheets against an in-memory HTTP client, never Google."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
import unittest
import warnings
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import gspread
from google.auth.credentials import AnonymousCredentials
from gspread.http_client import HTTPClient
from gspread.utils import a1_range_to_grid_range

from database import db
import google_sheets as sheets
from test_database import TemporaryDatabaseTestCase


class MemorySheetsClient(HTTPClient):
    """Keep cell values and record the actual payloads produced by gspread."""

    def __init__(self):
        session = Mock()
        session.request.side_effect = AssertionError('Real Google network forbidden')
        super().__init__(AnonymousCredentials(), session=session)
        self.rows = {name: [['header']] for name in ('Workouts', 'Sets', 'Daily')}
        self.formats = []
        self.reads = []
        self.writes = []
        self.fail_append_once = None

    @staticmethod
    def title(range_name):
        return range_name.split('!')[0].strip("'")

    def values_get(self, spreadsheet_id, range_name, params=None):
        self.reads.append((range_name, params))
        return {'range': range_name, 'majorDimension': 'ROWS',
                'values': copy.deepcopy(self.rows[self.title(range_name)])}

    def values_update(self, spreadsheet_id, range_name, params=None, body=None):
        self.writes.append(('update', range_name, copy.deepcopy(params), copy.deepcopy(body)))
        title = self.title(range_name)
        first = a1_range_to_grid_range(range_name.split('!')[1])['startRowIndex']
        for offset, row in enumerate(body['values']):
            while len(self.rows[title]) <= first + offset:
                self.rows[title].append([])
            self.rows[title][first + offset] = list(row)
        return {}

    def values_append(self, spreadsheet_id, range_name, params, body):
        self.writes.append(('append', range_name, copy.deepcopy(params), copy.deepcopy(body)))
        title = self.title(range_name)
        self.rows[title].extend(copy.deepcopy(body['values']))
        # Model an ambiguous timeout: Google saved the rows, the client saw failure.
        if self.fail_append_once == title:
            self.fail_append_once = None
            raise TimeoutError('Response lost after append')
        return {}

    def values_batch_update(self, spreadsheet_id, body=None):
        for entry in body['data']:
            self.values_update(spreadsheet_id, entry['range'],
                               {'valueInputOption': body['valueInputOption']}, entry)
        return {}

    def batch_update(self, spreadsheet_id, body):
        self.formats.extend(copy.deepcopy(body['requests']))
        return {}

    def worksheet(self, title, sheet_id):
        return gspread.Worksheet(None, {'title': title, 'sheetId': sheet_id,
                                       'gridProperties': {'rowCount': 1000, 'columnCount': 11}},
                                 spreadsheet_id='test-only', client=self)


class GoogleSheetsWorkoutTests(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.client = MemorySheetsClient()
        self.worksheets = {name: self.client.worksheet(name, i)
                           for i, name in enumerate(self.client.rows)}
        patches = [
            patch.dict('os.environ', {'GOOGLE_SHEETS_OWNER_TELEGRAM_ID': '111'}),
            patch.object(sheets, 'get_sheet', side_effect=lambda uid, name=None:
                         self.worksheets[name or 'Daily']),
            patch.object(sheets.gspread, 'authorize', side_effect=AssertionError('No Google auth')),
            redirect_stdout(io.StringIO()),
        ]
        for patcher in patches:
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def workout(self, start='2026-11-19T12:59:17+03:00', finish='2026-11-19T13:59:17+03:00'):
        wid = db.create_workout(self.user_id, 'A')
        db.save_set(self.user_id, wid, 'Bench', 1, 20, 10, True, exercise_id='bench-press')
        db.save_set(self.user_id, wid, 'Bench', 2, 62.5, 8, exercise_id='bench-press')
        db.finish_workout(self.user_id, wid)
        with db._connection(True) as connection:
            connection.execute('UPDATE workouts SET started_at=?,finished_at=? WHERE id=?',
                               (start, finish, wid))
        return wid

    def test_raw_numeric_payload_and_date_time_formats(self):
        wid = self.workout()
        self.assertTrue(sheets.sync_workout(self.user_id, wid))
        row = self.client.rows['Workouts'][1]
        self.assertEqual(row[5:], [60, 2, 1, 500.0])
        self.assertEqual(row[1], (datetime(2026, 11, 19) - datetime(1899, 12, 30)).days)
        self.assertAlmostEqual(row[4] % 1, (12 * 3600 + 59 * 60 + 17) / 86400)
        for index in (0, 1, 4, 5, 6, 7, 8):
            self.assertIs(type(row[index]), int if index != 4 and index != 8 else float)
        working = self.client.rows['Sets'][2]
        self.assertEqual(working[8:], [62.5, 8, 500.0])
        for index in (0, 1, 5, 6, 7, 8, 9, 10):
            self.assertIn(type(working[index]), (int, float))
        self.assertTrue(all(call[2]['valueInputOption'] == 'RAW' for call in self.client.writes))
        self.assertTrue(all(params['valueRenderOption'] == 'UNFORMATTED_VALUE'
                            for _, params in self.client.reads))
        formats = {}
        for request in self.client.formats:
            entry = request['repeatCell']
            grid = entry['range']
            self.assertEqual(grid['startRowIndex'], 1)
            self.assertEqual(entry['fields'], 'userEnteredFormat(numberFormat)')
            formats[(grid['sheetId'], grid['startColumnIndex'])] = entry['cell']['userEnteredFormat']['numberFormat']
        self.assertEqual(formats[(0, 1)]['type'], 'DATE')
        self.assertEqual(formats[(0, 4)]['type'], 'DATE_TIME')
        self.assertEqual(formats[(0, 4)]['pattern'], 'dd.MM.yyyy hh:mm:ss')
        self.assertEqual(formats[(1, 8)]['pattern'], '0.00')
        self.assertEqual(formats[(1, 9)]['pattern'], '0')
        self.assertEqual(formats[(1, 10)]['pattern'], '0.00')

    def test_naive_aware_and_mixed_timestamps_agree(self):
        for start, finish in [
            ('2026-11-19T12:59:17', '2026-11-19T13:59:17'),
            ('2026-11-19T12:59:17', '2026-11-19T10:59:17+00:00'),
            ('2026-11-19T09:59:17Z', '2026-11-19T13:59:17'),
        ]:
            with self.subTest(start=start, finish=finish):
                wid = self.workout(start, finish)
                sheets.sync_workout(self.user_id, wid)
                row = self.client.rows['Workouts'][-1]
                self.assertEqual(row[5], 60)
                self.assertAlmostEqual(row[4], sheets.to_sheets_datetime('2026-11-19T12:59:17'))

    def test_utc_midnight_uses_user_date_and_daily_flag(self):
        wid = self.workout('2026-11-19T22:30:00+00:00', '2026-11-19T23:30:00+00:00')
        with patch.object(sheets, 'sync_daily_metrics') as daily:
            self.assertTrue(sheets.safe_sync_workout(self.user_id, wid))
        daily.assert_called_once_with(self.user_id, '2026-11-20')
        self.assertEqual(self.client.rows['Workouts'][1][1], sheets.to_sheets_date('2026-11-20'))
        self.assertEqual(self.client.rows['Sets'][1][1], sheets.to_sheets_date('2026-11-20'))

    def test_dst_duration_uses_elapsed_instants(self):
        with db._connection(True) as connection:
            connection.execute("UPDATE users SET timezone='Europe/Berlin' WHERE id=?", (self.user_id,))
        wid = self.workout('2026-10-25T02:30:00+02:00', '2026-10-25T02:30:00+01:00')
        sheets.sync_workout(self.user_id, wid)
        self.assertEqual(self.client.rows['Workouts'][1][5], 60)

    def test_repeat_export_updates_existing_text_rows_without_deprecation(self):
        wid = self.workout()
        sheets.sync_workout(self.user_id, wid)
        for name in ('Workouts', 'Sets'):
            for row in self.client.rows[name][1:]:
                row[:] = [str(value) if type(value) in (int, float) else value for value in row]
                row[0] += ',0'
        with warnings.catch_warnings():
            warnings.simplefilter('error', DeprecationWarning)
            sheets.sync_workout(self.user_id, wid)
        self.assertEqual(len(self.client.rows['Workouts']), 2)
        self.assertEqual(len(self.client.rows['Sets']), 3)
        self.assertIs(type(self.client.rows['Sets'][2][8]), float)
        self.assertIs(type(self.client.rows['Workouts'][1][0]), int)

    def test_legacy_rows_are_upgraded_once_with_repeated_exercises(self):
        wid = self.workout()
        with db._connection(True) as connection:
            # The same exercise can appear twice with the same set number.
            connection.execute('UPDATE workout_sets SET set_number=1 WHERE workout_id=?', (wid,))
        legacy = [str(wid), '2026-11-19', 'A', 'bench-press', 'Bench', '', '1,0', '0', '20', '10', '200']
        self.client.rows['Sets'].extend([legacy.copy(), legacy.copy()])
        sheets.sync_workout(self.user_id, wid)
        sheets.sync_workout(self.user_id, wid)
        self.assertEqual(len(self.client.rows['Sets']), 3)
        self.assertEqual([row[5] for row in self.client.rows['Sets'][1:]],
                         [row[0] for row in db.get_workout_sets(self.user_id, wid, export=True)])

    def test_legacy_without_exercise_id_matches_by_name(self):
        wid = self.workout()
        with db._connection(True) as connection:
            connection.execute('UPDATE workout_sets SET exercise_id=NULL WHERE workout_id=?', (wid,))
        self.client.rows['Sets'].append([wid, '', 'A', '', 'Bench', '', 1, 1, 20, 10, 200])
        sheets.sync_workout(self.user_id, wid)
        self.assertEqual(len(self.client.rows['Sets']), 3)
        self.assertIs(type(self.client.rows['Sets'][1][5]), int)

    def test_foreign_rows_and_malformed_ids_do_not_block_or_get_overwritten(self):
        wid = self.workout()
        set_id = db.get_workout_sets(self.user_id, wid, export=True)[0][0]
        foreign = [wid + 1, '', 'B', 'bench-press', 'Other user', set_id, 1, 0, 90, 10, 900]
        invalid = ['invalid', '', 'B', 'bench-press', 'Invalid ID', '', 'invalid', 0, 0, 0, 0]
        self.client.rows['Sets'].extend([foreign.copy(), invalid.copy()])
        sheets.sync_workout(self.user_id, wid)
        self.assertEqual(self.client.rows['Sets'][1:3], [foreign, invalid])
        self.assertEqual(len(self.client.rows['Sets']), 5)

    def test_retry_after_append_timeout_does_not_duplicate_rows(self):
        for title in ('Workouts', 'Sets'):
            with self.subTest(title=title):
                wid = self.workout()
                self.client.fail_append_once = title
                self.assertFalse(sheets.safe_sync_workout(self.user_id, wid))
                sheets.sync_workout(self.user_id, wid)
                self.assertEqual(sum(row[0] == wid for row in self.client.rows['Workouts'][1:]), 1)
                self.assertEqual(sum(row[0] == wid for row in self.client.rows['Sets'][1:]), 2)
                self.assertIsNotNone(db.get_workout(self.user_id, wid)[3])

    def test_concurrent_exports_in_one_process_do_not_duplicate_rows(self):
        wid = self.workout()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: sheets.sync_workout(self.user_id, wid), range(2)))
        self.assertEqual(results, [True, True])
        self.assertEqual(len(self.client.rows['Workouts']), 2)
        self.assertEqual(len(self.client.rows['Sets']), 3)

    def test_unfinished_and_invalid_duration_do_not_reach_sheets(self):
        wid = db.create_workout(self.user_id, 'A')
        self.assertFalse(sheets.safe_sync_workout(self.user_id, wid))
        self.assertFalse(self.client.writes)
        db.finish_workout(self.user_id, wid)
        with db._connection(True) as connection:
            connection.execute("UPDATE workouts SET started_at='2026-11-19T13:00:00',finished_at='2026-11-19T12:00:00' WHERE id=?", (wid,))
        self.assertFalse(sheets.safe_sync_workout(self.user_id, wid))
        self.assertFalse(self.client.writes)

    def test_daily_update_uses_current_gspread_signature(self):
        db.save_morning_metrics(self.user_id, '2026-11-19', '23:00', '07:00', 480, 80)
        self.client.rows['Daily'].append(['19.11.2026', 'old'])
        with warnings.catch_warnings():
            warnings.simplefilter('error', DeprecationWarning)
            sheets.sync_daily_metrics(self.user_id, '2026-11-19')
        self.assertEqual(len(self.client.rows['Daily']), 2)
        self.assertEqual(self.client.rows['Daily'][1][2], 80)


if __name__ == '__main__':
    unittest.main()
