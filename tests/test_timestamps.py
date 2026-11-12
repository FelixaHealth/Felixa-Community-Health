"""Dates and times as exports write them. Run with:

    python3 -m unittest tests.test_timestamps -v
"""
import datetime as dt
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import timestamps  # noqa: E402
from vibe_check.common import parse_ts  # noqa: E402


def one(value, *others):
    """The first stamp, read together with the others of its file."""
    units = [{'text': 'x', 'timestamp': v} for v in (value,) + others]
    return timestamps.normalize(units)['units'][0]['timestamp']


class Formats(unittest.TestCase):
    CASES = [
        ('12 AM is midnight', '3/4/26 12:05 AM', '2026-03-04T00:05:00'),
        ('12 PM is noon', '3/4/26 12:05 PM', '2026-03-04T12:05:00'),
        ('pm', '3/4/26, 10:15 PM', '2026-03-04T22:15:00'),
        ('seconds', '12.03.2026 10:15:30', '2026-12-03T10:15:30'),
        ('year first with slashes', '2026/03/12 10:15', '2026-03-12T10:15:00'),
        ('day and month name', '5 March 2026 10:15', '2026-03-05T10:15:00'),
        ('month name first', 'Mar 5, 2026 10:15 AM', '2026-03-05T10:15:00'),
        ('with a weekday and an offset', 'Thu, 5 Mar 2026 10:15:00 +0200', '2026-03-05T10:15:00+02:00'),
        ('whatever brackets', '[03/04/2026, 10:15:30]', '2026-03-04T10:15:30'),
        ('unix seconds', '1773310500', '2026-03-12T10:15:00+00:00'),
        ('unix milliseconds', '1773310500000', '2026-03-12T10:15:00+00:00'),
        ('a date alone is midnight of that day', '3/4/2026', '2026-03-04T00:00:00'),
    ]

    def test_each_format_is_read(self):
        for name, raw, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(one(raw), want)

    def test_iso_and_unreadable_stamps_are_left_alone(self):
        self.assertEqual(one('2026-11-09T09:00:00'), '2026-11-09T09:00:00')
        self.assertEqual(one('yesterday around lunch'), 'yesterday around lunch')
        self.assertEqual(one('31/02/2026 10:00'), '31/02/2026 10:00')
        self.assertEqual(one(''), '')

    def test_a_day_above_12_decides_the_order_for_the_whole_file(self):
        self.assertEqual(one('03/04/2026 10:00', '25/03/2026 11:00'), '2026-04-03T10:00:00', 'day-first: 3 April')
        self.assertEqual(one('03/04/2026 10:00', '03/25/2026 11:00'), '2026-03-04T10:00:00', 'month-first: 4 March')

    def test_when_nothing_says_which_it_is_read_month_first_and_the_note_says_so(self):
        out = timestamps.normalize([{'text': 'x', 'timestamp': '03/04/2026 10:00'}, {'text': 'y', 'timestamp': '05/06/2026 11:00'}])
        self.assertEqual(out['units'][0]['timestamp'], '2026-03-04T10:00:00')
        self.assertIn('month first', out['note'])
        sure = timestamps.normalize([{'text': 'x', 'timestamp': '25/04/2026 10:00'}])
        self.assertEqual(sure['note'], '')

    def test_a_compact_stamp_is_already_read_by_the_app(self):
        self.assertIsNotNone(parse_ts('20260312T101500'))

    def test_two_digit_years(self):
        self.assertEqual(one('3/4/69 10:00'), '2069-03-04T10:00:00')
        self.assertEqual(one('3/4/70 10:00'), '1970-03-04T10:00:00')

    def test_the_app_reads_what_comes_out(self):
        for _name, raw, _want in self.CASES:
            with self.subTest(raw):
                self.assertIsNotNone(parse_ts(one(raw)))


class DaylightSaving(unittest.TestCase):
    def test_stamps_with_offsets_are_a_real_hour_apart_across_the_clock_change(self):
        # New York, 8 March 2026: 01:30 EST and then 03:30 EDT are one hour apart, not two
        a, b = parse_ts('2026-03-08T01:30:00-05:00'), parse_ts('2026-03-08T03:30:00-04:00')
        self.assertEqual((b - a), dt.timedelta(hours=1))


class InTheStore(unittest.TestCase):
    def test_units_added_to_an_analysis_keep_the_readable_dates(self):
        from tests.test_app import AppCase

        class T(AppCase):
            def runTest(self):
                aid = self.new_analysis(name='Dates Co')
                self.store.add_units(aid, [{'text': 'hello there', 'source': 's', 'timestamp': '25/03/2026 10:15'}, {'text': 'good morning', 'source': 's', 'timestamp': '03/04/2026 09:00'}], 'TEST')
                got = [u['timestamp'] for u in self.store.units_for(aid)]
                self.assertEqual(got, ['2026-03-25T10:15:00', '2026-04-03T09:00:00'])
        T.setUpClass()
        try:
            t = T()
            t.setUp()
            t.runTest()
        finally:
            T.tearDownClass()


if __name__ == '__main__':
    unittest.main()
