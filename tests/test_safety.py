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
        cat = self.client.get(f'/analysis/{aid}/safety?view=category&sub=totals')
        self.assertEqual(cat.status_code, 200)
        for needle in ('Quotes', 'Messages', 'Matches', 'Types', 'By incident', 'By category', 'Totals', 'Review', 'no reader yet'):
            self.assertIn(needle, cat.text, needle)
        self.assertIn('Blackmail', cat.text)
        self.assertNotIn('Over time', page.text)
        for noise in ('Held for a person', 'A question to ask', 'flag black'):
            self.assertNotIn(noise, page.text, noise)
        self.assertEqual(self.client.get(f'/analysis/{aid}/safety?view=category&sub=type').status_code, 200)
        import high_risk
        hr = high_risk.build(aid, safety_module.build(aid))
        ot = high_risk.over_time(aid, hr)
        story = high_risk.stories(aid, hr)
        self.assertTrue(story['cards'] or story['outside'])
        self.assertIn('Not in an escalation incident', page.text) if story['outside'] else None
        for node in (story['outside'] or {'nodes': []})['nodes']:
            self.assertTrue(node['types'])
        if ot:
            self.assertTrue(ot['svg_a'].startswith('<svg'))
            self.assertLessEqual(sum(d['count'] for d in ot['days']), len(hr['uids']))
            for needle in ('By incident', 'Escalation starts', 'Turns into conflict'):
                self.assertIn(needle, page.text, needle)
        self.assertEqual(len(hr['categories']), 9)
        names = {b['name'] for c in hr['categories'] for b in c['behaviours']}
        self.assertTrue({'Blackmail', 'Coercion'} & names, names)

    def test_forty_five_behaviours_in_nine_categories(self):
        import high_risk
        self.assertEqual(len(high_risk.CATEGORIES), 9)
        self.assertEqual(sum(len(b) for _c, b in high_risk.CATEGORIES), 45)
        self.assertEqual(high_risk.CATEGORIES[-1][0], 'Cyber Attacks & Security Threats')

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


class Story(unittest.TestCase):
    def test_long_runs_of_plain_matches_collapse_and_flagged_steps_stay(self):
        import high_risk
        plain = {'flags': [], 'held': False, 'types': ['Insults'], 'text': 'x', 'when': ''}
        flagged = {'flags': ['R'], 'held': False, 'types': [], 'text': 'y', 'when': ''}
        held = {'flags': [], 'held': True, 'types': ['Threats'], 'text': 'z', 'when': ''}
        out = high_risk._collapse([flagged] + [dict(plain) for _ in range(6)] + [held])
        self.assertEqual([bool(n.get('run')) for n in out], [False, False, True, False])
        self.assertEqual(out[2]['run'], 5)
        self.assertEqual(high_risk._after(0, True), 'at once')
        self.assertEqual(high_risk._after(14, True), 'after 14 min')
        self.assertEqual(high_risk._after(3, False), 'after 3 messages')


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


class SentimentPhases(unittest.TestCase):
    def test_a_tone_is_counted_in_the_escalating_or_the_calming_part(self):
        import sentiment_phases
        import analysis
        units = []
        for i in range(30):
            r = dict.fromkeys(analysis.ATTRIBUTES, 0)
            if 10 <= i <= 14:
                r['Aggressive'] = 3
                r['Rude'] = 2
            if i == 16:
                r['Empathetic'] = 3
            units.append({'id': i + 1, 'text': f'm{i}', 'source': 's', 'timestamp': f'2026-11-09T09:{i:02d}:00', 'ratings': r})
        out = sentiment_phases.build(units)
        self.assertTrue(out['any'])
        self.assertEqual([c['name'] for c in out['categories']], ['Positive', 'Tense', 'Toxic'])
        self.assertEqual(len(out['attributes']), 13)
        tox = out['categories'][2]
        self.assertGreater(tox['esc_n'], 0)
        self.assertEqual(tox['de_n'], 0)
        emp = next(a for a in out['attributes'] if a['name'] == 'Empathetic')
        self.assertEqual(emp['esc_n'], 0)


class TrendsWithoutConflict(unittest.TestCase):
    def test_trends_can_leave_conflict_to_its_own_view(self):
        import bei_charts
        import track
        from vibe_check.charts import to_svg
        st = [{'label': f'p{i}', 'axis': f'p{i}', 'rates': {'heated': i, 'hostile': i / 2, 'care': 3}} for i in range(5)]
        svg = to_svg(bei_charts.track_compare(st, width=700, conflict=False), 't3')
        self.assertNotIn('data-g="conflict"', svg)
        self.assertNotIn('Conflict', svg)
        self.assertIn('data-g="escalation"', svg)
        self.assertIn('data-g="conflict"', to_svg(bei_charts.track_compare(st, width=700), 't4'))

    def test_the_trends_sentences_leave_conflict_out(self):
        import track
        m = {'stretches': [{'rates': {'care': i % 3, 'hostile': i % 2, 'heated': i % 4}} for i in range(8)]}
        out = track.link(m, ('escalation',))
        self.assertTrue(all('conflict' not in s for s in out['sentences']))



