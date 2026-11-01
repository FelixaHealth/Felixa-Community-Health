"""The local language model: Ollama over its HTTP API on this machine. No cloud API or key is used."""
import json
import threading
import urllib.request

import config
from vibe_check.engine import ModelUnavailable, parse_json_loose  # noqa: F401  (parse_json_loose is re-exported: one tolerant JSON parser for the whole app)

# One model call at a time: a small hosted instance cannot load or run two at once.
MODEL_LOCK = threading.Lock()
# The model runs on this machine, so never send these calls through an HTTP proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local_model_call(instructions, input_text, model=None, schema=None):
    """Returns (response_text, model_name). `schema` is a JSON Schema the model must follow; without one it returns JSON.
    Raises ModelUnavailable when the model cannot be reached or does not answer in time, and a plain RuntimeError when its answer was cut short."""
    model = model or config.model_name()
    options = {'temperature': 0.1, 'num_ctx': 8192, 'num_predict': 2600}
    if config.num_threads():
        options['num_thread'] = config.num_threads()
    payload = {'model': model, 'system': instructions, 'prompt': input_text, 'stream': False,
               'format': schema if schema is not None else 'json', 'keep_alive': '5m', 'options': options}
    req = urllib.request.Request(config.ollama_url() + '/api/generate', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    with MODEL_LOCK:
        try:
            with _OPENER.open(req, timeout=config.model_timeout()) as response:
                result = json.load(response)
        except Exception as e:
            raise ModelUnavailable('The analysis engine could not finish. Please retry after it is ready.') from e
    if result.get('done_reason') == 'length':
        raise RuntimeError('The response reached its limit. Try a smaller set of communication.')
    return result.get('response', ''), model


def local_models() -> list:
    """The models downloaded on this machine: [{'name', 'details'}], or [] when the model server does not answer."""
    try:
        with _OPENER.open(config.ollama_url() + '/api/tags', timeout=2) as response:
            return [{'name': x.get('name', ''), 'details': x.get('details') or {}} for x in json.load(response).get('models', []) if x.get('name')]
    except Exception:
        return []


def local_model_ready(model=None) -> bool:
    """True when Ollama answers and `model` (by default the configured one) is downloaded."""
    names = [x['name'] for x in local_models()]
    want = model or config.model_name()
    return want in names or (':' not in want and want + ':latest' in names)
