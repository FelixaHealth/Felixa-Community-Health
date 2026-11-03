"""The detail pages of the results: one signal family (Positive, Tense, Toxic) or one of the 13 tones, with every message that shows it and a graph of it over time.

A message "shows" a tone when its rating is 1 or more (0 = not at all, 3 = strongly). The graph is the share of rated messages, per 100, that show the tone
on each day. Messages without a readable date have no timeline, so those pages show the totals only."""
import datetime as _dt

import analysis
import dashboard
import store
from vibe_check import figure
from vibe_check.common import parse_ts

PER_PAGE = 40
FAMILY_ROLE = {'Positive': 'good', 'Tense': 'warn', 'Toxic': 'crit'}
ROLES = ('cat1', 'cat2', 'cat3', 'cat4', 'cat5', 'cat6')


def family_attributes(name):
    return next((list(members) for fam, members in analysis.FAMILIES if fam == name), None)


def comments(aid, attrs, page=1):
    """Every message that shows at least one of `attrs`, strongest first: (rows, total, pages, page). A row has the text, date, source, and the tones with ratings."""
    marks = ','.join('?' * len(attrs))
    with store.db() as c:
        total = c.execute(f'SELECT COUNT(DISTINCT cu.id) n FROM communication_units cu JOIN unit_ratings ur ON ur.unit_id=cu.id '
                          f'WHERE cu.analysis_id=? AND ur.attribute IN ({marks}) AND ur.rating>=1', (aid, *attrs)).fetchone()['n']
        pages = max(1, -(-total // PER_PAGE))
        page = min(max(1, page), pages)
        ids = [r['id'] for r in c.execute(
            f'SELECT cu.id, MAX(ur.rating) top, SUM(ur.rating) s FROM communication_units cu JOIN unit_ratings ur ON ur.unit_id=cu.id '
            f'WHERE cu.analysis_id=? AND ur.attribute IN ({marks}) AND ur.rating>=1 GROUP BY cu.id ORDER BY top DESC, s DESC, cu.id LIMIT ? OFFSET ?',
            (aid, *attrs, PER_PAGE, (page - 1) * PER_PAGE))]
        rows = []
        for uid in ids:
            u = c.execute('SELECT text, timestamp, source_label FROM communication_units WHERE id=?', (uid,)).fetchone()
            tones = [(r['attribute'], r['rating']) for r in c.execute(
                f'SELECT attribute, rating FROM unit_ratings WHERE unit_id=? AND attribute IN ({marks}) AND rating>=1 ORDER BY rating DESC, attribute', (uid, *attrs))]
            when = parse_ts(u['timestamp']) if u['timestamp'] else None
            rows.append({'text': u['text'], 'when': when.strftime('%b %-d, %Y') if when else '', 'source': u['source_label'] or '', 'tones': tones, 'top': max(r for _a, r in tones)})
    return rows, total, pages, page


def daily_shares(aid, attrs):
    """{attribute: [(date as datetime, per-100 share of that day's rated messages showing it)]} and how many rated messages carried a date."""
    marks = ','.join('?' * len(attrs))
    with store.db() as c:
        rows = c.execute(f'SELECT cu.timestamp t, ur.attribute a, ur.rating r FROM communication_units cu JOIN unit_ratings ur ON ur.unit_id=cu.id '
                         f'WHERE cu.analysis_id=? AND ur.attribute IN ({marks}) AND cu.timestamp != ""', (aid, *attrs)).fetchall()
    per_day = {}
    for row in rows:
        when = parse_ts(row['t'])
        if when is None:
            continue
        cell = per_day.setdefault(row['a'], {}).setdefault(when.date(), [0, 0])
        cell[0] += 1
        cell[1] += 1 if row['r'] >= 1 else 0
    series = {a: sorted((_dt.datetime.combine(day, _dt.time(12)), 100.0 * hit / n) for day, (n, hit) in days.items()) for a, days in per_day.items()}
    dated = max((sum(n for n, _h in days.values()) for days in per_day.values()), default=0)
    return series, dated


def chart(aid, attrs, title):
    """The over-time figure of `attrs`, or ('', note) when the messages have no dates or only one day."""
    series, dated = daily_shares(aid, attrs)
    days = {t.date() for pts in series.values() for t, _v in pts}
    if not dated:
        return '', 'These messages carry no dates, so there is no timeline. The totals above still describe them.'
    if len(days) < 2:
        return '', 'All the dated messages are from one day, so there is nothing to draw over time yet. Add messages from other days or check again later.'
    top = max((v for pts in series.values() for _t, v in pts), default=10.0)
    ymax = 100.0 if top > 50 else 50.0 if top > 25 else 25.0 if top > 10 else 10.0
    lines = [{'name': a, 'role': ROLES[i % len(ROLES)], 'points': series[a]} for i, a in enumerate(attrs) if a in series]
    html = figure(lambda w: dashboard.lines(lines, title=title, desc=f'Share of messages per 100 that show {title.lower()} on each day.', y_max=ymax,
                                            ticks=[ymax * i / 5 for i in range(6)], unit=' per 100', width=w))
    return html, ''


def legend(attrs, series_attrs=None):
    return [(a, ROLES[i % len(ROLES)]) for i, a in enumerate(attrs)]
