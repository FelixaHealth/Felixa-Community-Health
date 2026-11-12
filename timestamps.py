"""Dates and times as chat exports write them, turned into ISO stamps the app reads.

The app's own reader (`vibe_check.common.parse_ts`) understands ISO 8601 only. Exports write other things: "12/03/2026, 10:15", "3/4/26 10:15 PM", "5 March 2026 10:15",
"12.03.2026 10:15:30", a Unix time, "20260312T101500". A stamp it cannot read is the same as no stamp: the chat then has no times, and every time view is locked.

`normalize(units)` reads the stamps of one upload together. A date like 03/04/2026 is day-first or month-first, and only the rest of the file can say which: a day above 12 in the first place
means day-first, in the second place month-first. When nothing in the file says, it is read month first (the American and Canadian order) and `note` says so. A stamp that is already ISO is
left as it is, and so is one that cannot be read. A time zone written in the stamp is kept. No stamp is guessed into existence: a date alone becomes midnight of that day only when the export wrote no time.

Times with no zone are left without one (the app reads them as they are). A change of clock for daylight saving inside such a chat cannot be seen, so a stretch across it can be an hour off."""
import datetime as _dt
import re

from vibe_check.common import parse_ts

MONTHS = {m: i for i, names in enumerate([('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'), ('may',), ('jun', 'june'), ('jul', 'july'), ('aug', 'august'),
                                          ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'), ('dec', 'december')], 1) for m in names}
_TIME = r'(?:[,\sT]+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:[.,]\d+)?)?\s*(?P<ap>[AaPp]\.?[Mm]\.?)?)?\s*(?P<tz>Z|UTC|GMT|[+-]\d{2}:?\d{2})?\s*$'
NUMERIC = re.compile(r'^\s*\[?(?P<a>\d{1,4})[./-](?P<b>\d{1,2})[./-](?P<c>\d{1,4})' + _TIME.replace('$', r'\]?\s*$'))
DAY_MONTH_NAME = re.compile(r'^\s*(?:[A-Za-z]{3,9},?\s+)?(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>[A-Za-z]{3,9})\.?,?\s+(?P<y>\d{4})' + _TIME)
MONTH_NAME_DAY = re.compile(r'^\s*(?:[A-Za-z]{3,9},?\s+)?(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})' + _TIME)
COMPACT = re.compile(r'^\s*(?P<y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})T?(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})?\s*$')
EPOCH = re.compile(r'^\s*(\d{10}|\d{13})(?:\.\d+)?\s*$')


def _year(y) -> int:
    y = int(y)
    return y if y >= 100 else (2000 + y if y < 70 else 1900 + y)


def _tz(text) -> str:
    if not text:
        return ''
    t = text.upper()
    if t in ('Z', 'UTC', 'GMT'):
        return '+00:00'
    return t if ':' in t else t[:3] + ':' + t[3:]


def _clock(g) -> tuple:
    """(hour, minute, second, had_time) from the time part of a match, with am and pm."""
    if not g.get('h'):
        return 0, 0, 0, False
    h, m, s = int(g['h']), int(g['mi']), int(g.get('s') or 0)
    ap = (g.get('ap') or '').lower().replace('.', '')
    if ap:
        h = h % 12 + (12 if ap == 'pm' else 0)
    return h, m, s, True


def _iso(y, mo, d, g) -> str | None:
    h, mi, s, _had = _clock(g)
    try:
        stamp = _dt.datetime(y, mo, d, h, mi, s).isoformat()
    except ValueError:
        return None
    return stamp + _tz(g.get('tz'))


def _order(values) -> bool:
    """True when the dates of the file are day-first. A first number above 12 says day-first, a second number above 12 says month-first; with no sign either way, month-first."""
    day_first = month_first = 0
    for v in values:
        m = NUMERIC.match(v)
        if not m or len(m['a']) == 4:
            continue
        a, b = int(m['a']), int(m['b'])
        day_first += a > 12
        month_first += b > 12
    return day_first > month_first


def read(value, day_first=False) -> str | None:
    """One stamp as an ISO string, or None when it is not a date this module can read."""
    s = str(value or '').strip()
    if not s:
        return None
    if m := EPOCH.match(s):
        n = int(m.group(1))
        try:
            return (_dt.datetime(1970, 1, 1) + _dt.timedelta(seconds=n / 1000 if len(m.group(1)) == 13 else n)).isoformat() + '+00:00'
        except (OverflowError, ValueError):
            return None
    if m := COMPACT.match(s):
        return _iso(int(m['y']), int(m['mo']), int(m['d']), {'h': m['h'], 'mi': m['mi'], 's': m['s']})
    if m := NUMERIC.match(s):
        a, b, c = m['a'], int(m['b']), m['c']
        g = m.groupdict()
        if len(a) == 4:                                         # 2026/03/12: year, month, day
            return _iso(int(a), b, int(c), g)
        if len(c) not in (2, 4):
            return None
        first, second = int(a), b
        day, month = (first, second) if (day_first or first > 12) and second <= 12 else (second, first)
        return _iso(_year(c), month, day, g)
    for pat, order in ((DAY_MONTH_NAME, 'dm'), (MONTH_NAME_DAY, 'md')):
        if m := pat.match(s):
            mo = MONTHS.get(m['mon'].lower())
            if mo:
                return _iso(int(m['y']), mo, int(m['d']), m.groupdict())
    return None


def normalize(units) -> dict:
    """{'units': the same units with readable stamps made ISO, 'changed': how many, 'order': 'day-first' / 'month-first' / '' (ISO or nothing to read), 'note': a sentence or ''}."""
    todo = [u for u in units if u.get('timestamp') and parse_ts(u['timestamp']) is None]
    if not todo:
        return {'units': units, 'changed': 0, 'order': '', 'note': ''}
    values = [str(u['timestamp']) for u in todo]
    day_first = _order(values)
    ambiguous = any((m := NUMERIC.match(v)) and len(m['a']) != 4 and int(m['a']) <= 12 and int(m['b']) <= 12 and int(m['a']) != int(m['b']) for v in values)
    sure = any((m := NUMERIC.match(v)) and len(m['a']) != 4 and (int(m['a']) > 12 or int(m['b']) > 12) for v in values)
    changed = 0
    out = []
    for u in units:
        if u in todo:
            iso = read(u['timestamp'], day_first)
            if iso:
                u = dict(u, timestamp=iso)
                changed += 1
        out.append(u)
    order = 'day-first' if day_first else 'month-first'
    note = ''
    if ambiguous and not sure and changed:
        note = 'The dates in this file could be day-first or month-first (like 03/04/2026). They were read month first.'
    return {'units': out, 'changed': changed, 'order': order if changed else '', 'note': note}
