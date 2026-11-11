"""Escalation and deescalation by tone: for each of the 3 families (Positive, Tense, Toxic) and each of the 13 tones, how often it shows in the escalating part of the chat and in the calming part.

Both parts come from the incidents of Escalation. The escalating part is every message inside an incident before anyone calmed it (the whole incident when nobody did). The calming part is
every message from the first calming message of an incident onward, up to the end of what the incident shows. A tone 'shows' in a message when it was rated 2 or 3. The rate is the messages that
show it per 100 messages of that part. It describes wording in the chat; it says nothing about a person."""
from __future__ import annotations

import analysis
import incidents
import measures

FAMILIES = analysis.FAMILIES


def build(units) -> dict:
    found = incidents.find(units)
    ratings = {u['id']: (u.get('ratings') or {}) for u in units}
    esc, de = {}, {}
    for inc in found:
        c = inc['calm']['i'] if inc['calm'] else None
        for r in inc['rows']:
            uid = r.get('uid')
            if uid is None or uid not in ratings:
                continue
            if c is not None and r['i'] >= c:
                de.setdefault(uid, True)
            elif r.get('inside'):
                esc.setdefault(uid, True)
    for uid in de:                              # a message counted in the calming part of one incident is not also an escalating one of another
        esc.pop(uid, None)
    n_esc, n_de = len(esc), len(de)

    def shows(uids, attrs):
        return sum(1 for u in uids if any((ratings[u].get(a) or 0) >= measures.FLAG_MIN for a in attrs))

    def row(name, attrs, family=''):
        e, d = shows(esc, attrs), shows(de, attrs)
        return {'name': name, 'family': family, 'esc_n': e, 'esc_rate': 100.0 * e / n_esc if n_esc else 0.0, 'de_n': d, 'de_rate': 100.0 * d / n_de if n_de else 0.0}
    categories = [row(name, members) for name, members in FAMILIES]
    attributes = [row(a, [a], fam) for fam, members in FAMILIES for a in members]
    return {'incidents': len(found), 'tried': sum(1 for i in found if i['calm']), 'n_esc': n_esc, 'n_de': n_de, 'categories': categories, 'attributes': attributes, 'any': bool(found)}
