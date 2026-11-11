"""The Track graphs by timeframe or by incident, instead of equal slices of the chat.

`rebase(m, units, by, tf, gap, flagged)` builds new bars for the same chat and hands back a copy of the measures `m` whose `stretches` are those bars, so the flag rules, the
cards and the figures of the Track pages work on them unchanged:

* by timeframe: one bar for each stretch of time (5 minutes up to a month). Stretches with no message are left out. `tf` 0 picks the size that gives about 8 to 24 bars.
* by incident: one bar for each escalation incident (`incidents.find`), with its context messages. Its flag is the incident's own: yellow where escalation starts, red where it
  turns into conflict.

A chat without usable times has no timeframe view; it falls back to the incident view, and to the old equal slices only when there are fewer than two incidents too."""
import datetime as _dt

import deescalation
import incidents
import measures
from vibe_check.common import short_date

FRAMES = ((0, 'Auto'), (5, '5 min'), (30, '30 min'), (60, '1 hour'), (180, '3 hours'), (720, '12 hours'), (1440, '1 day'), (10080, '1 week'), (43200, '1 month'))
SIZES = tuple(v for v, _ in FRAMES if v)
MIN_AUTO, MAX_AUTO, MAX_BARS = 4, 24, 60


def frame_name(minutes) -> str:
    return next((label for v, label in FRAMES if v == minutes), f'{minutes} min')


def _ordered(units):
    complete = [u for u in (units or []) if all(a in (u.get('ratings') or {}) and u['ratings'][a] is not None for a in measures.analysis.ATTRIBUTES)]
    ordered, stamps, basis, _ = measures.order_units(complete)
    return ordered, stamps, basis == 'time'


