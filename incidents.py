"""Escalation by incident: from the first heated message to the moment it leads to conflict, with the messages and a bar chart at a resolution of the reader's choice.

An incident is a run of heated messages (hostile, agitated or sarcastic digs) that keep following each other: the next heated message comes within `gap` minutes of the last
one (or within a few messages when the chat has no usable times). It needs two heated messages, or one openly hostile message. Its first heated message is where escalation
starts (the yellow flag). Its first openly hostile message is where it turns into conflict (the red flag). Two messages before and three after are kept for context.

It reads only the ratings the app already keeps: no new rating is made. A flag describes wording, never a person."""
import datetime as _dt

import analysis
import measures

GAPS = (15, 30, 60, 120)                     # minutes of quiet that end an incident
STEPS = (0, 1, 5, 10, 30, 60)                # bar width in minutes; 0 = one bar for each message
DEFAULT_GAP, DEFAULT_STEP = 30, 5
QUIET_MESSAGES = 5                           # without times: this many messages without heat end an incident
BEFORE, AFTER = 2, 3
MAX_BARS = 150
HOSTILE_TONES = tuple(sorted(measures.OVERT))
CALMING_TONES = ('Empathetic', 'Polite', 'Encouraging')     # a message that is not heated and shows one of these helps the mood settle


def _clock(stamp, with_date=False):
    if stamp is None:
        return ''
    return stamp.strftime('%b %-d, %H:%M') if with_date else stamp.strftime('%H:%M')


def _tags(ratings):
    r = {a: int(ratings.get(a) or 0) for a in analysis.ATTRIBUTES}
    out = [a for a in HOSTILE_TONES if r.get(a, 0) >= measures.FLAG_MIN]
    if r.get('Agitated', 0) >= measures.FLAG_MIN:
        out.append('Agitated')
    if r.get('Sarcastic', 0) >= measures.FLAG_MIN:
        out.append('Sarcastic')
    return out


def messages(units):
    """The rated messages in time order: [{'i', 'text', 'when', 'heated', 'hostile', 'tags'}], and whether the chat has usable times."""
    complete = [u for u in (units or []) if all(a in (u.get('ratings') or {}) and u['ratings'][a] is not None for a in analysis.ATTRIBUTES)]
    ordered, stamps, basis, _ = measures.order_units(complete)
    out = []
    for i, (u, s) in enumerate(zip(ordered, stamps)):
        f = measures.message_flags(u['ratings'])
        calming = (not f['heated']) and any(int(u['ratings'].get(a) or 0) >= measures.FLAG_MIN for a in CALMING_TONES)
        out.append({'i': i, 'uid': u.get('id'), 'text': ' '.join(str(u.get('text') or '').split()), 'when': s, 'heated': bool(f['heated']), 'hostile': bool(f['hostile']),
                    'calming': calming, 'calm_tags': [a for a in CALMING_TONES if int(u['ratings'].get(a) or 0) >= measures.FLAG_MIN], 'tags': _tags(u['ratings']), 'source': u.get('source') or ''})
    return out, basis == 'time'


def find(units, gap=DEFAULT_GAP):
    """Every incident of the chat, oldest first: [{'n', 'rows', 'start', 'turn', ...}]. `rows` are the messages of the incident with a role for each."""
    msgs, timed = messages(units)
    gap = gap if gap in GAPS else DEFAULT_GAP
    hot = [m['i'] for m in msgs if m['heated']]
    runs, cur = [], []
    for i in hot:
        if cur:
            last = msgs[cur[-1]]
            near = ((msgs[i]['when'] - last['when']).total_seconds() / 60.0 <= gap) if timed else (i - cur[-1] <= QUIET_MESSAGES)
            if not near:
                runs.append(cur)
                cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        a, b = run[0], run[-1]
        inside = msgs[a:b + 1]
        hostile = [m for m in inside if m['hostile']]
        if len(run) < 2 and not hostile:
            continue
        calm = _traction(msgs, a, b, timed, gap)
        lo, hi = max(0, a - BEFORE), min(len(msgs), b + 1 + AFTER)
        if timed:                                   # context from another day or hours away would only stretch the chart: keep what is within one quiet gap
            while lo < a and (msgs[a]['when'] - msgs[lo]['when']).total_seconds() / 60.0 > gap:
                lo += 1
            while hi > b + 1 and (msgs[hi - 1]['when'] - msgs[b]['when']).total_seconds() / 60.0 > gap:
                hi -= 1
        rows = []
        for m in msgs[lo:hi]:
            role = ''
            if m['i'] == a:
                role = 'start'
            if hostile and m['i'] == hostile[0]['i']:
                role = 'turn' if m['i'] != a else 'start-turn'
            rows.append(dict(m, role=role, inside=a <= m['i'] <= b, calm_mark=bool(calm and m['i'] == calm['i'])))
        start, turn = msgs[a], (hostile[0] if hostile else None)
        minutes_to = round((turn['when'] - start['when']).total_seconds() / 60.0) if (turn and timed) else None
        length = round((msgs[b]['when'] - start['when']).total_seconds() / 60.0) if timed else None
        out.append({'n': len(out) + 1, 'calm': calm, 'rows': rows, 'timed': timed, 'start': start, 'turn': turn, 'end': msgs[b], 'level': 'red' if turn else 'yellow',
                    'heated': len(run), 'hostile': len(hostile), 'messages': b - a + 1, 'minutes_to_conflict': minutes_to, 'minutes': length,
                    'messages_to_conflict': (turn['i'] - a) if turn else None,
                    'when': _clock(start['when'], True) if timed else f'Messages {a + 1} to {b + 1}',
                    'range': (f'{_clock(start["when"], True)} to {_clock(msgs[b]["when"])}' if timed else f'Messages {a + 1} to {b + 1}')})
    return out


