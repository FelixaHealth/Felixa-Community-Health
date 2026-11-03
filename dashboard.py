"""The results dashboard: every check of every workplace in one place, with filters for the workplace, the kind of community, the platform and the dates.

It reads only what the app already keeps after each check: the numbers of `measure_history` (health score, rates per 100 messages, members, messages rated)
and the scores of `vibe_history` (Vibe Check score and its friendly, neutral and unfriendly counts). No message, no name and no file name is read. When one
workplace is chosen, its latest alerts and badges and its signal profile come from the same report the workplace page shows.

`build(args)` takes the query parameters of the page and returns what `templates/dashboard.html` prints. Every chart is a Scene of the same kind the report uses
(`vibe_check.charts`), drawn in a wide and a phone layout, with a table twin for a screen reader."""
import datetime as _dt
import html as _html

import measures
import store
from vibe_check import data_table, figure
from vibe_check.charts import Scene, nice_ticks
from vibe_check.common import long_date, parse_ts, short_date

RANGES = (('30', 'Last 30 days', 30), ('90', 'Last 90 days', 90), ('365', 'Last year', 365), ('all', 'All time', None))
CATEGORY_ROLES = ('cat1', 'cat2', 'cat3', 'cat4', 'cat5', 'cat6')        # one colour per workplace, in a fixed order; a seventh workplace repeats them with a marker shape
MAX_LINES = 6
MAX_ROWS = 300
BEHAVIOUR = (('conflict', 'Conflict', 'cat4'), ('empathy', 'Empathy', 'cat3'), ('passive', 'Passive-aggressive', 'cat2'), ('heated', 'Heated messages', 'cat5'))


# ---- reading the checks ---------------------------------------------------------------
def _per_100(count, n):
    return None if count is None or not n else 100.0 * count / n


def _vibe_near(when, snapshots):
    """The Vibe Check snapshot saved with a check: the one closest in time, within three hours (both are written in the same run)."""
    best, gap = None, 3 * 3600.0
    for snap in snapshots:
        stamp = parse_ts(snap.get('created_at'))
        if stamp is None:
            continue
        distance = abs((stamp - when).total_seconds())
        if distance <= gap:
            best, gap = snap, distance
    return best


def all_checks() -> list:
    """Every saved check of every workplace that has been set up, oldest first, one dict each."""
    out = []
    for w in store.workplace_names(limit=500):
        a = store.get_analysis(w['id'])
        if not a:
            continue
        snaps = store.vibe_history(w['id'])
        for rec in store.measure_history(w['id']):
            when = parse_ts(rec.get('created_at'))
            if when is None or rec.get('n') is None:
                continue
            snap = _vibe_near(when, snaps) or {}
            n = rec['n']
            out.append({'aid': w['id'], 'workplace': w['name'], 'platform': a['channel'] or '', 'kind': a['subject_type'] or '', 'when': when, 'n': n,
                        'members': rec.get('members'), 'health': rec.get('health'), 'vibe': snap.get('score'), 'friendly': snap.get('friendly'),
                        'neutral': snap.get('neutral'), 'unfriendly': snap.get('unfriendly'), 'flagged': snap.get('flagged_count'),
                        'conflict': _per_100(rec.get('hostile_n'), n), 'empathy': _per_100(rec.get('care_n'), n), 'passive': _per_100(rec.get('dig_n'), n),
                        'heated': _per_100(rec.get('heated_n'), n), 'episodes': rec.get('episodes')})
    out.sort(key=lambda r: (r['when'], r['aid']))
    return out


# ---- the filters ------------------------------------------------------------------------
def _day(text):
    try:
        return _dt.date.fromisoformat(str(text or '').strip())
    except ValueError:
        return None


def _platforms(text):
    """The platforms of a workplace, from its comma list."""
    return [p.strip() for p in str(text or '').split(',') if p.strip()]


def read_filters(args, rows, today=None) -> dict:
    """The filters as the page was asked for them, each checked against what exists: a workplace that is gone, or a platform nobody uses, falls back to All."""
    today = today or _dt.date.today()
    workplaces = sorted({(r['aid'], r['workplace']) for r in rows}, key=lambda x: (x[1].lower(), x[0]))
    ids = {str(a) for a, _ in workplaces}
    wp = str(args.get('workplace') or '')
    platforms = sorted({p for r in rows for p in _platforms(r['platform'])})
    kinds = sorted({r['kind'] for r in rows if r['kind']})
    platform, kind = str(args.get('platform') or ''), str(args.get('kind') or '')
    span = str(args.get('range') or 'all')
    start, end = _day(args.get('from')), _day(args.get('to'))
    days = next((d for key, _l, d in RANGES if key == span), None)
    if span not in {k for k, _l, _d in RANGES}:
        span = 'all'
    if start is None and end is None and days:
        start = today - _dt.timedelta(days=days)
    if start and end and end < start:
        start, end = end, start
    custom = bool(_day(args.get('from')) or _day(args.get('to')))
    return {'workplace': wp if wp in ids else '', 'platform': platform if platform in platforms else '', 'kind': kind if kind in kinds else '',
            'range': 'custom' if custom else span, 'start': start, 'end': end, 'from': str(args.get('from') or '') if custom else '', 'to': str(args.get('to') or '') if custom else '',
            'workplaces': workplaces, 'platforms': platforms, 'kinds': kinds}


