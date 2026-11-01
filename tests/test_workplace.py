"""Unit tests for the pieces behind the workplace setup and the New check: choices, estimates, incognito, link reading, voice."""
import io
import json
import logging
import os
import pathlib
import socket
import sys
import types
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import cloud  # noqa: E402
import linkfetch  # noqa: E402
import privacy  # noqa: E402
import voice  # noqa: E402
import workplace  # noqa: E402


class Form(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class Choices(unittest.TestCase):
    def test_a_choice_must_be_on_the_list_and_other_needs_text(self):
        areas = workplace.AREAS
        self.assertEqual(workplace.pick(Form(area='Gaming'), 'area', areas, 'the area'), ('Gaming', ''))
        self.assertEqual(workplace.pick(Form(area='Space mining'), 'area', areas, 'the area')[0], '')       # not on the list
        self.assertEqual(workplace.pick(Form(), 'area', areas, 'the area')[0], '')
        self.assertIn('tell us', workplace.pick(Form(area='Other', area_other='  '), 'area', areas, 'the area')[1])
        self.assertEqual(workplace.pick(Form(area='Other', area_other='  Space   mining '), 'area', areas, 'the area'), ('Space mining', ''))
        self.assertEqual(len(workplace.pick(Form(area='Other', area_other='x' * 500), 'area', areas, 'the area')[0]), workplace.MAX_TEXT)

    def test_a_saved_value_comes_back_to_the_form(self):
        self.assertEqual(workplace.split_choice('Gaming', workplace.AREAS), ('Gaming', ''))
        self.assertEqual(workplace.split_choice('Space mining', workplace.AREAS), ('Other', 'Space mining'))
        self.assertEqual(workplace.split_choice('', workplace.AREAS), ('', ''))

    def test_goals_are_several_choices_and_other_needs_text(self):
        first, second = workplace.GOAL_OPTIONS[0], workplace.GOAL_OPTIONS[3]
        self.assertEqual(workplace.read_goals(Form(goals=[first, second])), ([first, second], '', ''))
        self.assertEqual(workplace.read_goals(Form(goals=[second, first]))[0], [first, second])         # in the order of the list
        self.assertIn('Choose at least one', workplace.read_goals(Form(goals=[]))[2])
        self.assertIn('Other', workplace.read_goals(Form(goals=['Other']))[2])
        self.assertEqual(workplace.read_goals(Form(goals=['Other'], goal_other=' do newcomers  stay? ')), ([], 'do newcomers stay?', ''))
        self.assertEqual(workplace.read_goals(Form(goals=[first], goal_other='ignored'))[1], '')          # text without the box ticked is dropped
        self.assertEqual(workplace.read_goals(Form(goals=['<script>', first]))[0], [first])               # unknown values are dropped


class Estimates(unittest.TestCase):
    def test_model_size_comes_from_the_server_or_the_name(self):
        self.assertEqual(workplace.params_billions('qwen2.5:7b', {'parameter_size': '7.6B'}), 7.6)
        self.assertEqual(workplace.params_billions('llama3.2:3b'), 3.0)
        self.assertEqual(workplace.params_billions('tinyllama', {'parameter_size': '637M'}), 0.637)
        self.assertEqual(workplace.params_billions('mystery'), 7.0)

    def test_calls_follow_the_volume_and_a_bigger_model_takes_longer(self):
        parts = workplace.calls_for(900, 300, sources=2)
        self.assertEqual(parts['messages'], 300)
        self.assertEqual(parts['rating'], 100)                                  # 300 messages, three to a call
        self.assertEqual(parts['vibe'], 8 + 2)                                  # 40 messages to a call, plus one per source
        self.assertEqual(parts['total'], 100 + 10 + 2)
        self.assertEqual(workplace.calls_for(50, 300)['messages'], 50)           # never more than there are
        self.assertEqual(workplace.calls_for(900, 0)['messages'], 900)           # 0 means all of them
        small, big = workplace.estimate(300, 300, 3.0), workplace.estimate(300, 300, 14.0)
        self.assertLess(small['high'], big['low'] * 2)
        self.assertGreater(big['low'], small['low'])
        self.assertLess(small['low'], small['high'])

    def test_the_sentence_reads_naturally_at_every_size(self):
        self.assertEqual(workplace.estimate(0, 300, 7)['text'], 'Add messages to see an estimate.')
        self.assertEqual(workplace.estimate(4, 300, 0.1)['text'], 'Under a minute')
        self.assertTrue(workplace.estimate(300, 300, 7)['text'].startswith('About '))
        self.assertNotIn('About under', workplace.estimate(10, 300, 7)['text'])
        self.assertIn('hours', workplace.estimate(5000, 0, 14)['text'])

    def test_engines_list_the_default_first_and_say_what_is_not_downloaded(self):
        models = [{'name': 'llama3.2:3b', 'details': {'parameter_size': '3.2B'}}, {'name': 'qwen2.5:7b', 'details': {'parameter_size': '7.6B'}}]
        engines = workplace.engine_options(models, 'qwen2.5:7b', 300, 300)
        self.assertEqual([e['name'] for e in engines], ['qwen2.5:7b', 'llama3.2:3b'])
        self.assertEqual([e['default'] for e in engines], [True, False])
        self.assertTrue(all(e['installed'] for e in engines))
        self.assertLess(engines[1]['estimate']['low'], engines[0]['estimate']['low'])
        missing = workplace.engine_options([], 'qwen2.5:7b', 300, 300)
        self.assertEqual((missing[0]['name'], missing[0]['installed']), ('qwen2.5:7b', False))
        bare = workplace.engine_options([{'name': 'qwen2.5:latest', 'details': {'parameter_size': '7.6B'}}], 'qwen2.5', 10, 300)
        self.assertTrue(bare[0]['installed'])


class Incognito(unittest.TestCase):
    def run_scrub(self, *texts):
        names = privacy.Pseudonyms()
        return [privacy.scrub(t, names) for t in texts]

    def test_speaker_labels_become_the_same_member_each_time(self):
        out = self.run_scrub('Alice Brown: hello there', 'bob: hi Alice', 'alice brown: thanks', 'Bob: bye')
        self.assertEqual(out, ['Member 1: hello there', 'Member 2: hi Alice', 'Member 1: thanks', 'Member 2: bye'])

    def test_whatsapp_lines_keep_the_time_and_lose_the_name(self):
        out = self.run_scrub('12/03/2026, 10:15 - Alice Brown: thanks for the help', '[12/03/2026 10:16] Bob: you are welcome')
        self.assertEqual(out[0], '12/03/2026, 10:15 - Member 1: thanks for the help')
        self.assertEqual(out[1], '[12/03/2026 10:16] Member 2: you are welcome')

    def test_mentions_and_emails_go(self):
        out = self.run_scrub('thanks @Bob_the-builder and <@1234567890123> and <@!1234567890123>, mail me at jo.smith@example.com')[0]
        self.assertEqual(out, 'thanks @member and @member and @member, mail me at [email]')

    def test_ordinary_prefixes_and_sentences_are_left_alone(self):
        for text in ('Note: the patch is out', 'Edit: fixed the typo', 'what time is it? 10:30 works', 'I think so: maybe later', 'Reminder: be kind'):
            self.assertEqual(self.run_scrub(text)[0], text if not text.startswith('I think') else 'Member 1: maybe later', text)

    def test_running_it_twice_changes_nothing(self):
        names = privacy.Pseudonyms()
        once = privacy.scrub('Alice: hi @Bob', names)
        self.assertEqual(privacy.scrub(once, privacy.Pseudonyms()), once)

    def test_a_name_inside_a_sentence_is_not_found(self):
        self.assertEqual(self.run_scrub('thanks Alice for the help')[0], 'thanks Alice for the help')            # said so on the page


class Hosts(unittest.TestCase):
    def resolve(self, *addresses):
        return mock.patch('linkfetch.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, 80)) for a in addresses])

    def test_only_public_addresses_are_allowed(self):
        for bad in ('127.0.0.1', '10.0.0.5', '192.168.1.1', '169.254.169.254', '172.16.0.1', '0.0.0.0', '100.64.0.1'):
            with self.resolve(bad), self.assertRaises(linkfetch.LinkError, msg=bad):
                linkfetch.public_address('example.com', 80)
        with self.resolve('93.184.216.34'):
            self.assertEqual(linkfetch.public_address('example.com', 80), '93.184.216.34')

    def test_one_private_answer_among_public_ones_refuses_the_name(self):
        with self.resolve('93.184.216.34', '10.0.0.5'), self.assertRaises(linkfetch.LinkError):
            linkfetch.public_address('example.com', 80)

    def test_ipv6_loopback_and_a_name_that_does_not_resolve(self):
        with mock.patch('linkfetch.socket.getaddrinfo', return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 80, 0, 0))]), self.assertRaises(linkfetch.LinkError):
            linkfetch.public_address('localhost', 80)
        with mock.patch('linkfetch.socket.getaddrinfo', side_effect=socket.gaierror), self.assertRaises(linkfetch.LinkError):
            linkfetch.public_address('no-such-host.invalid', 80)

    def test_the_address_is_checked_before_anything_is_sent(self):
        for url in ('ftp://example.com/x', 'file:///etc/passwd', 'javascript:alert(1)', 'http://user:pw@example.com/', 'http://example.com:22/', 'example.com', ''):
            with self.assertRaises(linkfetch.LinkError, msg=url):
                linkfetch.check_url(url)
        self.assertEqual(linkfetch.check_url('https://example.com/a?b=1')[1], 443)
        self.assertEqual(linkfetch.check_url('http://example.com:8080/')[1], 8080)


