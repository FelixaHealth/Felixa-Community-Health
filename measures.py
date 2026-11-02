"""Measures over time, alerts and badges, built from the 13 tone ratings of each message.

MEASURES.md has every rule in plain words. This module is those rules as code, and nothing else: every function takes numbers and gives numbers or
sentences back. The one database read is `store.rated_units`, which returns ratings and dates, never a message's text, so nothing here can see
what anyone wrote.

    m = analyse(units)                     # the measures, stretch by stretch
    found = alerts(m, vibe, previous)      # heads-ups, High to Good news
    earned = badges(m, metrics, vibe, previous)
    record = snapshot(m)                   # the numbers kept after each check (scores and counts only)
    build(aid, metrics, vibe)              # all of it for one report, with the check-by-check trail

Cut-offs are our own starting choices, written down here as named constants. None of them is a validated norm.
"""
from __future__ import annotations

import datetime as _dt
import logging

import analysis
import store
from vibe_check.common import clamp, js_round, long_date, parse_ts, round1, short_date
from vibe_check.forecast import TYPE_RECOMMENDATIONS

log = logging.getLogger('bei.measures')

# ---- the measures -------------------------------------------------------------------------------
KEYS = ('escalation', 'conflict', 'empathy', 'passive')            # the order they are shown in
NAMES = {'escalation': 'Escalation', 'conflict': 'Conflict', 'empathy': 'Empathy', 'passive': 'Passive-aggressive', 'health': 'Health score'}
MEANING = {
    'escalation': 'How fast tension climbs. It stays at zero while tension is flat or falling.',
    'conflict': 'How much of the talk is open hostility: aggressive, rude, disrespectful or judgmental wording.',
    'empathy': 'How much of the talk shows care and understanding.',
    'passive': 'Digs and sarcasm that stay polite on the surface, without open hostility.',
    'health': 'One number from all 13 tones. Kind tones lift it, tense and toxic tones pull it down, and harsher tones count for more.',
}
GOOD_WHEN = {'escalation': 'down', 'conflict': 'down', 'empathy': 'up', 'passive': 'down', 'health': 'up'}

OVERT = ('Aggressive', 'Rude', 'Disrespectful', 'Judgmental')        # open hostility
POSITIVE_WEIGHTS = {'Polite': 3, 'Empathetic': 3, 'Encouraging': 2, 'Funny': 1}
NEGATIVE_WEIGHTS = {'Ignorant': 1, 'Egocentric': 1, 'Neurotic': 1, 'Sarcastic': 2, 'Agitated': 2, 'Aggressive': 3, 'Judgmental': 3, 'Disrespectful': 3, 'Rude': 3}
HEALTH_SCALE = sum(POSITIVE_WEIGHTS.values())                        # 9: the weights of the Felixa backend, divided as it divides them
HEALTH_STATUS = ((80, 'Excellent'), (60, 'Good'), (40, 'Fair'), (0, 'Needs attention'))
HEALTH_TONE = {'Excellent': 'good', 'Good': 'good', 'Fair': 'info', 'Needs attention': 'bad'}

# ---- following the conversation -----------------------------------------------------------------
DATED_SHARE = 0.6              # share of rated messages that must have a readable date for the timeline to follow the clock
MIN_SERIES = 24                # rated messages needed for a timeline (three stretches of eight)
MIN_STRETCH = 8                # messages in the smallest stretch
MAX_STRETCHES = 24
EPISODE_MIN = 15               # escalation points that make a stretch part of an episode
DIRECTION_MIN, DIRECTION_REL = 3.0, 0.20      # a change of at least max(3 points, 20% of where it started) is rising or falling
HEALTH_DIRECTION_MIN = 5.0
BANDS = {                      # (cut-offs, words): below the first cut-off is the first word, and so on
    'conflict': ((8, 20), ('Low', 'Moderate', 'High')),
    'passive': ((5, 12), ('Low', 'Moderate', 'High')),
    'empathy': ((3, 10), ('Low', 'Moderate', 'High')),
    'escalation': ((10, 25, 40), ('None', 'Mild', 'Marked', 'Sharp')),
}