def apply(rows, f) -> list:
    out = []
    for r in rows:
        if f['workplace'] and str(r['aid']) != f['workplace']:
            continue
        if f['platform'] and f['platform'] not in _platforms(r['platform']):
            continue
        if f['kind'] and r['kind'] != f['kind']:
            continue
        day = r['when'].date()
        if (f['start'] and day < f['start']) or (f['end'] and day > f['end']):
            continue
        out.append(r)
    return out


# ---- charts -------------------------------------------------------------------------------
def _fmt(v, places=0):
    return f'{v:.{places}f}' if places else f'{round(v):,}'


def lines(series, *, title, desc, y_min=0.0, y_max=100.0, ticks=None, places=0, unit='', width=720):
    """Several lines over time on one scale, one colour per line, a dot for each check, the value at the end of each line and a hover target on every dot.
    `series` = [{'name', 'role', 'points': [(datetime, value), ...]}]. None when there is nothing to draw."""
    series = [s for s in series if s['points']]
    stamps = [t for s in series for t, _v in s['points']]
    if not stamps:
        return None
    t0, t1 = min(stamps), max(stamps)
    span = max(1.0, (t1 - t0).total_seconds())
    narrow = width < 600
    height = 250 if narrow else 280
    ml, mr, mt, mb = 44, 54, 16, 44
    pw, ph = width - ml - mr, height - mt - mb
    ticks = ticks or [y_min + (y_max - y_min) * i / 4 for i in range(5)]
    X = lambda t: ml + (pw / 2 if t1 == t0 else pw * (t - t0).total_seconds() / span)       # noqa: E731
    Y = lambda v: mt + ph * (1 - (min(max(v, y_min), y_max) - y_min) / ((y_max - y_min) or 1))       # noqa: E731
    sc = Scene(width, height, title, desc)
    for t in ticks:
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == y_min else 'grid', 1)
        sc.text(ml - 8, Y(t) + 4, _fmt(t, places), size=12, fill='ink2', anchor='end')
    marks = sorted({t.date() for t in stamps})
    if len(marks) <= 1:
        shown = marks
    else:
        want = 3 if narrow else 6
        shown = [marks[round(i * (len(marks) - 1) / (want - 1))] for i in range(want)] if len(marks) > want else marks
    for day in dict.fromkeys(shown):
        x = X(_dt.datetime.combine(day, _dt.time(12)) if t1 != t0 else t0)
        sc.text(min(max(x, ml + 20), width - mr - 20), height - mb + 22, short_date(day.isoformat()), size=12, fill='ink2', anchor='middle')
    for i, s in enumerate(series):
        pts = [(X(t), Y(v)) for t, v in sorted(s['points'])]
        if len(pts) > 1:
            sc.poly(pts, stroke=s['role'], width=2)
        for (x, y), (t, v) in zip(pts, sorted(s['points'])):
            sc.circle(x, y, 5.5, 'surface')
            sc.circle(x, y, 3.5, s['role'])
            tip = {'t': f'{s["name"]} · {long_date(t.isoformat())}', 'r': [[title, f'{_fmt(v, places)}{unit}', s['role']]]}
            sc.hit(x - 12, mt, 24, ph, tip, x)
        x, y = pts[-1]
        sc.text(min(x + 9, width - 6), y + 4, _fmt(sorted(s['points'])[-1][1], places), size=12, fill='ink', weight=600)
    sc.cross(mt, mt + ph)
    return sc


