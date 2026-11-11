"""The High Risk page: 45 behaviours in 9 categories, and the exact messages found for each.

Three readers feed it: the Vibe Check flags, the Aggressive tone of the 13 tones, and the Felixa Behavioural Intelligence detectors (via safety_module). A behaviour with no reader says so;
a zero never means "checked and clear" for those. Held items and questions are not here: they live on the Review page."""
from __future__ import annotations

import cyber_rules
import store

CATEGORIES = [
    ('Aggression & Violence', ['Aggression', 'Violence', 'Threats', 'Intimidation', 'Incitement', 'Coordinated Attacks']),
    ('Hate & Discrimination', ['Racism', 'Homophobia', 'Transphobia', 'Sexism', 'Xenophobia', 'Religious Hatred', 'Ableism', 'Hate Speech', 'Dehumanization']),
    ('Harassment & Abuse', ['Harassment', 'Bullying', 'Personal Attacks', 'Profanity', 'Public Humiliation', 'Victim Blaming']),
    ('Manipulation & Coercion', ['Manipulation', 'Coercion', 'Gaslighting', 'Blackmail', 'Social Exclusion']),
    ('Sexual Misconduct & Exploitation', ['Sexual Harassment', 'Sexual Exploitation', 'Grooming']),
    ('Extremism & Dangerous Recruitment', ['Extremist Recruitment', 'Radicalization Attempts', 'Predatory Recruitment']),
    ('Privacy, Deception & Digital Abuse', ['Stalking', 'Doxxing', 'Privacy Violations', 'Impersonation', 'Fraud and Scams']),
    ('Self-Harm & Dangerous Activities', ['Self-Harm Encouragement', 'Suicide Encouragement', 'Dangerous Challenges']),
    ('Cyber Attacks & Security Threats', list(cyber_rules.BEHAVIOURS)),
]
TOTAL = sum(len(b) for _c, b in CATEGORIES)
assert TOTAL == 45

VIBE = {'threat': 'Threats', 'harassment': 'Harassment', 'hate_speech': 'Hate Speech', 'bullying': 'Bullying', 'profanity': 'Profanity', 'insult': 'Personal Attacks'}
FBSI = {'D01': 'Threats', 'D03': 'Manipulation', 'D06': 'Social Exclusion', 'D07': 'Personal Attacks', 'D15': 'Blackmail', 'D16': 'Coercion', 'D17': 'Coercion', 'D18': 'Bullying'}
TONE = {'Aggressive': 'Aggression'}
READ = set(VIBE.values()) | set(FBSI.values()) | set(TONE.values()) | set(cyber_rules.BEHAVIOURS)


def _when(u) -> str:
    return (u.get('timestamp') or '')[:16].replace('T', ' ')


def build(aid, safety=None) -> dict:
    """{'categories': [{'name','count','behaviours':[{'name','count','quotes':[{'text','when','source','uid'}]}],'none':[names]}], 'messages', 'behaviours', 'hit_categories', 'by_uid', 'unread_categories'}"""
    units = store.rated_text_units(aid) or []
    base = {u['id']: u for u in store.units_for(aid)}
    by_text = {' '.join((u['text'] or '').split()): u['id'] for u in base.values()}
    found: dict = {}

    def add(beh, uid, text, when, source):
        slot = found.setdefault(beh, {})
        key = uid if uid is not None else text
        slot.setdefault(key, {'text': ' '.join(str(text or '').split()), 'when': when, 'source': source, 'uid': uid})

    for u in units:
        for tone, beh in TONE.items():
            if (u['ratings'].get(tone) or 0) >= 2:
                add(beh, u['id'], u['text'], _when(u), u['source'])
    vibe = store.latest_vibe(aid)
    for f in ((vibe or {}).get('flagged') or []) if (vibe or {}).get('status') == 'ok' else []:
        beh = VIBE.get(f.get('type'))
        if beh:
            text = ' '.join(str(f.get('message') or '').split())
            uid = by_text.get(text)
            add(beh, uid, text, str(f.get('timestamp') or '')[:16].replace('T', ' '), f.get('source') or '')
    ordered = list(base.values())
    for u in ordered:                                           # the word and link rules of Cyber Attacks & Security Threats
        for f in cyber_rules.scan(u['text']):
            add(f['behaviour'], u['id'], u['text'], _when(u), u.get('source_label') or '')
    for uid in cyber_rules.flooding(ordered):                   # the same line, again and again
        u = base[uid]
        add('Raids and Flooding', uid, u['text'], _when(u), u.get('source_label') or '')
    if safety:
        for it in safety.get('items', []):
            beh = FBSI.get(it['detector'])
            if beh and it['valence'] == 'risk':
                for uid in it['uids']:
                    u = base.get(uid)
                    if u:
                        add(beh, uid, u['text'], _when(u), u.get('source_label') or '')
    cat_of = {b: c for c, bs in CATEGORIES for b in bs}
    by_uid: dict = {}
    for beh, slot in found.items():
        for x in slot.values():
            if x['uid'] is not None:
                by_uid.setdefault(x['uid'], []).append(beh)
    cats, all_uids, texts = [], set(), set()
    for name, behs in CATEGORIES:
        rows, none = [], []
        for b in behs:
            q = list(found.get(b, {}).values())
            if q:
                rows.append({'name': b, 'count': len(q), 'quotes': q})
                for x in q:
                    (all_uids.add(x['uid']) if x['uid'] is not None else texts.add(x['text']))
            else:
                none.append(b)
        rows.sort(key=lambda r: -r['count'])
        cats.append({'name': name, 'count': sum(r['count'] for r in rows), 'behaviours': rows, 'none': none, 'readable': any(b in READ for b in behs)})
    return {'categories': cats, 'messages': len(all_uids) + len(texts), 'behaviours': sum(len(c['behaviours']) for c in cats), 'hit_categories': sum(1 for c in cats if c['count']),
            'uids': all_uids, 'by_uid': by_uid, 'cat_of': cat_of, 'unread_categories': [c['name'] for c in cats if not c['readable']], 'unread_behaviours': TOTAL - len(READ), 'total': TOTAL, 'n_categories': len(CATEGORIES)}


