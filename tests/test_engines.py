"""The engines of the Engine step as in the Felixa PTB app: the Felixa engine on this computer first, your Claude login next, and under Other engines the Word rules
(no AI), the Anthropic API and the OpenAI API. Run with:

    python3 -m unittest tests.test_engines -v
"""
import json
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_app import AppCase, flash  # noqa: E402

import cloud  # noqa: E402
import rules  # noqa: E402


class WordRules(unittest.TestCase):
    def tones(self, text):
        return {k: v for k, v in rules.rate(text)[0].items() if v}

    def test_each_kind_of_wording_lands_on_its_own_tones(self):
        self.assertEqual(self.tones('Thank you so much, please keep it up!'), {'Polite': 3, 'Encouraging': 1})
        self.assertEqual(self.tones('you absolute idiot, stupid take'), {'Disrespectful': 2})
        self.assertEqual(self.tones("I'll find you"), {'Aggressive': 2})
        self.assertEqual(self.tones("I’ll find you"), {'Aggressive': 2}, 'a curly apostrophe is the same word')
        self.assertEqual(self.tones('WHAT IS GOING ON HERE!! UGH'), {'Agitated': 3})
        self.assertIn('Sarcastic', self.tones('oh great, another patch'))
        self.assertEqual(self.tones('lol that was a good joke'), {'Funny': 2})

    def test_a_word_inside_another_word_is_not_a_hit(self):
        for text in ('hello there, how is it going', 'the shell script is posted', 'Hellsing is a manga', 'a class on thanksgiving dinner'):
            self.assertEqual(self.tones(text), {}, text)

    def test_every_rating_is_a_whole_number_from_0_to_3_for_all_13_tones(self):
        for text in ('', 'x', 'thanks thanks thanks thanks thanks please please', 'fuck fuck fuck you idiot stupid shut up'):
            ratings = rules.rate(text)[0]
            self.assertEqual(sorted(ratings), sorted(rules.ATTRIBUTES), text)
            self.assertTrue(all(isinstance(v, int) and 0 <= v <= 3 for v in ratings.values()), text)

    def test_the_answers_have_the_shapes_the_app_reads(self):
        rating = json.loads(rules.rules_call('You are the FELIXA observable-communication rating engine', json.dumps({'units': [{'external_id': '7', 'text': 'thanks!'}]}))[0])
        self.assertEqual(rating['units'][0]['external_id'], '7')
        self.assertEqual(set(rating['units'][0]['ratings']), set(rules.ATTRIBUTES))
        vibe, model = rules.rules_call('You are the FELIXA Vibe Check engine SENSITIVITY: MEDIUM', '1. thanks all!\n2. you idiot\n3. the schedule is posted')
        vibe = json.loads(vibe)
        self.assertEqual(model, 'word-rules')
        self.assertEqual(vibe['sentiment'], {'friendly': 1, 'neutral': 1, 'unfriendly': 1})
        self.assertEqual(vibe['flaggedMessages'], [{'n': 2, 'type': 'insult', 'severity': 5}])
        self.assertEqual(vibe['toxicityTypes']['insults'], 1)
        self.assertEqual(sum(vibe['sentiment'].values()), 3, 'the counts add up to the messages, as Vibe Check demands')
        guidance = json.loads(rules.rules_call('You are FELIXA, a behavioural intelligence system', json.dumps({'behavior_metrics': [{'attribute': 'Polite', 'FAS': 40.0}], 'goals': ['Are people engaged?']}))[0])
        for key in ('summary', 'working', 'limitations', 'recommendations', 'confidence'):
            self.assertIn(key, guidance)
        self.assertIn('word lists', guidance['summary'])
        narrative = json.loads(rules.rules_call('Write the narrative for the report', json.dumps({'sentimentPercent': {'friendly': 41}, 'impactScore': 6.2, 'messagesAnalyzed': 70}))[0])
        self.assertIn('41%', narrative['communityStrengths'])
        self.assertEqual(rules.rules_call('something else', 'x')[0], '{}')

    def test_sensitivity_changes_what_counts_as_unfriendly(self):
        lines = '1. damn this patch\n2. you are such a clown'
        flagged = lambda level: [f['type'] for f in json.loads(rules.vibe_reply(f'SENSITIVITY: {level}', lines))['flaggedMessages']]  # noqa: E731
        self.assertEqual(flagged('LOW'), [])
        self.assertEqual(flagged('MEDIUM'), ['profanity', 'insult'])

    def test_the_words_it_writes_never_suggest_punishment(self):
        from vibe_check.engine import _PUNITIVE
        for text in (rules.guidance_reply(json.dumps({'behavior_metrics': [], 'goals': []})), rules.synthesis_reply(json.dumps({'sentimentPercent': {'friendly': 10}})),
                     rules.vibe_reply('SENSITIVITY: MEDIUM', '1. you idiot')):
            self.assertIsNone(_PUNITIVE.search(text), text)

    def test_nothing_leaves_this_computer(self):
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('the word rules opened a connection')):
            rules.rules_call('You are the FELIXA Vibe Check engine', '1. hello')


