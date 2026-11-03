"""The Settings page: defaults for the whole app (engine, strictness, hiding names), the look, the Claude login, and deleting everything. Run with:

    python3 -m unittest tests.test_settings -v
"""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_app import AppCase, flash  # noqa: E402


class Settings(AppCase):
    def test_the_page_is_in_the_top_bar_and_covers_the_whole_app(self):
        top = self.client.get('/').text.split('</header>', 1)[0]
        self.assertIn('href="/settings"', top)
        page = self.client.get('/settings').text
        for needle in ('Settings for the whole app', 'How a new check starts', 'Appearance', 'Your Claude login', 'About this app', 'Danger zone', 'name="default_engine"',
                       'name="default_sensitivity"', 'name="default_incognito"', 'name="theme-choice"'):
            self.assertIn(needle, page, needle)
        self.assertIn('aria-current="page"', page.split('</header>', 1)[0])

    def test_saved_defaults_come_back_and_are_used_by_new_workplaces_and_new_checks(self):
        r = self.client.post('/settings', data={'default_engine': 'rules:words', 'default_sensitivity': 'low', 'default_incognito': '1'})
        self.assertIn('Settings saved', flash(r))
        page = self.client.get('/settings').text
        self.assertRegex(page, r'<option value="rules:words" selected>')
        self.assertRegex(page, r'name="default_sensitivity" value="low" checked')
        self.assertRegex(page, r'name="default_incognito" value="1" checked')
        aid = self.new_analysis(name='Defaults')
        self.assertEqual(self.store.get_analysis(aid)['vibe_sensitivity'], 'low')
        self.store.new_check(aid)
        self.assertEqual(self.store.get_analysis(aid)['incognito'], 1)
        engine_page = self.client.get(f'/analysis/{aid}/check/6').text
        self.assertRegex(engine_page, r'value="rules:words"\s+checked')
        self.client.post('/settings', data={'default_engine': '', 'default_sensitivity': 'medium'})
        self.store.new_check(aid)
        self.assertEqual(self.store.get_analysis(aid)['incognito'], 0)

    def test_a_value_that_is_not_offered_is_refused_and_nothing_changes(self):
        self.client.post('/settings', data={'default_engine': '', 'default_sensitivity': 'high'})
        self.assertIn('Choose one of the engines', flash(self.client.post('/settings', data={'default_engine': 'gpt-99', 'default_sensitivity': 'low'})))
        self.assertIn('Choose how strict', flash(self.client.post('/settings', data={'default_engine': '', 'default_sensitivity': 'wild'})))
        self.assertEqual(self.store.setting('default_sensitivity'), 'high')

    def test_the_claude_login_is_shown_as_ready_missing_or_off(self):
        import cloud
        with mock.patch.object(cloud, 'login_available', return_value=True), mock.patch.object(cloud, 'login_offered', return_value=True):
            self.assertIn('Ready.', self.client.get('/settings').text)
        with mock.patch.object(cloud, 'login_available', return_value=False), mock.patch.object(cloud, 'login_offered', return_value=True):
            self.assertIn('Claude Code was not found', self.client.get('/settings').text)
        with mock.patch.object(cloud, 'login_offered', return_value=False):
            self.assertIn('switched off', self.client.get('/settings').text)

    def test_delete_everything_needs_the_words_and_then_removes_every_workplace(self):
        self.new_analysis(name='One')
        self.new_analysis(name='Two')
        self.client.post('/settings', data={'default_engine': '', 'default_sensitivity': 'low'})
        before = len(self.store.workplaces())
        self.assertGreaterEqual(before, 2)
        self.assertIn('Nothing was deleted', flash(self.client.post('/settings/delete-all', data={'confirm': 'yes'})))
        self.assertEqual(len(self.store.workplaces()), before)
        self.assertIn('Deleted', flash(self.client.post('/settings/delete-all', data={'confirm': ' Delete Everything '})))
        self.assertEqual(self.store.workplaces(), [])
        self.assertEqual(self.store.setting('default_sensitivity'), 'low', 'the settings stay')


if __name__ == '__main__':
    unittest.main()
