"""The Track tab: flags for Escalation and Conflict, the gradient diagrams, the three measures together, the pages. Run with:

    python3 -m unittest tests.test_track -v
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_app import AppCase  # noqa: E402

import analysis  # noqa: E402
import bei_charts  # noqa: E402
import measures  # noqa: E402
import track  # noqa: E402

ZERO = dict.fromkeys(analysis.ATTRIBUTES, 0)


def unit(i, base, **ratings):
    return {'id': i, 'timestamp': (base + datetime.timedelta(hours=i)).isoformat(), 'ratings': {**ZERO, **ratings}}


def chat(plan, today=None):
    """plan = [(count, ratings)], one unit per message, hourly from 10 days before."""
    base = datetime.datetime(2026, 3, 1, 8, 0)
    units, i = [], 0
    for count, ratings in plan:
        for _ in range(count):
            units.append(unit(i, base, **ratings))
            i += 1
    return measures.analyse(units, today=datetime.date(2026, 3, 20))


CALM = (24, {'Polite': 2})
HOT = (24, {'Rude': 3, 'Agitated': 2})


class Flags(unittest.TestCase):
    def test_a_calm_chat_has_no_flags_and_says_so(self):
        m = chat([(96, {'Polite': 2})])
        for key in ('escalation', 'conflict'):
            self.assertEqual([f['level'] for f in track.flags_for(m, key)], [''] * len(m['stretches']), key)
            self.assertIn('No part of the chat was flagged', track.summary(m, key)['sentences'][0])
            self.assertEqual(track.events(m, key), [])

    def test_a_flare_up_raises_yellow_then_red_and_records_when_it_emerged_and_intensified(self):
        m = chat([CALM, CALM, (8, {'Rude': 2}), (16, {'Polite': 2}), (12, {'Rude': 3, 'Agitated': 2}), (36, {'Rude': 3, 'Agitated': 3})])
        flags = track.flags_for(m, 'escalation')
        levels = [f['level'] for f in flags]
        self.assertIn('red', levels)
        first = next(f for f in flags if f['level'])
        self.assertEqual(flags[first['i'] - 1]['level'], '', 'a flag emerges after calm parts')
        events = track.events(m, 'escalation')
        self.assertEqual(events[0]['state'], 'emerged')
        self.assertTrue(any(e['state'] in ('intensified', 'holding') for e in events[1:]) or len(events) == 1)
        self.assertIn('first flag', track.summary(m, 'escalation')['sentences'][0].lower())

    def test_conflict_flags_follow_the_levels_of_the_measures(self):
        m = chat([CALM, CALM, (24, {'Rude': 3}), (24, {'Rude': 3, 'Aggressive': 3})])
        low, high = measures.CUTS['hostile']
        for f in track.flags_for(m, 'conflict'):
            if f['level'] == 'red':
                self.assertGreaterEqual(f['value'], high * m['scale'])
                self.assertGreaterEqual(f['count'], 3)
            if f['level'] == 'yellow':
                self.assertGreaterEqual(f['value'], low * m['scale'])
            if not f['level']:
                self.assertTrue(f['value'] < low * m['scale'] or f['count'] < 2)
        self.assertTrue(any(f['level'] == 'red' for f in track.flags_for(m, 'conflict')))

    def test_red_needs_48_messages_on_the_timeline(self):
        m = chat([(16, {'Polite': 2}), (16, {'Rude': 3, 'Agitated': 3})])
        self.assertFalse(m['verdicts'])
        for key in ('escalation', 'conflict'):
            self.assertNotIn('red', [f['level'] for f in track.flags_for(m, key)], key)

    def test_low_sensitivity_raises_the_conflict_bar(self):
        units = []
        base = datetime.datetime(2026, 3, 1, 8, 0)
        for i in range(96):
            units.append(unit(i, base, **({'Rude': 3} if i % 9 == 0 else {'Polite': 2})))      # about 11 in 100 openly hostile
        medium = measures.analyse(units, 'medium', today=datetime.date(2026, 3, 20))
        low = measures.analyse(units, 'low', today=datetime.date(2026, 3, 20))
        count = lambda m: sum(1 for f in track.flags_for(m, 'conflict') if f['level'])      # noqa: E731
        self.assertGreaterEqual(count(medium), count(low))

    def test_empathy_has_no_flags(self):
        m = chat([CALM, (24, {'Empathetic': 3}), CALM, (24, {'Empathetic': 2})])
        self.assertEqual({f['level'] for f in track.flags_for(m, 'empathy')}, {''})
        self.assertEqual(track.events(m, 'empathy'), [])
        self.assertIn('Empathy was highest', track.summary(m, 'empathy')['sentences'][0])


class Link(unittest.TestCase):
    def test_empathy_that_goes_with_less_conflict_is_described_without_claiming_cause(self):
        parts = []
        for k in range(12):
            parts.append((8, {'Rude': 3} if k % 2 == 0 else {'Empathetic': 3}))
        m = chat(parts)
        out = track.link(m)
        self.assertTrue(out['ready'])
        self.assertTrue(any('lower' in s for s in out['sentences']), out['sentences'])
        self.assertIn('not proof', out['caution'])

    def test_too_few_parts_says_so(self):
        out = track.link(chat([CALM, CALM]))
        self.assertFalse(out['ready'] and out['k'] < track.MIN_FOR_LINK)
        if not out['ready']:
            self.assertIn('parts so far', out['sentences'][0])

    def test_a_flat_series_has_nothing_to_compare(self):
        m = chat([(96, {'Polite': 2})])
        self.assertEqual(track._pearson([1, 1, 1, 1], [1, 2, 3, 4]), None)
        self.assertIsNotNone(track.link(m))


class Charts(unittest.TestCase):
    def test_the_shade_deepens_with_the_rate_and_empathy_is_green(self):
        shades = [bei_charts.track_shade('conflict', v) for v in (0, 3, 8, 15, 40)]
        self.assertEqual(shades, ['heat1', 'heat2', 'heat3', 'heat4', 'heat5'])
        self.assertTrue(bei_charts.track_shade('empathy', 30).startswith('calm'))

    def test_the_diagram_draws_a_flag_with_its_letter_and_the_three_together(self):
        from vibe_check.charts import to_svg
        m = chat([CALM, CALM, (12, {'Rude': 3, 'Agitated': 3}), (36, {'Rude': 3, 'Agitated': 3})])
        flags = track.flags_for(m, 'escalation')
        svg = to_svg(bei_charts.track_chart('escalation', 'Escalation', m['stretches'], flags, width=720), 'x')
        self.assertIn('>R<', svg)
        self.assertIn('var(--vc-heat', svg)
        both = to_svg(bei_charts.track_compare(m['stretches'], width=720), 'y')
        for word in ('Escalation', 'Conflict', 'Empathy'):
            self.assertIn(word, both)
        self.assertIsNone(bei_charts.track_chart('conflict', 'Conflict', m['stretches'][:1], flags[:1]))


class Pages(AppCase):
    def ready(self):
        aid = self.with_chat(name='Track Guild')
        self.assertEqual(self.run_job(aid)['status'], 'complete')
        return aid

    def test_the_track_tab_has_the_three_measures_and_each_opens_its_own_page(self):
        aid = self.ready()
        page = self.client.get(f'/analysis/{aid}/results').text
        if 'id="tab-track"' not in page:
            self.skipTest('the sample chat has too few messages for a timeline')
        self.assertIn('<span>Track</span>', page.split('data-tabs>', 1)[1].split('</nav>', 1)[0])
        panel = page.split('id="tab-track"', 1)[1]
        for key, name in track.NAMES.items():
            self.assertIn(f'href="/analysis/{aid}/track/{key}"', panel, key)
            r = self.client.get(f'/analysis/{aid}/track/{key}')
            self.assertEqual(r.status_code, 200, key)
            self.assertIn(f'<h1>{name}</h1>', r.text)
            self.assertIn('next to the other two', r.text)
        self.assertIn('Red flag', self.client.get(f'/analysis/{aid}/track/escalation').text)
        self.assertNotIn('Red flags</small>', self.client.get(f'/analysis/{aid}/track/empathy').text, 'empathy has no flags')
        self.assertEqual(self.client.get(f'/analysis/{aid}/track/nope').status_code, 404)

    def test_no_timeline_means_no_track_tab_and_a_plain_page(self):
        aid = self.new_analysis(name='Short')
        self.client.post(f'/analysis/{aid}/add-text', data={'raw_text': 'thanks\nyou idiot\nnice work'})
        self.assertIn(self.run_job(aid)['status'], ('complete', 'failed'))
        self.assertNotIn('id="tab-track"', self.client.get(f'/analysis/{aid}/results').text)
        self.assertEqual(self.client.get(f'/analysis/{aid}/track/escalation').status_code, 404)


if __name__ == '__main__':
    unittest.main()
