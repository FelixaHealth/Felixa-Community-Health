"""The Safety module: the FBSI detectors on a workplace's messages, held and never live, with a black flag on graphs and diagrams."""
import unittest

import safety_module
from tests.test_app import AppCase

if safety_module.AVAILABLE:
    from fbsi import be_detectors, be_safeguarding

CHAT = """message,timestamp
Morning all,2026-11-09T09:00:00
Send 500 or I'll post your private photos.,2026-11-09T09:05:00
We are keeping your passport until you finish the work.,2026-11-09T09:06:00
I won't reply until you apologise.,2026-11-09T09:08:00
Thanks for the update,2026-11-09T09:10:00
Lunch anyone?,2026-11-09T12:00:00
Sure,2026-11-09T12:01:00
"""


def unit(i, text, minute):
    return {'id': i, 'text': text, 'timestamp': f'2026-11-09T09:{minute:02d}:00', 'source_label': '#team', 'actor_role': None}


@unittest.skipUnless(safety_module.AVAILABLE, 'the FBSI engine files are not installed here')
class Engine(unittest.TestCase):
    def test_the_engine_is_the_original_twenty_detectors(self):
        self.assertEqual(len(be_detectors.CATALOGUE), 20)
        self.assertEqual(be_detectors.DETECTORS_VERSION, '0.4.0-unvalidated')

    def test_nothing_is_ever_live_without_the_safeguarding_record(self):
        self.assertFalse(be_safeguarding.live_safety_allowed())
        self.assertTrue(be_safeguarding.missing())
        self.assertTrue(be_safeguarding.hold_reason().startswith('Held'))

    def test_an_exposure_threat_and_kept_papers_are_held_and_a_silence_is_only_a_question(self):
        units = [unit(1, 'Morning all', 0), unit(2, "Send 500 or I'll post your private photos.", 5), unit(3, 'We are keeping your passport until you finish the work.', 6),
                 unit(4, "I won't reply until you apologise.", 8), unit(5, 'Thanks for the update', 10)]
        res = safety_module.run(units)
        found = {m['detector']: m for m in res['matches']}
        self.assertEqual(found['D15']['status'], 'urgent_human_review')
        self.assertIn('D16', found)
        self.assertEqual(found['D17']['status'], 'needs_clarification')
        for m in res['matches']:
            self.assertFalse(m['validated'])
            self.assertEqual(m['confidence'], 'unassessed')

    def test_a_calm_chat_matches_nothing(self):
        res = safety_module.run([unit(i, t, i) for i, t in enumerate(['Morning', 'Can you review the draft?', 'Sure, done', 'Thanks'], 1)])
        self.assertEqual([m for m in res['matches'] if m['valence'] == 'risk'], [])

    def test_a_quoted_threat_is_not_evidence(self):
        res = safety_module.run([unit(1, 'The training says: "Send 500 or I will post your private photos" is blackmail and must be reported.', 1)])
        self.assertEqual([m for m in res['matches'] if m['detector'] == 'D15'], [])


@unittest.skipUnless(safety_module.AVAILABLE, 'the FBSI engine files are not installed here')
class Pages(AppCase):
    def test_the_safety_screen_holds_what_matches_and_the_black_flag_reaches_the_graphs(self):
        aid = self.new_analysis(name='Safety Co')
        self.upload(aid, ('chat.csv', CHAT, 'text/csv'))
        self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
        page = self.client.get(f'/analysis/{aid}/safety/review')
        self.assertEqual(page.status_code, 200)
        for needle in ('Held for a person to read', 'Held, not live', 'Blackmail or exposure threat', 'Identity document confiscation', 'flag black'):
            self.assertIn(needle, page.text, needle)
        self.assertNotIn('Alert sent', page.text)
        self.assertEqual(self.client.get('/analysis/9999/safety').status_code, 404)

    def test_the_overview_sorts_messages_into_behaviours_with_exact_quotes(self):
        aid = self.new_analysis(name='Safety Overview')
        self.upload(aid, ('chat.csv', CHAT, 'text/csv'))
        self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
        page = self.client.get(f'/analysis/{aid}/safety')
        self.assertEqual(page.status_code, 200)
        for needle in ('Aggression &amp; Violence', 'Self-Harm &amp; Dangerous Activities', 'Quotes', 'Messages found', 'Review', 'no reader yet'):
            self.assertIn(needle, page.text, needle)
        for noise in ('Held for a person', 'A question to ask', 'flag black'):
            self.assertNotIn(noise, page.text, noise)
        import high_risk
        hr = high_risk.build(aid, safety_module.build(aid))
        ot = high_risk.over_time(aid, hr)
        if ot:
            self.assertTrue(ot['svg_a'].startswith('<svg'))
            self.assertLessEqual(sum(d['count'] for d in ot['days']), len(hr['uids']))
            for needle in ('By day', 'Escalation starts', 'Turns into conflict', 'Exact messages, day by day'):
                self.assertIn(needle, page.text, needle)
        else:
            self.assertNotIn('Over time', page.text)
        self.assertEqual(len(hr['categories']), 8)
        names = {b['name'] for c in hr['categories'] for b in c['behaviours']}
        self.assertTrue({'Blackmail', 'Coercion'} & names, names)

    def test_forty_behaviours_in_eight_categories(self):
        import high_risk
        self.assertEqual(len(high_risk.CATEGORIES), 8)
        self.assertEqual(sum(len(b) for _c, b in high_risk.CATEGORIES), 40)

    def test_the_summary_counts_the_held_items(self):
        aid = self.new_analysis(name='Safety Two')
        self.upload(aid, ('chat.csv', CHAT, 'text/csv'))
        s = safety_module.summary(aid)
        self.assertGreaterEqual(s['held'], 2)
        self.assertTrue(safety_module.flagged_uids(aid))


