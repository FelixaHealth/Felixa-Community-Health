"""Answers to the questions the person set out to find out (the goals chosen when the workplace was made), written from the numbers of the results.

Each answer says plainly what the evidence shows, with the figures behind it, and what the messages cannot tell. Nothing here is a new rating: every figure is one the
results already hold. An answer describes wording in a chat, never a person, and never suggests a punishment."""
import json

import store
import workplace

HEALTHY = 'Is my community healthy?'
LEAVING = 'Why are people leaving?'
REVENUE = 'Why did my revenue go down?'
COMMUNICATE = 'How do people communicate?'
ENGAGED = 'Are people engaged?'


def _rate(m, kind):
    n = m['overall']['n'] or 1
    return 100.0 * m['overall'][kind] / n


def _per100(x):
    return f'{x:.0f} in every 100 messages' if x >= 1 else 'fewer than 1 in every 100 messages' if x > 0 else 'none'


def _dates(e):
    return e.get('dates') or e.get('label') or ''


def _moments(m, limit=3):
    out = []
    for e in sorted(m['episodes'], key=lambda e: -e['peak'])[:limit]:
        where = _dates(e)
        out.append(f'{where}: heated messages climbed {e["peak"]:.0f} points above the recent level, up to {e["reached"]:.0f} in every 100.')
    return out


def _tone_links(ctx, names):
    return [(n, f'/analysis/{ctx["aid"]}/tone/{n}') for n in names]


def healthy(ctx, m, ms):
    health = ms['health']
    vibe = ctx['vibe'] if ctx['vibe'] and ctx['vibe'].get('status') == 'ok' else None
    status = health['status']
    verdict = {'Excellent': 'Yes, very much.', 'Good': 'Mostly yes.', 'Fair': 'It is mixed.', 'Needs attention': 'Not yet. It needs attention.'}.get(status, 'It is mixed.')
    pts = [f'The health score is {health["score"]:.0f} on a scale of 0 to 100 ({status}). 50 is neutral and higher is warmer.']
    if vibe:
        pct = (vibe.get('sentiment') or {}).get('pct') or {}
        mix = f' {pct.get("friendly", 0)}% of messages read as friendly, {pct.get("neutral", 0)}% neutral and {pct.get("unfriendly", 0)}% unfriendly.' if pct else ''
        pts.append(f'Vibe Check scores the chat {vibe["score"]:g} out of 10 ({vibe.get("label", "")}).{mix}')
    pts.append(f'Open hostility shows in {_per100(_rate(m, "hostile"))}, and care and understanding in {_per100(_rate(m, "care"))}.')
    count = len(m['episodes'])
    if count:
        pts.append(f'The mood heated up in {count} moment{"" if count == 1 else "s"}. The clearest: ' + _moments(m, 1)[0])
    else:
        pts.append('No heated moments stand out beyond what chance would give.')
    if health.get('trend'):
        pts.append('Mood over time: ' + health['trend'])
    return {'verdict': verdict, 'points': pts, 'links': [('Open the Track tab', f'/analysis/{ctx["aid"]}/results#tab-track')],
            'caution': 'A language model made the ratings and people have not checked them yet. Use this to start a conversation, never to judge a person.'}


def leaving(ctx, m, ms):
    push, keep = [], []
    hostile, dig, care, polite = _rate(m, 'hostile'), _rate(m, 'dig'), _rate(m, 'care'), _rate(m, 'polite')
    if hostile >= 5:
        push.append(f'Open hostility: {_per100(hostile)} are aggressive, rude, disrespectful or judgmental.')
    if dig >= 5:
        push.append(f'Sarcastic digs: {_per100(dig)}.')
    for e in sorted(m['episodes'], key=lambda e: -e['peak'])[:3]:
        push.append(f'A heated moment in {_dates(e)}: heated messages climbed {e["peak"]:.0f} points above the recent level.')
    if care >= 3:
        keep.append(f'Care and understanding: {_per100(care)}.')
    if polite >= 10:
        keep.append(f'Politeness: {_per100(polite)}.')
    if _rate(m, 'encouraging') >= 3:
        keep.append(f'Encouragement: {_per100(_rate(m, "encouraging"))}.')
    verdict = 'The wording shows things that can push people away.' if push else 'Nothing in the wording stands out as pushing people away.'
    pts = ['May push people away: ' + x for x in push] or ['No open hostility, sarcastic digs or heated moments stand out in these messages.']
    pts += ['May keep people here: ' + x for x in keep] or ['Not much care, politeness or encouragement shows in these messages.']
    return {'verdict': verdict, 'points': pts, 'links': [('Read the toxic-wording messages', f'/analysis/{ctx["aid"]}/category/Toxic'), ('Open the Track tab', f'/analysis/{ctx["aid"]}/results#tab-track')],
            'caution': 'Messages show how people talk, not why someone left. To learn why, ask people who left, and compare when they left with the dates above.'}


