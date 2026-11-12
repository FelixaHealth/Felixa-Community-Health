"""The Track tab: Escalation, Conflict and Empathy built up over the parts of a chat, with red and yellow flags for Escalation and Conflict.

Everything here reads `measures.analyse` (the per-part counts and rates the app already keeps): no new rating is made. A flag marks a part of the chat:

* Escalation: the rate of heated messages (open hostility, agitation, or sarcastic digs) climbed above its recent level, the average of up to three parts before.
  YELLOW = up 10 points or more, with 2 or more heated messages: it is emerging. RED = a counted heated moment of the measures (up 15 points or more and clear
  of chance), or up 25 points or more with 3 or more heated messages. The first part has no past, so it is flagged only when it opens already heated.
* Conflict: the rate of openly hostile messages per 100 reaches the Some level (YELLOW, 2 or more messages) or the Often level (RED, 3 or more messages) of
  `measures.CUTS`, raised at Low sensitivity as everywhere else.
* RED needs 48 messages or more on the timeline (`m['verdicts']`), as every verdict in the app does. With fewer messages a part is at most YELLOW.
* Empathy has no flags. Its numbers are the rate of messages that clearly show empathy, from the same measures, until the PBT Empathy Track is added.

A flag describes wording in a part of the chat, never a person."""
import math

import measures

KEYS = ('escalation', 'conflict', 'empathy')
NAMES = {'escalation': 'Escalation', 'conflict': 'Conflict', 'empathy': 'Empathy'}
KIND = {'escalation': 'heated', 'conflict': 'hostile', 'empathy': 'care'}
ABOUT = {'escalation': 'How heated the chat gets, and how fast: messages that are openly hostile, agitated or sarcastic digs, over time.',
         'conflict': 'How openly hostile the wording is, over time: messages that are aggressive, rude, disrespectful or judgmental.',
         'empathy': 'How much care and understanding people show each other, over time: messages that clearly show empathy.'}
SAYS = {'escalation': 'heated', 'conflict': 'openly hostile', 'empathy': 'caring'}
YELLOW_CLIMB, RED_CLIMB, YELLOW_MIN, RED_MIN = 10.0, 25.0, 2, 3
OPEN_YELLOW, OPEN_RED = 20.0, 30.0
STEP = 5.0            # points per 100 between two parts for the wording to say it rose or eased
MIN_FOR_LINK = 8      # parts needed before the link between measures is described


def available(ms) -> bool:
    return bool(ms and ms.get('m') and ms['m'].get('has_series') and len(ms['m']['stretches']) >= 2)


def _rate(st, kind):
    return float(st['rates'][kind])


def _level_word(level):
    return {'red': 'Red flag', 'yellow': 'Yellow flag', '': ''}[level]


def escalation_flags(m):
    stretches = m['stretches']
    out = []
    for i, st in enumerate(stretches):
        h, n = st['counts']['heated'], st['n']
        value = _rate(st, 'heated')
        if m.get('levels'):                                 # bars are incidents: each carries its own flag
            out.append({'i': i, 'level': m['levels'][i], 'value': value, 'base': None, 'climb': 0.0, 'count': h, 'n': n})
            continue
        if i == 0:
            base, climb = None, 0.0
            level = 'red' if (m['verdicts'] and value >= OPEN_RED and h >= RED_MIN) else 'yellow' if (value >= OPEN_YELLOW and h >= YELLOW_MIN) else ''
        else:
            lo = max(0, i - measures.BASELINE_STRETCHES)
            for j in range(i, lo, -1):                          # a gap of quiet time ends the recent level: the bars before it are not compared
                if stretches[j].get('after_gap'):
                    lo = j
                    break
            before = stretches[lo:i]
            if not before:                                      # the first bar after a gap has no recent level: it is read like the first bar of the chat
                value_here = value
                level = 'red' if (m['verdicts'] and value_here >= OPEN_RED and h >= RED_MIN) else 'yellow' if (value_here >= OPEN_YELLOW and h >= YELLOW_MIN) else ''
                out.append({'i': i, 'level': level, 'value': value, 'base': None, 'climb': 0.0, 'count': h, 'n': n})
                continue
            base = 100.0 * sum(s['counts']['heated'] for s in before) / max(1, sum(s['n'] for s in before))
            climb = value - base
            counted = bool(st.get('counted'))
            level = 'red' if (m['verdicts'] and (counted or (climb >= RED_CLIMB and h >= RED_MIN))) else \
                    'yellow' if (climb >= YELLOW_CLIMB and h >= YELLOW_MIN) else ''
        out.append({'i': i, 'level': level, 'value': value, 'base': base, 'climb': climb, 'count': h, 'n': n})
    return out


