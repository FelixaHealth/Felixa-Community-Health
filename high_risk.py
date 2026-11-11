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
            'uids': all_uids, 'unread_categories': [c['name'] for c in cats if not c['readable']], 'unread_behaviours': 40 - len(READ)}


def over_time(aid, uids, stretch_count=None) -> list:
    """For each part of the chat (as Trends cuts it): [{'label','count'}], the messages found in that part."""
    import incidents
    import measures
    units = store.rated_text_units(aid)
    msgs, _timed = incidents.messages(units)
    spans = measures.cut(len(msgs))
    out = []
    for a, b in spans or []:
        part = msgs[a:b]
        when = part[0].get('when') if part else None
        label = when.strftime('%b %-d') if hasattr(when, 'strftime') else str(a + 1)
        out.append({'label': label, 'count': sum(1 for m in part if m.get('uid') in uids)})
    return out
