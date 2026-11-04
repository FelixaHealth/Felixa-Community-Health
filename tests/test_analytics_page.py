"""The Analytics page: the whole set-up on one page, then Run Analysis."""
import unittest

import rules
from tests.test_app import AppCase


class Analytics(AppCase):
    def test_the_page_lists_the_six_choices_and_a_run_button(self):
        aid = self.with_chat()
        page = self.client.get(f'/analysis/{aid}/analytics')
        self.assertEqual(page.status_code, 200)
        for needle in ('1. Upload Data', '2. Incognito', '3. Timeframe', '4. Number of Messages', '5. Sensitivity', '6. Engine', 'Run Analysis', 'name="engine"', 'name="vibe_sensitivity"'):
            self.assertIn(needle, page.text, needle)
        self.assertIn('Word rules', page.text, 'every engine shows with its time and price')
        self.assertEqual(self.client.get('/analysis/9999/analytics').status_code, 404)

    def test_run_analysis_saves_every_choice_and_starts_the_check(self):
        aid = self.with_chat()
        r = self.run_job(aid, path='analytics', incognito='on', period='all', volume='all', vibe_sensitivity='high', engine=rules.ENGINE)
        self.assertEqual(r['status'], 'complete')
        a = self.store.get_analysis(aid)
        self.assertEqual((a['incognito'], a['check_volume'], a['vibe_sensitivity'], a['engine']), (1, 0, 'high', rules.ENGINE))

    def test_a_missing_or_wrong_choice_comes_back_to_the_page_with_a_reason(self):
        aid = self.with_chat()
        for data, said in (({'vibe_sensitivity': 'nope', 'volume': 'all', 'engine': rules.ENGINE}, 'sensitive'),
                           ({'vibe_sensitivity': 'low', 'volume': 'x', 'engine': rules.ENGINE}, 'how many'),
                           ({'vibe_sensitivity': 'low', 'volume': 'all', 'engine': 'claude:haiku'}, '')):
            r = self.client.post(f'/analysis/{aid}/analytics', data=data)
            self.assertEqual((r.status_code, r.headers['location']), (303, f'/analysis/{aid}/analytics'))

    def test_new_analytics_and_uploads_lead_to_the_page(self):
        aid = self.with_chat()
        self.assertEqual(self.client.post(f'/analysis/{aid}/check/new').headers['location'], f'/analysis/{aid}/analytics')
        r = self.client.post(f'/analysis/{aid}/add-text', data={'raw_text': 'hello there\nsecond line', 'back': 'analytics'})
        self.assertEqual(r.headers['location'], f'/analysis/{aid}/analytics')


if __name__ == '__main__':
    unittest.main()
