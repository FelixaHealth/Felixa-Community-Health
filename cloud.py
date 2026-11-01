"""Claude as an engine. The only place where messages leave this computer for a model, and only when a person picks a Claude engine and
ticks the box that says so. Two ways in:

  * the API, with the server's key (FELIXA_ANTHROPIC_API_KEY), engine names 'claude:<model>';
  * the person's own Claude login, through the Claude Code program installed on this computer, engine names 'claudecode:<alias>'. It needs no
    key. It is for testing on one's own computer: it is switched off whenever the app is password-protected (a hosted app), so nobody
    can use the host's Claude login through the app.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

import config
from vibe_check.engine import ModelUnavailable

log = logging.getLogger('bei.cloud')
PREFIX = 'claude:'
LOGIN_PREFIX = 'claudecode:'
# (model id, name, one line, typical seconds for one call)
CLAUDE_MODELS = [('claude-haiku-5-5', 'Claude Haiku 5.5', 'Fastest and lightest', 3.0),
                 ('claude-sonnet-5-5', 'Claude Sonnet 5.5', 'Balanced speed and care', 6.0),
                 ('claude-opus-5-5', 'Claude Opus 5.5', 'Most careful, slowest', 11.0)]
# Claude Code takes an alias for the latest model of each kind. (alias, name, one line, typical seconds for one call: the program starts afresh each time)
LOGIN_MODELS = [('haiku', 'Claude Haiku', 'Fastest and lightest', 9.0),
                ('sonnet', 'Claude Sonnet', 'Balanced speed and care', 14.0),
                ('opus', 'Claude Opus', 'Most careful, slowest', 24.0)]
MAX_TOKENS = 2600
LOGIN_LOCK = threading.Lock()                                                                  # one question at a time, as a person would ask
REFUSED = ('Claude could not finish. The server\'s Claude key may be missing, wrong or out of credit, or Claude may be busy. '
           'Please retry, or choose another engine.')


class ClaudeUnavailable(ModelUnavailable):
    """Claude could not be reached or refused the request. Its message is safe to show: it names no key and no message."""


def is_api(engine) -> bool:
    return str(engine or '').startswith(PREFIX)


def is_login(engine) -> bool:
    return str(engine or '').startswith(LOGIN_PREFIX)


def is_cloud(engine) -> bool:
    """Any Claude engine: the person's messages go to Anthropic, so they must have ticked the box."""
    return is_api(engine) or is_login(engine)


def model_of(engine) -> str:
    return str(engine)[len(LOGIN_PREFIX):] if is_login(engine) else str(engine)[len(PREFIX):]


def known(engine) -> bool:
    if is_login(engine):
        return model_of(engine) in {m[0] for m in LOGIN_MODELS}
    return is_api(engine) and model_of(engine) in {m[0] for m in CLAUDE_MODELS}


def login_offered() -> bool:
    """Whether the page shows 'Your Claude login' at all: never on a password-protected (hosted) app, and not when FELIXA_CLAUDE_LOGIN=0."""
    return not os.getenv('FELIXA_PASSWORD') and os.getenv('FELIXA_CLAUDE_LOGIN', '1') != '0'


def cli_path():
    """The Claude Code program: the one FELIXA_CLAUDE_CLI_PATH names (if it can run), else `claude` on the PATH, else None."""
    named = (os.getenv('FELIXA_CLAUDE_CLI_PATH') or '').strip()
    if named:
        return named if os.path.isfile(named) and os.access(named, os.X_OK) else None
    return shutil.which('claude')


def login_available() -> bool:
    return login_offered() and bool(cli_path())


def available(engine=None) -> bool:
    """With no engine, whether the server has an API key. With one, whether that engine can run now."""
    return login_available() if is_login(engine) else bool(config.anthropic_key())


def label(engine) -> str:
    """What a person reads for an engine: 'Claude Haiku 5.5 (cloud)', or the local model's own name."""
    if is_login(engine):
        return next((f'{name} (your Claude login)' for model, name, _about, _secs in LOGIN_MODELS if model == model_of(engine)), str(engine))
    if is_api(engine):
        return next((f'{name} (cloud)' for model, name, _about, _secs in CLAUDE_MODELS if model == model_of(engine)), str(engine))
    return str(engine or '')


def api_call(engine):
    """A model function like `local_model_call`: (instructions, text, schema=None) -> (answer, model). Raises ModelUnavailable when Claude cannot
    be reached or refuses the key, and RuntimeError when an answer was cut short, as the local one does."""
    model = model_of(engine)

    def call(instructions, input_text, schema=None, **_ignored):
        key = config.anthropic_key()
        if not key:
            raise ClaudeUnavailable(REFUSED)
        system = instructions + ('\n\nAnswer with JSON only, matching this JSON Schema exactly:\n' + json.dumps(schema) if schema is not None else '\n\nAnswer with JSON only.')
        payload = {'model': model, 'max_tokens': MAX_TOKENS, 'system': system, 'messages': [{'role': 'user', 'content': input_text}]}
        request = urllib.request.Request(config.anthropic_url() + '/v1/messages', data=json.dumps(payload).encode(),
                                         headers={'content-type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01'})
        try:
            with urllib.request.urlopen(request, timeout=config.model_timeout()) as response:
                result = json.load(response)
        except urllib.error.HTTPError as e:
            log.warning('Claude answered %s', e.code)                                  # the status only: never the key, never the messages
            raise ClaudeUnavailable(REFUSED) from None
        except Exception as e:
            log.warning('Claude could not be reached: %s', type(e).__name__)
            raise ClaudeUnavailable(REFUSED) from None
        if result.get('stop_reason') == 'max_tokens':
            raise RuntimeError('The response reached its limit. Try a smaller set of communication.')
        text = ''.join(block.get('text', '') for block in result.get('content', []) if block.get('type') == 'text')
        return text, result.get('model') or model
    return call