def _quote(m, limit=160):
    text = m['text']
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _traction(msgs, a, b, timed, gap):
    """The first calming message of an incident and what happened to the heat after it, or None when nobody tried to calm things.
    Looked for from the second message of the incident until the end of it, and among the messages that follow within one quiet gap (or three messages without times).
    held = no heated message came after it."""
    limit = b
    for j in range(b + 1, len(msgs)):
        near = ((msgs[j]['when'] - msgs[b]['when']).total_seconds() / 60.0 <= gap) if timed else (j - b <= 3)
        if not near:
            break
        limit = j
    c = next((j for j in range(a + 1, limit + 1) if msgs[j]['calming']), None)
    if c is None:
        return None
    after = msgs[c + 1:b + 1]
    heated_after = sum(1 for m in after if m['heated'])
    hostile_after = sum(1 for m in after if m['hostile'])
    before = [m for m in msgs[a:c] if m['heated']]
    follow = [_quote(m) for m in msgs[c + 1:c + 3]]
    minutes = None
    if timed:
        minutes = max(0, round((msgs[b]['when'] - msgs[c]['when']).total_seconds() / 60.0))
    return {'i': c, 'text': _quote(msgs[c]), 'when': msgs[c]['when'], 'tags': msgs[c].get('calm_tags', []),
            'heated_before': len(before), 'heated_after': heated_after, 'hostile_after': hostile_after, 'held': heated_after == 0,
            'last_heat': _quote(before[-1]) if before else '', 'follow': follow, 'minutes_after': minutes, 'before_conflict': c > a and (not any(m['hostile'] for m in msgs[a:c]))}


def bins(incident, step, flagged=None, beh=None):
    """The bars of the incident chart: [{'label', 'n', 'calm', 'heated', 'hostile'}]. step 0 = a bar for each message; otherwise a bar for each `step` minutes.
    A chat without times only has the message-by-message view."""
    rows = incident['rows']
    beh = beh or {}
    if not step or not incident['timed']:
        return [{'label': _clock(r['when']) if incident['timed'] else str(r['i'] + 1), 'n': 1, 'calm': int(not r['heated']), 'heated': int(r['heated'] and not r['hostile']),
                 'hostile': int(r['hostile']), 'black': int(r.get('uid') in (flagged or ())), 'text': r['text'], 'beh': list(beh.get(r.get('uid'), [])),
                 'start': r.get('role') in ('start', 'start-turn'), 'turn': r.get('role') in ('turn', 'start-turn'), 'calm_mark': bool(r.get('calm_mark'))} for r in rows]
    first = rows[0]['when'].replace(second=0, microsecond=0)
    base = first - _dt.timedelta(minutes=first.minute % step if step < 60 else first.minute, hours=0)
    last = rows[-1]['when']
    count = int((last - base).total_seconds() // (step * 60)) + 1
    count = min(count, MAX_BARS)
    out = [{'label': _clock(base + _dt.timedelta(minutes=step * k)), 'n': 0, 'calm': 0, 'heated': 0, 'hostile': 0, 'black': 0, 'text': '', 'beh': [], 'start': False, 'turn': False, 'calm_mark': False} for k in range(count)]
    for r in rows:
        k = min(count - 1, int((r['when'] - base).total_seconds() // (step * 60)))
        b = out[k]
        b['n'] += 1
        b['beh'] += list(beh.get(r.get('uid'), []))
        if r.get('role') in ('start', 'start-turn'):
            b['start'] = True
        if r.get('role') in ('turn', 'start-turn'):
            b['turn'] = True
        if r.get('calm_mark'):
            b['calm_mark'] = True
        if r.get('uid') in (flagged or ()):
            b['black'] += 1
        if r['hostile']:
            b['hostile'] += 1
        elif r['heated']:
            b['heated'] += 1
        else:
            b['calm'] += 1
    return out


def step_label(step):
    return 'Message by message' if not step else ('1 minute' if step == 1 else f'{step} minutes')


def calm_parts(stretch_count, units) -> list:
    """For each part of the chat (as Trends cuts it), how many incidents were first calmed by a message in it: the green flag. [] when nothing was calmed or the parts do not line up."""
    msgs, _timed = messages(units)
    spans = measures.cut(len(msgs))
    if not spans or len(spans) != stretch_count:
        return []
    out = [0] * len(spans)
    for inc in find(units):
        if inc['calm']:
            for k, (a, b) in enumerate(spans):
                if a <= inc['calm']['i'] < b:
                    out[k] += 1
    return out if any(out) else []