def revenue(ctx, m, ms):
    pts, t = [], m['trend'].get('health')
    if t and m['verdicts']:
        word = {'rising': 'rose', 'falling': 'fell'}.get(t.get('call'), 'did not change clearly')
        pts.append(f'Across this chat the health score {word} (from {t["first"]:.0f} in the first third to {t["last"]:.0f} in the last third).')
    else:
        pts.append('There are too few messages on the timeline to say whether the tone rose or fell.')
    pts += _moments(m, 3) or ['No heated moments stand out in the dates of this chat.']
    pts.append('If revenue fell around any of these dates, the tone may be part of the story. If it did not, look at other causes first.')
    return {'verdict': 'The messages cannot say, because they hold no revenue figures. They can show whether the tone changed.', 'points': pts,
            'links': [('Open the Track tab', f'/analysis/{ctx["aid"]}/results#tab-track')],
            'caution': 'A change in tone that happens at the same time as a change in revenue is not proof that one caused the other. Compare with sales, prices and other changes.'}


def communicate(ctx, m, ms):
    metrics = [x for x in ctx['metrics'] if x['detection_rate'] > 0]
    top = sorted(metrics, key=lambda x: -x['fas'])[:3]
    fams = ctx['families']
    pts = []
    if top:
        pts.append('The tones that show most: ' + ', '.join(f'{x["attribute"]} ({x["detection_rate"]:.0f}% of messages)' for x in top) + '.')
    if fams:
        pts.append('By family, average strength out of 100: ' + ', '.join(f'{f["name"]} {f["avg_fas"]:.0f}' for f in fams) + '.')
    pts.append(f'{_per100(_rate(m, "polite"))} are clearly polite, {_per100(_rate(m, "funny"))} funny, {_per100(_rate(m, "care"))} caring and {_per100(_rate(m, "dig"))} sarcastic digs.')
    if ctx['a']['channel']:
        pts.append(f'They talk on {ctx["a"]["channel"]}.')
    lead = top[0]['attribute'].lower() if top else ''
    hostile = _rate(m, 'hostile')
    verdict = (f'Mostly {lead}. Openly hostile wording is ' + ('rare.' if hostile < 5 else 'present in some messages.' if hostile < 12 else 'common.')) if top else 'See the tones below.'
    return {'verdict': verdict, 'points': pts, 'links': _tone_links(ctx, [x['attribute'] for x in top]) + [('Open the signal profile', f'/analysis/{ctx["aid"]}/results#tab-signals')],
            'caution': 'These are the shares of messages that show each tone, from the wording alone.'}


def engaged(ctx, m, ms):
    span = store.timestamp_span(ctx['aid'])
    n = m['overall']['n']
    pts = []
    if span and (span[1] - span[0]).days >= 1:
        days = (span[1] - span[0]).days + 1
        pts.append(f'There are {n:,} messages over {days} days, about {n / days:.0f} a day.')
    else:
        pts.append(f'There are {n:,} messages in this check.')
    enc, care = _rate(m, 'encouraging'), _rate(m, 'care')
    pts.append(f'{_per100(enc)} encourage others, and {_per100(care)} show care. These are the messages that draw people in.')
    t = m['trend'].get('care')
    if t and m['verdicts'] and t.get('call') in ('rising', 'falling'):
        pts.append(f'Care in the chat is {t["call"]}: {t["first"]:.0f} in every 100 in the first third and {t["last"]:.0f} in the last third.')
    score = enc + care
    verdict = 'Yes, there are clear signs of it.' if score >= 12 else 'Some, but few messages invite others in.' if score >= 4 else 'Little shows in the wording.'
    return {'verdict': verdict, 'points': pts, 'links': _tone_links(ctx, ['Encouraging', 'Empathetic']),
            'caution': 'The messages show who spoke, not who read, reacted or stayed quiet. Reactions, replies and attendance would say more.'}


STANDARD = {HEALTHY: healthy, LEAVING: leaving, REVENUE: revenue, COMMUNICATE: communicate, ENGAGED: engaged}


def build(ctx):
    """[{'question', 'verdict', 'points', 'links', 'caution', 'standard'}] for each question chosen, in the order they were chosen, then the person's own words. [] when there is
    nothing to answer from."""
    ms = ctx.get('measures')
    if not ms or not ms.get('m') or not ctx.get('metrics'):
        return []
    m = ms['m']
    asked = [g for g in ctx['goals'] if g in STANDARD] + [g for g in ctx['goals'] if g not in STANDARD]
    out = []
    for q in asked:
        out.append({'question': q, 'standard': True, **STANDARD[q](ctx, m, ms)})
    own = (ctx['a']['goal_text'] or '').strip()
    if own:
        g = ctx.get('guidance') or {}
        pts = [g['summary']] if g.get('summary') else []
        pts += [f'{r.get("action")}' for r in (g.get('recommendations') or [])[:3] if r.get('action')]
        out.append({'question': own, 'standard': False, 'verdict': 'Here is what the evidence says that bears on it.' if pts else 'Run the full check to get guidance on it.',
                    'points': pts or ['The written guidance is made when the full check runs. It is written for your goals, including this one.'], 'links': [('Open the guidance', f'/analysis/{ctx["aid"]}/results#tab-guidance')],
                    'caution': 'This comes from the written guidance, which a language model wrote from the same measured numbers.'})
    return out
