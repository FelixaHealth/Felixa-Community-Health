"""A stand-in for Ollama, for tests and for trying Felixa Community Health without a model.

    python3 tests/fake_ollama.py --port 11434 --model qwen2.5:7b

It answers /api/tags and /api/generate in Ollama's shape. Replies are produced by keyword rules, NOT by a
language model, so every number it yields is meaningless. It exists to exercise the plumbing: uploads, batching,
validation, jobs, reports and PDFs. Failures can be injected (see FakeOllama.fail_next).
"""
import argparse
import json
import pathlib
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from vibe_check.testing import KeywordStubModel  # noqa: E402

ATTRIBUTES = ['Polite', 'Funny', 'Empathetic', 'Encouraging', 'Ignorant', 'Egocentric', 'Neurotic', 'Sarcastic', 'Agitated',
              'Aggressive', 'Rude', 'Disrespectful', 'Judgmental']
BANNER = 'FAKE MODEL: replies come from keyword rules. The numbers are not an analysis of anything.'


def rate(text: str):
    t = text.lower()
    r = dict.fromkeys(ATTRIBUTES, 0)
    if any(k in t for k in ('thanks', 'thank you', 'welcome', 'appreciate', 'please')):
        r['Polite'] = 3 if 'thank' in t else 2
    if any(k in t for k in ('great', 'well played', 'good job', 'congrats', 'proud', 'good luck', 'nice', 'amazing')):
        r['Encouraging'] = 2
    if any(k in t for k in ('sorry', 'understand', 'glad', 'happy to help', 'i feel')):
        r['Empathetic'] = 2
    if any(k in t for k in ('lol', 'haha', 'funny', 'joke')):
        r['Funny'] = 2
    if any(k in t for k in ('idiot', 'trash', 'noob', 'stupid', 'loser', 'pathetic', 'useless', 'uninstall', 'clown', 'dumb', 'shut up')):
        r['Rude'], r['Disrespectful'] = 2, 2
    if any(k in t for k in ("i'll find you", "you're dead", 'watch your back', 'report you')):
        r['Aggressive'] = 3
    if 'wtf' in t or 'damn' in t or '!!' in text or (len(text) > 8 and text.upper() == text and any(c.isalpha() for c in text)):
        r['Agitated'] = 2
    if 'whatever' in t or 'sure,' in t or 'yeah right' in t:
        r['Sarcastic'] = 1
    if any(k in t for k in ('oh brilliant', 'oh great, another', 'what a surprise', 'how original')):      # a dig: sarcasm that clearly shows (2), with no insult in it
        r['Sarcastic'] = max(r['Sarcastic'], 2)
    if 'you always' in t or 'you never' in t or 'nobody likes' in t:
        r['Judgmental'] = 2
    return r, {a: f'keyword rule matched in "{text[:40]}"' for a, v in r.items() if v}


def rating_reply(prompt: str) -> dict:
    units = json.loads(prompt)['units']
    out = []
    for u in units:
        ratings, why = rate(u['text'])
        out.append({'external_id': u['external_id'], 'ratings': ratings, 'rationale': why})
    return {'units': out}


def guidance_reply(prompt: str) -> dict:
    data = json.loads(prompt)
    metrics = sorted(data.get('behavior_metrics', []), key=lambda m: -m['FAS'])
    top = metrics[0] if metrics else {'attribute': 'none', 'FAS': 0}
    return {
        'summary': f'Across the rated communication the strongest signal is {top["attribute"]} (FAS {top["FAS"]}). This summary was written by a fake model.',
        'working': [{'title': 'Positive signals are present', 'body': 'Polite and encouraging language appears in the rated units.'}],
        'limitations': [{'title': 'Tense or toxic language needs a closer look', 'body': 'Review the units that carry rude or aggressive ratings.'}],
        'recommendations': [{'action': 'Recognise the members who model respectful communication', 'why': 'What is celebrated gets repeated.',
                             'measure': 'Positive-signal FAS on the next run'}],
        'confidence': 'LOW', 'confidence_reason': 'Fake model: keyword rules, not a language model.'}


