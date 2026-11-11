"""Measures over time, alerts and badges, built from the 13 tone ratings of each message.

MEASURES.md has every rule in plain words. This module is those rules as code, and the one place where the wording is written, so the page, the
PDF and the CSV cannot drift apart. Every function takes numbers and gives numbers or sentences back. The one database read is
`store.rated_units`, which returns ratings and dates and never a message's text, so nothing here can see what anyone wrote.

    m = analyse(units, sensitivity)          # the numbers: flags, stretches, trends, heated moments
    out = build(aid, analysis_row, vibe, coverage)    # all of it for one report: cards, alerts, notes, badges, the trail
    save_snapshot(aid, rated)                # the numbers kept after a check (scores and counts only)

A message "clearly shows" a tone when the model rated it 2 or 3. The measures count such messages, per 100 messages, because a count is easy
to read ("8 of 70 messages") and does not move with how many weak 1s a model hands out. Whatever a trend, an alert or a badge says, it has
to pass a size floor and a chance test first (see Z_* and MIN_*), so a single message cannot raise it, and every alert shows how many messages stand behind it. Every cut-off is our own starting
choice, written down as a named constant. None of them is a validated norm.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import math
import re

import analysis
import store
from vibe_check.common import clamp, js_round, parse_ts, short_date
from vibe_check.forecast import SEVERE_TYPES, TYPE_RECOMMENDATIONS

log = logging.getLogger('bei.measures')

# ---- what is counted ----------------------------------------------------------------------------
FLAG_MIN = 2                                        # a tone is clearly shown when rated 2 or 3
OVERT = ('Aggressive', 'Rude', 'Disrespectful', 'Judgmental')        # open hostility
POSITIVE_WEIGHTS = {'Polite': 3, 'Empathetic': 3, 'Encouraging': 2, 'Funny': 1}      # the weights of the Felixa backend
NEGATIVE_WEIGHTS = {'Ignorant': 1, 'Egocentric': 1, 'Neurotic': 1, 'Sarcastic': 2, 'Agitated': 2, 'Aggressive': 3, 'Judgmental': 3, 'Disrespectful': 3, 'Rude': 3}
HEALTH_DIVISOR = 9         # as in the Felixa backend (community_health.py): score = 50 + 50 x (positive - negative) / (messages x 9), where a tone counts when it is present
HEALTH_STATUS = ((80, 'Excellent'), (60, 'Good'), (40, 'Fair'), (0, 'Needs attention'))
HEALTH_TONE = {'Excellent': 'good', 'Good': 'good', 'Fair': 'info', 'Needs attention': 'bad'}
TONE_WORDS = {'good': 'A good sign', 'info': 'Neutral', 'warn': 'Worth a look', 'bad': 'Look at this first'}      # the icon in words, for the PDF and the CSV
KINDS = ('hostile', 'care', 'dig', 'heated', 'polite', 'encouraging', 'funny')      # the flags counted for every message
KEYS = ('escalation', 'conflict', 'empathy', 'passive')            # the four measures, in the order they are shown
NAMES = {'escalation': 'Escalation', 'conflict': 'Conflict', 'empathy': 'Empathy', 'passive': 'Passive-aggressive'}
KIND_OF = {'conflict': 'hostile', 'empathy': 'care', 'passive': 'dig', 'escalation': 'heated'}

# ---- following the conversation -----------------------------------------------------------------
MIN_TIMELINE = 24          # messages on the timeline for lines over time
MIN_VERDICT = 48           # messages on the timeline before anything says rising, falling or heated (each third then holds 16)
MIN_STRETCH = 8            # messages in the smallest stretch
MAX_STRETCHES = 24
LAST_THIRD_MIN = 24        # a level alert reads the latest third only when it holds this many messages, else the whole check
DATED_SHARE = (3, 5)       # at least 3 in 5 rated messages must carry a usable date for the timeline to follow the clock
DATE_FROM = _dt.date(1990, 1, 1)
Z_CLEAR, Z_STRONG, Z_EPISODE = 2.0, 2.5, 3.5       # chance tests: a change must be this many standard errors; alerts and badges use the strong one
DIRECTION_MIN, DIRECTION_REL = 3.0, 0.20           # and at least max(3 points, 20% of where it started)
HEALTH_DIRECTION_MIN = 5.0
EPISODE_MIN = 15.0         # points of climb above the recent level before a stretch can count as a heated moment
BASELINE_STRETCHES = 3     # the recent level is the average of up to this many stretches before
LOW_SCALE = 1.5            # Low sensitivity (adult and gaming chat) treats banter as normal: the cut-offs for conflict and sarcasm are this much higher
LOW_TENSION = 1.2          # and the Medium heated-moment cut-offs
CUTS = {'hostile': (5, 12), 'dig': (5, 12), 'care': (3, 10)}      # per 100 messages: below the first is Rare, below the second Some, else Often
SCALED = ('hostile', 'dig')
ESCALATION_WORDS = ((40, 'steep'), (25, 'clear'), (15, 'small'), (0, 'none'))     # by the biggest counted climb
ESCALATION_LABEL = {'none': 'None', 'small': 'Small', 'clear': 'Clear', 'steep': 'Steep', 'early': 'Too early'}

# ---- alerts, comparisons and badges -------------------------------------------------------------
LEVELS = ('high', 'medium', 'good')
LEVEL_NAMES = {'high': 'High priority', 'medium': 'Medium priority', 'good': 'Good news'}
LEVEL_TONE = {'high': 'bad', 'medium': 'warn', 'good': 'good'}
EVIDENCE_MIN = 3           # messages that clearly show the tone, inside the stretch an alert reads
CONFLICT_HIGH, CONFLICT_MEDIUM, RISING_FLOOR = 20, 12, 5
DIG_MEDIUM = 12
HEATED_HIGH, HEATED_MEDIUM = (40, 45), (25, 30)    # (climb, level reached), per 100 messages
CARE_FADING_FROM = 5       # clear-care messages in the first third before a fall is called fading
CHANGE_MIN, HEALTH_CHANGE_MIN = 5.0, 8.0           # points on the previous check that count as a change
COMPARE_RATIO = 3          # the two checks must have rated numbers of messages within this factor
MIN_BADGE = 30
BADGE_POLITE, BADGE_CARE, BADGE_ENCOURAGING, BADGE_FUNNY, BADGE_HOSTILE_MAX = 20, 12, 12, 12, 3
BADGE_COOL_PEAK, BADGE_COOL_BACK, BADGE_COOL_WITHIN = 25, 10, 3
BADGE_CALM_HEATED = 10      # Calm waters also needs fewer than this many heated messages in 100
SERIOUS_SCORE = 7          # Vibe Check scores a flagged message out of 10; 7 and over is the red band of its list
SILENT_SHARE = (19, 20)    # when 19 in 20 messages have all 13 ratings at 0, the model probably did not read the text


# ---- helpers ------------------------------------------------------------------------------------
def is_silent(m) -> bool:
    """True when 19 in 20 messages have all 13 ratings at 0: the model most likely did not read the text, so nothing here may look like good news."""
    return bool(m['n']) and SILENT_SHARE[1] * m['silent'] >= SILENT_SHARE[0] * m['n']


def whole(x) -> int:
    """A figure as it is printed: whole numbers, halves up. Every word and cut-off is judged on this, so a printed 8 is never called Rare."""
    return js_round(x + 1e-9)


def _rating(value) -> int:
    try:
        return int(clamp(int(value), 0, 3))
    except (TypeError, ValueError, OverflowError):
        return 0


def _lap_var(k, n) -> float:
    """Variance of a yes/no flag with a little prior (one yes and one no), so a rate of 0 or 100 still has some doubt."""
    p = (k + 1) / (n + 2)
    return p * (1 - p)


def _messages(k, n, what) -> str:
    if n == 1:
        return f'{k:,} of 1 message {what.replace("were ", "was ", 1)}'
    return f'None of the {n:,} messages {what}' if k == 0 else f'{k:,} of {n:,} messages {what}'


def _clears(k, n, cut) -> bool:
    """True when the rate k/n is at least `cut` per 100 messages by more than one standard error."""
    if not n:
        return False
    p = k / n
    return 100 * p - 100 * math.sqrt(p * (1 - p) / n) >= cut - 1e-9


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')


def health_status(score) -> str:
    shown = whole(score)
    return next(word for floor, word in HEALTH_STATUS if shown >= floor)


# ---- one message --------------------------------------------------------------------------------
def message_flags(ratings) -> dict:
    """What one message contributes. `ratings` maps each of the 13 tones to 0..3 (bad values count as 0)."""
    r = {a: _rating((ratings or {}).get(a)) for a in analysis.ATTRIBUTES}
    hostile = max(r[a] for a in OVERT) >= FLAG_MIN
    dig = r['Sarcastic'] >= FLAG_MIN and not hostile and r['Funny'] < FLAG_MIN       # sarcasm that is neither open hostility nor a joke
    return {'hostile': int(hostile), 'care': int(r['Empathetic'] >= FLAG_MIN), 'dig': int(dig),
            'heated': int(hostile or r['Agitated'] >= FLAG_MIN or dig),
            'polite': int(r['Polite'] >= FLAG_MIN), 'encouraging': int(r['Encouraging'] >= FLAG_MIN), 'funny': int(r['Funny'] >= FLAG_MIN),
            'pos': sum(w for a, w in POSITIVE_WEIGHTS.items() if r[a] >= FLAG_MIN), 'neg': sum(w for a, w in NEGATIVE_WEIGHTS.items() if r[a] >= FLAG_MIN),     # present = rated 2 or 3, as the Felixa backend counts a tone present or not
            'silent': int(not any(r.values()))}


def health_of(mean_positive, mean_negative) -> float:
    return clamp(50 + 50 * (mean_positive - mean_negative) / HEALTH_DIVISOR, 0.0, 100.0)


# ---- groups of messages -------------------------------------------------------------------------
def tally(flag_list) -> dict:
    """Counts for a group of messages: how many show each flag, and the sums the health score and its chance test need."""
    t = {k: 0 for k in KINDS + ('silent',)}
    t.update({'n': len(flag_list), 'pos': 0.0, 'neg': 0.0, 'd': 0.0, 'dd': 0.0})
    for f in flag_list:
        for k in KINDS + ('silent',):
            t[k] += f[k]
        d = f['pos'] - f['neg']
        t['pos'] += f['pos']
        t['neg'] += f['neg']
        t['d'] += d
        t['dd'] += d * d
    return t


def merge(tallies) -> dict:
    out = {k: 0 for k in tally([])}
    for t in tallies:
        for k in out:
            out[k] += t[k]
    return out


def rate(t, kind) -> float:
    return 100 * t[kind] / t['n'] if t['n'] else 0.0


def health_mean(t) -> float:
    return health_of(t['pos'] / t['n'], t['neg'] / t['n']) if t['n'] else 50.0


def _d_var(t) -> float:
    return max(0.0, (t['dd'] - t['d'] ** 2 / t['n']) / (t['n'] - 1)) if t['n'] > 1 else 0.0


def rate_change(t1, t2, kind, minimum=DIRECTION_MIN, relative=DIRECTION_REL) -> dict:
    """A change of a rate from one group to another, and whether it is bigger than chance: the points it moved, its standard error and the verdicts
    (`call`: rising, falling or steady at Z_CLEAR; `strong` at Z_STRONG, which alerts and badges need)."""
    first, last = rate(t1, kind), rate(t2, kind)
    delta = last - first
    se = 100 * math.sqrt(_lap_var(t1[kind], t1['n']) / max(1, t1['n']) + _lap_var(t2[kind], t2['n']) / max(1, t2['n']))
    return _verdict(first, last, delta, se, max(minimum, relative * first))


def health_change(t1, t2) -> dict:
    scale = 50 / HEALTH_DIVISOR
    se = scale * math.sqrt(_d_var(t1) / max(1, t1['n']) + _d_var(t2) / max(1, t2['n']))
    first, last = health_mean(t1), health_mean(t2)
    return _verdict(first, last, last - first, se, HEALTH_DIRECTION_MIN)


def _verdict(first, last, delta, se, need) -> dict:
    z = abs(delta) / se if se > 0 else (math.inf if abs(delta) > 0 else 0.0)
    big = abs(delta) >= need - 1e-9
    sign = 'rising' if delta > 0 else 'falling'
    return {'first': first, 'last': last, 'delta': delta, 'se': se, 'z': z,
            'call': sign if big and z >= Z_CLEAR else 'steady', 'strong': bool(big and z >= Z_STRONG)}


# ---- order, stretches and labels ----------------------------------------------------------------
def _usable(stamp, today) -> bool:
    return stamp is not None and DATE_FROM <= stamp.date() <= today + _dt.timedelta(days=1)


def order_units(units, today=None):
    """(ordered units, their times, basis, left off). Time order when at least 3 in 5 of the rated messages carry a usable date (and there are at least three
    different moments); the ones without a date are left off. Otherwise the order the messages were added in."""
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    stamps = [parse_ts(u.get('timestamp')) for u in units]
    stamps = [s if _usable(s, today) else None for s in stamps]
    dated = [i for i, s in enumerate(stamps) if s is not None]
    if units and DATED_SHARE[1] * len(dated) >= DATED_SHARE[0] * len(units) and len({stamps[i] for i in dated}) >= 3:
        keep = sorted(dated, key=lambda i: (stamps[i], i))
        return [units[i] for i in keep], [stamps[i] for i in keep], 'time', len(units) - len(dated)
    return list(units), [None] * len(units), 'order', 0


def cut(n) -> list:
    """The (start, end) positions of the stretches for n ordered messages: equal sizes (they differ by at most one), at least MIN_STRETCH each."""
    if n < MIN_TIMELINE:
        return []
    k = min(MAX_STRETCHES, n // MIN_STRETCH)
    edges = [i * n // k for i in range(k + 1)]
    return list(zip(edges[:-1], edges[1:]))


def _has_offset(raw) -> bool:
    return bool(re.search(r'\d:\d\d(?::\d\d(?:[.,]\d+)?)?\s*(?:Z|UTC|[+-]\d{2}:?\d{2})\s*$', str(raw or '').strip()))


def _day(d, year=False) -> str:
    return short_date(d.isoformat()) + (f', {d.year}' if year else '')


def _uses_clock(firsts, lasts, incognito) -> bool:
    """True when the part labels carry a time of day: the data has times, the parts fall within three days and Incognito is off."""
    timed = any(s.time() != _dt.time.min for s in firsts + lasts)
    return bool(timed and (lasts[-1].date() - firsts[0].date()).days <= 3 and not incognito)


def stretch_labels(spans, stamps, basis, utc=False, incognito=False):
    """For each stretch: (axis label, tooltip and table label). The axis label is '' where the previous stretch has the same one. Clock times appear
    only when the data has them, the stretches fall within three days, and Incognito is off."""
    k = len(spans)
    if basis == 'order' or not spans:
        return [(f'#{a + 1}' if i % max(1, k // 6) == 0 else '', f'Part {i + 1} of {k}: rated messages {a + 1} to {b}') for i, (a, b) in enumerate(spans)]
    firsts, lasts = [stamps[a] for a, _ in spans], [stamps[b - 1] for _, b in spans]
    clock = _uses_clock(firsts, lasts, incognito)
    day = lambda d: _day(d, (lasts[-1].date() - firsts[0].date()).days > 300)      # noqa: E731  a chat of more than a year names the year, or two parts could share a label
    tz = ' UTC' if utc else ''

    def span(a, b):
        if not clock:
            return day(a) if a.date() == b.date() else f'{day(a)} to {day(b)}'
        if a.date() == b.date():
            return f'{day(a)} {a:%H:%M} to {b:%H:%M}{tz}'
        return f'{day(a)} {a:%H:%M} to {day(b)} {b:%H:%M}{tz}'
    axis, seen = [], None
    single_day = firsts[0].date() == lasts[-1].date()
    for i, s in enumerate(firsts):
        if clock:
            text = f'{s:%H:%M}' if single_day else f'{day(s)} {s:%H:%M}'
        elif single_day:
            text = f'Part {i + 1}'
        else:
            text = day(s)
        axis.append(text if text != seen else '')
        seen = text
    return [(axis[i], f'Part {i + 1} of {k}: ' + span(firsts[i], lasts[i])) for i in range(k)]


def _stamp_text(stamp, incognito) -> str:
    """A stretch's start or end as saved in the analysis: the whole moment, or only the day while Incognito is on (no clock time may leave this module)."""
    return stamp.date().isoformat() if incognito else stamp.isoformat()