# Tools Claude Code could use. Used by name when the installed version does not know `--tools`.
TOOLS_OFF = 'Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit,TodoWrite'
SECRET = __import__('re').compile(r'sk-ant-[\w-]+|Bearer\s+\S+')


def login_failure(reason) -> ClaudeUnavailable:
    return ClaudeUnavailable(f'Claude Code could not answer. {reason}')


def diagnose(stderr='', result='', timed_out=False, missing=False) -> str:
    """What went wrong with a Claude Code call, in words for the person, from what the program printed. Never includes a message or a key."""
    if missing:
        return 'Claude Code was not found. Install it, then try again.'
    if timed_out:
        return 'Claude Code took too long. Try again, or choose a faster model.'
    said = f'{stderr} {result}'.lower()
    if any(x in said for x in ('not logged in', 'please run /login', '/login', 'authenticat', 'invalid api key', 'unauthorized', 'sign in', 'signed in', '401')):
        return 'You are not signed in to Claude Code. Run `claude` once in a terminal, sign in, then try again.'
    if any(x in said for x in ('usage limit', 'rate limit', 'limit reached', 'quota', 'credit', 'overloaded', '429', '529')):
        return 'Your Claude usage limit may be reached, or Claude is busy. Wait a little and try again, or choose another engine.'
    if any(x in said for x in ('unknown option', 'unrecognized', 'unknown argument', 'invalid option')):
        return 'Your copy of Claude Code is too old for this app. Update it (run `claude update` in a terminal), then try again.'
    first = SECRET.sub('[hidden]', ' '.join(str(stderr or result).split()))[:160]
    return 'Claude Code reported a problem' + (f': {first}' if first else ' and gave no details') + '.'


def run_login(program, alias, system, input_text):
    """Runs Claude Code once. Returns (text, None) or (None, reason). The first try uses every safety switch; a Claude Code too old to know one
    of them is asked again with the tools turned off by name, and nothing else is relaxed."""
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'FELIXA_ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
    base = [program, '-p', '--output-format', 'json', '--model', alias, '--system-prompt', system]
    tries = [base + ['--tools', '', '--no-session-persistence', '--disable-slash-commands', '--strict-mcp-config'],
             base + ['--disallowedTools', TOOLS_OFF]]
    reason = ''
    for command in tries:
        try:
            with LOGIN_LOCK, tempfile.TemporaryDirectory(prefix='felixa-claude-') as folder:
                done = subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=config.model_timeout(), cwd=folder, env=env)
        except subprocess.TimeoutExpired:
            log.warning('Claude Code took too long')
            return None, diagnose(timed_out=True)
        except OSError as e:
            log.warning('Claude Code could not be started: %s', type(e).__name__)
            return None, diagnose(missing=True)
        try:
            result = json.loads(done.stdout)
        except ValueError:
            result = None
        answer = result.get('result') if isinstance(result, dict) else None
        if done.returncode == 0 and isinstance(result, dict) and not result.get('is_error') and isinstance(answer, str):
            return answer, None
        reason = diagnose(done.stderr, answer if isinstance(answer, str) else done.stdout)
        log.warning('Claude Code did not answer (exit %s): %s', done.returncode, reason[:120])
        if 'too old' not in reason:
            break
    return None, reason


def login_call(engine):
    """A model function that asks Claude Code, signed in as the person, with every tool switched off: the messages are text to rate, and nothing
    in them can make the program read a file or run a command. It runs in an empty folder, without the API key variables, so that it uses the
    login and not a key."""
    alias = model_of(engine)

    def call(instructions, input_text, schema=None, **_ignored):
        program = cli_path()
        if not login_available() or not program:
            raise login_failure('It is not available here: Claude Code is not installed on this computer, or this app is password-protected.')
        system = instructions + ('\n\nAnswer with JSON only, matching this JSON Schema exactly:\n' + json.dumps(schema) if schema is not None else '\n\nAnswer with JSON only.')
        text, reason = run_login(program, alias, system, input_text)
        if text is None:
            raise login_failure(reason)
        return text, 'claude-code:' + alias
    return call


def login_test(alias='haiku'):
    """A one-word question to check that Claude Code is installed, signed in and answering. Returns (ok, sentence for the person)."""
    program = cli_path()
    if not login_available() or not program:
        return False, 'Claude Code was not found on this computer, or this app is password-protected.'
    text, reason = run_login(program, alias, 'Reply with the single word: ready', 'ping')
    return (True, 'Claude Code answered. Your Claude login works.') if text is not None else (False, reason)


def claude_call(engine):
    """The model function for any Claude engine."""
    return login_call(engine) if is_login(engine) else api_call(engine)