class CyberAttacks(unittest.TestCase):
    CASES = [
        ('Your account is locked, verify here https://secure-login.example.xyz/a', 'Phishing'),
        ('Send me your password please', 'Credential or Code Requests'),
        ('send the code you just received', 'Credential or Code Requests'),
        ('Run setup_update.exe now', 'Malicious Links or Files'),
        ("I'll hack your account tonight", 'Hacking Threats'),
        ('everyone join and spam him', 'Raids and Flooding'),
        ('they are going to ddos the server', 'Raids and Flooding'),
    ]
    HARMLESS = ['Never send your password to anyone', 'Please remember to verify the numbers in the report', 'I will take over the meeting agenda',
                'we should raid the dungeon tonight', "I'll wipe the whiteboard", 'Do not share the code you just received with anyone']

    def test_each_behaviour_is_read(self):
        import cyber_rules
        for text, behaviour in self.CASES:
            self.assertIn(behaviour, [f['behaviour'] for f in cyber_rules.scan(text)], text)

    def test_warnings_and_everyday_wording_are_not_matches(self):
        import cyber_rules
        for text in self.HARMLESS:
            self.assertEqual(cyber_rules.scan(text), [], text)

    def test_the_same_line_again_and_again_is_flooding(self):
        import cyber_rules
        units = [{'id': i, 'text': 'join the raid now join the raid', 'timestamp': f'2026-11-09T09:0{i}:00'} for i in range(1, 5)] + [{'id': 9, 'text': 'a different line altogether', 'timestamp': '2026-11-09T09:06:00'}]
        self.assertEqual(cyber_rules.flooding(units), {1, 2, 3, 4})

    def test_the_cyber_category_is_listed_and_found_on_the_page(self):
        import high_risk
        self.assertTrue(set(cyber_rules_names()) <= high_risk.READ)


def cyber_rules_names():
    import cyber_rules
    return cyber_rules.BEHAVIOURS


class SenderCapture(unittest.TestCase):
    def test_an_author_column_and_a_reply_column_are_kept(self):
        from vibe_check import ingest
        rows = [{'author': 'Ana', 'message': 'You never listen to anyone here, honestly', 'date': '2026-11-09 09:00', 'reply_to': 'Bo'},
                {'author': 'Bo', 'message': 'Stop it, that is not okay please', 'date': '2026-11-09 09:01'}]
        units = ingest.units_from_rows(rows, 't')
        self.assertEqual([(u['sender'], u['reply_to']) for u in units], [('Ana', 'Bo'), ('Bo', None)])

    def test_a_chat_label_and_a_mention_are_read_from_the_text(self):
        import store
        self.assertEqual(store._label_of('Alice Brown: hello there'), 'Alice Brown')
        self.assertIsNone(store._label_of('Note: remember this'))
        self.assertEqual(store._mention_of('hey @carol look at this'), 'carol')


class TimeframeView(unittest.TestCase):
    def test_a_time_step_that_would_give_too_many_bars_is_swapped_for_a_larger_one(self):
        import datetime as dt
        import high_risk
        base = dt.datetime(2026, 11, 1, 0, 0)
        ordered = [{'id': i} for i in range(3)]
        stamps = [base, base + dt.timedelta(days=3), base + dt.timedelta(days=9)]
        groups, starts, used = high_risk._time_buckets(ordered, stamps, {0, 2}, 30)
        self.assertEqual(used, 300)                       # 30 minutes over 9 days is far too many bars: 5 hours is the smallest step that stays within 60
        self.assertEqual(len(groups), 44)
        self.assertEqual([len(g) for g in groups][0], 1)
        self.assertEqual([len(g) for g in groups][-1], 1)
        groups, starts, used = high_risk._time_buckets(ordered, stamps, {0, 2}, 0)
        self.assertEqual((len(groups), used), (1, 0))
        self.assertEqual(groups[0], [0, 2])


class TrendsAnswer(unittest.TestCase):
    def test_the_trends_page_answers_the_question_in_one_sentence(self):
        import track
        m = {'stretches': [{'rates': {'care': i, 'hostile': i % 3, 'heated': 10 - i}} for i in range(8)]}
        out = track.link(m, ('escalation',))
        self.assertTrue(out['answer'].startswith('Yes, it goes with less'), out['answer'])
        few = track.link({'stretches': [{'rates': {'care': 1, 'hostile': 1, 'heated': 1}}] * 2}, ('escalation',))
        self.assertEqual(few['answer'], 'Not enough bars yet to say.')