def conflict_flags(m):
    low, high = measures.CUTS['hostile']
    low, high = low * m['scale'], high * m['scale']
    out = []
    for i, st in enumerate(m['stretches']):
        c, n = st['counts']['hostile'], st['n']
        value = _rate(st, 'hostile')
        if m.get('levels'):
            out.append({'i': i, 'level': 'red' if m['levels'][i] == 'red' else '', 'value': value, 'base': None, 'climb': 0.0, 'count': c, 'n': n})
            continue
        level = 'red' if (m['verdicts'] and value >= high and c >= RED_MIN) else 'yellow' if (value >= low and c >= YELLOW_MIN) else ''
        out.append({'i': i, 'level': level, 'value': value, 'base': None, 'climb': 0.0, 'count': c, 'n': n})
    return out


def empathy_series(m):
    return [{'i': i, 'level': '', 'value': _rate(st, 'care'), 'base': None, 'climb': 0.0, 'count': st['counts']['care'], 'n': st['n']} for i, st in enumerate(m['stretches'])]


def flags_for(m, key):
    return {'escalation': escalation_flags, 'conflict': conflict_flags, 'empathy': empathy_series}[key](m)


def _state(flags, i):
    """What the part does compared with the one before: emerged, intensified, holding, easing, or cleared. '' for a part with no flag that follows none."""
    f = flags[i]
    prev = flags[i - 1] if i else None
    change = f['value'] - prev['value'] if prev else 0.0
    if f['level']:
        if not prev or not prev['level']:
            return 'emerged'
        if (f['level'] == 'red' and prev['level'] == 'yellow') or change >= STEP:
            return 'intensified'
        if change <= -STEP:
            return 'easing'
        return 'holding'
    return 'cleared' if prev and prev['level'] else ''


STATE_WORDS = {'emerged': 'Emerged', 'intensified': 'Intensified', 'holding': 'Holding', 'easing': 'Easing', 'cleared': 'Cleared'}


def events(m, key):
    """The flagged parts, and the parts where a flag cleared, in order: [{'i', 'label', 'level', 'state', 'text'}]. Empty for Empathy."""
    if key == 'empathy':
        return []
    flags = flags_for(m, key)
    said = SAYS[key]
    out = []
    for i, f in enumerate(flags):
        state = _state(flags, i)
        if not state:
            continue
        st = m['stretches'][i]
        prev = flags[i - 1] if i else None
        move = ''
        if prev:
            d = f['value'] - prev['value']
            move = f' That is {abs(d):.0f} {"more" if d > 0 else "fewer"} than in the bar before.' if abs(d) >= 1 else ' That is the same as the bar before.'
        if state == 'cleared':
            text = f'{f["value"]:.0f} in every 100 messages were {said}, back below the flag level.{move}'
        else:
            lead = f'{f["value"]:.0f} in every 100 messages were {said} ({f["count"]} of {f["n"]}).'
            if key == 'escalation' and f['base'] is not None:
                lead += f' That is {f["climb"]:.0f} above the recent level of {f["base"]:.0f}.'
            text = lead + move
        out.append({'i': i, 'label': st['label'], 'axis': st.get('axis') or '', 'level': f['level'], 'state': state, 'text': text, 'dates': st.get('start') or ''})
    return out


def summary(m, key):
    """The plain-words story of a measure: when it first appeared, how it intensified, how high it got, and where it stands now."""
    flags = flags_for(m, key)
    k = len(flags)
    st = m['stretches']
    reds = [f for f in flags if f['level'] == 'red']
    yellows = [f for f in flags if f['level'] == 'yellow']
    top = max(flags, key=lambda f: (f['value'], -f['i']))
    out = {'k': k, 'red': len(reds), 'yellow': len(yellows), 'latest': flags[-1]['value'], 'highest': top['value'], 'highest_at': st[top['i']]['label'], 'sentences': []}
    if key == 'empathy':
        out['sentences'].append(f'Empathy was highest in {st[top["i"]]["label"]}, with {top["value"]:.0f} in every 100 messages, and was {flags[-1]["value"]:.0f} in the latest bar.')
        return out
    flagged = [f for f in flags if f['level']]
    if not flagged:
        out['sentences'].append(f'No bar was flagged. The highest was {top["value"]:.0f} in every 100 messages, in {st[top["i"]]["label"]}.')
        return out
    first = flagged[0]
    out['sentences'].append(f'The first flag, {_level_word(first["level"]).lower()}, appeared in {st[first["i"]]["label"]}.')
    rose = [i for i in range(len(flags)) if _state(flags, i) == 'intensified']
    if rose:
        out['sentences'].append(f'It intensified in {len(rose)} bar{"" if len(rose) == 1 else "s"}, first in {st[rose[0]]["label"]}.')
    else:
        out['sentences'].append('It did not intensify from one bar to the next.')
    if reds:
        peak = max(reds, key=lambda f: (f['value'], -f['i']))
        out['sentences'].append(f'The highest red flag was in {st[peak["i"]]["label"]}, at {peak["value"]:.0f} in every 100 messages.')
    last = flags[-1]
    out['sentences'].append('In the latest bar it is flagged ' + _level_word(last['level']).lower().replace(' flag', '') + '.' if last['level'] else 'In the latest bar it is not flagged.')
    return out