def hbars(items, *, title, desc, unit='', vmax=None, places=0, width=720):
    """One horizontal bar per item, longest first: [(label, value, role)]. The value sits at the tip."""
    items = [i for i in items if i[1] is not None]
    if not items:
        return None
    items.sort(key=lambda i: -i[1])
    row_h, bar_h, top, label_w = 34, 18, 6, (190 if width >= 600 else 110)
    x0, x1 = label_w + 14, width - 70
    mx = vmax or max(max(v for _l, v, _r in items), 1e-9)
    sc = Scene(width, top + row_h * len(items) + 4, title, desc)
    sc.line(x0, top, x0, top + row_h * len(items), 'axis', 1)
    for i, (label, v, role) in enumerate(items):
        yc = top + i * row_h + row_h / 2
        length = max(4.0, (x1 - x0) * min(v, mx) / mx)
        sc.rect(x0, yc - bar_h / 2, length, bar_h, role, r=(0, 4, 4, 0), tip={'t': label, 'r': [[title, f'{_fmt(v, places)}{unit}', role]]})
        sc.text(label_w, yc + 4.5, (label if len(label) <= 24 else label[:23] + '…'), anchor='end', fill='ink2')
        sc.text(x0 + length + 8, yc + 4.5, f'{_fmt(v, places)}{unit}', fill='ink', weight=600)
    return sc


def _roles(names):
    """A fixed colour per workplace, by its place in the sorted list of all workplaces, so that filtering never repaints the ones that stay."""
    return {n: CATEGORY_ROLES[i % len(CATEGORY_ROLES)] for i, n in enumerate(names)}


# ---- the dashboard -------------------------------------------------------------------------
def _avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _latest_per_workplace(rows) -> list:
    last = {}
    for r in rows:
        last[r['aid']] = r
    return sorted(last.values(), key=lambda r: r['workplace'].lower())


def _cell(v, places=0, suffix=''):
    return '–' if v is None else f'{_fmt(v, places)}{suffix}'