class FakeConnection:
    def __init__(self, status=200, headers=None, body=b'', location=None):
        self.status, self.body, self.location = status, body, location
        self.headers = {'Content-Type': 'text/html; charset=utf-8', **(headers or {})}
        self.closed = False

    def request(self, method, path, headers=None):
        self.sent = (method, path, headers)

    def getresponse(self):
        return self

    def getheader(self, name):
        if name == 'Location':
            return self.location
        return self.headers.get(name)

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def close(self):
        self.closed = True


PAGE = b"""<html><head><title>x</title><style>p{}</style></head><body><nav>Home About Contact us today</nav>
<h1>Welcome to the guild forum</h1><p>Thanks everyone for the warm welcome, it was great.</p><script>var secret = 'do not read this line';</script>
<ul><li>One match ended with sharp words, but we talked it out.</li></ul><footer>Copyright notice for the whole forum</footer></body></html>"""


class Pages(unittest.TestCase):
    def test_a_page_gives_its_readable_lines_without_menus_or_scripts(self):
        lines = linkfetch.page_text(PAGE.decode())
        self.assertIn('Welcome to the guild forum', lines)
        self.assertIn('Thanks everyone for the warm welcome, it was great.', lines)
        self.assertIn('One match ended with sharp words, but we talked it out.', lines)
        self.assertFalse([x for x in lines if 'secret' in x or 'Home About' in x or 'Copyright' in x])

    def test_a_page_is_fetched_with_a_plain_get_and_closed(self):
        conn = FakeConnection(body=PAGE)
        host, lines = linkfetch.fetch_lines('https://forum.example/thread/1?page=2', opener=lambda scheme, h, port: conn)
        self.assertEqual(host, 'forum.example')
        self.assertEqual(conn.sent[:2], ('GET', '/thread/1?page=2'))
        self.assertIn('FelixaCommunityHealth', conn.sent[2]['User-Agent'])
        self.assertTrue(conn.closed)
        self.assertGreaterEqual(len(lines), 3)

    def test_redirects_are_followed_a_few_times_and_each_one_is_checked(self):
        seen = []

        def opener(scheme, host, port):
            seen.append(host)
            return FakeConnection(status=302, location='http://localhost:8080/admin') if host == 'a.example' else FakeConnection(body=PAGE)
        self.assertEqual(linkfetch.fetch_lines('https://a.example/', opener=opener)[0], 'a.example')       # the page is named for the link that was typed
        self.assertEqual(seen, ['a.example', 'localhost'])                                                  # and the redirect was followed through the opener
        with self.assertRaises(linkfetch.LinkError):
            linkfetch.fetch_html('https://a.example/', opener=lambda *a: FakeConnection(status=301, location='https://a.example/'))      # a loop

    def test_the_real_connection_refuses_a_private_address_even_after_a_redirect_or_for_a_tricky_name(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
        with mock.patch('linkfetch.socket.getaddrinfo', return_value=private), mock.patch('linkfetch.socket.create_connection') as connect:
            for url in ('http://evil.example/', 'https://evil.example/', 'http://127.0.0.1/', 'http://localhost:8080/'):
                with self.assertRaises(linkfetch.LinkError, msg=url):
                    linkfetch.fetch_html(url)
            connect.assert_not_called()                                              # no connection was ever made

    def test_errors_are_said_in_words(self):
        for conn, said in ((FakeConnection(status=404), '404'), (FakeConnection(headers={'Content-Type': 'application/pdf'}, body=b'%PDF'), 'not a web page'),
                           (FakeConnection(body=b'<html><body><script>x()</script></body></html>'), 'no readable text')):
            with self.assertRaises(linkfetch.LinkError) as raised:
                linkfetch.fetch_lines('https://x.example/', opener=lambda *a, c=conn: c)
            self.assertIn(said, str(raised.exception))

    def test_a_huge_page_is_cut_and_plain_text_is_read_as_it_is(self):
        big = FakeConnection(body=b'<p>' + b'word ' * 1_000_000 + b'</p>')
        text, plain = linkfetch.fetch_html('https://x.example/', opener=lambda *a: big)
        self.assertLessEqual(len(text.encode()), linkfetch.MAX_BYTES)
        notes = FakeConnection(headers={'Content-Type': 'text/plain'}, body=b'first message here\nsecond message here\n')
        self.assertEqual(linkfetch.fetch_lines('https://x.example/n.txt', opener=lambda *a: notes)[1], ['first message here', 'second message here'])


class Voice(unittest.TestCase):
    def test_without_the_add_on_it_says_how_to_install_it(self):
        with mock.patch.dict(sys.modules, {'faster_whisper': None}), self.assertRaises(voice.VoiceUnavailable) as raised:
            voice.transcribe('x.wav')
        self.assertIn('pip install faster-whisper', str(raised.exception))

    def test_with_the_add_on_each_phrase_is_a_line(self):
        class Segment:
            def __init__(self, text):
                self.text = text

        class Model:
            def __init__(self, size, **kw):
                self.size = size

            def transcribe(self, path):
                return [Segment(' Thanks for coming. '), Segment('  '), Segment('See you next week.')], None
        with mock.patch.dict(sys.modules, {'faster_whisper': types.SimpleNamespace(WhisperModel=Model)}):
            self.assertEqual(voice.transcribe('x.wav'), ['Thanks for coming.', 'See you next week.'])


class Claude(unittest.TestCase):
    KEY = 'sk-ant-secret-key-value'

    def env(self):
        return mock.patch.dict(os.environ, {'FELIXA_ANTHROPIC_API_KEY': self.KEY, 'FELIXA_ANTHROPIC_URL': 'https://claude.test'})

    def reply(self, **over):
        body = {'model': 'claude-haiku-5-5', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '{"ok": true}'}], **over}
        return mock.MagicMock(__enter__=lambda s: io.BytesIO(json.dumps(body).encode()), __exit__=lambda *a: False)

    def test_names_labels_and_what_is_known(self):
        self.assertTrue(cloud.is_cloud('claude:claude-haiku-5-5'))
        self.assertFalse(cloud.is_cloud('qwen2.5:7b'))
        self.assertFalse(cloud.is_cloud(None))
        self.assertEqual(cloud.model_of('claude:claude-opus-5-5'), 'claude-opus-5-5')
        self.assertTrue(cloud.known('claude:claude-sonnet-5-5'))
        self.assertFalse(cloud.known('claude:gpt-9'))
        self.assertFalse(cloud.known('claude-sonnet-5-5'))
        self.assertEqual(cloud.label('claude:claude-haiku-5-5'), 'Claude Haiku 5.5 (cloud)')
        self.assertEqual(cloud.label('qwen2.5:7b'), 'qwen2.5:7b')

    def test_a_call_sends_the_key_and_the_schema_and_returns_the_text(self):
        seen = {}

        def fake_open(request, timeout=None):
            seen.update(url=request.full_url, headers={k.lower(): v for k, v in request.header_items()}, body=json.loads(request.data), timeout=timeout)
            return self.reply()
        with self.env(), mock.patch('cloud.urllib.request.urlopen', fake_open):
            text, model = cloud.claude_call('claude:claude-haiku-5-5')('Rate these.', '{"units": []}', schema={'type': 'object'})
        self.assertEqual((text, model), ('{"ok": true}', 'claude-haiku-5-5'))
        self.assertEqual(seen['url'], 'https://claude.test/v1/messages')
        self.assertEqual(seen['headers']['x-api-key'], self.KEY)
        self.assertEqual(seen['headers']['anthropic-version'], '2023-06-01')
        self.assertTrue(seen['body']['system'].startswith('Rate these.'))                         # the instructions stay first, whatever is added
        self.assertIn('JSON Schema', seen['body']['system'])
        self.assertEqual(seen['body']['messages'], [{'role': 'user', 'content': '{"units": []}'}])
        self.assertGreater(seen['timeout'], 0)

    def test_every_failure_is_one_safe_message_and_the_key_is_never_logged_or_shown(self):
        failures = [urllib.error.HTTPError('https://claude.test', 401, 'bad key ' + self.KEY, {}, io.BytesIO(self.KEY.encode())), urllib.error.URLError('down'),
                    TimeoutError('slow'), ConnectionResetError('reset')]
        for failure in failures:
            with self.env(), mock.patch('cloud.urllib.request.urlopen', side_effect=failure), self.assertLogs('bei.cloud', logging.WARNING) as logs:
                with self.assertRaises(cloud.ClaudeUnavailable) as raised:
                    cloud.claude_call('claude:claude-haiku-5-5')('x', 'y')
            self.assertEqual(str(raised.exception), cloud.REFUSED)
            self.assertNotIn(self.KEY, str(raised.exception) + ' '.join(logs.output))
            self.assertIsNone(raised.exception.__cause__)                                          # nothing carries the original error, and so the key, along

    def test_no_key_means_no_request(self):
        with mock.patch.dict(os.environ, {'FELIXA_ANTHROPIC_API_KEY': '', 'ANTHROPIC_API_KEY': ''}), mock.patch('cloud.urllib.request.urlopen') as opened:
            with self.assertRaises(cloud.ClaudeUnavailable):
                cloud.claude_call('claude:claude-haiku-5-5')('x', 'y')
            opened.assert_not_called()
            self.assertFalse(cloud.available())

    def test_an_answer_cut_short_is_an_error_and_text_blocks_are_joined(self):
        with self.env(), mock.patch('cloud.urllib.request.urlopen', return_value=self.reply(stop_reason='max_tokens')), self.assertRaises(RuntimeError):
            cloud.claude_call('claude:claude-haiku-5-5')('x', 'y')
        two = self.reply(content=[{'type': 'text', 'text': '{"a":'}, {'type': 'tool_use', 'name': 'x'}, {'type': 'text', 'text': ' 1}'}])
        with self.env(), mock.patch('cloud.urllib.request.urlopen', return_value=two):
            self.assertEqual(cloud.claude_call('claude:claude-haiku-5-5')('x', 'y')[0], '{"a": 1}')

    def test_the_standard_key_name_works_too(self):
        with mock.patch.dict(os.environ, {'FELIXA_ANTHROPIC_API_KEY': '', 'ANTHROPIC_API_KEY': ' sk-standard '}):
            self.assertEqual(cloud.available(), True)
            import config
            self.assertEqual(config.anthropic_key(), 'sk-standard')

    def test_cloud_engines_follow_the_local_ones_with_their_own_estimate(self):
        engines = workplace.engine_options([{'name': 'qwen2.5:7b', 'details': {'parameter_size': '7.6B'}}], 'qwen2.5:7b', 300, 300, 1, cloud.CLAUDE_MODELS, True)
        self.assertEqual([(e['kind'], e['name']) for e in engines], [('local', 'qwen2.5:7b'), ('cloud', 'claude:claude-haiku-5-5'), ('cloud', 'claude:claude-sonnet-5-5'),
                                                                     ('cloud', 'claude:claude-opus-5-5')])
        cloud_estimates = [e['estimate']['low'] for e in engines if e['kind'] == 'cloud']
        self.assertEqual(cloud_estimates, sorted(cloud_estimates))                                  # a heavier Claude takes longer
        self.assertLess(cloud_estimates[0], engines[0]['estimate']['low'])                          # and the light one beats a 7B model on a laptop
        off = workplace.engine_options([], 'qwen2.5:7b', 300, 300, 1, cloud.CLAUDE_MODELS, False)
        self.assertEqual([e['installed'] for e in off if e['kind'] == 'cloud'], [False] * 3)


if __name__ == '__main__':
    unittest.main()
