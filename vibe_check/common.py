"""Small helpers shared by the Vibe Check modules. Standard library only."""
from __future__ import annotations

import datetime as _dt
import math
import re

MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def js_round(x: float) -> int:
    """JavaScript Math.round: halves round up. Python's round() rounds halves to even."""
    return int(math.floor(x + 0.5))


def round1(x: float) -> float:
    """One decimal place, rounding halves up (the original's parseFloat(x.toFixed(1)))."""
    return js_round(x * 10) / 10


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def to_float(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def to_int(value, default=0) -> int:
    x = to_float(value)
    return default if x is None else int(x)


def pct(part, whole) -> int:
    return 0 if not whole else js_round(part / whole * 100)


def largest_remainder(values: list[int], total: int) -> list[int]:
    """Scales non-negative ints to sum to `total`, never going negative."""
    s = sum(values)
    raw = [v * total / s for v in values]
    base = [int(math.floor(r)) for r in raw]
    for i in sorted(range(len(values)), key=lambda i: (-(raw[i] - base[i]), i))[: total - sum(base)]:
        base[i] += 1
    return base


def shares(values: list[int]) -> list[int]:
    """Whole-number percentages of `values` that always sum to 100 (all 0 when there is nothing)."""
    return largest_remainder(values, 100) if sum(values) > 0 else [0] * len(values)


def fmt_score(x: float) -> str:
    """7.0 -> '7', 7.4 -> '7.4'."""
    return f'{x:.1f}'.rstrip('0').rstrip('.')


def fmt_int(n) -> str:
    return f'{int(n):,}'


# ---- score tiers -----------------------------------------------------------------
# Label thresholds follow the original bot (8 / 6 / 4). The original used different
# cut-offs for its colour (7 / 4) and emoji (8 / 5); one set keeps words and colour in step.
TIERS = (('excellent', 8, 'Excellent'), ('good', 6, 'Good'), ('attention', 4, 'Needs attention'), ('poor', 0, 'Poor'))


def score_tier(score: float) -> str:
    for key, floor, _ in TIERS:
        if score >= floor:
            return key
    return 'poor'


def score_label(score: float) -> str:
    key = score_tier(score)
    return next(label for k, _, label in TIERS if k == key)


# ---- text ------------------------------------------------------------------------
def truncate(text: str, limit: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + '…'


def type_label(key: str) -> str:
    """'hate_speech' -> 'Hate speech'."""
    s = str(key or '').replace('_', ' ').strip()
    return s[:1].upper() + s[1:]


# ---- dates -----------------------------------------------------------------------
def parse_ts(value) -> _dt.datetime | None:
    """Tolerant ISO-8601 parser. Returns a naive UTC datetime or None."""
    s = str(value or '').strip()
    if not s:
        return None
    s = s.replace('Z', '+00:00').replace(' UTC', '+00:00')
    candidates = (s, s.replace(' ', 'T', 1), s[:19].replace(' ', 'T'), s[:10])
    for cand in candidates:
        try:
            d = _dt.datetime.fromisoformat(cand)
            if d.tzinfo is not None:
                d = d.astimezone(_dt.timezone.utc).replace(tzinfo=None)     # overflows for a year-1 or year-9999 stamp with an offset
        except (ValueError, OverflowError):
            continue
        return d
    return None


def short_date(value) -> str:
    """'2026-03-05T10:00:00' -> 'Mar 5' (matches the original chart labels)."""
    d = parse_ts(value)
    return f'{MONTHS[d.month - 1]} {d.day}' if d else str(value or '')[:10]


def long_date(value) -> str:
    d = parse_ts(value)
    return f'{MONTHS[d.month - 1]} {d.day}, {d.year}' if d else str(value or '')[:10]


def run_labels(reports) -> list[dict]:
    """Names for the saved runs on the progress charts and in their tables: [{'short': axis label, 'long': hover and table label}].

    A date names a run while every run has its own day. Re-running after each upload is the normal flow, so several runs often share
    a day and would all read 'Nov 1'. Then every run is named by its number ('Run 3'), and the long label adds the date and the time
    (run stamps are UTC). The time alone would not do: runs often fall in the same minute.
    """
    stamps = [r.get('created_at') for r in reports]
    if len({short_date(t) for t in stamps}) == len(stamps):
        return [{'short': short_date(t), 'long': long_date(t)} for t in stamps]
    out = []
    for i, t in enumerate(stamps, 1):
        d = parse_ts(t)
        when = long_date(t) + (f' {d:%H:%M} UTC' if d else '')
        out.append({'short': f'Run {i}', 'long': f'Run {i} · {when}' if when else f'Run {i}'})
    return out


def md_bold_to_html(escaped: str) -> str:
    """Turns **bold** in already-escaped text into <strong>. Input must be escaped first."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