class Absent(unittest.TestCase):
    def test_without_the_engine_the_module_is_quietly_empty(self):
        from unittest import mock
        with mock.patch.object(safety_module, 'AVAILABLE', False):
            self.assertEqual(safety_module.build(1)['held'], [])
            self.assertEqual(safety_module.flagged_uids(1), set())


if __name__ == '__main__':
    unittest.main()


class Connection(unittest.TestCase):
    """Escalation, conflict and High Risk on one timeline, and the on/off groups of the graphs."""

    def bars(self):
        return [{'label': '09:00', 'n': 3, 'calm': 1, 'heated': 1, 'hostile': 1, 'black': 1, 'text': '', 'beh': ['Threats'], 'start': True, 'turn': True},
                {'label': '09:05', 'n': 2, 'calm': 2, 'heated': 0, 'hostile': 0, 'black': 0, 'text': '', 'beh': ['Threats', 'Blackmail'], 'start': False, 'turn': False}]

    def test_the_lane_lists_each_behaviour_with_the_bars_it_appears_in(self):
        import high_risk
        lane = high_risk.lane(self.bars())
        self.assertEqual(lane[0][0], 'Threats')
        self.assertEqual(lane[0][1], {0: 1, 1: 1})
        self.assertEqual(dict(lane)['Blackmail'], {1: 1})

    def test_every_series_of_the_incident_chart_can_be_switched_off(self):
        import bei_charts
        from vibe_check.charts import to_svg
        import high_risk
        svg = to_svg(bei_charts.incident_chart(self.bars(), width=700, lane=high_risk.lane(self.bars())), 't1')
        for g in ('conflict', 'escalation', 'highrisk'):
            self.assertIn(f'data-g="{g}"', svg, g)
        self.assertIn('Threats', svg)

    def test_the_trends_lines_carry_their_series_name(self):
        import bei_charts
        from vibe_check.charts import to_svg
        st = [{'label': f'p{i}', 'axis': f'p{i}', 'rates': {'heated': i, 'hostile': i / 2, 'care': 3}} for i in range(5)]
        svg = to_svg(bei_charts.track_compare(st, width=700, deescalation=[1, 2, 3, 2, 1], black=[0, 0, 1, 0, 0]), 't2')
        for g in ('empathy', 'conflict', 'escalation', 'deescalation', 'highrisk'):
            self.assertIn(f'data-g="{g}"', svg, g)

    def test_incidents_are_annotated_with_their_behaviours(self):
        import high_risk
        rows = [{'uid': 1, 'inside': True}, {'uid': 2, 'inside': True}, {'uid': 3, 'inside': False}, {'uid': 9, 'inside': True}]
        item = {'rows': rows}
        import safety_module
        orig = high_risk.build
        high_risk.build = lambda aid, safety=None: {'by_uid': {1: ['Threats'], 2: ['Threats', 'Blackmail'], 3: ['Racism']}}
        try:
            high_risk.annotate(1, [item])
        finally:
            high_risk.build = orig
        self.assertEqual(item['hr'], [('Threats', 2), ('Blackmail', 1)])
        self.assertEqual(item['hr_n'], 2)