import datetime as _dt
import html as _html
from collections import Counter

_E = _html.escape
YELLOW, RED, GREEN, INK = '#fab219', '#d03b3b', '#2e9e5b', '#16251d'


def _flag(x, y, kind):
    c = {'Y': YELLOW, 'R': RED, 'D': GREEN}[kind]
    return f'<g data-g="{ {"Y": "escalation", "R": "conflict", "D": "deescalation"}[kind] }"><line x1="{x:.0f}" y1="{y:.0f}" x2="{x:.0f}" y2="{y - 22:.0f}" stroke="{INK}" stroke-width="1.6"/><path d="M{x:.0f} {y - 22:.0f} L{x + 15:.0f} {y - 17:.0f} L{x:.0f} {y - 12:.0f} Z" fill="{c}"/></g>'


def _buckets(msgs, timed):
    """[(label, [message index])]: one bucket per day when the chat has times over more than a day (a week when it spans more than 31 days), else equal parts."""
    import measures
    if timed and msgs:
        days = [m['when'].date() if hasattr(m['when'], 'date') else None for m in msgs]
        if all(d is not None for d in days) and max(days) > min(days):
            first, last = min(days), max(days)
            step = 1 if (last - first).days <= 31 else 7
            n = (last - first).days // step + 1
            out = [[(first + _dt.timedelta(days=k * step)).strftime('%b %-d'), []] for k in range(n)]
            for i, d in enumerate(days):
                out[(d - first).days // step][1].append(i)
            return [(a, b) for a, b in out]
    spans = measures.cut(len(msgs))
    if timed and msgs and spans:                    # one day of messages: each part is named by the time it starts
        return [(msgs[a]['when'].strftime('%H:%M') if hasattr(msgs[a]['when'], 'strftime') else f'Part {k + 1}', list(range(a, b))) for k, (a, b) in enumerate(spans)]
    return [(f'Part {k + 1}', list(range(a, b))) for k, (a, b) in enumerate(spans or [])]


def _detail(msgs, idx, timed) -> str:
    """What a part or a day holds, in words: its messages by number, and its times when the chat has them."""
    if not idx:
        return ''
    words = f'messages {idx[0] + 1} to {idx[-1] + 1}' if len(idx) > 1 else f'message {idx[0] + 1}'
    if timed and hasattr(msgs[idx[0]]['when'], 'strftime'):
        a, b = msgs[idx[0]]['when'], msgs[idx[-1]]['when']
        words += f', {a:%b %-d %H:%M}' + (f' to {b:%H:%M}' if a.date() == b.date() and len(idx) > 1 else (f' to {b:%b %-d %H:%M}' if len(idx) > 1 else ''))
    return words


def _clusters(ordered, stamps, timed, uids) -> list:
    """The High Risk incidents: runs of matched messages that follow each other within the quiet gap (30 minutes, or 5 messages without times), as lists of positions in `ordered`."""
    import incidents
    groups, cur = [], []
    for i, u in enumerate(ordered):
        if u['id'] not in uids:
            continue
        if cur:
            near = ((stamps[i] - stamps[cur[-1]]).total_seconds() / 60.0 <= incidents.DEFAULT_GAP) if timed else (i - cur[-1] <= incidents.QUIET_MESSAGES)
            if not near:
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


TIMEFRAMES = ((30, '30 min'), (60, '1 hour'), (180, '3 hours'), (300, '5 hours'), (720, '12 hours'), (1440, '24 hours'), (0, 'All time'))
MAX_BARS = 60


def _time_buckets(ordered, stamps, uids, minutes):
    """The bars of the timeframe view: the chat from its first to its last message in steps of `minutes` (0 = one bar for all of it). Returns ([positions of the matches in each step], [the start of each step], the minutes used).
    A step that would give more than MAX_BARS bars is swapped for the next larger one."""
    import datetime as _d
    first, last = stamps[0], stamps[-1]
    span = (last - first).total_seconds() / 60.0
    sizes = [m for m, _l in TIMEFRAMES if m]
    if minutes:
        minutes = next((m for m in sizes if m >= minutes and span / m + 1 <= MAX_BARS), 0)
    if not minutes:
        return [[i for i, u in enumerate(ordered) if u['id'] in uids]], [first], 0
    n = int(span // minutes) + 1
    groups = [[] for _ in range(n)]
    for i, u in enumerate(ordered):
        if u['id'] in uids:
            groups[min(n - 1, int(((stamps[i] - first).total_seconds() / 60.0) // minutes))].append(i)
    return groups, [first + _d.timedelta(minutes=minutes * k) for k in range(n)], minutes


def over_time(aid, hr, mode='incident', minutes=0) -> dict:
    """The Over time views: one bar for each High Risk incident (A) and dots by category inside each (B). A High Risk incident is a run of matches that follow each other within the quiet gap.
    A bar carries the flags of the escalation incident it sits in: yellow where it starts, red where it turns into conflict, green where someone calms it.
    {'svg_a', 'svg_b', 'days': [{'label','count','quotes','flags','incidents','detail'}], 'has_flags', 'n', 'gap'}; {} when nothing matched."""
    import incidents
    import measures
    uids, by_uid, cat_of = hr['uids'], hr['by_uid'], hr['cat_of']
    ordered, stamps, basis, _left = measures.order_units(store.units_for(aid))
    timed = basis == 'time'
    starts, used = None, None
    if mode == 'time' and timed and ordered:
        groups, starts, used = _time_buckets(ordered, stamps, uids, minutes)
        if not any(groups):
            return {}
    else:
        mode = 'incident'
        groups = _clusters(ordered, stamps, timed, uids)
    if not groups:
        return {}
    inc_of = {}
    for inc in incidents.find(store.rated_text_units(aid)):
        for r in inc['rows']:
            if r.get('inside') and r.get('uid') is not None:
                inc_of[r['uid']] = inc
    days = []
    for k, g in enumerate(groups):
        mine = [ordered[i] for i in g]
        incs = {inc_of[u['id']]['n']: inc_of[u['id']] for u in mine if u['id'] in inc_of}
        fl = (['Y'] if incs else []) + (['R'] if any(x['turn'] is not None for x in incs.values()) else []) + (['D'] if any(x['calm'] for x in incs.values()) else [])
        q = [{'text': ' '.join(str(u['text'] or '').split()), 'when': stamps[i].strftime('%H:%M') if timed else '', 'behaviour': ', '.join(by_uid.get(u['id'], [])),
              'inc': inc_of[u['id']]['n'] if u['id'] in inc_of else None,
              'turn': bool(u['id'] in inc_of and inc_of[u['id']]['turn'] is not None and inc_of[u['id']]['turn'].get('uid') == u['id'])} for i, u in zip(g, mine)]
        if mode == 'time':
            st = starts[k]
            label = 'All time' if not used else (f'{st:%b %-d}' if used >= 1440 else f'{st:%b %-d %H:%M}')
            span = 'the whole chat' if not used else f'{st:%b %-d %H:%M} for {used // 60} h' if used >= 60 and used % 60 == 0 else f'{st:%b %-d %H:%M} for {used} min'
        elif timed:
            a, b = stamps[g[0]], stamps[g[-1]]
            label = f'Incident {k + 1}'
            span = f'{a:%b %-d %H:%M}' + (f' to {b:%H:%M}' if len(g) > 1 and a.date() == b.date() else (f' to {b:%b %-d %H:%M}' if len(g) > 1 else ''))
        else:
            label = f'Incident {k + 1}'
            span = f'messages {g[0] + 1} to {g[-1] + 1}' if len(g) > 1 else f'message {g[0] + 1}'
        days.append({'label': label, 'count': len(q), 'quotes': q, 'flags': fl, 'k': k, 'incidents': sorted((n, x['level']) for n, x in incs.items()), 'detail': span})
    n = len(days)
    mx = max(d['count'] for d in days) or 1
    lab_step = 1 if (mode == 'incident' and n <= 12) else max(1, -(-n // 6))        # long time labels need room: about six on the axis
    W, H = 640, 190
    bw = W / n
    a = ''
    for d in days:
        h = max(d['count'] / mx * 100, 0)
        w = min(bw - 2 * min(10, bw * .15), 72)
        x = d['k'] * bw + (bw - w) / 2
        y = 150 - h
        href = f' data-href="/analysis/{aid}/incident/{d["incidents"][0][0]}?src=safety"' if d['incidents'] else ''
        a += f'<g class="hr-bar" data-g="highrisk" data-day="{d["k"]}"{href}><rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="4" fill="#1f3a2e"><title>{_E(d["label"])}: {d["count"]}</title></rect>'
        if d['count']:
            a += f'<text class="v" x="{x + w / 2:.0f}" y="{y - 5:.0f}" text-anchor="middle">{d["count"]}</text>'
        for j, f in enumerate(d['flags']):
            a += _flag(x + w / 2 + (j * 17 - (len(d['flags']) - 1) * 8), y - 22, f)
        a += '</g>'
        if d['k'] % lab_step == 0 or d['k'] == n - 1:
            a += f'<text x="{d["k"] * bw + bw / 2:.0f}" y="172" text-anchor="middle">{_E(d["label"])}</text>'
    svg_a = f'<svg class="hr-over" viewBox="0 0 {W} {H}" role="img" aria-label="Messages found in each bar, with flags">{a}</svg>'
    rows = [c for c, _b in CATEGORIES if any(cat_of.get(b) == c for u in ordered if u['id'] in uids for b in by_uid.get(u['id'], []))]
    svg_b = ''
    if rows:
        top, rowh, lab = 44, 34, 150
        Wb = 640
        cw = (Wb - lab) / n
        Hb = top + rowh * len(rows) + 34
        b = ''
        for d in days:
            cx = lab + d['k'] * cw + cw / 2
            b += f'<line x1="{cx:.0f}" y1="{top - 6}" x2="{cx:.0f}" y2="{top + rowh * len(rows)}" stroke="#eee9db"/>'
            if d['k'] % lab_step == 0 or d['k'] == n - 1:
                b += f'<text x="{cx:.0f}" y="{Hb - 10}" text-anchor="middle">{_E(d["label"])}</text>'
            for j, f in enumerate(d['flags']):
                b += _flag(cx + (j * 17 - (len(d['flags']) - 1) * 8), top - 12, f)
        for r, c in enumerate(rows):
            y = top + r * rowh + rowh / 2
            b += f'<text x="0" y="{y + 4:.0f}">{_E(c if len(c) < 24 else c[:22] + "…")}</text><line x1="{lab}" y1="{y:.0f}" x2="{Wb}" y2="{y:.0f}" stroke="#f1ecdd"/>'
        for k, g in enumerate(groups):
            for pos, i in enumerate(g):
                u = ordered[i]
                for bname in by_uid.get(u['id'], [])[:1]:
                    r = rows.index(cat_of[bname])
                    b += f'<circle data-g="highrisk" cx="{lab + k * cw + (pos + .5) / len(g) * cw:.0f}" cy="{top + r * rowh + rowh / 2:.0f}" r="6" fill="#1f3a2e" opacity=".85"><title>{_E(bname)}: {_E(str(u["text"] or "")[:80])}</title></circle>'
        svg_b = f'<svg class="hr-over" viewBox="0 0 {Wb} {Hb}" role="img" aria-label="Which behaviour, when, with flags">{b}</svg>'
    return {'svg_a': svg_a, 'svg_b': svg_b, 'days': days, 'has_flags': any(d['flags'] for d in days), 'n': n, 'by_incident': mode == 'incident', 'mode': mode, 'used': used, 'timed': timed,
            'gap': f'{incidents.DEFAULT_GAP} minutes' if timed else f'{incidents.QUIET_MESSAGES} messages'}


def lane(bars) -> list:
    """The High Risk lane of an incident chart: [(behaviour, {bar index: messages})], the most frequent behaviour first."""
    rows: dict = {}
    for k, b in enumerate(bars):
        for name in b.get('beh') or []:
            rows.setdefault(name, {})
            rows[name][k] = rows[name].get(k, 0) + 1
    return sorted(rows.items(), key=lambda kv: -sum(kv[1].values()))


def annotate(aid, found) -> dict:
    """Puts what each incident contains on it: item['hr'] = [(behaviour, messages)], item['hr_n'] = High Risk messages inside it. Returns the behaviours of every message (uid -> [behaviour])."""
    import safety_module
    hr = build(aid, safety_module.build(aid)) if safety_module.AVAILABLE else build(aid)
    by_uid = hr['by_uid']
    for item in found or []:
        c = Counter()
        n = 0
        for r in item['rows']:
            if r.get('inside') and r.get('uid') in by_uid:
                n += 1
                c.update(by_uid[r['uid']])
        item['hr'] = c.most_common()
        item['hr_n'] = n
    return by_uid