def _groups(stamps, step):
    """[(bucket start, [positions])] for stretches of `step` minutes counted from midnight of the first day; the stretches with no message are left out."""
    origin = stamps[0].replace(hour=0, minute=0, second=0, microsecond=0)
    seen = {}
    for pos, s in enumerate(stamps):
        seen.setdefault(int((s - origin).total_seconds() // (step * 60)), []).append(pos)
    return [(origin + _dt.timedelta(minutes=step * k), seen[k]) for k in sorted(seen)]


def _pick(stamps, tf):
    """(step, groups, bumped): the stretch size asked for, or the next larger one while it would give more than MAX_BARS bars; Auto picks the size that gives 8 to 24 bars."""
    if not tf:
        for step in SIZES:
            g = _groups(stamps, step)
            if MIN_AUTO <= len(g) <= MAX_AUTO:
                return step, g, False
        g = _groups(stamps, SIZES[-1])
        return SIZES[-1], g, False
    step = tf if tf in SIZES else 60
    for size in [s for s in SIZES if s >= step]:
        g = _groups(stamps, size)
        if len(g) <= MAX_BARS:
            return size, g, size != step
    return SIZES[-1], _groups(stamps, SIZES[-1]), True


def _label(start, step, incognito, multi=False):
    """(axis label, full label) of a stretch starting at `start`."""
    end = start + _dt.timedelta(minutes=step)
    day = short_date(start.date().isoformat())
    if step >= 1440:
        if step == 1440:
            return day, day
        last = short_date((end - _dt.timedelta(days=1)).date().isoformat())
        return day, f'{day} to {last}'
    if incognito:
        return day, day
    span = f'{start:%H:%M} to {end:%H:%M}'
    return (f'{day} {start:%H:%M}' if multi else f'{start:%H:%M}'), f'{day} {span}'


def _bar(i, positions, flags, label, axis, first, last, incognito, timed):
    t = measures.tally([flags[p] for p in positions])
    return {'i': i, 'n': t['n'], 'axis': axis, 'label': label, 'health': measures.health_mean(t),
            'start': (first.date().isoformat() if incognito else first.isoformat()) if timed else None,
            'end': (last.date().isoformat() if incognito else last.isoformat()) if timed else None,
            'counts': {x: t[x] for x in measures.KINDS}, 'rates': {x: measures.rate(t, x) for x in measures.KINDS}, 'climb': 0.0, 'counted': False}


def _thin(stretches):
    """Keep an axis label on about every sixth bar, so the labels never run together."""
    every = max(1, -(-len(stretches) // 7))
    for i, s in enumerate(stretches):
        if i % every:
            s['axis'] = ''
    return stretches


def rebase(m, units, by='time', tf=0, gap=incidents.DEFAULT_GAP, flagged=()) -> dict:
    """{'m', 'de', 'black', 'green', 'positions', 'by', 'used', 'note'}: the measures with new bars, and what the other lines of the Trends graph need for the same bars.
    When the new bars cannot be built, `m` is the original and `note` says why."""
    ordered, stamps, timed = _ordered(units)
    out = {'m': m, 'de': [], 'black': [], 'green': [], 'positions': None, 'by': 'parts', 'used': 0, 'note': '', 'bumped': False}
    if not ordered:
        return out
    flags = [measures.message_flags(u['ratings']) for u in ordered]
    msgs, _ = incidents.messages(units)
    incognito = bool(m.get('incognito'))
    note = ''
    groups = None
    if by == 'time' and timed:
        step, g, bumped = _pick(stamps, tf)
        if len(g) >= 2:
            groups, out['used'], out['bumped'] = g, step, bumped
        else:
            note = 'Everything happened in one stretch of time, so the incident view is shown.'
            by = 'incident'
    elif by == 'time':
        note = 'This chat has no usable times, so the timeframe view is not available. The incident view is shown.'
        by = 'incident'
    levels = None
    if by == 'incident':
        found = incidents.find(units, gap)
        if len(found) >= 2:
            groups, levels = [], []
            pos_of = {u.get('id'): p for p, u in enumerate(ordered)}
            for inc in found:
                groups.append((inc, [pos_of[r['uid']] for r in inc['rows'] if r.get('uid') in pos_of]))
                levels.append(inc['level'])
        else:
            note = (note + ' ' if note else '') + 'Fewer than two incidents were found, so the chat is shown in equal parts.'
    if not groups:
        out['note'] = note
        return out
    multi = timed and stamps[0].date() != stamps[-1].date()
    stretches = []
    for i, (head, positions) in enumerate(groups):
        if by == 'incident':
            inc = head
            first, last = ordered_stamp(stamps, positions, timed)
            label = f'Incident {inc["n"]}: ' + inc['range']
            axis = f'#{inc["n"]}'
        else:
            first = last = head
            axis, label = _label(head, out['used'], incognito, multi)
        stretches.append(_bar(i, positions, flags, label, axis, first, last, incognito, timed))
    if by != 'incident':
        _thin(stretches)
    hit = deescalation._reacting(msgs, timed)
    out['positions'] = [p for _, p in groups]
    out['de'] = [100.0 * sum(1 for p in pos if p in hit) / max(1, len(pos)) for pos in out['positions']]
    out['black'] = [sum(1 for p in pos if msgs[p].get('uid') in flagged) for pos in out['positions']] if flagged else []
    if by == 'incident':
        out['green'] = [1 if (inc['calm'] and inc['calm']['i'] in set(pos)) else 0 for (inc, pos) in groups]
    else:
        calm_at = [inc['calm']['i'] for inc in incidents.find(units, gap) if inc['calm']]
        out['green'] = [sum(1 for c in calm_at if c in set(pos)) for pos in out['positions']]
    if not any(out['green']):
        out['green'] = []
    if not any(out['black']):
        out['black'] = []
    m2 = dict(m, stretches=stretches, episodes=[], levels=levels, has_series=True, basis='time' if timed else 'order', by=by, positions=out['positions'])
    out.update(m=m2, by=by, note=note)
    return out


def ordered_stamp(stamps, positions, timed):
    if not timed or not positions:
        return None, None
    return stamps[min(positions)], stamps[max(positions)]


def messages(m, units, key, limit=15):
    """The exact messages behind each bar for Escalation (heated) or Conflict (openly hostile): {bar index: {'items', 'total'}}. {} for the equal parts: track.part_messages does those."""
    kind = {'escalation': 'heated', 'conflict': 'hostile'}.get(key)
    pos = m.get('positions')
    if not kind or not pos:
        return {}
    ordered, _, _ = _ordered(units)
    out = {}
    for i, group in enumerate(pos):
        hits = [' '.join(str(ordered[p]['text'] or '').split()) for p in group if measures.message_flags(ordered[p]['ratings'])[kind]]
        if hits:
            out[i] = {'items': hits[:limit], 'total': len(hits)}
    return out
