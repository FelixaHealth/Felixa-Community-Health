"""The flags of the Felixa backend, by name."""
import unittest

import flags
import rules
from tests.test_app import AppCase


class OfMessage(unittest.TestCase):
    def test_a_high_tone_or_category_is_high_severity(self):
        for tone in flags.HIGH_TONES:
            self.assertEqual(flags.of_message({tone: 2}), ('high', [tone]))
            self.assertEqual(flags.of_message({tone: 3}), ('high', [tone]))
            self.assertEqual(flags.of_message({tone: 1}), ('', []), 'a rating of 1 is not present')
        self.assertEqual(flags.of_message({}, {'Hate Speech'}), ('high', ['Hate Speech']))
        self.assertEqual(flags.of_message({'Rude': 3}, {'Violence', 'Hate Speech'}), ('high', ['Rude', 'Hate Speech', 'Violence']))

    def test_sarcasm_agitated_and_profanity_are_medium_only_when_nothing_is_high(self):
        self.assertEqual(flags.of_message({'Sarcastic': 2, 'Agitated': 3}), ('medium', ['Sarcasm', 'Agitated']))
        self.assertEqual(flags.of_message({}, {'Profanity'}), ('medium', ['Profanity']))
        self.assertEqual(flags.of_message({'Sarcastic': 3}, {'Profanity'}), ('medium', ['Profanity', 'Sarcasm']))
        self.assertEqual(flags.of_message({'Sarcastic': 3, 'Rude': 2}), ('high', ['Rude']), 'a high flag hides the medium ones, as in the backend')

    def test_spam_and_the_other_tones_flag_nothing(self):
        self.assertEqual(flags.of_message({'Ignorant': 3, 'Egocentric': 3, 'Neurotic': 3, 'Polite': 3}, {'Spam'}), ('', []))


class OnThePages(AppCase):
    def test_the_flags_are_listed_by_name_on_the_alerts_and_the_flagged_page(self):
        aid = self.with_chat()
        self.run_job(aid, path='analytics', incognito='off', period='all', volume='all', vibe_sensitivity='medium', engine=rules.ENGINE)
        import report
        ctx = report.build(aid)
        found = ctx['felixa_flags']
        self.assertEqual(found['rated'], ctx['metrics'][0]['n'])
        names = [n for n, _ in found['high']] + [n for n, _ in found['medium']]
        self.assertTrue(set(names) <= set(flags.HIGH_NAMES + flags.MEDIUM_NAMES))
        self.assertEqual(found['total_high'] + found['total_medium'], len(found['rows']))
        page = self.client.get(f'/analysis/{aid}/flagged').text
        alerts = self.client.get(f'/analysis/{aid}/results/alerts').text
        self.assertTrue(found['rows'], 'the sample chat has hostile and sarcastic messages')
        if found['rows']:
            self.assertIn('Flags by tone', page)
            self.assertIn('High severity', alerts)
            for name in names:
                self.assertIn(name, page)
                self.assertIn(name, alerts)


if __name__ == '__main__':
    unittest.main()