# ---- alerts and badges --------------------------------------------------------------------------
LEVELS = ('high', 'medium', 'low', 'good')
LEVEL_NAMES = {'high': 'High', 'medium': 'Medium', 'low': 'Low', 'good': 'Good news'}
LEVEL_TONE = {'high': 'bad', 'medium': 'warn', 'low': 'info', 'good': 'good'}
CHANGE_MIN = 5.0               # points on the previous check that count as a change
ESC_HIGH, ESC_MEDIUM = (40, 45), (25, 30)     # (escalation peak, tension reached)
CONFLICT_HIGH, CONFLICT_MEDIUM, CONFLICT_RISING = 20, 12, 8
PASSIVE_HIGH, PASSIVE_MEDIUM, PASSIVE_RISING = 25, 12, 5
EMPATHY_FADING_FROM = 5
SPAM_SHARE, PROFANITY_SHARE, VIBE_MIN_READ = 5, 10, 20
UNDATED_SHARE = 40
MIN_BADGE_MESSAGES, MIN_CLEAN_MESSAGES = 20, 30
BADGE_POLITE_SHARE, BADGE_EMPATHY, BADGE_ENCOURAGING, BADGE_FUNNY_SHARE, BADGE_CALM_CONFLICT, BADGE_CALM_STRETCH = 25, 8, 8, 15, 8, 15
BADGE_COOL_PEAK, BADGE_COOL_BACK, BADGE_COOL_WITHIN = 25, 10, 3
SEVERE_KINDS = ('threats', 'hate_speech', 'harassment', 'bullying')


# ---- helpers ------------------------------------------------------------------------------------
def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def whole(x) -> int:
    """A figure as it is printed: whole numbers, halves up. Every cut-off is tested on this, so a printed 8 is never called Low."""
    return js_round(x)


def _rating(value) -> int:
    try:
        return int(clamp(int(value), 0, 3))
    except (TypeError, ValueError):
        return 0


def _band(value, key) -> str:
    cuts, words = BANDS[key]
    shown = whole(value)
    for cut, word in zip(cuts, words):
        if shown < cut:
            return word
    return words[-1]


def health_status(score) -> str:
    shown = whole(score)
    return next(word for floor, word in HEALTH_STATUS if shown >= floor)


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')


def points(x) -> str:
    return str(whole(x))


# ---- one message --------------------------------------------------------------------------------
def message_values(ratings) -> dict:
    """What one message contributes. `ratings` maps each of the 13 tones to 0..3. Everything returned runs 0 to 1 (the weights run higher)."""
    r = {a: _rating((ratings or {}).get(a)) for a in analysis.ATTRIBUTES}
    x = {a: v / 3 for a, v in r.items()}
    overt_rating = max(r[a] for a in OVERT)
    overt = overt_rating / 3
    return {
        'conflict': overt,
        'passive': x['Sarcastic'] * (1 - overt),           # sarcasm counts as a dig only to the degree it is not open hostility
        'empathy': x['Empathetic'],
        'tension': max(overt, x['Agitated'], x['Sarcastic']),
        'positive': sum(w * x[a] for a, w in POSITIVE_WEIGHTS.items()),
        'negative': sum(w * x[a] for a, w in NEGATIVE_WEIGHTS.items()),
        'hostile': 1.0 if overt_rating >= 2 else 0.0,
    }


def health_of(mean_positive, mean_negative) -> float:
    return clamp(50 + 50 * (mean_positive - mean_negative) / HEALTH_SCALE, 0.0, 100.0)


# ---- order and stretches ------------------------------------------------------------------------
def order_units(units):
    """(ordered units, their times, basis, undated). Time order when most of them have a readable date (those without one are left off), else
    the order they were added in. The times are UTC datetimes, or None throughout when the order is the order added."""
    stamps = [parse_ts(u.get('timestamp')) for u in units]
    dated = [i for i, s in enumerate(stamps) if s is not None]
    if units and len(dated) >= DATED_SHARE * len(units):
        keep = sorted(dated, key=lambda i: (stamps[i], i))
        return [units[i] for i in keep], [stamps[i] for i in keep], 'time', len(units) - len(dated)
    return list(units), [None] * len(units), 'order', 0


