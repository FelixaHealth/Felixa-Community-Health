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
REFUSED_LOGIN = ('Claude Code could not answer. Check that it is installed and that you are signed in (run `claude` once in a terminal), '
                 'and that you have usage left. Then retry, or choose another engine.')
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


def login_call(engine):
    """A model function that asks Claude Code, signed in as the person, with every tool switched off: the messages are text to rate, and nothing
    in them can make the program read a file or run a command. It runs in an empty folder, without the API key variables, so that it uses the
    login and not a key."""
    alias = model_of(engine)

    def call(instructions, input_text, schema=None, **_ignored):
        program = cli_path()
        if not login_available() or not program:
            raise ClaudeUnavailable(REFUSED_LOGIN)
        system = instructions + ('\n\nAnswer with JSON only, matching this JSON Schema exactly:\n' + json.dumps(schema) if schema is not None else '\n\nAnswer with JSON only.')
        command = [program, '-p', '--output-format', 'json', '--model', alias, '--system-prompt', system, '--tools', '', '--no-session-persistence',
                   '--disable-slash-commands', '--strict-mcp-config']
        env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'FELIXA_ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
        try:
            with LOGIN_LOCK, tempfile.TemporaryDirectory(prefix='felixa-claude-') as folder:
                done = subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=config.model_timeout(), cwd=folder, env=env)
            result = json.loads(done.stdout)
        except Exception as e:
            log.warning('Claude Code could not be used: %s', type(e).__name__)
            raise ClaudeUnavailable(REFUSED_LOGIN) from None
        if done.returncode != 0 or not isinstance(result, dict) or result.get('is_error') or not isinstance(result.get('result'), str):
            log.warning('Claude Code did not answer (exit %s)', done.returncode)
            raise ClaudeUnavailable(REFUSED_LOGIN)
        return result['result'], 'claude-code:' + alias
    return call


def claude_call(engine):
    """The model function for any Claude engine."""
    return login_call(engine) if is_login(engine) else api_call(engine)
