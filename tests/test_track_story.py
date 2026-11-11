"""The progression diagram under the Escalation and Conflict graphs: calm, yellow flag, red flag, easing, with example messages."""
import unittest

import track
from tests.test_app import AppCase


class Story(AppCase):
    def test_a_chat_without_flags_gets_a_labelled_example(self):
        s = track.story({'stretches': []}, [])
        self.assertFalse(s['real'])
        self.assertEqual([x['key'] for x in s['stages']], ['calm', 'yellow', 'red', 'ease'])
        self.assertTrue(all(x['examples'] and not x['real'] for x in s['stages']))

    def test_the_track_pages_show_the_diagram_with_both_flag_colours(self):
        aid = self.with_chat()
        self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
        for key in ('escalation', 'conflict'):
            r = self.client.get(f'/analysis/{aid}/track/{key}')
            if r.status_code == 404:
                continue                                    # too few messages for a timeline: no track page at all
            self.assertIn('From heat to conflict', r.text)
            self.assertIn('Yellow flag: escalation starts', r.text)
            self.assertIn('Red flag: it turns into conflict', r.text)
        r = self.client.get(f'/analysis/{aid}/track/empathy')
        self.assertNotIn('From heat to conflict', r.text, 'Empathy has no flags')


if __name__ == '__main__':
    unittest.main()