class Engines(AppCase):
    def engine_page(self, aid):
        return self.client.get(f'/analysis/{aid}/check/6').text

    def openai_env(self, key='test-openai-key'):
        return mock.patch.dict(os.environ, {'FELIXA_OPENAI_URL': self.url, 'FELIXA_OPENAI_API_KEY': key})

    def test_the_engine_step_is_laid_out_like_ptb(self):
        aid = self.with_chat()
        page = self.engine_page(aid)
        felixa, other = page.index('Felixa engine'), page.index('<details class="other-engines"')
        self.assertLess(felixa, other, 'the Felixa engine comes first')
        fold = page[other:]
        order = [fold.index(x) for x in ('Word rules', 'Anthropic API', 'OpenAI API')]
        self.assertEqual(order, sorted(order), 'Word rules, then Anthropic API, then OpenAI API sit under Other engines')
        for kind in ('rules', 'cloud', 'openai'):
            self.assertIn('class="tag tag-', fold)
        for tag in ('tag-fastest', 'tag-balanced'):
            self.assertIn(tag, page)
        self.assertIn('Free, nothing leaves this computer', page)
        self.assertIn('Uses your OpenAI key', page)
        self.assertIn('Your Claude login', page)                                                  # with a Claude Code here it is a choice; without, a sentence says where it works
        self.assertEqual(len(re.findall(r'name="engine" value="openai:[\w.-]+"\s*disabled', page)), 2, 'off until the server has a key')

    def test_the_word_rules_run_a_whole_check_with_no_model_and_nothing_sent(self):
        aid = self.with_chat()
        self.fake.calls.clear()
        job = self.run_job(aid, path='check/6', engine='rules:words')                                # no consent box: nothing leaves this computer
        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(self.fake.calls, [], 'no request reached any model')
        row = self.store.get_analysis(aid)
        self.assertEqual((row['engine'], row['cloud_ok'], row['model_used']), ('rules:words', 0, 'word-rules'))
        self.assertTrue(self.store.rated_units(aid), 'the 13 tones were rated')
        self.assertTrue(self.store.latest_vibe(aid)['status'] == 'ok')
        page = self.client.get(f'/analysis/{aid}/workspace').text
        self.assertIn('Your report is ready', page)
        self.assertIn('Alerts', page)

    def test_openai_runs_a_check_through_the_key_after_the_box_is_ticked(self):
        aid = self.with_chat()
        with self.openai_env():
            page = self.engine_page(aid)
            self.assertEqual(len(re.findall(r'name="engine" value="openai:[\w.-]+"\s*disabled', page)), 0)
            self.assertNotIn('OpenAI is not set up on this server yet', page)
            r = self.client.post(f'/analysis/{aid}/check/6', data={'engine': 'openai:gpt-4o-mini'})
            self.assertIn('OpenAI to be read', flash(r))
            self.assertIsNone(self.store.latest_job(aid))
            for bad in ('openai:gpt-9', 'openai:'):
                self.assertIn('Choose an engine', flash(self.client.post(f'/analysis/{aid}/check/6', data={'engine': bad, 'cloud_ok': '1'})))
            self.fake.openai_requests.clear()
            job = self.run_job(aid, path='check/6', engine='openai:gpt-4o-mini', cloud_ok='1')
            self.assertEqual(job['status'], 'complete', job)
        self.assertGreater(len(self.fake.openai_requests), 3)
        body = self.fake.openai_requests[0]
        self.assertEqual(body['model'], 'gpt-4o-mini')
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user'])
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        row = self.store.get_analysis(aid)
        self.assertEqual((row['engine'], row['cloud_ok']), ('openai:gpt-4o-mini', 1))
        self.assertEqual(set(self.fake.models_used[-len(self.fake.openai_requests):]), {'openai:gpt-4o-mini'})

    def test_openai_without_a_key_is_refused_in_plain_words(self):
        aid = self.with_chat()
        with mock.patch.dict(os.environ, {'FELIXA_OPENAI_URL': self.url}):
            for name in ('FELIXA_OPENAI_API_KEY', 'OPENAI_API_KEY'):
                os.environ.pop(name, None)
            self.assertIn('OpenAI is not set up on this server yet', self.engine_page(aid))
            r = self.client.post(f'/analysis/{aid}/check/6', data={'engine': 'openai:gpt-4o-mini', 'cloud_ok': '1'})
            self.assertIn('OpenAI is not set up on this server', flash(r))
            self.assertIsNone(self.store.latest_job(aid))

    def test_a_wrong_openai_key_fails_in_plain_words_and_never_shows_the_key(self):
        aid = self.with_chat()
        secret = 'sk-wrong-key-123456789'
        with self.openai_env(secret):
            job = self.run_job_fast(aid, path='check/6', engine='openai:gpt-4o-mini', cloud_ok='1')
        self.assertEqual(job['status'], 'failed', job)
        self.assertIn('OpenAI', job['message'])
        self.assertNotIn(secret, job['message'])
        self.assertNotIn(secret, self.client.get(f'/analysis/{aid}/workspace').text)

    def test_engine_helpers_know_the_new_engines(self):
        self.assertTrue(cloud.is_cloud('openai:gpt-4o'))
        self.assertFalse(cloud.is_cloud('rules:words'), 'the word rules send nothing, so they need no consent')
        self.assertTrue(rules.is_rules('rules:words'))
        self.assertTrue(cloud.known('openai:gpt-4o') and not cloud.known('openai:nope'))
        self.assertEqual((cloud.provider('openai:gpt-4o'), cloud.provider('claude:claude-haiku-5-5')), ('OpenAI', 'Anthropic'))
        self.assertEqual(cloud.label('openai:gpt-4o-mini'), 'GPT-4o mini (OpenAI)')
        with self.openai_env():
            self.assertTrue(cloud.available('openai:gpt-4o'))
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ('FELIXA_OPENAI_API_KEY', 'OPENAI_API_KEY'):
                os.environ.pop(name, None)
            self.assertFalse(cloud.available('openai:gpt-4o'))


if __name__ == '__main__':
    unittest.main()
