"""The Safety module: the FBSI detectors (D01 to D20) of Felixa Behavioural Intelligence, read on the messages of a workplace.

The detectors live unchanged in `fbsi/` (copied byte for byte from Felixa-Behavioural-Intelligence; see fbsi/README.md). This file only feeds them and turns what they say into something the
screens can show. Three rules of the algorithm are kept as they are:

* HELD. A Safety alert (a threat, an exposure threat, identity documents kept) is a candidate for a person to read. Until the safeguarding readiness record exists (be_safeguarding: a named qualified
  reviewer, an independent second reviewer, an approved emergency protocol) it is held: it is not operational, cannot be confirmed and triggers nothing. This app has no such record, so nothing is ever live.
* QUESTIONS, NEVER VERDICTS. Most detectors can only ever ask a question (was this meant to hurt? was this a joke?). A protective move is noted, with no badge.
* UNVALIDATED. Literal English rules with no measured accuracy. A missed or a false match is expected. A match describes wording in a message, never a person.

The app does not keep who wrote a message, so every message is read as coming from one speaker: the detectors that need two people (pressure after a refusal, a boundary that was respected)
cannot see an exchange here, and the ones that read one message (a threat, an exposure threat, identity papers, a stated silence) work as designed."""
from __future__ import annotations

import datetime as _dt

import store
from vibe_check.common import parse_ts

try:                                    # the engine files are installed beside the app (see fbsi/README.md); without them the module is simply absent
    from fbsi import be_acts, be_catalogue, be_detectors, be_safeguarding, org_signals
    AVAILABLE = True
except ImportError:                     # pragma: no cover - depends on the machine
    AVAILABLE = False

GAP_HOURS = 3                    # a quiet stretch this long starts a new episode (a new conversation)
MAX_QUOTE = 220
SAFETY_DETECTORS = {'D01', 'D15', 'D16'}        # the Safety route: held, whatever else is true
STATUS_WORDS = {'urgent_human_review': 'Held for a person to read', 'concerning_pattern': 'A pattern to look at', 'needs_clarification': 'A question to ask',
                'observed': 'Noted', 'protective': 'A protective move'}
_CACHE: dict = {}


def _speaker(u) -> str:
    return str(u.get('actor_role') or 'speaker')


def _episodes(units):
    """Conversations: the messages of one source, in order, split where the chat was quiet for GAP_HOURS. [{'seq', 'evidence_ids'}]"""
    by_source: dict = {}
    for u in units:
        by_source.setdefault(u.get('source_label') or '', []).append(u)
    chunks = []
    for _src, us in by_source.items():
        cur, last = [], None
        for u in us:
            at = parse_ts(u.get('timestamp'))
            if cur and at is not None and last is not None and (at - last).total_seconds() > GAP_HOURS * 3600:
                chunks.append(cur)
                cur = []
            cur.append(u)
            last = at if at is not None else last
        if cur:
            chunks.append(cur)
    chunks.sort(key=lambda c: (parse_ts(c[0].get('timestamp')) or _dt.datetime.max, c[0]['id']))
    return [{'seq': i + 1, 'evidence_ids': [u['id'] for u in c]} for i, c in enumerate(chunks)]


def _rows(units):
    rows = {}
    for u in units:
        text = u.get('text') or ''
        feat = be_acts.features_of(text)
        feat['sig'] = org_signals.signals_in(text)
        rows[u['id']] = {'id': u['id'], 'subject': _speaker(u), 'at': parse_ts(u.get('timestamp')), 'features': feat}
    return rows


def _quote(text) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= MAX_QUOTE else text[:MAX_QUOTE - 1].rstrip() + '…'


def run(units) -> dict:
    """The engine's output, with the evidence ids it pointed at. Nothing is stored."""
    episodes = _episodes(units)
    return be_detectors.run(episodes, _rows(units), {}, cap=None)


EMPTY = {'items': [], 'held': [], 'questions': [], 'protective': [], 'flagged_uids': [], 'incidents': 0, 'ran': [], 'messages': 0, 'excluded': 0, 'limits': [], 'live': False, 'missing': [], 'catalogue': [],
         'available': False}