def cut(n) -> list:
    """The (start, end) positions of the stretches for n ordered messages: equal sizes, at least MIN_STRETCH each, none when n is too small."""
    if n < MIN_SERIES:
        return []
    k = min(MAX_STRETCHES, n // MIN_STRETCH)
    edges = [i * n // k for i in range(k + 1)]
    return list(zip(edges[:-1], edges[1:]))


def _unique(labels) -> list:
    seen, out = {}, []
    for text in labels:
        seen[text] = seen.get(text, 0) + 1
        out.append(text if seen[text] == 1 else f'{text} ({seen[text]})')
    return out


def stretch_labels(spans, stamps, basis) -> list:
    """[(short label for the axis, long label for the tooltip and table)] for each stretch."""
    if basis == 'order' or not spans:
        return [(f'#{a + 1}', f'Messages {a + 1} to {b}') for a, b in spans]
    firsts, lasts = [stamps[a] for a, _ in spans], [stamps[b - 1] for _, b in spans]
    timed = any(s.time() != _dt.time.min for s in firsts + lasts)
    one_day = firsts[0].date() == lasts[-1].date()
    days = {s.date() for s in firsts}

    def short(s):
        if one_day:
            return f'{s:%H:%M}'
        return short_date(s.isoformat()) if (not timed or len(days) >= 0.7 * len(firsts)) else f'{short_date(s.isoformat())} {s:%H:%M}'

    def long(a, b):
        if not timed:
            return long_date(a.isoformat()) if a.date() == b.date() else f'{long_date(a.isoformat())} to {long_date(b.isoformat())}'
        if a.date() == b.date():
            return f'{long_date(a.isoformat())} {a:%H:%M} to {b:%H:%M} UTC'
        return f'{long_date(a.isoformat())} {a:%H:%M} to {long_date(b.isoformat())} {b:%H:%M} UTC'
    return list(zip(_unique(short(s) for s in firsts), _unique(long(a, b) for a, b in zip(firsts, lasts))))


def direction_of(values, minimum=DIRECTION_MIN, relative=DIRECTION_REL) -> dict:
    """The first third of the stretches against the last third (at least one each): rising, falling or steady."""
    g = max(1, len(values) // 3)
    first, last = _mean(values[:g]), _mean(values[-g:])
    delta = last - first
    need = max(minimum, relative * first)
    return {'first': first, 'last': last, 'delta': delta, 'direction': 'rising' if delta >= need else 'falling' if delta <= -need else 'steady'}


def escalation_series(tension) -> list:
    """The climb of tension above the recent low: 0 at the start, then max(0, T[i] - the lower of the two stretches before it)."""
    out = []
    for i, t in enumerate(tension):
        if i == 0:
            out.append(0.0)
        else:
            low = min(tension[max(0, i - 2):i])
            out.append(max(0.0, t - low))
    return out


def episodes_of(escalation, tension, labels) -> list:
    """Runs of consecutive stretches whose escalation is at least EPISODE_MIN, with the recent low they climbed from and the peak they reached."""
    out, i, n = [], 0, len(escalation)
    while i < n:
        if escalation[i] < EPISODE_MIN:
            i += 1
            continue
        j = i
        while j + 1 < n and escalation[j + 1] >= EPISODE_MIN:
            j += 1
        low = min(tension[max(0, i - 2):i]) if i else tension[0]
        out.append({'first': i, 'last': j, 'peak': round1(max(escalation[i:j + 1])), 'baseline': round1(low), 'tension': round1(max(tension[i:j + 1])),
                    'short': labels[i][0], 'long': labels[i][1] if i == j else f'{labels[i][1]} onwards'})
        i = j + 1
    return out


# ---- the analysis -------------------------------------------------------------------------------
def analyse(units) -> dict | None:
    """The measures for a list of rated units [{'id', 'timestamp', 'ratings'}]. None when no unit has all 13 ratings. JSON-friendly."""
    units = [u for u in units if all(a in (u.get('ratings') or {}) for a in analysis.ATTRIBUTES)]
    if not units:
        return None
    values = {u['id']: message_values(u['ratings']) for u in units}
    all_vals = [values[u['id']] for u in units]
    n = len(units)
    ordered, stamps, basis, undated = order_units(units)
    spans = cut(len(ordered))
    labels = stretch_labels(spans, stamps, basis)

    def mean_of(group, key):
        return _mean(v[key] for v in group)

    def health_of_group(group):
        return health_of(mean_of(group, 'positive'), mean_of(group, 'negative'))

    stretches = []
    for i, (a, b) in enumerate(spans):
        group = [values[u['id']] for u in ordered[a:b]]
        stretches.append({'i': i, 'n': len(group), 'short': labels[i][0], 'long': labels[i][1],
                          'start': stamps[a].isoformat() if basis == 'time' else None, 'end': stamps[b - 1].isoformat() if basis == 'time' else None,
                          'empathy': round1(100 * mean_of(group, 'empathy')), 'conflict': round1(100 * mean_of(group, 'conflict')),
                          'passive': round1(100 * mean_of(group, 'passive')), 'tension': round1(100 * mean_of(group, 'tension')),
                          'hostile': round1(100 * mean_of(group, 'hostile')), 'health': round1(health_of_group(group))})
    esc = escalation_series([s['tension'] for s in stretches])
    for s, e in zip(stretches, esc):
        s['escalation'] = round1(e)
    episodes = episodes_of([s['escalation'] for s in stretches], [s['tension'] for s in stretches], labels)

    def peak_label(key):
        top = max(stretches, key=lambda s: s[key])
        return top['long']

    means = {k: 100 * mean_of(all_vals, k) for k in ('conflict', 'empathy', 'passive', 'tension', 'hostile')}
    measures = {}
    for key in ('conflict', 'passive', 'empathy'):
        entry = {'key': key, 'name': NAMES[key], 'meaning': MEANING[key], 'good': GOOD_WHEN[key], 'mean': round1(means[key]), 'band': _band(means[key], key)}
        if stretches:
            d = direction_of([s[key] for s in stretches])
            entry.update({'first': round1(d['first']), 'latest': round1(d['last']), 'delta': round1(d['delta']), 'direction': d['direction'],
                          'peak': max(s[key] for s in stretches), 'peak_label': peak_label(key)})
        else:
            entry.update({'first': None, 'latest': round1(means[key]), 'delta': None, 'direction': None, 'peak': None, 'peak_label': ''})
        entry['band_now'] = _band(entry['latest'], key)
        entry['tone'] = _tone(key, entry['band_now'])
        measures[key] = entry
    peak_e = max(esc) if esc else 0.0
    measures['escalation'] = {
        'key': 'escalation', 'name': NAMES['escalation'], 'meaning': MEANING['escalation'], 'good': 'down',
        'peak': round1(peak_e), 'peak_label': peak_label('escalation') if stretches and peak_e > 0 else '', 'episodes': len(episodes),
        'tension_peak': round1(max((s['tension'] for s in stretches), default=100 * means['tension'])), 'tension_mean': round1(means['tension']),
        'band': _band(peak_e, 'escalation') if stretches else None}
    measures['escalation']['tone'] = _tone('escalation', measures['escalation']['band'])

    score = health_of_group(all_vals)
    health = {'key': 'health', 'name': NAMES['health'], 'meaning': MEANING['health'], 'good': 'up', 'score': round1(score), 'status': health_status(score)}
    health['tone'] = HEALTH_TONE[health['status']]
    if stretches:
        d = direction_of([s['health'] for s in stretches], HEALTH_DIRECTION_MIN, 0.0)
        health.update({'first': round1(d['first']), 'last': round1(d['last']), 'delta': round1(d['delta']), 'direction': d['direction']})
    else:
        health.update({'first': None, 'last': None, 'delta': None, 'direction': None})

    return {'n': n, 'basis': basis, 'undated': undated, 'sig': f'{n}:{sum(sum(_rating(v) for v in u["ratings"].values()) for u in units)}',
            'has_series': bool(stretches), 'stretches': stretches, 'episodes': episodes, 'measures': measures, 'health': health,
            'hostile_share': round1(means['hostile'])}


def _tone(key, band) -> str:
    """The icon tone for a measure: the level word decides it, and which way is good."""
    if band is None:
        return 'info'
    if key == 'empathy':
        return 'good' if band == 'High' else 'info'
    if key == 'escalation':
        return {'None': 'good', 'Mild': 'info', 'Marked': 'warn', 'Sharp': 'bad'}[band]
    return {'Low': 'good', 'Moderate': 'warn', 'High': 'bad'}[band]


# ---- numbers kept after each check --------------------------------------------------------------
def snapshot(m, created_at=None) -> dict:
    """The record kept after a check: scores and counts. No message, no name, no file name."""
    ms = m['measures']
    return {'created_at': created_at or utc_now(), 'n': m['n'], 'sig': m['sig'], 'basis': m['basis'], 'stretches': len(m['stretches']),
            'health': m['health']['score'], 'empathy': ms['empathy']['mean'], 'conflict': ms['conflict']['mean'], 'passive': ms['passive']['mean'],
            'escalation_peak': ms['escalation']['peak'], 'episodes': ms['escalation']['episodes']}


def save_snapshot(aid):
    """Computes the measures of an analysis and keeps their numbers. Never raises: a check does not fail for this. Returns the record or None."""
    try:
        m = analyse(store.rated_units(aid))
        if m is None:
            return None
        record = snapshot(m)
        store.save_measure_snapshot(aid, record)
        return record
    except Exception:
        log.exception('could not save the measures (analysis %s)', aid)
        return None


def trail_of(history, live) -> list:
    """The check-by-check records ending with the current check: neighbours with identical ratings count once, and `live` is added when the
    last saved record is not the current one (a check whose record was never saved)."""
    out = []
    for rec in history:
        if not isinstance(rec, dict):
            continue
        if out and rec.get('sig') and rec.get('sig') == out[-1].get('sig'):
            out[-1] = rec
        else:
            out.append(rec)
    if not out or out[-1].get('sig') != live['sig']:
        out.append(live)
    return out


# ---- alerts -------------------------------------------------------------------------------------
def _alert(level, key, title, detail, when='', step='') -> dict:
    return {'level': level, 'key': key, 'title': title, 'detail': detail, 'when': when, 'try': step}


def _plural(n, one, many=None) -> str:
    return f'{n:,} {one}' if n == 1 else f'{n:,} {many or one + "s"}'


def _tox(vibe, kind) -> int:
    try:
        return int(((vibe or {}).get('toxicity_types') or {}).get(kind) or 0)
    except (TypeError, ValueError):
        return 0


def alerts(m, vibe=None, previous=None) -> list:
    """The heads-ups for a check, High first and Good news last. `vibe` is the Vibe Check result (or None), `previous` the record of the check before."""
    ms, found = m['measures'], []
    ok = bool(vibe) and vibe.get('status') == 'ok'
    now = {k: ms[k]['latest'] for k in ('conflict', 'passive', 'empathy')}

    threats, hate = _tox(vibe, 'threats') if ok else 0, _tox(vibe, 'hate_speech') if ok else 0
    if threats + hate:
        parts = [x for x in (_plural(threats, 'message') + ' as threats' if threats else '', _plural(hate, 'message') + ' as hate speech' if hate else '') if x]
        step = ' '.join(TYPE_RECOMMENDATIONS[k] for k, c in (('threats', threats), ('hate_speech', hate)) if c)
        found.append(_alert('high', 'severe', 'Threats or hate speech were flagged',
                            f'Vibe Check flagged {" and ".join(parts)}. They are quoted under Flagged messages in the Vibe Check section.', '',
                            'Read the quoted messages first, then reach out to the people involved with care. ' + step))

    if m['episodes']:
        top = max(m['episodes'], key=lambda e: (e['peak'], e['tension']))
        high = whole(top['peak']) >= ESC_HIGH[0] and whole(top['tension']) >= ESC_HIGH[1]
        medium = whole(top['peak']) >= ESC_MEDIUM[0] and whole(top['tension']) >= ESC_MEDIUM[1]
        if high or medium:
            more = f' {len(m["episodes"]) - 1} more stretch{"es" if len(m["episodes"]) > 2 else ""} of rising tension followed.' if len(m['episodes']) > 1 else ''
            found.append(_alert('high' if high else 'medium', 'escalation', 'Tension climbed quickly',
                                f'Tension rose from {points(top["baseline"])} to {points(top["tension"])} out of 100 compared with the stretch before it '
                                f'(escalation peaked at {points(top["peak"])}).{more}', top['long'],
                                'Look at what was said just before then. A pinned reminder of the shared goal, a thank-you to the members who calmed things down '
                                'and a lighter activity afterwards help the group reset.'))

    c = now['conflict']
    if whole(c) >= CONFLICT_HIGH or whole(c) >= CONFLICT_MEDIUM or (ms['conflict']['direction'] == 'rising' and whole(c) >= CONFLICT_RISING):
        level = 'high' if whole(c) >= CONFLICT_HIGH else 'medium'
        title = ('Conflict is high' if level == 'high' else 'Conflict is noticeable' if whole(c) >= CONFLICT_MEDIUM else 'Conflict is rising')
        span = 'in the latest stretch' if m['has_series'] else 'across the messages'
        found.append(_alert(level, 'conflict', f'{title} {span}',
                            f'Conflict stands at {points(c)} out of 100 {span}; about {points(_hostile_latest(m))}% of the messages carry clear hostility.'
                            + (f' It moved from {points(ms["conflict"]["first"])} at the start.' if m['has_series'] and ms['conflict']['direction'] == 'rising' else ''),
                            '', 'Spotlight a respectful exchange this week and invite members to say what they appreciate about each other.'))

    p = now['passive']
    if whole(p) >= PASSIVE_MEDIUM or (ms['passive']['direction'] == 'rising' and whole(p) >= PASSIVE_RISING):
        found.append(_alert('high' if whole(p) >= PASSIVE_HIGH else 'medium', 'passive',
                            'Passive-aggressive tone is ' + ('common' if whole(p) >= PASSIVE_MEDIUM else 'growing'),
                            f'Sarcasm and digs without open hostility stand at {points(p)} out of 100 {"in the latest stretch" if m["has_series"] else "across the messages"}.',
                            '', 'Make direct, kind feedback easy. A short pinned note such as "say it straight, say it kindly" helps, and so does answering an '
                                'indirect dig with a warm, direct question.'))

    prev = previous or {}
    e_first, e_last = ms['empathy']['first'], ms['empathy']['latest']
    fading_in = ms['empathy']['direction'] == 'falling' and (e_first or 0) >= EMPATHY_FADING_FROM
    fading_since = prev.get('empathy') is not None and prev['empathy'] >= EMPATHY_FADING_FROM and prev['empathy'] - ms['empathy']['mean'] >= CHANGE_MIN
    if fading_in or fading_since:
        detail = (f'Empathy went from {points(e_first)} to {points(e_last)} out of 100 during this check.' if fading_in
                  else f'Empathy is {points(ms["empathy"]["mean"])} out of 100, down from {points(prev["empathy"])} at the last check.')
        found.append(_alert('medium', 'empathy_down', 'Empathy is fading', detail, '',
                            'Recognise the members who check in on others. A weekly thanks-and-wins thread makes caring visible.'))

    score = m['health']['score']
    if m['health']['status'] == 'Needs attention':
        found.append(_alert('high', 'health_low', 'The health score needs attention', f'The health score is {points(score)} out of 100.', '',
                            'Start small: pick one shared goal for the next conversation and spotlight the members who set a warm tone.'))
    if prev.get('health') is not None and prev['health'] - score >= CHANGE_MIN:
        found.append(_alert('medium', 'health_drop', 'The health score fell',
                            f'It is {points(score)} out of 100, down from {points(prev["health"])} at the last check.', '',
                            'Look at which tones changed most in the signal profile, then celebrate the behaviour you want to see more of.'))

    if ok and (vibe.get('analyzed_n') or 0) >= VIBE_MIN_READ:
        read = vibe['analyzed_n']
        for kind, limit, title, name in (('spam', SPAM_SHARE, 'Spam is crowding the chat', 'as spam'), ('profanity', PROFANITY_SHARE, 'Strong language is frequent', 'for profanity')):
            share = 100 * _tox(vibe, kind) / read
            if whole(share) >= limit:
                found.append(_alert('low', kind, title, f'{_plural(_tox(vibe, kind), "message")} ({points(share)}% of the {read:,} read) were flagged {name}.', '',
                                    TYPE_RECOMMENDATIONS[kind]))

    if not m['has_series']:
        found.append(_alert('low', 'no_timeline', 'Not enough messages for a timeline',
                            f'{_plural(m["n"], "message")} {"was" if m["n"] == 1 else "were"} rated, and a timeline needs at least {MIN_SERIES}.', '',
                            'Add more messages to see how the measures move.'))
    elif m['basis'] == 'order' or whole(100 * m['undated'] / max(1, m['n'])) >= UNDATED_SHARE:
        missing = m['n'] - sum(s['n'] for s in m['stretches']) if m['basis'] == 'time' else None
        found.append(_alert('low', 'undated', 'Many messages have no date',
                            'The measures follow the order the messages were added in, not the clock.' if m['basis'] == 'order'
                            else f'{_plural(m["undated"], "message")} had no readable date and {"is" if m["undated"] == 1 else "are"} left off the timeline.', '',
                            'Upload the export with its dates to see when things changed.'))

    if ms['empathy']['direction'] == 'rising' or (prev.get('empathy') is not None and ms['empathy']['mean'] - prev['empathy'] >= CHANGE_MIN):
        found.append(_alert('good', 'empathy_up', 'Empathy is growing', 'Members are showing more care and understanding than before.', '',
                            'Thank the members who set that tone, so it keeps spreading.'))
    if ms['conflict']['direction'] == 'falling' or (prev.get('conflict') is not None and prev['conflict'] - ms['conflict']['mean'] >= CHANGE_MIN):
        found.append(_alert('good', 'conflict_down', 'Conflict is easing', 'Open hostility is lower than before.', '',
                            'Name what changed in the community, so the group can repeat it.'))
    if prev.get('health') is not None and score - prev['health'] >= CHANGE_MIN:
        found.append(_alert('good', 'health_up', 'The health score rose', f'It is {points(score)} out of 100, up from {points(prev["health"])} at the last check.', '',
                            'Share the improvement with the community. What gets celebrated gets repeated.'))
    order = {name: i for i, name in enumerate(LEVELS)}
    return sorted(found, key=lambda a: order[a['level']])


def _hostile_latest(m) -> float:
    if m['has_series']:
        g = max(1, len(m['stretches']) // 3)
        return _mean(s['hostile'] for s in m['stretches'][-g:])
    return m['hostile_share']


# ---- badges -------------------------------------------------------------------------------------
BADGES = (
    ('welcoming', 'Welcoming', 'Members are courteous to each other.', 'Polite wording in at least 25% of the messages, with Conflict under 8.'),
    ('supportive', 'Supportive', 'Members show care and understanding.', 'Empathy of at least 8 out of 100.'),
    ('encouraging', 'Encouraging', 'Members cheer each other on.', 'Encouraging wording of at least 8 out of 100.'),
    ('lighthearted', 'Light-hearted', 'Humour is part of the mix.', 'Funny wording in at least 15% of the messages.'),
    ('calm', 'Calm waters', 'Tension stays low from start to finish.', 'A timeline, Conflict under 8, no stretch with Conflict at 15 or more, and no rise in tension.'),
    ('cool', 'Quick to cool', 'Tension rose, then settled quickly.', 'A rise in tension followed by a return to within 10 points of where it started inside three stretches.'),
    ('clean', 'Clean chat', 'No serious toxicity was flagged.', 'Vibe Check read at least 30 messages and flagged no threats, hate speech, harassment or bullying.'),
    ('rising', 'On the rise', 'The health score is climbing.', 'The health score is up 5 points or more on the last check, or rising across this check.'),
)


def badges(m, metrics=None, vibe=None, previous=None) -> list:
    """All eight badges, earned or not. Each is {'key','name','what','how','earned','evidence','needs'}; `needs` says why a badge cannot be judged yet."""
    by_signal = {x['attribute']: x for x in (metrics or [])}
    ms, prev = m['measures'], previous or {}
    enough = m['n'] >= MIN_BADGE_MESSAGES
    ok = bool(vibe) and vibe.get('status') == 'ok'
    polite = by_signal.get('Polite', {}).get('detection_rate')
    funny = by_signal.get('Funny', {}).get('detection_rate')
    encouraging = by_signal.get('Encouraging', {}).get('fas')

    def verdict(key, earned, evidence, needs=''):
        meta = next(b for b in BADGES if b[0] == key)
        return {'key': key, 'name': meta[1], 'what': meta[2], 'how': meta[3], 'earned': bool(earned and not needs), 'evidence': evidence if earned and not needs else '', 'needs': needs}
    short = f'Needs at least {MIN_BADGE_MESSAGES} rated messages.' if not enough else ''
    out = [
        verdict('welcoming', polite is not None and whole(polite) >= BADGE_POLITE_SHARE and whole(ms['conflict']['mean']) < BADGE_CALM_CONFLICT,
                f'{points(polite or 0)}% of messages polite, Conflict {points(ms["conflict"]["mean"])}.', short),
        verdict('supportive', whole(ms['empathy']['mean']) >= BADGE_EMPATHY, f'Empathy {points(ms["empathy"]["mean"])} out of 100.', short),
        verdict('encouraging', encouraging is not None and whole(encouraging) >= BADGE_ENCOURAGING, f'Encouraging wording {points(encouraging or 0)} out of 100.', short),
        verdict('lighthearted', funny is not None and whole(funny) >= BADGE_FUNNY_SHARE, f'{points(funny or 0)}% of messages funny.', short),
    ]
    calm_needs = short or ('' if m['has_series'] else f'Needs a timeline of at least {MIN_SERIES} messages.')
    worst = max((s['conflict'] for s in m['stretches']), default=0)
    out.append(verdict('calm', m['has_series'] and whole(ms['conflict']['mean']) < BADGE_CALM_CONFLICT and whole(worst) < BADGE_CALM_STRETCH and not m['episodes'],
                       f'Conflict {points(ms["conflict"]["mean"])} overall and {points(worst)} at its highest.', calm_needs))
    cooled = _cooled(m)
    out.append(verdict('cool', cooled is not None, f'Tension was back near {points(cooled["baseline"])} within {BADGE_COOL_WITHIN} stretches of its peak.' if cooled else '', calm_needs))
    severe = sum(_tox(vibe, k) for k in SEVERE_KINDS) if ok else 0
    clean_needs = '' if ok and vibe.get('analyzed_n', 0) >= MIN_CLEAN_MESSAGES else f'Needs a Vibe Check on at least {MIN_CLEAN_MESSAGES} messages.'
    out.append(verdict('clean', ok and severe == 0, f'{vibe.get("analyzed_n", 0):,} messages read, none flagged for threats, hate speech, harassment or bullying.' if ok else '', clean_needs))
    delta = m['health']['score'] - prev['health'] if prev.get('health') is not None else None
    rising = (delta is not None and delta >= CHANGE_MIN) or m['health']['direction'] == 'rising'
    out.append(verdict('rising', rising, (f'Health score up {points(delta)} points on the last check.' if delta is not None and delta >= CHANGE_MIN
                                           else f'Health score rose from {points(m["health"]["first"])} to {points(m["health"]["last"])} during this check.') if rising else '', short))
    return out


def _cooled(m):
    """The first episode (peak at least BADGE_COOL_PEAK) after which tension is back within BADGE_COOL_BACK points of its baseline inside
    BADGE_COOL_WITHIN stretches, or None."""
    tension = [s['tension'] for s in m['stretches']]
    for ep in m['episodes']:
        if whole(ep['peak']) < BADGE_COOL_PEAK:
            continue
        after = tension[ep['last'] + 1:ep['last'] + 1 + BADGE_COOL_WITHIN]
        if after and min(after) <= ep['baseline'] + BADGE_COOL_BACK:
            return ep
    return None


# ---- everything for one report ------------------------------------------------------------------
def build(aid, metrics, vibe) -> dict | None:
    """The measures, alerts, badges and check-by-check trail for one analysis, or None when it has no ratings."""
    m = analyse(store.rated_units(aid))
    if m is None:
        return None
    trail = trail_of(store.measure_history(aid), snapshot(m))
    previous = trail[-2] if len(trail) >= 2 else None
    found, earned = alerts(m, vibe, previous), badges(m, metrics, vibe, previous)
    counts = {level: sum(1 for a in found if a['level'] == level) for level in LEVELS}
    return {'m': m, 'alerts': found, 'alert_counts': counts, 'badges': earned, 'badges_earned': sum(1 for b in earned if b['earned']),
            'trail': trail, 'previous': previous}