def build(args, today=None) -> dict:
    everything = all_checks()
    f = read_filters(args, everything, today)
    rows = apply(everything, f)
    roles = _roles([name for _a, name in f['workplaces']])
    names = {a: n for a, n in f['workplaces']}
    latest = _latest_per_workplace(rows)
    multiple = len({r['aid'] for r in rows}) > 1
    one = f['workplace'] or (str(rows[0]['aid']) if rows and not multiple else '')

    # one line per workplace (the first six when there are more); one workplace gives its own few lines
    def per_workplace(key, floor=None):
        out = []
        for aid in dict.fromkeys(r['aid'] for r in rows):
            pts = [(r['when'], r[key]) for r in rows if r['aid'] == aid and r[key] is not None]
            if pts:
                out.append({'name': names.get(aid, '?'), 'role': roles.get(names.get(aid, '?'), 'cat1'), 'points': pts})
        return out[:MAX_LINES]

    figs, tables = {}, {}
    health = per_workplace('health')
    figs['health'] = figure(lambda w: lines(health, title='Health score', desc='Health score at each check, out of 100, one line for each workplace.', y_max=100, ticks=[0, 25, 50, 75, 100], unit='', width=w)) if health else ''
    vibe = per_workplace('vibe')
    figs['vibe'] = figure(lambda w: lines(vibe, title='Vibe Check score', desc='Vibe Check score at each check, out of 10.', y_max=10, ticks=[0, 2, 4, 6, 8, 10], places=1, unit='/10', width=w)) if vibe else ''
    members = per_workplace('members')
    top = nice_ticks(max([v for s in members for _t, v in s['points']] + [1]), 4) if members else [0, 1]
    figs['members'] = figure(lambda w: lines(members, title='Members', desc='Number of members at each check.', y_max=top[-1], ticks=top, width=w)) if members else ''
    behaviour = []
    if one:
        mine = [r for r in rows if str(r['aid']) == one]
        for key, label, role in BEHAVIOUR:
            pts = [(r['when'], r[key]) for r in mine if r[key] is not None]
            if pts:
                behaviour.append({'name': label, 'role': role, 'points': pts})
    else:
        for key, label, role in BEHAVIOUR:
            pts = []
            week = lambda r: r['when'].date() - _dt.timedelta(days=r['when'].weekday())      # noqa: E731  checks of different workplaces rarely fall on one day: average by week
            for start in sorted({week(r) for r in rows}):
                same = [r for r in rows if week(r) == start and r[key] is not None]
                if same:
                    pts.append((_dt.datetime.combine(start, _dt.time(12)), sum(r[key] * r['n'] for r in same) / sum(r['n'] for r in same)))
            if pts:
                behaviour.append({'name': label, 'role': role, 'points': pts})
    bmax = nice_ticks(max([v for s in behaviour for _t, v in s['points']] + [10]), 4) if behaviour else [0, 10]
    figs['behaviour'] = figure(lambda w: lines(behaviour, title='Messages per 100', desc='Messages per 100 that were openly hostile, caring, sarcastic or heated, at each check.',
                                              y_max=bmax[-1], ticks=bmax, width=w)) if behaviour else ''
    figs['compare_health'] = figure(lambda w: hbars([(r['workplace'], r['health'], roles.get(r['workplace'], 'cat1')) for r in latest], title='Health score',
                                                    desc='Health score of the latest check of each workplace, out of 100.', vmax=100, width=w)) if len(latest) > 1 else ''
    figs['compare_vibe'] = figure(lambda w: hbars([(r['workplace'], r['vibe'], roles.get(r['workplace'], 'cat1')) for r in latest], title='Vibe Check score',
                                                  desc='Vibe Check score of the latest check of each workplace, out of 10.', vmax=10, places=1, unit='/10', width=w)) if len(latest) > 1 else ''
    figs['compare_conflict'] = figure(lambda w: hbars([(r['workplace'], r['conflict'], roles.get(r['workplace'], 'cat1')) for r in latest], title='Openly hostile per 100',
                                                      desc='Messages per 100 that were openly hostile in the latest check of each workplace.', places=1, width=w)) if len(latest) > 1 else ''
    figs['compare_empathy'] = figure(lambda w: hbars([(r['workplace'], r['empathy'], roles.get(r['workplace'], 'cat1')) for r in latest], title='Care per 100',
                                                     desc='Messages per 100 that showed care in the latest check of each workplace.', places=1, width=w)) if len(latest) > 1 else ''

    checks = list(reversed(rows))[:MAX_ROWS]
    check_rows = [[long_date(r['when'].isoformat()), r['workplace'], r['platform'] or '–', f'{r["n"]:,}', _cell(r['members']), _cell(r['health']), _cell(r['vibe'], 1),
                   _cell(r['conflict'], 1), _cell(r['empathy'], 1), _cell(r['passive'], 1), _cell(r['heated'], 1), _cell(r['episodes'])] for r in checks]
    head = ['Date', 'Workplace', 'Platform', 'Messages rated', 'Members', 'Health score', 'Vibe Check', 'Hostile per 100', 'Care per 100', 'Sarcasm per 100', 'Heated per 100', 'Heated moments']
    tables['checks'] = data_table(head, check_rows) if check_rows else ''

    # the tiles
    health_now = _avg([r['health'] for r in latest])
    vibe_now = _avg([r['vibe'] for r in latest])
    first_last = None
    if one:
        mine = [r for r in rows if str(r['aid']) == one and r['health'] is not None]
        if len(mine) > 1:
            first_last = mine[-1]['health'] - mine[0]['health']
    members_now = sum(r['members'] or 0 for r in latest) if any(r['members'] for r in latest) else None
    kpis = [{'label': 'Workplaces', 'value': f'{len({r["aid"] for r in rows}):,}', 'sub': 'in this view'},
            {'label': 'Checks', 'value': f'{len(rows):,}', 'sub': f'{sum(r["n"] for r in rows):,} messages rated'},
            {'label': 'Health score', 'value': _cell(health_now) if health_now is None else f'{round(health_now)}', 'sub': ('average of the latest checks' if len(latest) > 1 else 'latest check')
             + ('' if first_last is None else f', {"up" if first_last > 0 else "down" if first_last < 0 else "no change"}{"" if first_last == 0 else f" {abs(round(first_last))}"} since the first'), 'band': measures.health_status(health_now) if health_now is not None else ''},
            {'label': 'Vibe Check', 'value': '–' if vibe_now is None else f'{vibe_now:.1f}', 'sub': 'out of 10, latest checks'},
            {'label': 'Members', 'value': '–' if members_now is None else f'{members_now:,}', 'sub': 'across the latest checks'}]

    latest_rows = [[r['workplace'], r['platform'] or '–', r['kind'] or '–', long_date(r['when'].isoformat()), f'{r["n"]:,}', _cell(r['members']), _cell(r['health']),
                    _cell(r['vibe'], 1), _cell(r['conflict'], 1), _cell(r['empathy'], 1), _cell(r['heated'], 1)] for r in latest]
    detail = None
    if one and rows:
        detail = {'aid': int(one), 'name': names.get(int(one), '')}
    return {'f': f, 'ranges': RANGES, 'kpis': kpis, 'figs': figs, 'tables': tables, 'latest': latest_rows, 'latest_head': ['Workplace', 'Platform', 'Kind', 'Last check', 'Messages', 'Members', 'Health', 'Vibe', 'Hostile /100', 'Care /100', 'Heated /100'],
            'checks_n': len(rows), 'shown_n': len(check_rows), 'legend': [(names[a], roles[names[a]]) for a in dict.fromkeys(r['aid'] for r in rows)][:MAX_LINES],
            'detail': detail, 'empty': not everything, 'none_in_view': bool(everything) and not rows, 'esc': _html.escape}