def where_phrase(first, last, k) -> str:
    """Where in the check a heated moment fell, in words (never a clock time): 'near the start', 'around the middle' ..."""
    centre = (first + last + 1) / 2 / max(1, k)
    return ('near the start' if centre < 0.15 else 'about a third of the way in' if centre < 0.45 else 'around the middle' if centre < 0.55
            else 'about two thirds of the way in' if centre < 0.85 else 'near the end')


def dates_phrase(stretches, first, last, year=False) -> str:
    a, b = stretches[first].get('start'), stretches[last].get('end')
    if not a or not b:
        return ''
    a, b = parse_ts(a), parse_ts(b)
    return _day(a, year) if a.date() == b.date() else f'{_day(a, year)} to {_day(b, year)}'


# ---- heated moments -----------------------------------------------------------------------------
def climbs(stretch_tallies) -> list:
    """For each stretch from the second on: how far its rate of heated messages climbed above the average of up to BASELINE_STRETCHES stretches
    before it, and the standard error that gap would have by chance if the community were steady at its own overall rate. The first stretch has
    no past to climb from."""
    total = merge(stretch_tallies)
    p = (total['heated'] + 0.5) / (total['n'] + 1)            # the community's overall rate of heated messages, with a little doubt at 0 and 100
    out = [{'climb': 0.0, 'se': 0.0, 'base': None}]
    for i in range(1, len(stretch_tallies)):
        base = merge(stretch_tallies[max(0, i - BASELINE_STRETCHES):i])
        cur = stretch_tallies[i]
        gap = 100 * (cur['heated'] / cur['n'] - base['heated'] / base['n'])
        out.append({'climb': max(0.0, gap), 'se': 100 * math.sqrt(p * (1 - p) * (1 / cur['n'] + 1 / base['n'])), 'base': 100 * base['heated'] / base['n']})
    return out


