"""Claude as an engine. The only place where messages leave this computer for a model, and only when a person picks a Claude engine and
ticks the box that says so. The key belongs to the server (FELIXA_ANTHROPIC_API_KEY), so a person tests with Claude for free."""
import json
import logging
import urllib.error
import urllib.request

import config
from vibe_check.engine import ModelUnavailable

log = logging.getLogger('bei.cloud')
PREFIX = 'claude:'
# (model id, name, one line, typical seconds for one call)
CLAUDE_MODELS = [('claude-haiku-5-5', 'Claude Haiku 5.5', 'Fastest and lightest', 3.0),
                 ('claude-sonnet-5-5', 'Claude Sonnet 5.5', 'Balanced speed and care', 6.0),
                 ('claude-opus-5-5', 'Claude Opus 5.5', 'Most careful, slowest', 11.0)]
MAX_TOKENS = 2600
REFUSED = ('Claude could not finish. The server\'s Claude key may be missing, wrong or out of credit, or Claude may be busy. '
           'Please retry, or choose another engine.')


class ClaudeUnavailable(ModelUnavailable):
    """Claude could not be reached or refused the request. Its message is safe to show: it names no key and no message."""


def is_cloud(engine) -> bool:
    return str(engine or '').startswith(PREFIX)


def model_of(engine) -> str:
    return str(engine)[len(PREFIX):]


def known(engine) -> bool:
    return is_cloud(engine) and model_of(engine) in {m[0] for m in CLAUDE_MODELS}


def available() -> bool:
    return bool(config.anthropic_key())


def label(engine) -> str:
    """What a person reads for an engine: 'Claude Haiku 5.5 (cloud)', or the local model's own name."""
    if is_cloud(engine):
        return next((f'{name} (cloud)' for model, name, _about, _secs in CLAUDE_MODELS if model == model_of(engine)), str(engine))
    return str(engine or '')


def claude_call(engine):
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