class FakeOllama:
    def __init__(self, model='qwen2.5:7b', delay=0.0):
        self.model, self.delay = model, delay
        self.calls = []                  # (kind, system_prompt_start)
        self.systems = []                # (kind, full system prompt, prompt)
        self.failures = {}               # kind -> remaining injected failures
        self.garbage = {}                # kind -> remaining answers that are not JSON
        self.models_listed = True
        self.extra_models = []           # more downloaded models: [{'name': ..., 'details': {'parameter_size': '3B'}}]
        self.models_used = []            # the model each request asked for (Claude's are written 'claude:<model>')
        self.claude_key = 'test-claude-key'
        self.claude_requests = []        # the bodies of Messages API requests, so a test can see what was sent
        self._vibe = KeywordStubModel()
        self._lock = threading.Lock()
        self.httpd = None

    # -- control --------------------------------------------------------------
    def fail_next(self, kind, n=1):
        """kind: 'rating' | 'guidance' | 'vibe'. The next n requests of that kind return HTTP 500."""
        self.failures[kind] = n

    def garbage_next(self, kind, n=1):
        self.garbage[kind] = n

    def kinds(self):
        return [k for k, _ in self.calls]

    # -- server ---------------------------------------------------------------
    def start(self, port=0, host='127.0.0.1'):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith('/api/tags'):
                    return self._send(200, {'models': ([{'name': outer.model, 'details': {'parameter_size': '7.6B'}}] + outer.extra_models) if outer.models_listed else []})
                self._send(404, {'error': 'not found'})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                if self.path.startswith('/api/generate'):
                    return outer._generate(self, body)
                if self.path.startswith('/v1/messages'):
                    return outer._messages(self, body)
                self._send(404, {'error': 'not found'})

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f'http://{host}:{self.httpd.server_port}'

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def _kind(self, system):
        if system.startswith('You are the FELIXA observable-communication rating engine'):
            return 'rating'
        if system.startswith('You are FELIXA, a behavioural intelligence system'):
            return 'guidance'
        if system.startswith('You are the FELIXA Vibe Check engine'):
            return 'vibe'
        return 'other'

    def _answer(self, system, prompt, model):
        """What either server answers to one request: ('fail', None) for an injected failure, else ('ok', text)."""
        kind = self._kind(system)
        with self._lock:
            self.calls.append((kind, system[:60]))
            self.models_used.append(model)
            self.systems.append((kind, system, prompt))
            if self.failures.get(kind, 0) > 0:
                self.failures[kind] -= 1
                return 'fail', None
            garbage = self.garbage.get(kind, 0) > 0
            if garbage:
                self.garbage[kind] -= 1
        if self.delay:
            time.sleep(self.delay)
        if garbage:
            return 'ok', 'I am sorry, I cannot produce JSON right now.'
        if kind == 'rating':
            return 'ok', json.dumps(rating_reply(prompt))
        if kind == 'guidance':
            return 'ok', json.dumps(guidance_reply(prompt))
        if kind == 'vibe':
            return 'ok', self._vibe(system, prompt)[0]
        return 'ok', '{}'

    def _generate(self, handler, body):
        state, text = self._answer(body.get('system', ''), body.get('prompt', ''), body.get('model'))
        if state == 'fail':
            return handler._send(500, {'error': 'injected failure'})
        handler._send(200, {'model': body.get('model', self.model), 'response': text, 'done': True, 'done_reason': 'stop'})

    def _messages(self, handler, body):
        """Claude's Messages API: needs the key `claude_key`, answers in its shape."""
        if handler.headers.get('x-api-key') != self.claude_key:
            return handler._send(401, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}})
        self.claude_requests.append(body)
        content = body.get('messages', [{}])[0].get('content', '')
        state, text = self._answer(body.get('system', ''), content if isinstance(content, str) else json.dumps(content), 'claude:' + str(body.get('model')))
        if state == 'fail':
            return handler._send(529, {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'injected failure'}})
        handler._send(200, {'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'model': body.get('model'), 'stop_reason': 'end_turn',
                            'content': [{'type': 'text', 'text': text}]})


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--port', type=int, default=11434)
    ap.add_argument('--model', default='qwen2.5:7b')
    ap.add_argument('--delay', type=float, default=0.0, help='seconds to wait before each reply')
    args = ap.parse_args()
    fake = FakeOllama(args.model, args.delay)
    url = fake.start(args.port)
    print(BANNER, file=sys.stderr)
    print(f'Fake Ollama listening on {url} as model {args.model}. Ctrl+C to stop.', file=sys.stderr)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fake.stop()
