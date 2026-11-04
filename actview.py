"""The Badges & Alerts view of a report: five headline numbers with their change since an earlier check, the strengths (badges), the areas to review (alerts),
the five numbers side by side for two checks, and a one-paragraph insight. Everything is read from the report and from the numbers kept for earlier checks
(no message text is kept there). A change is in percentage points (pp)."""
import store

COMPARE = (('previous', 'Previous check'), ('first', 'First check'))
# (key, label, icon, polarity): polarity +1 means a rise is good news, -1 means a fall is
METRICS = (('vibe', 'Vibe Check', 'users', 1), ('health', 'Health Score', 'pulse', 1), ('sentiment', 'Sentiment', 'chart', 1), ('escalation', 'Escalation', 'trend', -1), ('conflict', 'Conflict', 'users', -1))
BADGE_STRENGTH = {'courteous': 'polite', 'supportive': 'care', 'encouraging': 'encouraging', 'lighthearted': 'funny'}
BADGE_ICON = {'courteous': 'users', 'supportive': 'heart', 'encouraging': 'chat', 'lighthearted': 'spark', 'calm': 'check', 'cools': 'trend', 'noflags': 'shield'}
ALERT_ICON = {'escalation': 'trend', 'heated': 'trend', 'conflict': 'users', 'hostile': 'users', 'sarcasm': 'chat', 'passive': 'chat', 'dig': 'chat'}
LEVEL_WORD = {'high': 'High', 'medium': 'Moderate'}


def _pct(a, b):
    return round(100.0 * a / b) if b else 0


def _current(ctx):
    ms, vibe = ctx.get('measures'), ctx['vibe'] if ctx.get('vibe') and ctx['vibe'].get('status') == 'ok' else None
    out = {}
    if vibe:
        out['vibe'] = round(vibe['score'] * 10)
        pct = (vibe.get('sentiment') or {}).get('pct') or {}
        out['sentiment'] = pct.get('friendly', 0)
    if ms:
        o, n = ms['m']['overall'], ms['m']['overall']['n']
        out['health'] = round(ms['health']['score'])
        out['escalation'] = _pct(o['heated'], n)
        out['conflict'] = _pct(o['hostile'], n)
    return out


def _earlier(aid, which):
    """The same five numbers from an earlier check, or {} when there is none. `which` is 'previous' or 'first'."""
    out = {}
    vh, mh = store.vibe_history(aid), store.measure_history(aid)
    pick = lambda items: (items[-2] if len(items) >= 2 else None) if which == 'previous' else (items[0] if len(items) >= 2 else None)      # noqa: E731
    v, m = pick(vh), pick(mh)
    if v:
        total = (v.get('friendly') or 0) + (v.get('neutral') or 0) + (v.get('unfriendly') or 0)
        out['vibe'] = round(v['score'] * 10)
        out['sentiment'] = _pct(v.get('friendly') or 0, total)
    if m:
        out['health'] = round(m['health'])
        out['escalation'] = _pct(m['heated_n'], m['n'])
        out['conflict'] = _pct(m['hostile_n'], m['n'])
    return out


def build(ctx, compare='previous'):
    if compare not in dict(COMPARE):
        compare = 'previous'
    aid, ms = ctx['aid'], ctx.get('measures')
    cur, prev = _current(ctx), _earlier(aid, compare)
    kpis = []
    for key, label, icon, pol in METRICS:
        if key not in cur:
            continue
        item = {'key': key, 'label': label, 'icon': icon, 'value': cur[key], 'unit': '/100' if key == 'health' else '%', 'delta': None, 'prev': prev.get(key)}
        if key in prev:
            d = cur[key] - prev[key]
            item['delta'] = {'pp': d, 'tone': 'same' if d == 0 else 'good' if d * pol > 0 else 'bad', 'dir': 'up' if d > 0 else 'down' if d < 0 else 'flat'}
        kpis.append(item)
    strengths = []
    if ms:
        o, n = ms['m']['overall'], ms['m']['overall']['n']
        for b in ms['badges']:
            if not b['earned']:
                continue
            kind = BADGE_STRENGTH.get(b['key'])
            if kind:
                pct = _pct(o[kind], n)
            elif b['key'] == 'calm':
                pct = 100 - _pct(o['heated'], n)
            elif b['key'] == 'noflags':
                pct = 100
            else:
                pct = 100
            strengths.append({'name': b['name'], 'pct': pct, 'icon': BADGE_ICON.get(b['key'], 'award'), 'evidence': b['evidence']})
        strengths.sort(key=lambda s: -s['pct'])
    alerts = []
    if ms:
        for a in ms['alerts']:
            if a['level'] == 'good':
                continue
            text = a['detail'].split('. ')[0].rstrip('.') + '.'
            alerts.append({'title': a['title'], 'sub': text[:110], 'level': a['level'], 'chip': LEVEL_WORD[a['level']], 'icon': next((v for k, v in ALERT_ICON.items() if k in a['key']), 'warning'),
                           'detail': a['detail'], 'try': a['try'], 'base': a.get('base', '')})
    cats = [(k, l) for k, l, _i, _p in METRICS if k in cur]
    trend = {'cats': [l for _k, l in cats], 'cur': [cur[k] for k, _l in cats], 'prev': [prev.get(k) for k, _l in cats] if any(k in prev for k, _l in cats) else None}
    return {'kpis': kpis, 'strengths': strengths[:3], 'strengths_more': max(0, len(strengths) - 3), 'alerts': alerts, 'trend': trend, 'insight': insight(kpis, strengths, alerts),
            'compare': compare, 'compare_options': COMPARE, 'compare_label': dict(COMPARE)[compare].lower(), 'has_prev': bool(prev)}


def insight(kpis, strengths, alerts):
    """One honest paragraph from the numbers. No cause is claimed and no person is judged."""
    moved = [k for k in kpis if k['delta']]
    if not moved:
        first = f' The strongest badge is {strengths[0]["name"]}.' if strengths else ''
        look = f' {len(alerts)} thing{"" if len(alerts) == 1 else "s"} to review.' if alerts else ' Nothing needs urgent attention.'
        return f'This is the first check, so it sets the baseline.{first}{look} Run another check to see what changes.'
    better = [k['label'] for k in moved if k['delta']['tone'] == 'good']
    worse = [k['label'] for k in moved if k['delta']['tone'] == 'bad']
    if better and not worse:
        text = 'Everything that moved went the right way: ' + ', '.join(better) + '.'
    elif worse and not better:
        text = 'Everything that moved went the wrong way: ' + ', '.join(worse) + '. Start with the alerts below.'
    elif better and worse:
        text = 'Mixed. Better: ' + ', '.join(better) + '. Worse: ' + ', '.join(worse) + '.'
    else:
        text = 'Nothing moved by a full point since the earlier check.'
    return text + ' This compares two checks of the same workplace and does not say why the numbers moved.'
