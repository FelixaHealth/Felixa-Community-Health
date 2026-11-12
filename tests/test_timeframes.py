"""The Track graphs by timeframe or incident: bars of time or of incidents instead of equal parts. Run with:

    python3 -m unittest tests.test_timeframes -v
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import analysis  # noqa: E402
import measures  # noqa: E402
import timeframes  # noqa: E402
import track  # noqa: E402

ZERO = dict.fromkeys(analysis.ATTRIBUTES, 0)
CALM, HOT = {'Polite': 2}, {'Rude': 3, 'Agitated': 2}


def units(plan, timed=True):
    """plan = [(count, ratings, minutes between messages, minutes of quiet before)]."""
    out, t, i = [], datetime.datetime(2026, 3, 1, 9, 0), 0
    for count, ratings, step, quiet in plan:
        t += datetime.timedelta(minutes=quiet)
        for _ in range(count):
            out.append({'id': i, 'text': f'm{i}', 'timestamp': t.isoformat() if timed else '', 'ratings': {**ZERO, **ratings}})
            t += datetime.timedelta(minutes=step)
            i += 1
    return out


PLAN = [(30, CALM, 8, 0), (10, HOT, 2, 180), (25, CALM, 8, 20), (30, CALM, 9, 0), (12, HOT, 2, 600), (25, CALM, 8, 15)]


class Bars(unittest.TestCase):
    def setUp(self):
        self.units = units(PLAN)
        self.m = measures.analyse(self.units, today=datetime.date(2026, 3, 20))

    def test_auto_gives_a_readable_number_of_bars_and_no_empty_ones(self):
        r = timeframes.rebase(self.m, self.units, 'time', 0)
        k = len(r['m']['stretches'])
        self.assertEqual(r['by'], 'time')
        self.assertTrue(timeframes.MIN_AUTO <= k <= timeframes.MAX_AUTO)
        self.assertTrue(all(s['n'] > 0 for s in r['m']['stretches']))
        self.assertEqual(sum(s['n'] for s in r['m']['stretches']), len(self.units))

    def test_a_size_that_gives_too_many_bars_moves_to_a_larger_one(self):
        r = timeframes.rebase(self.m, self.units, 'time', 5)
        self.assertLessEqual(len(r['m']['stretches']), timeframes.MAX_BARS)

    def test_the_flag_rules_run_on_the_new_bars(self):
        r = timeframes.rebase(self.m, self.units, 'time', 30)
        flags = track.escalation_flags(r['m'])
        self.assertEqual(len(flags), len(r['m']['stretches']))
        self.assertTrue(any(f['level'] for f in flags))
        self.assertEqual(len(r['de']), len(flags))

    def test_incidents_become_bars_with_their_own_flags(self):
        r = timeframes.rebase(self.m, self.units, 'incident', 0)
        self.assertEqual(r['by'], 'incident')
        self.assertEqual(len(r['m']['stretches']), 2)
        self.assertTrue(r['m']['stretches'][0]['label'].startswith('Incident 1'))
        self.assertEqual([f['level'] for f in track.escalation_flags(r['m'])], ['red', 'red'])

    def test_a_chat_without_times_falls_back_to_messages_and_incidents(self):
        u = units(PLAN, timed=False)
        r = timeframes.rebase(measures.analyse(u, today=datetime.date(2026, 3, 20)), u, 'time', 0)
        self.assertEqual(r['by'], 'messages')
        r = timeframes.rebase(measures.analyse(u, today=datetime.date(2026, 3, 20)), u, 'incident', 0)
        self.assertEqual(r['by'], 'incident')

    def test_no_part_wording_in_the_new_bars(self):
        r = timeframes.rebase(self.m, self.units, 'time', 60)
        for key in track.KEYS:
            text = ' '.join(track.summary(r['m'], key)['sentences'] + [e['text'] for e in track.events(r['m'], key)])
            self.assertNotIn('part', text.replace('particular', ''), key)
        self.assertNotIn('Part ', r['m']['stretches'][0]['label'])


if __name__ == '__main__':
    unittest.main()


class Gaps(unittest.TestCase):
    def test_the_level_before_a_gap_is_not_the_recent_level_after_it(self):
        # a hot hour, 20 quiet hours (no bar at all), then another hot hour: the second one must be flagged by itself, not measured against the first
        plan = [(30, CALM, 2, 0), (12, HOT, 1, 0), (6, CALM, 2, 0), (12, HOT, 1, 20 * 60)]
        u = units(plan)
        m = measures.analyse(u, today=datetime.date(2026, 3, 20))
        r = timeframes.rebase(m, u, 'time', 60)
        bars = r['m']['stretches']
        gap_bars = [i for i, s in enumerate(bars) if s.get('after_gap')]
        self.assertTrue(gap_bars, 'the bar after the 20 quiet hours says it follows a gap')
        flags = track.escalation_flags(r['m'])
        after = gap_bars[0]
        self.assertIsNone(flags[after]['base'], 'no recent level across a gap')
        self.assertTrue(flags[after]['level'], 'the second hot hour is flagged on its own')


class Messages(unittest.TestCase):
    def test_a_chat_with_times_can_also_be_cut_by_messages(self):
        u = units(PLAN)
        m = measures.analyse(u, today=datetime.date(2026, 3, 20))
        r = timeframes.rebase(m, u, 'messages', 50)
        self.assertEqual((r['by'], r['used']), ('messages', 50))
        self.assertEqual(sum(s['n'] for s in r['m']['stretches']), len(u))


class WithoutTimes(unittest.TestCase):
    def test_a_chat_without_times_gets_bars_of_messages_you_can_resize(self):
        u = units(PLAN, timed=False)
        m = measures.analyse(u, today=datetime.date(2026, 3, 20))
        auto = timeframes.rebase(m, u, 'time', 0)
        self.assertEqual(auto['by'], 'messages')
        self.assertGreaterEqual(len(auto['m']['stretches']), 2)
        big = timeframes.rebase(m, u, 'time', 50)
        self.assertEqual(big['used'], 50)
        self.assertEqual(sum(s['n'] for s in big['m']['stretches']), len(u))
        self.assertTrue(big['m']['stretches'][0]['label'].startswith('Messages 1 to'))