def build(aid) -> dict:
    """What the Safety screen shows for a workplace: held items, questions, protective moves, and the unit ids that carry a black flag."""
    if not AVAILABLE:
        return EMPTY
    units = store.units_for(aid)
    key = (aid, len(units), units[-1]['id'] if units else 0, store.evidence_generation(aid))
    if key in _CACHE:
        return _CACHE[key]
    by_id = {u['id']: u for u in units}
    res = run(units) if units else {'matches': [], 'incidents': 0, 'ran': [], 'stance': {'excluded_total': 0}, 'limits': [], 'unread_messages': 0, 'not_run': []}
    items = []
    for m in res['matches']:
        uids = list(dict.fromkeys(m.get('_eids') or []))
        alert_id = be_catalogue.ALERT_OF.get(m['detector'])
        alert = next((a for a in be_catalogue.ALERTS if a['id'] == alert_id), None)
        held = m['detector'] in SAFETY_DETECTORS and m['status'] in ('urgent_human_review', 'concerning_pattern')
        quotes = []
        for uid in uids[:m['evidence_total'] if m.get('evidence_total') else 3][:4]:
            u = by_id.get(uid)
            if u:
                quotes.append({'text': _quote(u['text']), 'when': (u.get('timestamp') or '')[:16].replace('T', ' '), 'source': u.get('source_label') or '', 'uid': uid})
        items.append({'detector': m['detector'], 'name': m['name'], 'valence': m['valence'], 'status': m['status'], 'status_word': STATUS_WORDS.get(m['status'], m['status']),
                      'basis': m['basis'], 'note': m.get('note') or '', 'questions': m.get('clarification_questions') or [], 'alert_id': alert_id, 'alert_name': alert['name'] if alert else '',
                      'held': held, 'hold_reason': be_safeguarding.hold_reason() if held else '', 'quotes': quotes, 'uids': uids, 'incident': m.get('incident'),
                      'quality': m.get('evidence_quality'), 'total': m.get('evidence_total') or len(uids)})
    held = [i for i in items if i['held']]
    questions = [i for i in items if not i['held'] and i['valence'] == 'risk']
    protective = [i for i in items if i['valence'] == 'constructive']
    flagged = sorted({uid for i in held for uid in i['uids']})
    out = {'items': items, 'held': held, 'questions': questions, 'protective': protective, 'flagged_uids': flagged, 'incidents': res['incidents'], 'ran': res['ran'], 'messages': len(units),
           'excluded': (res.get('stance') or {}).get('excluded_total', 0), 'limits': res['limits'], 'live': be_safeguarding.live_safety_allowed(), 'missing': be_safeguarding.missing(),
           'catalogue': be_detectors.catalogue(), 'available': True}
    _CACHE.clear() if len(_CACHE) > 20 else None
    _CACHE[key] = out
    return out


def summary(aid) -> dict:
    """The small figure for the Home card: how many things are held, and how many questions and protective moves there are."""
    b = build(aid)
    return {'held': len(b['held']), 'questions': len(b['questions']), 'protective': len(b['protective'])}


def flagged_uids(aid) -> set:
    """The messages that carry a black flag on graphs and diagrams: the evidence of the held items."""
    return set(build(aid)['flagged_uids'])


def part_counts(stretch_count, units, flagged) -> list:
    """For each part of the chat (the parts Trends and the track graphs draw), how many held messages it holds, in the same order the measures use. [] when the parts do not line up."""
    import incidents
    import measures
    msgs, _timed = incidents.messages(units)
    spans = measures.cut(len(msgs))
    if not spans or len(spans) != stretch_count or not flagged:
        return []
    return [sum(1 for j in range(a, b) if msgs[j].get('uid') in flagged) for a, b in spans]


def flagged_texts(aid) -> list:
    """The words of the held messages, for marking the example quotes of the diagrams with the black flag."""
    by_id = {u['id']: u for u in store.units_for(aid)}
    return [' '.join(str(by_id[uid]['text'] or '').split()) for uid in flagged_uids(aid) if uid in by_id]
