"""The High Risk page: 40 behaviours in 8 categories, and the exact messages found for each.

Three readers feed it: the Vibe Check flags, the Aggressive tone of the 13 tones, and the Felixa Behavioural Intelligence detectors (via safety_module). A behaviour with no reader says so;
a zero never means "checked and clear" for those. Held items and questions are not here: they live on the Review page."""
from __future__ import annotations

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
]
assert sum(len(b) for _c, b in CATEGORIES) == 40

VIBE = {'threat': 'Threats', 'harassment': 'Harassment', 'hate_speech': 'Hate Speech', 'bullying': 'Bullying', 'profanity': 'Profanity', 'insult': 'Personal Attacks'}
FBSI = {'D01': 'Threats', 'D03': 'Manipulation', 'D06': 'Social Exclusion', 'D07': 'Personal Attacks', 'D15': 'Blackmail', 'D16': 'Coercion', 'D17': 'Coercion', 'D18': 'Bullying'}
TONE = {'Aggressive': 'Aggression'}
READ = set(VIBE.values()) | set(FBSI.values()) | set(TONE.values())


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
            'uids': all_uids, 'by_uid': by_uid, 'cat_of': cat_of, 'unread_categories': [c['name'] for c in cats if not c['readable']], 'unread_behaviours': 40 - len(READ)}


import datetime as _dt
import html as _html

_E = _html.escape
YELLOW, RED, INK = '#fab219', '#d03b3b', '#16251d'


def _flag(x, y, kind):
    c = YELLOW if kind == 'Y' else RED
    return f'<g><line x1="{x:.0f}" y1="{y:.0f}" x2="{x:.0f}" y2="{y - 22:.0f}" stroke="{INK}" stroke-width="1.6"/><path d="M{x:.0f} {y - 22:.0f} L{x + 15:.0f} {y - 17:.0f} L{x:.0f} {y - 12:.0f} Z" fill="{c}"/></g>'


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
    return [(f'Part {k + 1}', list(range(a, b))) for k, (a, b) in enumerate(spans or [])]


def over_time(aid, hr) -> dict:
    """The Over time views: bars per day (A) and dots per category (B), both with the Trends flags (yellow where an escalation starts, red where it turns into conflict).
    {'svg_a', 'svg_b', 'days': [{'label','count','quotes'}], 'has_flags'}; {} when there is nothing to draw."""
    import incidents
    units = store.rated_text_units(aid)
    msgs, timed = incidents.messages(units)
    uids, by_uid, cat_of = hr['uids'], hr['by_uid'], hr['cat_of']
    buckets = _buckets(msgs, timed)
    if not buckets:
        return {}
    where = {i: k for k, (_l, idx) in enumerate(buckets) for i in idx}
    flags: dict = {}
    for inc in incidents.find(units):
        if inc['start'] is not None and inc['start']['i'] in where:
            flags.setdefault(where[inc['start']['i']], set()).add('Y')
        if inc['turn'] is not None and inc['turn']['i'] in where:
            flags.setdefault(where[inc['turn']['i']], set()).add('R')
    days = []
    for k, (label, idx) in enumerate(buckets):
        q = [{'text': msgs[i]['text'], 'when': msgs[i]['when'].strftime('%H:%M') if timed and hasattr(msgs[i]['when'], 'strftime') else '', 'behaviour': ', '.join(by_uid.get(msgs[i]['uid'], []))}
             for i in idx if msgs[i]['uid'] in uids]
        days.append({'label': label, 'count': len(q), 'quotes': q, 'flags': sorted(flags.get(k, []), reverse=True), 'k': k})
    n = len(days)
    mx = max(d['count'] for d in days) or 1
    # A: bars
    W, H = 640, 190
    bw = W / n
    a = ''
    for d in days:
        h = d['count'] / mx * 100
        x, w = d['k'] * bw + min(10, bw * .15), bw - 2 * min(10, bw * .15)
        y = 150 - h
        a += f'<g class="hr-bar" data-day="{d["k"]}"><rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="4" fill="#1f3a2e"><title>{_E(d["label"])}: {d["count"]}</title></rect>'
        if d['count']:
            a += f'<text class="v" x="{x + w / 2:.0f}" y="{y - 5:.0f}" text-anchor="middle">{d["count"]}</text>'
        for j, f in enumerate(d['flags']):
            a += _flag(x + w / 2 + (j * 17 - (len(d['flags']) - 1) * 8), (y - 22 if d['count'] else 146), f)
        a += '</g>'
        if n <= 12 or d['k'] % ((n + 9) // 10) == 0 or d['k'] == n - 1:
            a += f'<text x="{d["k"] * bw + bw / 2:.0f}" y="172" text-anchor="middle">{_E(d["label"])}</text>'
    svg_a = f'<svg class="hr-over" viewBox="0 0 {W} {H}" role="img" aria-label="Messages found per part of the chat, with flags">{a}</svg>'
    # B: dots by category
    rows = [c for c, _b in CATEGORIES if any(cat_of.get(b) == c for i in range(len(msgs)) if msgs[i]['uid'] in uids for b in by_uid.get(msgs[i]['uid'], []))]
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
            if n <= 12 or d['k'] % ((n + 9) // 10) == 0 or d['k'] == n - 1:
                b += f'<text x="{cx:.0f}" y="{Hb - 10}" text-anchor="middle">{_E(d["label"])}</text>'
            for j, f in enumerate(d['flags']):
                b += _flag(cx + (j * 17 - (len(d['flags']) - 1) * 8), top - 12, f)
        for r, c in enumerate(rows):
            y = top + r * rowh + rowh / 2
            b += f'<text x="0" y="{y + 4:.0f}">{_E(c if len(c) < 24 else c[:22] + "…")}</text><line x1="{lab}" y1="{y:.0f}" x2="{Wb}" y2="{y:.0f}" stroke="#f1ecdd"/>'
        for k, (_l, idx) in enumerate(buckets):
            for pos, i in enumerate(idx):
                if msgs[i]['uid'] not in uids:
                    continue
                m = msgs[i]
                frac = (m['when'].hour * 60 + m['when'].minute) / 1440 if timed and hasattr(m['when'], 'hour') and (buckets[0][0][:4] != 'Part') else (pos + .5) / max(len(idx), 1)
                for bname in by_uid.get(m['uid'], [])[:1]:
                    r = rows.index(cat_of[bname])
                    b += f'<circle cx="{lab + k * cw + frac * cw:.0f}" cy="{top + r * rowh + rowh / 2:.0f}" r="6" fill="#1f3a2e" opacity=".85"><title>{_E(bname)}: {_E(m["text"][:80])}</title></circle>'
        svg_b = f'<svg class="hr-over" viewBox="0 0 {Wb} {Hb}" role="img" aria-label="Which behaviour, when, with flags">{b}</svg>'
    return {'svg_a': svg_a, 'svg_b': svg_b, 'days': days, 'has_flags': any(d['flags'] for d in days)}