def episodes_of(stretch_tallies, steps, enabled) -> list:
    """Runs of consecutive stretches whose climb is at least EPISODE_MIN points and Z_EPISODE standard errors, with at least EVIDENCE_MIN heated
    messages in the stretch. None unless `enabled` (enough messages)."""
    counted = [enabled and i > 0 and s['climb'] >= max(EPISODE_MIN, Z_EPISODE * s['se']) - 1e-9 and stretch_tallies[i]['heated'] >= EVIDENCE_MIN
               for i, s in enumerate(steps)]
    out, i, n = [], 0, len(steps)
    while i < n:
        if not counted[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and counted[j + 1]:
            j += 1
        run = stretch_tallies[i:j + 1]
        out.append({'first': i, 'last': j, 'peak': max(steps[x]['climb'] for x in range(i, j + 1)), 'baseline': steps[i]['base'],
                    'reached': max(100 * t['heated'] / t['n'] for t in run), 'heated': sum(t['heated'] for t in run), 'n': sum(t['n'] for t in run)})
        i = j + 1
    return out


def escalation_word(peak) -> str:
    shown = whole(peak)
    return next(word for floor, word in ESCALATION_WORDS if shown >= floor)


# ---- the analysis -------------------------------------------------------------------------------
def analyse(units, sensitivity='medium', today=None, incognito=False) -> dict | None:
    """The numbers for a list of rated units [{'id', 'timestamp', 'ratings'}]. None when no unit has all 13 ratings."""
    complete = [u for u in units if all(a in (u.get('ratings') or {}) for a in analysis.ATTRIBUTES)]
    if not complete:
        return None
    flags = {u['id']: message_flags(u['ratings']) for u in complete}
    overall = tally([flags[u['id']] for u in complete])
    ordered, stamps, basis, left_off = order_units(complete, today)
    spans = cut(len(ordered))
    utc = basis == 'time' and all(_has_offset(u.get('timestamp')) for u, s in zip(ordered, stamps) if s.time() != _dt.time.min) and any(s.time() != _dt.time.min for s in stamps)
    labels = stretch_labels(spans, stamps, basis, utc, incognito)
    k = len(spans)
    stretches, tallies = [], []
    for i, (a, b) in enumerate(spans):
        t = tally([flags[u['id']] for u in ordered[a:b]])
        tallies.append(t)
        stretches.append({'i': i, 'n': t['n'], 'axis': labels[i][0], 'label': labels[i][1], 'health': health_mean(t),
                          'start': _stamp_text(stamps[a], incognito) if basis == 'time' else None, 'end': _stamp_text(stamps[b - 1], incognito) if basis == 'time' else None,
                          'counts': {x: t[x] for x in KINDS}, 'rates': {x: rate(t, x) for x in KINDS}})
    timeline_n = len(ordered)
    verdicts = timeline_n >= MIN_VERDICT
    long_span = bool(basis == 'time' and spans and (stamps[spans[-1][1] - 1].date() - stamps[spans[0][0]].date()).days > 300)
    today_ = today or _dt.datetime.now(_dt.timezone.utc).date()
    dated = sum(1 for u in complete if _usable(parse_ts(u.get('timestamp')), today_))
    steps = climbs(tallies) if tallies else []
    eps = episodes_of(tallies, steps, verdicts)
    for e in eps:
        e['where'] = where_phrase(e['first'], e['last'], k)
        e['dates'] = dates_phrase(stretches, e['first'], e['last'], long_span) if basis == 'time' else ''
        e['label'] = stretches[e['first']]['label'] if e['first'] == e['last'] else stretches[e['first']]['label'].split(': ')[0] + ' to ' + stretches[e['last']]['label'].split(': ')[0]
    for s, st in zip(stretches, steps):
        s['climb'] = st['climb'] if any(e['first'] <= s['i'] <= e['last'] for e in eps) else 0.0
        s['counted'] = bool(s['climb'])
    g = max(1, k // 3)
    first3, last3 = (merge(tallies[:g]), merge(tallies[-g:])) if tallies else (None, None)
    trend = {x: (rate_change(first3, last3, x) if verdicts else None) for x in ('hostile', 'care', 'dig', 'heated')}
    trend['health'] = health_change(first3, last3) if verdicts else None
    clock = bool(basis == 'time' and spans and _uses_clock([stamps[a] for a, _ in spans], [stamps[b - 1] for _, b in spans], incognito))
    return {'n': len(complete), 'incomplete': len(units) - len(complete), 'basis': basis, 'left_off': left_off, 'dated': dated, 'timeline_n': timeline_n, 'utc': utc, 'clock': clock,
            'has_series': bool(stretches), 'verdicts': verdicts, 'sensitivity': sensitivity if sensitivity in ('low', 'medium', 'high') else 'medium',
            'scale': LOW_SCALE if sensitivity == 'low' else 1.0, 'incognito': bool(incognito), 'overall': overall, 'stretches': stretches, 'episodes': eps,
            'first3': first3, 'last3': last3, 'trend': trend, 'opening_heated': bool(tallies) and rate(tallies[0], 'heated') >= 30,
            'health': round(health_mean(overall), 3), 'silent': overall['silent'], 'sig': f'{len(complete)}:{round(overall["pos"] * 1000)}:{round(overall["neg"] * 1000)}:{overall["hostile"]}:{overall["care"]}'}


def level_word(kind, value, scale=1.0) -> str:
    low, high = CUTS[kind]
    if kind in SCALED:
        low, high = low * scale, high * scale
    shown = whole(value)
    return 'Rare' if shown < whole(low) else 'Some' if shown < whole(high) else 'Often'


def _tone(kind, word) -> str:
    if kind == 'care':
        return 'good' if word == 'Often' else 'info'
    return {'Rare': 'good', 'Some': 'warn', 'Often': 'bad' if kind == 'hostile' else 'warn'}[word]


def reading(m):
    """The group of messages a level alert reads: the latest third when it holds LAST_THIRD_MIN messages and every rated message is on the timeline, else the whole
    check (messages left off the timeline for having no date are still messages, so a level alert must count them). (tally, words, of-words)."""
    if m['last3'] and m['verdicts'] and m['last3']['n'] >= LAST_THIRD_MIN and not m['left_off']:
        words = 'in the latest part of the chat' if m['basis'] == 'time' else 'in the last part of the list'
        return m['last3'], words, f'of the last {m["last3"]["n"]:,} messages'
    return m['overall'], 'across the check', f'of the {m["overall"]["n"]:,} messages'


# ---- the cards ----------------------------------------------------------------------------------
CARD_TEXT = {
    'escalation': ('Escalation', 'How quickly the mood heats up.'),
    'conflict': ('Conflict', 'How much of the talk is openly hostile.'),
    'empathy': ('Empathy', 'How much of the talk shows care and understanding.'),
    'passive': ('Passive-aggressive', 'Sarcasm without open hostility. Friendly banter can count too.'),
}
NO_CALL = 'Too few messages to call a direction.'


def _trend_text(change, kind, m) -> str:
    if change is None:
        return NO_CALL if m['has_series'] else ''
    first, last = whole(change['first']), whole(change['last'])
    if change['call'] == 'steady':
        return 'No clear change.'
    word = 'Rising' if change['call'] == 'rising' else 'Falling'
    return f'{word}: {first} in 100 early on, {last} in 100 lately.'


def cards(m) -> list:
    """The four measure cards, with their words. Each: key, name, meaning, level, tone, headline, trend, note, series (what the chart draws)."""
    o, out = m['overall'], []
    stretches = m['stretches']
    for key in KEYS:
        name, meaning = CARD_TEXT[key]
        kind = KIND_OF[key]
        card = {'key': key, 'name': name, 'meaning': meaning, 'kind': kind, 'series': [s['rates'][kind] for s in stretches]}
        if key == 'escalation':
            eps = m['episodes']
            flagged = []                                              # the parts the Track page flags: the card and the graphs now say the same thing
            if m['has_series'] and m['verdicts']:
                import track
                flagged = [(i, f) for i, f in enumerate(track.escalation_flags(m)) if f['level']]
            peak = max((f['climb'] for _i, f in flagged), default=0.0)
            word = escalation_word(peak) if flagged else 'none'
            if flagged and word == 'none':
                word = 'small'
            card.update({'level': ESCALATION_LABEL[word], 'word': word, 'tone': {'none': 'good', 'small': 'info', 'clear': 'warn', 'steep': 'bad'}[word], 'peak': peak})
            if not m['has_series'] or not m['verdicts']:                # nothing could be judged: never a green 'None'
                card.update(level=ESCALATION_LABEL['early'], word='early', tone='info')
            if not m['has_series']:
                card['headline'] = f'Needs at least {MIN_TIMELINE} messages to follow the mood over time.'
            elif not m['verdicts']:
                card['headline'] = f'Needs at least {MIN_VERDICT} messages to spot heated moments.'
            elif not flagged:
                card['headline'] = 'No heated moments.'
            else:
                red = sum(1 for _i, f in flagged if f['level'] == 'red')
                yellow = len(flagged) - red
                big_i = max(flagged, key=lambda x: x[1]['climb'])[0]
                said = ' and '.join(x for x in (f'{red} red flag{"" if red == 1 else "s"}' if red else '', f'{yellow} yellow flag{"" if yellow == 1 else "s"}' if yellow else '') if x)
                card['headline'] = f'{said}, the biggest {where_phrase(big_i, big_i, len(stretches))}.' if len(flagged) > 1 else f'{said}, {where_phrase(big_i, big_i, len(stretches))}.'
            card['trend'] = ''
            hottest = max((s['rates']['heated'] for s in stretches), default=0.0)
            card['note'] = ('The mood was already tense when the messages start. Escalation only counts a rise.' if m['opening_heated'] and m['verdicts'] else
                            'Some parts were heated, but none climbed above the parts before it, so no part is flagged.' if m['verdicts'] and not flagged and whole(hottest) >= 30 else '')
            card['count'] = len(flagged) if m['verdicts'] else None
            if m['opening_heated'] and m['verdicts'] and not flagged:       # hot from the first part: a rise could not be seen, so no green mark
                card['tone'] = 'info'
        else:
            word = level_word(kind, rate(o, kind), m['scale'])
            what = {'hostile': 'were openly hostile.', 'care': 'showed care or understanding.', 'dig': 'had sarcasm without open hostility.'}[kind]
            card.update({'level': word, 'tone': _tone(kind, word), 'headline': _messages(o[kind], o['n'], what), 'count': o[kind], 'of': o['n'],
                         'trend': _trend_text(m['trend'][kind], kind, m), 'note': ''})
            if m['n'] < MIN_VERDICT:                                      # too few messages to call it good or bad: a neutral mark
                card['tone'] = 'info'
            if m['last3'] and m['verdicts']:
                card['latest'] = level_word(kind, rate(m['last3'], kind), m['scale'])
                card['latest_label'] = 'Last third'
        if is_silent(m):                                             # almost no tone found: nothing here is good news
            card['tone'] = 'info'
        card['tone_word'] = TONE_WORDS[card['tone']]
        out.append(card)
    return out


def health_tile(m) -> dict:
    status = health_status(m['health'])
    t = m['trend']['health']
    trend = ''
    if t is not None:
        trend = 'No clear change.' if t['call'] == 'steady' else f'{"Rising" if t["call"] == "rising" else "Falling"}: {whole(t["first"])} early on, {whole(t["last"])} lately.'
    tone = 'info' if m['n'] < MIN_VERDICT or is_silent(m) else HEALTH_TONE[status]       # too few messages, or no tone found: a neutral mark
    return {'key': 'health', 'name': 'Health score', 'score': m['health'], 'status': status, 'tone': tone, 'tone_word': TONE_WORDS[tone], 'trend': trend,
            'series': [s['health'] for s in m['stretches']],
            'caption': 'The health score shows how warm your conversations are, from 0 to 100. Around 50 is neutral, higher means warmer and lower means colder. '
                       'Vibe Check measures friendliness separately, so the two can differ.'}


# ---- numbers kept after each check --------------------------------------------------------------
def _fingerprint(name) -> str:
    """A short, one-way code for a model name, so two names the store cannot keep (too long, odd characters) are still told apart."""
    return hashlib.sha256(str(name).encode('utf-8', 'replace')).hexdigest()[:12] if name else ''


def _model_key(rec) -> str:
    return rec.get('model_key') or _fingerprint(rec.get('model'))


def record(m, a=None, rated=None, gen=None, created_at=None) -> dict:
    """The record kept after a check: scores, counts and the names of its settings. No message, no name, no file name (the store checks this too)."""
    o = m['overall']
    a = a or {}
    keys = a.keys() if hasattr(a, 'keys') else ()
    get = lambda k: (a[k] if k in keys else None)       # noqa: E731
    model = get('model_used') or (rated or {}).get('model') or ''
    rec = {'created_at': created_at or utc_now(), 'gen': gen, 'n': m['n'], 'total': (rated or {}).get('total') or m['n'], 'sampled': int(bool((rated or {}).get('note'))),
           'basis': m['basis'], 'stretches': len(m['stretches']), 'model': model,
           'model_key': _fingerprint(model), 'sensitivity': m['sensitivity'], 'period_start': get('period_start') or '', 'period_end': get('period_end') or '',
           'members': get('members') or None, 'health': round(m['health'], 3), 'health_sd': round(math.sqrt(_d_var(o)) * 50 / HEALTH_DIVISOR, 3),
           'hostile_n': o['hostile'], 'care_n': o['care'], 'dig_n': o['dig'], 'heated_n': o['heated'],
           'episodes': len(m['episodes']) if m['verdicts'] else None, 'peak': round(max((e['peak'] for e in m['episodes']), default=0.0), 3) if m['verdicts'] else None, 'sig': m['sig']}
    return {k: v for k, v in rec.items() if v is not None}          # no empty fields: what is saved is exactly what is here


def save_snapshot(aid, rated=None):
    """Computes the measures of an analysis and keeps the numbers of this check (one record per set of evidence). Never raises: a check does not fail for
    this. Returns the record, or None."""
    try:
        a = store.get_analysis(aid)
        m = analyse(store.rated_units(aid), (a['vibe_sensitivity'] if a else None) or 'medium', incognito=bool(a and a['incognito']))
        if m is None:
            return None
        rec = record(m, a, rated, store.evidence_generation(aid))
        return store.save_measure_snapshot(aid, rec) and rec
    except Exception:
        log.exception('could not save the measures (analysis %s)', aid)
        return None


# ---- comparing with the check before ------------------------------------------------------------
def compare(cur, prev) -> dict | None:
    """The current check's record against the one before it. `comparable` is False (with `why`) when the two were not made alike, and then no
    change is called. Each change in `changes` says how far the figure moved, whether that is more than chance, and whether it is better or worse."""
    if not prev:
        return None
    out = {'previous': prev, 'comparable': True, 'why': '', 'changes': {}}
    if _model_key(cur) != _model_key(prev):
        out.update(comparable=False, why='Your last check used a different model, so part of the change may come from the model and not from the community.')
    elif (cur.get('sensitivity') or '') != (prev.get('sensitivity') or ''):
        out.update(comparable=False, why='Your last check used a different sensitivity, so part of the change may come from the setting and not from the community.')
    elif min(cur['n'], prev['n']) < MIN_VERDICT:
        out.update(comparable=False, why=f'One of the two checks rated fewer than {MIN_VERDICT} messages, which is too few to call a change.')
    elif max(cur['n'], prev['n']) > COMPARE_RATIO * min(cur['n'], prev['n']):
        out.update(comparable=False, why='The two checks rated very different numbers of messages, so part of the change may come from that.')
    if not out['comparable']:
        return out
    for kind, label in (('hostile', 'conflict'), ('care', 'empathy'), ('dig', 'passive'), ('heated', 'heated')):
        k1, n1, k2, n2 = prev.get(f'{kind}_n'), prev['n'], cur.get(f'{kind}_n'), cur['n']
        if k1 is None or k2 is None:
            continue
        k1, k2 = min(max(int(k1), 0), n1), min(max(int(k2), 0), n2)        # a damaged record cannot push a count past its own total
        first, last = 100 * k1 / n1, 100 * k2 / n2
        se = 100 * math.sqrt(_lap_var(k1, n1) / n1 + _lap_var(k2, n2) / n2)
        out['changes'][label] = _change(first, last, se, max(CHANGE_MIN, Z_CLEAR * se), good='up' if kind == 'care' else 'down')
    if prev.get('health') is not None and prev.get('health_sd') is not None and cur.get('health_sd') is not None:      # without both spreads a chance-sized move could pass
        sd1, sd2 = prev['health_sd'], cur['health_sd']
        se = math.sqrt(sd1 ** 2 / prev['n'] + sd2 ** 2 / cur['n'])
        out['changes']['health'] = _change(prev['health'], cur['health'], se, max(HEALTH_CHANGE_MIN, Z_CLEAR * se), good='up')
    return out


def _change(first, last, se, need, good) -> dict:
    delta = last - first
    big = abs(delta) >= need - 1e-9
    strong = abs(delta) >= max(need, Z_STRONG * se) - 1e-9
    better = (delta > 0) == (good == 'up')
    return {'first': first, 'last': last, 'delta': delta, 'se': se, 'clear': big, 'strong': strong,
            'verdict': ('better' if better else 'worse') if big else 'same', 'strong_verdict': ('better' if better else 'worse') if strong else 'same'}


def trail_of(history, live) -> list:
    """The check-by-check records ending with the current check: each earlier check once (the newest record of each set of evidence), then the live one."""
    out, where = [], {}
    for rec in history:
        gen = rec.get('gen')
        if gen is not None and gen == live.get('gen'):
            continue                                    # that is the current check: the live record stands for it
        if gen is not None and gen in where:
            out[where[gen]] = rec                       # a later record of the same check replaces the earlier one
            continue
        if gen is not None:
            where[gen] = len(out)
        out.append(rec)
    return out + [live]


# ---- alerts and notes ---------------------------------------------------------------------------
NOTE_ABOUT = {'no_vibe': 'alerts'}       # every other note is about the measures; a page prints each note once, in its own section
def _alert(level, key, title, detail, step, base, when='') -> dict:
    return {'level': level, 'key': key, 'title': title, 'detail': detail, 'try': step, 'base': base, 'when': when}


def alert_catalog() -> list:
    """Every alert and flag this app can raise, in groups, for the folded list on the Alerts screen: [(group, [(name, level, how)])]."""
    import flags
    return [
        ('Flags on single messages', [(n, 'High', 'A message rated 2 or 3 for this tone.' if n in flags.HIGH_TONES else 'A message Vibe Check marked as this kind.') for n in flags.HIGH_NAMES]
         + [(n, 'Medium', 'A message with this tone or wording, and no high flag.') for n in flags.MEDIUM_NAMES]),
        ('What Vibe Check can flag', [(n, '', 'Messages quoted under Flagged messages, each with a severity from 1 to 10.')
                                      for n in ('Insults', 'Harassment', 'Threats', 'Hate speech', 'Bullying', 'Profanity', 'Spam')]),
        ('Alerts about the whole chat', [
            ('Vibe Check flagged serious messages', 'High or Medium', 'Messages scored 7 or more out of 10, or several flagged ones.'),
            ('The mood heated up fast', 'High or Medium', 'A part of the chat where heated messages climbed well above the usual level.'),
            ('Many messages were openly hostile', 'High', 'A large share of messages were aggressive, rude, disrespectful or judgmental.'),
            ('Some messages were openly hostile', 'Medium', 'A noticeable share of hostile messages.'),
            ('Sarcastic wording is common', 'Medium', 'A noticeable share of messages with sarcasm.'),
            ('Hostile wording is picking up', 'Medium', 'Hostile messages grew from the start to the end of the chat.'),
            ('Sarcastic wording is growing', 'Medium', 'Sarcasm grew from the start to the end of the chat.'),
            ('Fewer messages show care', 'Medium', 'Care and understanding faded over the chat.'),
            ('The overall mood slipped since your last check', 'Medium', 'The health score fell clearly compared with your last check.'),
            ('Hostile wording is higher than at your last check', 'Medium', 'More hostile messages than last time.')]),
        ('Good news', [
            ('Care is growing', 'Good', 'More messages show care and understanding.'),
            ('Hostility is easing', 'Good', 'Fewer hostile messages.'),
            ('The overall mood improved since your last check', 'Good', 'The health score rose clearly.')])]


def _flag_count(vibe, kind) -> int:
    """Messages Vibe Check counted for a kind of toxicity: its tally or the flagged messages of that type, whichever is more."""
    tally_n = int(((vibe or {}).get('toxicity_types') or {}).get(kind) or 0)
    flagged = sum(1 for f in (vibe or {}).get('flagged') or [] if {'threat': 'threats', 'insult': 'insults', 'harassment': 'harassment', 'hate_speech': 'hate_speech',
                                                                  'bullying': 'bullying', 'profanity': 'profanity', 'spam': 'spam'}.get(f.get('type')) == kind)
    return max(tally_n, flagged)


SERIOUS_LABEL = {'threats': 'threats', 'hate_speech': 'hate speech', 'harassment': 'harassment', 'bullying': 'bullying'}


def serious_flags(vibe) -> dict:
    """The serious kinds Vibe Check flagged, with counts, and how many of the flagged messages scored SERIOUS_SCORE or more: {'kinds': {kind: n}, 'total', 'red', 'quoted'}."""
    kinds = {k: _flag_count(vibe, k) for k in SEVERE_TYPES}
    kinds = {k: n for k, n in kinds.items() if n}
    flagged = [f for f in (vibe or {}).get('flagged') or [] if f.get('type') in ('threat', 'hate_speech', 'harassment', 'bullying')]
    return {'kinds': kinds, 'total': sum(kinds.values()), 'red': sum(1 for f in flagged if (f.get('severity') or 0) >= SERIOUS_SCORE), 'quoted': len(flagged)}


def _flagged_what(kinds) -> str:
    """'3 messages as threats', or 'messages for threats (6), harassment (6) and bullying (6)' when several kinds were counted (the tallies overlap, so they are not added)."""
    if len(kinds) == 1:
        (kind, n), = kinds.items()
        return f'{n:,} message{"" if n == 1 else "s"} as {"a threat" if kind == "threats" and n == 1 else SERIOUS_LABEL[kind]}'
    parts = [f'{SERIOUS_LABEL[k]} ({kinds[k]:,})' for k in ('threats', 'hate_speech', 'harassment', 'bullying') if k in kinds]
    return 'messages for ' + ', '.join(parts[:-1]) + ' and ' + parts[-1]


def alerts(m, vibe=None, comparison=None, cov=None) -> tuple:
    """(alerts, notes) for a check. Alerts are ordered High priority, Medium priority, Good news. Notes are quiet lines about the basis of the figures.
    Nothing here reads a message: every sentence is built from numbers and the words in this module."""
    cov = cov or {}
    ok = bool(vibe) and vibe.get('status') == 'ok'
    found, notes = [], []
    o, s = m['overall'], m['scale']
    scaled = lambda x: x * s     # noqa: E731

    # Vibe Check's serious flags (the one alert that fires on a single message)
    if ok:
        sf = serious_flags(vibe)
        if sf['total']:
            kinds = sf['kinds']
            floor = max(kinds.values())                    # the fewest messages it can be: the tallies overlap, so they are never added up
            threat = bool(kinds.get('threats') or kinds.get('hate_speech'))
            high = threat or sf['red'] > 0
            single = floor == 1 and len(kinds) == 1
            read = vibe.get('analyzed_n', 0)
            red = '' if not sf['red'] else (' One scored 7 or more out of 10.' if sf['red'] == 1 else f' {sf["red"]} scored {SERIOUS_SCORE} or more out of 10.')
            if sf['quoted'] == 0:
                where = 'It was counted, not quoted.' if single else 'They were counted, not quoted.'
            elif sf['quoted'] >= floor:
                where = ('It is quoted' if single else 'They are quoted') + ' under Flagged messages.'
            else:
                where = f'{sf["quoted"]:,} of them {"is" if sf["quoted"] == 1 else "are"} quoted under Flagged messages. The rest were counted, not quoted.'
            safety = ("A model can mistake jokes, quotes and game talk for threats. If a message describes a real risk to someone, follow your community's safety process "
                      'or contact local emergency services.')
            if threat:
                step = (('Read the quoted messages in their context first. ' if sf['quoted'] else 'Vibe Check counted these but did not quote them. ') + safety
                        + (' Otherwise, check in with whoever the message was aimed at, and agree one shared goal for the next conversation.' if sf['quoted']
                           else ' Otherwise, agree one shared goal for the next conversation.'))
            elif sf['quoted']:
                step = 'Read the quoted messages in their context first. Check in with whoever the message was aimed at, and thank everyone who helps keep the tone respectful.'
            else:
                step = 'Vibe Check counted these but did not quote them. Thank everyone who helps keep the tone respectful.'
            found.append(_alert('high' if high else 'medium', 'serious', 'Vibe Check flagged serious messages',
                                f'It flagged {_flagged_what(kinds)}.{red} A model can be wrong. {where}', step, f'Vibe Check read {read:,} message{"" if read == 1 else "s"}.'))
    else:
        notes.append({'key': 'no_vibe', 'text': 'Alerts about flagged messages need Vibe Check, which has not run. This list is not an all clear.'})

    # what the 13-tone ratings say
    if m['n'] < MIN_TIMELINE:
        notes.append({'key': 'few', 'text': f'We rated only {m["n"]:,} message{"" if m["n"] == 1 else "s"}, so there are no lines over time yet. With {MIN_TIMELINE} or more '
                                             'you can see how the mood moved, and with 48 or more, whether it is rising or falling.'})
    else:
        if not m['has_series']:
            notes.append({'key': 'few_dated', 'text': f'Only {m["timeline_n"]:,} of {m["n"]:,} messages have a date, so there are no lines over time yet. Lines need at least {MIN_TIMELINE} messages with a date.'})
        elif not m['verdicts']:
            notes.append({'key': 'no_verdict', 'text': f'The lines show what happened. A direction, or a heated moment, needs at least {MIN_VERDICT} messages on the timeline; this one has {m["timeline_n"]:,}.'})
        # a heated moment: each is judged on its own and the strongest level that stands is the one shown
        if m['episodes']:
            hi, me = HEATED_HIGH, tuple(c * (LOW_TENSION if m['sensitivity'] == 'low' else 1.0) for c in HEATED_MEDIUM)
            ranked = []
            for i, e in enumerate(m['episodes']):
                rank = 2 if whole(e['peak']) >= hi[0] and whole(e['reached']) >= hi[1] else 1 if whole(e['peak']) >= whole(me[0]) and whole(e['reached']) >= whole(me[1]) else 0
                if rank and e['heated'] >= EVIDENCE_MIN:
                    ranked.append((rank, e['peak'], e['reached'], i))
            if ranked:
                rank, _p, _r, at = max(ranked)
                top = m['episodes'][at]
                parts = top['last'] - top['first'] + 1
                when = top['where'] + (f' ({top["dates"]})' if top['dates'] else '')
                before = whole(top['baseline'] or 0)
                base = 'Before that, almost none were.' if before == 0 else f'Before that, about {before} in 100 were.'
                found.append(_alert('high' if rank == 2 else 'medium', 'heated', 'The mood heated up fast',
                                    f'{top["where"].capitalize()}, {top["heated"]:,} of {top["n"]:,} messages in {"one part" if parts == 1 else f"{parts} parts in a row"} of the chat were heated. {base}',
                                    'Think about what was going on around then (an event, a deadline, a change) rather than who was speaking. A pinned reminder of the shared goal helps. '
                                    'So does a thank-you to everyone who helped things settle, and a lighter activity afterwards.',
                                    f'Based on {top["n"]:,} messages in {"that part" if parts == 1 else "those parts"}, {top["heated"]:,} of them heated.', when))
        # level alerts: conflict, then sarcasm. Each reads the latest third (when it holds enough messages) or the whole check, and needs the rate to clear its
        # cut-off by more than one standard error, with at least EVIDENCE_MIN messages that clearly show it.
        tk, where_words, of = reading(m)
        read_base = f'Based on {tk["n"]:,} messages {where_words}.'
        early_late = f'Based on {m["first3"]["n"]:,} early and {m["last3"]["n"]:,} recent messages.' if m['last3'] else ''
        for kind, key in (('hostile', 'conflict'), ('dig', 'passive')):
            count, level, title, detail, step = tk[kind], None, '', '', ''
            floor_ok = m['n'] >= MIN_TIMELINE and count >= EVIDENCE_MIN
            if kind == 'hostile':
                latest = tk is m['last3']
                recent = 'recent messages' if m['basis'] == 'time' else 'messages in the last part'
                if floor_ok and _clears(count, tk['n'], scaled(CONFLICT_HIGH)):
                    level, title = 'high', (f'Many {recent} were openly hostile' if latest else 'Many messages were openly hostile')
                    step = "Say what a good conversation looks like here. Spotlight a few messages that show it, with their authors' OK. What gets celebrated gets repeated."
                elif floor_ok and _clears(count, tk['n'], scaled(CONFLICT_MEDIUM)):
                    level, title = 'medium', (f'Some {recent} were openly hostile' if latest else 'Some messages were openly hostile')
                    step = 'Welcome calm replies when a thread gets sharp, and thank whoever steadies it.'
                detail = f'{count:,} {of} were rated aggressive, rude, disrespectful or judgmental (about {whole(rate(tk, kind))} in 100).'
                rising_title, rising_text = 'Hostile wording is picking up', 'Openly hostile messages went from {a} of {n1} early on to {b} of {n2} lately.'
                rising_step = 'Open the next session with one thing that went well. A small ritual sets the tone early.'
            else:
                if floor_ok and _clears(count, tk['n'], scaled(DIG_MEDIUM)):
                    level, title = 'medium', 'Sarcastic wording is common'
                    step = 'Make direct, kind feedback easy for everyone. A short pinned note helps, such as "say it straight, say it kindly", with one example.'
                detail = f'{count:,} {of} had sarcasm without open hostility (about {whole(rate(tk, kind))} in 100). Friendly banter can count too.'
                rising_title, rising_text = 'Sarcastic wording is growing', 'Messages with sarcasm went from {a} of {n1} early on to {b} of {n2} lately.'
                rising_step = 'Make direct, kind feedback easy for everyone. A short pinned note helps, such as "say it straight, say it kindly", with one example.'
            base = read_base
            tr = m['trend'][kind]
            if level is None and tr and tr['strong'] and tr['call'] == 'rising' and m['last3'][kind] >= EVIDENCE_MIN and whole(tr['last']) >= whole(scaled(RISING_FLOOR)):
                level, title, step, base = 'medium', rising_title, rising_step, early_late
                detail = rising_text.format(a=m['first3'][kind], n1=m['first3']['n'], b=m['last3'][kind], n2=m['last3']['n'])
            if level:
                found.append(_alert(level, key, title, detail, step, base))
        # care fading, and the good news that goes with each direction
        tr = m['trend']['care']
        care_fading = bool(tr and tr['strong'] and tr['call'] == 'falling' and m['first3']['care'] >= CARE_FADING_FROM)
        if care_fading:
            found.append(_alert('medium', 'empathy', 'Fewer messages show care',
                                f'{m["first3"]["care"]:,} of {m["first3"]["n"]:,} early messages showed care or understanding. Lately it was {m["last3"]["care"]:,} of {m["last3"]["n"]:,}.',
                                "Say thank you in public. Ask an open question, such as 'What helped you this week?', and answer a few replies yourself.",
                                f'Based on {m["first3"]["n"]:,} early and {m["last3"]["n"]:,} recent messages.'))
        elif tr and tr['strong'] and tr['call'] == 'rising':
            found.append(_alert('good', 'empathy_up', 'Care is growing',
                                f'{m["last3"]["care"]:,} of {m["last3"]["n"]:,} recent messages showed care or understanding, up from {m["first3"]["care"]:,} of {m["first3"]["n"]:,} early on.',
                                'Say what you noticed. A public thank you keeps it going.', f'Based on {m["first3"]["n"]:,} early and {m["last3"]["n"]:,} recent messages.'))
        tr = m['trend']['hostile']
        if tr and tr['strong'] and tr['call'] == 'falling' and not any(a['key'] == 'conflict' for a in found):
            found.append(_alert('good', 'conflict_down', 'Hostility is easing',
                                f'Openly hostile messages fell from {m["first3"]["hostile"]:,} of {m["first3"]["n"]:,} early on to {m["last3"]["hostile"]:,} of {m["last3"]["n"]:,} lately.',
                                'Thank everyone who helped reset the tone.', f'Based on {m["first3"]["n"]:,} early and {m["last3"]["n"]:,} recent messages.'))

    # against the check before
    if comparison and comparison['comparable']:
        ch = comparison['changes']
        h = ch.get('health')
        before_n = comparison['previous']['n']
        base = f'Based on {m["n"]:,} messages in each check.' if before_n == m['n'] else f'Based on {before_n:,} and {m["n"]:,} messages.'
        if h and h['strong_verdict'] == 'worse':
            found.append(_alert('medium', 'mood_down', 'The overall mood slipped since your last check',
                                f'The health score went from {whole(h["first"])} to {whole(h["last"])} out of 100.',
                                'Start with the measure that moved the most. Pick one small habit to try before the next check.', base))
        elif h and h['strong_verdict'] == 'better':
            found.append(_alert('good', 'mood_up', 'The overall mood improved since your last check',
                                f'The health score rose from {whole(h["first"])} to {whole(h["last"])} out of 100.', 'Note what you changed. Repeat what worked.', base))
        c = ch.get('empathy')
        if c and c['strong_verdict'] == 'worse' and whole(c['first']) >= CARE_FADING_FROM and not any(a['key'] == 'empathy' for a in found):
            found.append(_alert('medium', 'empathy', 'Fewer messages show care',
                                f'{whole(c["first"])} in 100 messages showed care or understanding at your last check. Now it is {whole(c["last"])}.',
                                "Say thank you in public. Ask an open question, such as 'What helped you this week?', and answer a few replies yourself.", base))
        elif c and c['strong_verdict'] == 'better' and not any(a['key'] in ('empathy', 'empathy_up') for a in found):
            found.append(_alert('good', 'empathy_up', 'Care is growing', f'{whole(c["last"])} in 100 messages showed care or understanding, up from {whole(c["first"])} at your last check.',
                                'Say what you noticed. A public thank you keeps it going.', base))
        c = ch.get('conflict')
        if c and c['strong_verdict'] == 'better' and not any(a['key'] in ('conflict', 'conflict_down') for a in found):
            found.append(_alert('good', 'conflict_down', 'Hostility is lower than at your last check', f'{whole(c["last"])} in 100 messages were openly hostile, down from {whole(c["first"])} at your last check.',
                                'Thank everyone who helped reset the tone.', base))
        elif c and c['strong_verdict'] == 'worse' and not any(a['key'] == 'conflict' for a in found) and whole(c['last']) >= whole(scaled(RISING_FLOOR)):
            found.append(_alert('medium', 'conflict', 'Hostile wording is higher than at your last check', f'{whole(c["last"])} in 100 messages were openly hostile, up from {whole(c["first"])} at your last check.',
                                'Open the next session with one thing that went well. A small ritual sets the tone early.', base))

    if m['has_series']:
        if m['basis'] == 'order':
            dated = m.get('dated', 0)
            enough = DATED_SHARE[1] * dated >= DATED_SHARE[0] * m['n']
            lead = ('These messages have no usable dates' if not dated else f'Only {dated:,} of {m["n"]:,} messages have a usable date' if not enough
                    else 'These messages fall on fewer than three different dates and times')
            notes.append({'key': 'order', 'text': f'{lead}, so the lines follow the order you added them. Add dates to see when things changed. '
                                                  'If your export lists the newest message first, read rising and falling the other way round.'})
        elif m['left_off']:
            notes.append({'key': 'undated', 'text': f'Only {m["timeline_n"]:,} of {m["n"]:,} messages have a date. The lines show those. The figures here still count all {m["n"]:,}.'})
    if m['incomplete']:
        notes.append({'key': 'incomplete', 'text': f'{m["incomplete"]:,} message{"" if m["incomplete"] == 1 else "s"} had incomplete ratings and {"is" if m["incomplete"] == 1 else "are"} left out of these figures.'})
    if cov.get('sampled'):
        notes.append({'key': 'sampled', 'text': 'A short flare-up can fall between the samples. Choose "all of them" for the volume to see every message.'})
    if m['scale'] > 1:
        notes.append({'key': 'low_sensitivity', 'text': 'Your sensitivity is Low, so banter counts as normal. The bars for Conflict, Passive-aggressive and heated moments are higher.'})
    if m['n'] >= MIN_BADGE and is_silent(m):
        notes.append({'key': 'silent', 'text': 'Almost no tone was found in these messages. The model may not have read this text well.'})
    if cov.get('added_since'):
        k = cov['added_since']
        notes.append({'key': 'added', 'text': f'{k:,} message{"" if k == 1 else "s"} fall{"s" if k == 1 else ""} inside the review period but {"was" if k == 1 else "were"} not rated, '
                                              f'so {"it is" if k == 1 else "they are"} not included. Run the check again to include {"it" if k == 1 else "them"}.'})
    if cov.get('left_out'):
        k = cov['left_out']
        notes.append({'key': 'period', 'text': f'The review period now leaves out {k:,} message{"" if k == 1 else "s"} that {"was" if k == 1 else "were"} rated. Run the check again to apply it.'})
    notes = [dict(n, about=NOTE_ABOUT.get(n['key'], 'measures')) for n in notes]
    # one verdict per measure: a good-news line is dropped when an alert about the same measure is standing
    standing = {a['key'] for a in found if a['level'] != 'good'}
    found = [a for a in found if not (a['level'] == 'good' and ((a['key'] == 'empathy_up' and 'empathy' in standing) or (a['key'] == 'conflict_down' and 'conflict' in standing)
                                                                 or (a['key'] == 'mood_up' and 'mood_down' in standing)))]
    order = {name: i for i, name in enumerate(LEVELS)}
    return sorted(found, key=lambda a: order[a['level']]), notes


# ---- badges -------------------------------------------------------------------------------------
BADGES = (
    ('courteous', 'Courteous', 'Clearly polite wording in at least 20 messages in 100, and fewer than 3 in 100 openly hostile.'),
    ('supportive', 'Supportive', 'Care or understanding in at least 12 messages in 100.'),
    ('encouraging', 'Encouraging', 'Encouraging wording in at least 12 messages in 100.'),
    ('lighthearted', 'Light-hearted', 'Funny wording in at least 12 messages in 100.'),
    ('calm', 'Calm waters', 'No heated moments, fewer than 10 in 100 messages heated, and fewer than 3 in 100 openly hostile. Needs every message to be read.'),
    ('cools', 'Cools fast', 'A heated moment that settled soon after it began. You do not need to aim for one. Needs every message to be read.'),
    ('noflags', 'No serious flags', 'Vibe Check read at least 30 messages, flagged fewer than 1 serious message in 200, and none scored 7 or more. Needs every message to be read.'),
)


def _cooled(m):
    steps = [s['rates']['heated'] for s in m['stretches']]
    for e in m['episodes']:
        if whole(e['peak']) < BADGE_COOL_PEAK:
            continue
        after = steps[e['last'] + 1:e['last'] + 1 + BADGE_COOL_WITHIN]
        if after and min(after) <= (e['baseline'] or 0) + BADGE_COOL_BACK:
            return e
    return None


def badges(m, vibe=None, cov=None) -> tuple:
    """(badges, note). Each badge: key, name, how, earned, evidence, needs (why it cannot be judged, if it cannot)."""
    cov = cov or {}
    o = m['overall']
    n = o['n']
    ok = bool(vibe) and vibe.get('status') == 'ok'
    full = not cov.get('sampled') and not cov.get('added_since')
    out, note = [], ''
    silent = is_silent(m)
    few = f'Badges start at {MIN_BADGE} messages. This check has {n:,}.' if n < MIN_BADGE else ''
    if not few and silent:
        note = 'Almost no tone was found in these messages, so no badges were given. The model may not have read this text well.'

    def make(key, earned, evidence, needs=''):
        meta = next(b for b in BADGES if b[0] == key)
        blocked = few or note or needs
        return {'key': key, 'name': meta[1], 'how': meta[2], 'earned': bool(earned and not blocked), 'evidence': evidence if earned and not blocked else '', 'needs': '' if few or note else needs}
    hostile_low = 100 * o['hostile'] / n < BADGE_HOSTILE_MAX if n else False
    out.append(make('courteous', _clears(o['polite'], n, BADGE_POLITE) and hostile_low, f'Polite wording in {o["polite"]:,} of {n:,} messages.'))
    out.append(make('supportive', _clears(o['care'], n, BADGE_CARE), f'Care or understanding in {o["care"]:,} of {n:,} messages.'))
    out.append(make('encouraging', _clears(o['encouraging'], n, BADGE_ENCOURAGING), f'Encouraging wording in {o["encouraging"]:,} of {n:,} messages.'))
    out.append(make('lighthearted', _clears(o['funny'], n, BADGE_FUNNY), f'Funny wording in {o["funny"]:,} of {n:,} messages.'))
    cant = "Can't tell yet: only part of the chat was read." if not full else ''
    needs_line = cant or ('' if m['verdicts'] else f'Needs a timeline of at least {MIN_VERDICT} messages.')
    out.append(make('calm', m['verdicts'] and not m['episodes'] and hostile_low and 100 * o['heated'] / n < BADGE_CALM_HEATED, f'No heated moments, and {o["hostile"]:,} of {n:,} messages openly hostile.', needs_line))
    cooled = _cooled(m)
    evidence = ''
    if cooled is not None:                  # the figure behind the badge: how hot it got, and how far it fell back
        steps = [t['rates']['heated'] for t in m['stretches']]
        low = min(steps[cooled['last'] + 1:cooled['last'] + 1 + BADGE_COOL_WITHIN])
        evidence = f'Heated messages reached {whole(cooled["reached"])} in 100, then fell back to {whole(low)} in 100 within {BADGE_COOL_WITHIN} parts of the chat.'
    out.append(make('cools', cooled is not None, evidence, needs_line))
    sf = serious_flags(vibe) if ok else {'total': 0, 'red': 0}
    read = vibe.get('analyzed_n', 0) if ok else 0
    vibe_full = ok and read >= MIN_BADGE and (not cov.get('scope') or read >= 0.9 * cov['scope']) and full
    flag_needs = '' if vibe_full else (cant if ok and read >= MIN_BADGE else f'Needs a Vibe Check on at least {MIN_BADGE} messages.' if not ok or read < MIN_BADGE else cant)
    out.append(make('noflags', vibe_full and 200 * sf['total'] < read and sf['red'] == 0, f'{read:,} messages read, with {sf["total"]:,} serious flag{"" if sf["total"] == 1 else "s"} (threats, hate speech, harassment or bullying).', flag_needs))
    return out, (few or note)


# ---- since the last check -----------------------------------------------------------------------
SINCE_ROWS = (('health', 'Health score'), ('conflict', 'Conflict'), ('empathy', 'Empathy'), ('passive', 'Passive-aggressive'), ('heated', 'Heated messages'))
WORDS = {'better': 'Better', 'worse': 'Worse', 'same': 'About the same'}


def since_table(comparison, cur) -> dict | None:
    """The 'Since your last check' rows: last check, this check, and a verdict in words. None before a second check."""
    if not comparison:
        return None
    prev, rows = comparison['previous'], []
    for key, label in SINCE_ROWS:
        ch = comparison['changes'].get(key) if comparison['comparable'] else None
        if key == 'health':
            before, now = whole(prev.get('health') or 0), whole(cur.get('health') or 0)
        else:
            kind = {'conflict': 'hostile', 'empathy': 'care', 'passive': 'dig', 'heated': 'heated'}[key]
            before = f'{prev[kind + "_n"]:,} of {prev["n"]:,}' if prev.get(kind + '_n') is not None else 'Not known'
            now = f'{cur.get(kind + "_n", 0):,} of {cur["n"]:,}'
        rows.append({'label': label, 'before': before, 'now': now, 'verdict': WORDS[ch['verdict']] if ch else ''})
    return {'rows': rows, 'comparable': comparison['comparable'], 'why': comparison['why']}


# ---- everything for one report ------------------------------------------------------------------
CAVEAT_ALERTS = ('Alerts are prompts to look, not findings. A language model rated each message from 0 to 3. Trained people have not checked those ratings. '
                 'A handful of messages can move a figure, so every alert says how many messages stand behind it. They describe wording in part of a chat, never a person or '
                 'a team. High priority and Medium priority show how large a figure is. They are not a ruling on any person. The cut-offs are our own starting choice.')
CAVEAT_EMPTY = 'No alerts this time. That does not mean nothing happened: the model reads wording only, and a short or sampled check can miss things.'
CAVEAT_MEASURES = ('A language model rated each message and people have not checked the ratings, so treat these as prompts to look, not findings. They describe wording, never a person. '
                   'Friendly teasing can look heated to a model, and Rare, Some and Often are our own starting cut-offs, not norms.')
CAVEAT_BADGES = ('Badges describe the wording in this check only. They are not awards to people, not a ranking of teams, and not for performance reviews. A language model '
                 'rated the messages and people have not checked the ratings. A badge never cancels an alert. The cut-offs are our own starting choice.')
CAVEAT_SINCE = 'A change counts only when the two checks were made alike and the difference is bigger than chance would give. It describes wording, not people.'
FIRST_CHECK = 'This is your first check. Run another after your next batch of messages and this panel can show what moved, if both checks used the same settings.'
NO_BADGES = 'No badges this time. Each one is earned only when the messages clearly show it.'
STRENGTH_ONLY = 'A language model rated these messages and people have not checked the ratings. These describe wording, not people.'


def build(aid, a=None, vibe=None, cov=None) -> dict | None:
    """The measures, cards, alerts, notes, badges and the check-by-check trail for one analysis, or None when it has no ratings."""
    a = a or store.get_analysis(aid)
    cov = dict(cov or {})
    m = analyse(store.rated_units(aid), (a['vibe_sensitivity'] if a else None) or 'medium', incognito=bool(a and a['incognito']))
    if m is None:
        return None
    gen = store.evidence_generation(aid)
    history = [r for r in store.measure_history(aid) if r.get('gen') is not None]                  # a record with no evidence generation is not a check of its own
    saved = next((r for r in reversed(history) if r.get('gen') == gen), None)
    live = record(m, a, {'total': cov.get('total'), 'note': cov.get('sampled')}, gen, created_at=(saved or {}).get('created_at'))      # a check keeps the date it ran, not the day it is viewed
    live = store.clean_measure_snapshot(live) or live          # the same rules as a saved record, so two checks always compare like with like
    trail = trail_of(history, live)
    previous = trail[-2] if len(trail) >= 2 else None
    comparison = compare(live, previous)
    found, notes = alerts(m, vibe, comparison, cov)
    earned, badge_note = badges(m, vibe, cov)
    counts = {level: sum(1 for x in found if x['level'] == level) for level in LEVELS}
    if counts['high'] or any(x['key'] == 'serious' for x in found):
        for b in earned:
            if b['key'] == 'noflags' and b['earned']:
                b.update(earned=False, evidence='', needs='Held back while an alert about serious flags or a High priority alert stands.')
    ok_vibe = bool(vibe) and vibe.get('status') == 'ok'
    card_list, tile = cards(m), health_tile(m)
    read = vibe.get('analyzed_n', 0) if ok_vibe else 0
    covered = ok_vibe and read >= MIN_BADGE and (not cov.get('scope') or read >= 0.9 * cov['scope']) and not cov.get('vibe_stale')
    marked = any(c['tone'] in ('warn', 'bad') for c in card_list) or tile['tone'] == 'bad'
    # the all-clear line is a claim, so it needs every ground: enough messages, Vibe Check read nearly all of them and its newest run finished, no alert, no marked figure
    all_clear = bool(covered and m['n'] >= MIN_TIMELINE and not counts['high'] and not counts['medium'] and not is_silent(m) and not marked)
    return {'members': members_view(trail), 'm': m, 'cards': card_list, 'health': tile, 'alerts': found, 'alert_counts': counts, 'notes': notes, 'badges': earned, 'badge_note': badge_note,
            'badges_earned': sum(1 for b in earned if b['earned']), 'trail': trail, 'since': since_table(comparison, live), 'comparison': comparison, 'record': live,
            'basis_text': basis_text(m, cov, vibe), 'chart_key': chart_key(m), 'all_clear': all_clear,
            'alert_catalog': alert_catalog(), 'caveats': {'alerts': CAVEAT_ALERTS, 'empty': CAVEAT_EMPTY, 'measures': CAVEAT_MEASURES, 'badges': CAVEAT_BADGES, 'since': CAVEAT_SINCE},
            'texts': {'first_check': FIRST_CHECK, 'no_badges': NO_BADGES}}


MEMBERS_CHANGE = 1.25         # a group that grew or shrank by a quarter or more since the last count (up 25%, or down to 4 in 5)


def members_view(trail) -> dict | None:
    """The size of the group beside the checks: how many members this check was made with, how many the check before it named, and a caution when the group
    changed a lot. None when this check names no members. `trail` ends with the current check."""
    if not trail or not trail[-1].get('members'):
        return None
    now = trail[-1]['members']
    earlier = [r for r in trail[:-1] if r.get('members')]
    if not earlier:
        return {'now': now, 'before': None, 'text': f'Members at this check: {now:,}. Later checks show how the group changes.', 'note': ''}
    before = earlier[-1]['members']
    last = earlier[-1] is trail[-2]
    gap = now - before
    change = 'the same number' if gap == 0 else f'{abs(gap):,} {"more" if gap > 0 else "fewer"}'
    big = max(now, before) >= MEMBERS_CHANGE * min(now, before)
    return {'now': now, 'before': before, 'change': gap,
            'text': f'Members: {before:,} at {"your last check" if last else "an earlier check"}, {now:,} now ({change}).',
            'note': 'The group changed by a quarter or more, so part of any change may come from who is in it.' if big else ''}


def chart_key(m) -> str:
    """The sentence under the four small charts, for the page and the PDF: what the lines and the bands mean."""
    parts = ['Lines show messages per 100.', 'Shaded bands show where Rare, Some and Often begin.', 'A darker band is a heated moment.']
    if m['basis'] == 'order':
        parts.append('The numbers under each chart are rated message numbers, in the order you added them.')
    elif m.get('utc') and m.get('clock'):
        parts.append('Times are UTC.')
    return ' '.join(parts)


def basis_text(m, cov, vibe=None) -> str:
    """What the figures are built from, in one sentence: how many messages, which part of them, and how many Vibe Check read."""
    total = cov.get('total') or m['n']
    text = f'Built from the {m["n"]:,} message{"" if m["n"] == 1 else "s"} that {"was" if m["n"] == 1 else "were"} rated' + (f' (an even sample of {total:,})' if cov.get('sampled') else '') + '.'
    if vibe and vibe.get('status') == 'ok':
        text += f' Vibe Check read {vibe.get("analyzed_n", 0):,}.'
    return text