def _pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if va <= 1e-12 or vb <= 1e-12:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def _strength(r):
    r = abs(r)
    return 'a strong' if r >= 0.6 else 'a clear' if r >= 0.4 else 'a weak'


def link(m, kinds=('conflict', 'escalation')):
    """Does empathy go with less conflict and escalation? Plain sentences from the per-part rates, or an honest 'not enough parts'. Never a claim of cause."""
    st = m['stretches']
    k = len(st)
    care, hostile, heated = ([_rate(s, kind) for s in st] for kind in ('care', 'hostile', 'heated'))
    out = {'k': k, 'ready': k >= MIN_FOR_LINK, 'sentences': [], 'r': {}, 'answer': ''}
    if not out['ready']:
        out['answer'] = 'Not enough bars yet to say.'
        out['sentences'].append(f'There are {k} bars so far. With {MIN_FOR_LINK} or more bars the page says whether empathy goes with less ' + ' and '.join(kinds) + '.')
        return out
    for key, series, word in [x for x in (('conflict', hostile, 'conflict'), ('escalation', heated, 'escalation')) if x[0] in kinds]:
        same = _pearson(care, series)
        nxt = _pearson(care[:-1], series[1:])
        out['r'][key] = {'same': same, 'next': nxt}
        if same is None:
            out['sentences'].append(f'Empathy or {word} did not change from bar to bar, so there is nothing to compare.')
            continue
        if abs(same) < 0.3:
            out['sentences'].append(f'Empathy and {word} do not move together in a clear way in this chat.')
        elif same < 0:
            out['sentences'].append(f'In the bars where empathy was higher, {word} tended to be lower ({_strength(same)} link).')
        else:
            out['sentences'].append(f'In the bars where empathy was higher, {word} tended to be higher too ({_strength(same)} link). That can happen when people talk about hard things with care.')
        if nxt is not None and abs(nxt) >= 0.4 and abs(nxt) > abs(same) + 0.1:
            out['sentences'].append(f'The link is stronger with the bar after: empathy in one bar goes with {"less" if nxt < 0 else "more"} {word} in the next.')
    esc = out['r'].get('escalation')
    if esc is not None:                                                   # the short answer to "Does empathy change escalation?", in one sentence
        r = esc['same']
        if r is None:
            out['answer'] = 'Too little change from bar to bar to tell.'
        elif abs(r) < 0.3:
            out['answer'] = 'No clear link: empathy and escalation do not move together in this chat.'
        elif r < 0:
            out['answer'] = f'Yes, it goes with less: where empathy was higher, escalation tended to be lower ({_strength(r)} link).'
        else:
            out['answer'] = f'Not in a way that lowers it: where empathy was higher, escalation tended to be higher too ({_strength(r)} link).'
    out['caution'] = f'This compares {k} bars of one chat. A link in the numbers is not proof that one thing causes the other.'
    return out


def card(m, key):
    """What the Track tab shows for one measure: the headline numbers."""
    flags = flags_for(m, key)
    s = summary(m, key)
    now = flags[-1]
    return {'key': key, 'name': NAMES[key], 'about': ABOUT[key], 'latest': round(now['value']), 'level': now['level'], 'red': s['red'], 'yellow': s['yellow'], 'k': s['k'],
            'highest': round(s['highest']), 'highest_at': s['highest_at'], 'flagged': key != 'empathy'}


# ---- the story of one chat: calm, escalation starts (yellow), turns into conflict (red), eases ---------------------
GENERIC = [
    {'key': 'calm', 'examples': ['Anyone free to look at the build later?', 'Sure, I will take it after lunch.']},
    {'key': 'yellow', 'examples': ['That is the third time this has slipped. Great, thanks a lot.', 'I am getting tired of chasing this.']},
    {'key': 'red', 'examples': ['You never listen. You always do this.', 'This is useless and you know it.']},
    {'key': 'ease', 'examples': ['Sorry for snapping earlier. Let us sort it out together.', 'Thanks for saying that. I would like to talk it through.']}]
STAGES = {'calm': ('Calm', 'People talk normally. Nothing is flagged.'),
          'yellow': ('Yellow flag: escalation starts', 'The mood begins to heat up. Messages turn agitated, sarcastic or sharp, but not openly hostile yet.'),
          'red': ('Red flag: it turns into conflict', 'Heat becomes open hostility: blame, insults, put-downs. This is the moment to step in.'),
          'ease': ('Easing', 'Someone steadies the conversation, and the hostile wording falls away.')}


