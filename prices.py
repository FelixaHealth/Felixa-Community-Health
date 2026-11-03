"""What one check costs on a paid engine, from the published price lists, and when those prices were read.

Prices are US dollars per million tokens (input, output). The Claude prices were read from Anthropic's price list (platform.claude.com/docs/en/about-claude/pricing)
on AS_OF. The OpenAI prices are the last ones published that this app knows; the server could not reach openai.com to refresh them, and the note says so.
A check's token use is a guess from typical message length: each kind of model call below has an input and an output size, and the total is shown as a range."""
import datetime as _dt
import math

AS_OF = _dt.date(2026, 11, 3)
SOURCE = 'platform.claude.com/docs/en/about-claude/pricing'
CLAUDE = {'claude-haiku-5-5': (0.10, 0.50), 'claude-sonnet-5-5': (2.00, 10.00), 'claude-opus-5-5': (4.00, 20.00)}
LOGIN = {'haiku': 'claude-haiku-5-5', 'sonnet': 'claude-sonnet-5-5', 'opus': 'claude-opus-5-5'}      # an alias is the latest model of its kind
OPENAI = {'gpt-4o-mini': (0.15, 0.60), 'gpt-4o': (2.50, 10.00)}
# (input tokens, output tokens) of one model call of each kind
RATING_CALL, VIBE_CALL, EXTRA_CALL = (1100, 350), (3300, 700), (3500, 900)
LOW, HIGH = 0.7, 1.6                                                                          # message length varies: the range around the guess


def per_million(engine):
    """(input, output) dollars per million tokens for an engine id, or None when it costs nothing or is unknown."""
    engine = str(engine or '')
    if engine.startswith('claude:'):
        return CLAUDE.get(engine[7:])
    if engine.startswith('claudecode:'):
        return CLAUDE.get(LOGIN.get(engine[11:], ''))
    if engine.startswith('openai:'):
        return OPENAI.get(engine[7:])
    return None


DEFAULT_CHARS = 240                      # a typical chat message; the real average of the stored messages replaces it when it is known
MAX_CHARS = 2500                         # the longest a rated message is sent
CHARS_PER_TOKEN = 4
RATING_SETUP, VIBE_SETUP = 700, 900      # tokens of instructions in a rating call and in a Vibe Check call
VIBE_CHUNK_CHARS = 8000                  # Vibe Check reads about this much text per call, 40 messages at most


def cost(engine, parts, avg_chars=None):
    """The dollars one check costs on `engine` as (low, high), from the call counts of `workplace.calls_for` and the average length of the messages
    (`avg_chars`, characters). Long messages make each call bigger and Vibe Check read fewer of them per call. None for a free engine."""
    rate = per_million(engine)
    if not rate or not parts.get('total'):
        return None
    chars = min(max(float(avg_chars or DEFAULT_CHARS), 20.0), float(MAX_CHARS))
    per_message = chars / CHARS_PER_TOKEN
    n = parts.get('messages', 0)
    rating_in = RATING_SETUP + 3 * per_message
    per_source = max(0, parts['vibe'] - math.ceil(n / 40)) if n else 0
    vibe_calls = (max(math.ceil(n / 40), math.ceil(n * chars / VIBE_CHUNK_CHARS)) + per_source) if n else parts['vibe']
    vibe_in = VIBE_SETUP + min(40 * per_message, VIBE_CHUNK_CHARS / CHARS_PER_TOKEN)
    inp = parts['rating'] * rating_in + vibe_calls * vibe_in + parts['extra'] * EXTRA_CALL[0]
    out = parts['rating'] * RATING_CALL[1] + vibe_calls * VIBE_CALL[1] + parts['extra'] * EXTRA_CALL[1]
    mid = (inp * rate[0] + out * rate[1]) / 1_000_000
    return mid * LOW, mid * HIGH


def money(x):
    return '$0.01' if x < 0.005 else f'${x:.2f}' if x < 100 else f'${x:,.0f}'


def text(engine, parts, avg_chars=None):
    """What a person reads: 'About $0.12', 'About $0.08 to $0.19', 'Up to $0.01' or 'Under $0.01'. Empty for a free engine."""
    c = cost(engine, parts, avg_chars)
    if not c:
        return ''
    low, high = c
    if high < 0.005:
        return 'Under $0.01'
    if low < 0.005:
        return f'Up to {money(high)}'
    a, b = money(low), money(high)
    return f'About {a}' if a == b else f'About {a} to {b}'


def as_of_note(openai=False):
    stamp = f'{AS_OF:%b} {AS_OF.day}, {AS_OF.year}'
    note = f'Claude prices as of {stamp} (Anthropic price list), at the length of your messages.'
    return note + (' OpenAI prices are the last published ones this app knows and may have changed.' if openai else '')
