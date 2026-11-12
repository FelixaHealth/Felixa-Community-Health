"""The High Risk story and its edge cases: identical texts, overlapping incidents, Incognito. Run with:

    python3 -m unittest tests.test_high_risk_story -v
"""
import datetime as dt
import re
import unittest

import analysis
import high_risk
from tests.test_app import AppCase

CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')


class Story(AppCase):
    def chat(self, rows, name='Story Co', incognito=False):
        """rows = [(minute, text, {tone: rating})] from Nov 9, 09:00."""
        aid = self.new_analysis(name=name)
        base = dt.datetime(2026, 11, 9, 9, 0)
        self.store.add_units(aid, [{'text': t, 'source': 'general', 'timestamp': (base + dt.timedelta(minutes=m)).isoformat()} for m, t, _r in rows], 'TEST')
        with self.store.db() as c:
            for u, (_m, _t, r) in zip(self.store.units_for(aid), rows):
                for a in analysis.ATTRIBUTES:
                    c.execute('INSERT INTO unit_ratings(unit_id,attribute,rating,rationale) VALUES(?,?,?,?)', (u['id'], a, r.get(a, 0), ''))
        analysis.calculate_metrics(aid)
        self.store.update_analysis(aid, rated_units_total=len(rows), model_used='qwen2.5:7b', status='analyzed')
        if incognito:
            self.store.update_analysis(aid, incognito=1)
        return aid

    def test_two_messages_with_the_same_words_are_both_found(self):
        aid = self.chat([(0, 'Morning', {'Polite': 2}), (1, 'You are pathetic', {'Rude': 3}), (2, 'ok', {'Polite': 2}), (3, 'You are pathetic', {'Rude': 3})])
        self.store.save_vibe_run(aid, None, {'status': 'ok', 'flagged': [{'type': 'insult', 'message': 'You are pathetic', 'timestamp': '2026-11-09T09:01:00', 'source': 'general'}]})
        hr = high_risk.build(aid)
        ids = [u['id'] for u in self.store.units_for(aid) if u['text'] == 'You are pathetic']
        self.assertEqual(len(ids), 2)
        self.assertTrue(all('Personal Attacks' in hr['by_uid'].get(i, []) for i in ids), hr['by_uid'])

    def test_a_message_shown_as_context_of_one_incident_is_not_repeated_in_the_next(self):
        hot = {'Aggressive': 3, 'Rude': 3}
        rows = [(0, 'hello', {'Polite': 2}), (1, 'You never listen', hot), (2, 'Stop it', hot), (12, 'Let us take a break', {'Empathetic': 3}), (20, 'Thanks all', {'Polite': 2}),
                (40, 'This is useless', hot), (41, 'You are useless', hot)]
        aid = self.chat(rows)
        story = high_risk.stories(aid, high_risk.build(aid))
        texts = [n['text'] for c in story['cards'] for n in c['nodes'] if not n.get('run')]
        self.assertEqual(len(texts), len(set(texts)), texts)

    def test_incognito_leaves_no_clock_time_on_the_high_risk_page(self):
        hot = {'Aggressive': 3, 'Rude': 3}
        rows = [(i * 3, f'calm {i}', {'Polite': 2}) for i in range(10)] + [(40, 'You never listen', hot), (42, 'You are useless', hot), (50, 'Let us take a break', {'Empathetic': 3})]
        aid = self.chat(rows, name='Hidden Co', incognito=True)
        for q in ('', '?view=time&tf=30', '?view=category&sub=type', '?view=category&sub=totals'):
            page = self.client.get(f'/analysis/{aid}/safety{q}')
            self.assertEqual(page.status_code, 200)
            body = page.text[page.text.index('class="hr-sw"'):page.text.index('class="hr-foot"')]
            self.assertIsNone(CLOCK.search(re.sub(r'<(?:script|style)\b.*?</(?:script|style)>', '', body, flags=re.S)), (q, CLOCK.search(body)))

    def test_with_incognito_off_the_times_show(self):
        hot = {'Aggressive': 3, 'Rude': 3}
        rows = [(i * 3, f'calm {i}', {'Polite': 2}) for i in range(10)] + [(40, 'You never listen', hot), (42, 'You are useless', hot)]
        aid = self.chat(rows, name='Open Co')
        page = self.client.get(f'/analysis/{aid}/safety')
        self.assertIsNotNone(CLOCK.search(page.text[page.text.index('class="hr-sw"'):page.text.index('class="hr-foot"')]))


if __name__ == '__main__':
    unittest.main()