def _quote(text, limit=150):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _pick(group, ok, n=2, used=None):
    used = set() if used is None else used
    out = []
    for u in group:
        if len(out) < n and ok(u) and id(u) not in used:
            used.add(id(u))
            out.append(_quote(u['text']))
    return out


def story(m, units):
    """The progression of this chat as four stages with real example messages: [{'key', 'title', 'about', 'part', 'examples', 'real'}].
    Stages the chat never reached are left out. When it reached none of the flagged ones, a made-up example is shown, marked as one."""
    complete = [u for u in (units or []) if all(a in (u.get('ratings') or {}) and u['ratings'][a] is not None for a in measures.analysis.ATTRIBUTES)]
    ordered = measures.order_units(complete)[0]
    spans = measures.cut(len(ordered))
    if not spans or len(spans) != len(m['stretches']):
        return {'stages': _generic(), 'real': False}
    parts = [ordered[a:b] for a, b in spans]
    esc, con = escalation_flags(m), conflict_flags(m)
    f = [measures.message_flags(u['ratings']) for u in ordered]
    flag_of = {id(u): f[i] for i, u in enumerate(ordered)}
    def label(i):
        return m['stretches'][i]['label']
    yellow = next((i for i, e in enumerate(esc) if e['level']), None)
    red = next((i for i in range(len(esc)) if (con[i]['level'] or esc[i]['level'] == 'red') and (yellow is None or i >= yellow)), None)
    if yellow is None and red is None:
        return {'stages': _generic(), 'real': False}
    start = yellow if yellow is not None else red
    stages, used = [], set()
    calm_at = next((i for i in range(start - 1, -1, -1) if not esc[i]['level'] and not con[i]['level']), None) if start else None
    if calm_at is not None:
        ex = _pick(parts[calm_at], lambda u: not flag_of[id(u)]['heated'] and not flag_of[id(u)]['neg'], used=used)
        if ex:
            stages.append({'key': 'calm', 'part': label(calm_at), 'examples': ex, 'real': True})
    ex = _pick(parts[start], lambda u: flag_of[id(u)]['heated'] and not flag_of[id(u)]['hostile'], n=1, used=used) or _pick(parts[start], lambda u: flag_of[id(u)]['heated'], n=1, used=used)
    if yellow is not None and ex:
        stages.append({'key': 'yellow', 'part': label(start), 'examples': ex, 'real': True})
    if red is not None:
        ex = _pick(parts[red], lambda u: flag_of[id(u)]['hostile'], used=used) or _pick(parts[red], lambda u: flag_of[id(u)]['heated'], used=used)
        if ex:
            stages.append({'key': 'red', 'part': label(red), 'examples': ex, 'real': True})
        ease_at = next((i for i in range(red + 1, len(parts)) if not con[i]['level'] and not esc[i]['level']), None)
        if ease_at is not None:
            ex = _pick(parts[ease_at], lambda u: flag_of[id(u)]['care'] or flag_of[id(u)]['polite'], used=used) or _pick(parts[ease_at], lambda u: not flag_of[id(u)]['heated'], used=used)
            if ex:
                stages.append({'key': 'ease', 'part': label(ease_at), 'examples': ex, 'real': True})
    if not any(s['key'] in ('yellow', 'red') for s in stages):
        return {'stages': _generic(), 'real': False}
    for s in stages:
        s['title'], s['about'] = STAGES[s['key']]
    return {'stages': stages, 'real': True}


def _generic():
    out = []
    for g in GENERIC:
        title, about = STAGES[g['key']]
        out.append({'key': g['key'], 'title': title, 'about': about, 'part': '', 'examples': g['examples'], 'real': False})
    return out


def part_messages(m, units, key, limit=15):
    """The exact messages behind each part of the chat for Escalation (the heated ones) or Conflict (the openly hostile ones): {part index: {'items', 'total'}}."""
    kind = KIND.get(key)
    if key == 'empathy' or not kind:
        return {}
    complete = [u for u in (units or []) if all(a in (u.get('ratings') or {}) and u['ratings'][a] is not None for a in measures.analysis.ATTRIBUTES)]
    ordered = measures.order_units(complete)[0]
    spans = measures.cut(len(ordered))
    if not spans or len(spans) != len(m['stretches']):
        return {}
    out = {}
    for i, (a, b) in enumerate(spans):
        hits = [' '.join(str(u['text'] or '').split()) for u in ordered[a:b] if measures.message_flags(u['ratings'])[kind]]
        if hits:
            out[i] = {'items': hits[:limit], 'total': len(hits)}
    return out
