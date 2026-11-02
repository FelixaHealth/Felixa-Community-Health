"""The measures, alerts and badges as HTML for the workplace page and the report page (the PDF and the CSV have their own: measures_export.py).

`html(ms, a)` takes the result of `measures.build` and the analysis row, and returns a dict of ready-to-print pieces for the templates
(`templates/_summary.html`, `_alerts.html`, `_over_time.html`, `_badges.html`):

    charts        {'escalation': figure, 'conflict': figure, 'empathy': figure, 'passive': figure}: one chart per measure card, in both layouts
                  (wide and phone), or '' while the check is too short to draw a line
    table         ONE table twin for all four charts: every part of the chat, its messages, the rates per 100 messages and the health score
    health        the health meter on its 0 to 100 track, in both layouts
    health_score  the health score as the whole number it is printed as (the status word is judged on the same number)
    trail         the check-by-check chart followed by its table, from the third check on, else ''
    icons         {'good': svg, 'info': svg, 'warn': svg, 'bad': svg}: the status icons of Vibe Check (a shape as well as a colour, never colour alone)
    levels        {'high': {'name': 'High priority', 'tone': 'bad'}, ...}: the words and the tone of the alert priorities (from measures.py)
    verdict_tone  {'Better': 'good', 'Worse': 'warn', 'About the same': 'info'}: the tone of each word of the "Since your last check" table

Only builder HTML is marked for output: `vibe_check.figure` (charts), `vibe_check.data_table` (tables, which escape every cell) and
`vibe_check.charts.icon_svg` (icons). The module reads numbers and the fixed wording of measures.py; no message, name or file name reaches it.
Under Incognito no clock time is printed: the part labels come ready-made from measures.py (`stretch['label']`), and the check-by-check table
names a check by its number and its date only.
"""
import re

import bei_charts
import measures
from vibe_check import data_table, figure
from vibe_check.charts import icon_svg
from vibe_check.common import long_date

TONES = ('good', 'info', 'warn', 'bad')
CLOCK_TIME = re.compile(r'\b\d{1,2}:\d{2}\b')        # an hour and a minute, as in "04:26 UTC"


def _bands(card, scale):
    """The (low, high) cut-offs behind Rare, Some and Often for a card's chart: measures.CUTS, with the Conflict and sarcasm ones raised at Low
    sensitivity. None for Escalation, which has no level cut-offs: its chart shades the heated moments instead."""
    cuts = measures.CUTS.get(card['kind'])
    if not cuts:
        return None
    return tuple(c * scale for c in cuts) if card['kind'] in measures.SCALED else tuple(cuts)


def _charts(ms) -> dict:
    m = ms['m']
    if not m['has_series']:
        return {card['key']: '' for card in ms['cards']}
    return {card['key']: figure(lambda w, c=card: bei_charts.measure_chart(c, m['stretches'], _bands(c, m['scale']), width=w)) for card in ms['cards']}


def _parts_table(ms) -> str:
    """The table twin of the four charts and the health score: one row for every part of the chat, with the label measures.py built for it."""
    m = ms['m']
    if not m['has_series']:
        return ''
    rows = [[s['label'], f'{s["n"]:,}', measures.whole(s['rates']['hostile']), measures.whole(s['rates']['care']), measures.whole(s['rates']['dig']),
             measures.whole(s['rates']['heated']), measures.whole(s['health'])] for s in m['stretches']]
    return data_table(['Part of the chat', 'Messages', 'Conflict per 100 messages', 'Empathy per 100 messages', 'Passive-aggressive per 100 messages',
                       'Heated per 100 messages', 'Health score'], rows, summary='View the four charts as a table')


def _of(r, kind) -> str:
    return f'{r.get(kind + "_n") or 0:,} of {r.get("n") or 0:,}'


def _trail(ms) -> str:
    """The health score check by check: the chart and its table, from the third check on."""
    trail = ms['trail']
    chart = figure(lambda w: bei_charts.trail_chart(trail, 'health', title='Health score, check by check', series='Health score', width=w))
    if not chart:
        return ''
    if ms['m']['incognito'] and CLOCK_TIME.search(chart):      # the chart names two checks of one day by the time they ran: Incognito shows no clock time, so only the table stays
        chart = ''
    rows = []
    for i, r in enumerate(trail, 1):
        rows.append([f'Check {i}' + (' (this check)' if i == len(trail) else ''), long_date(r.get('created_at')), f'{r.get("n") or 0:,}',
                     measures.whole(r.get('health') or 0), _of(r, 'hostile'), _of(r, 'care'), _of(r, 'dig'), _of(r, 'heated')])
    table = data_table(['Check', 'Date', 'Messages rated', 'Health score', 'Conflict', 'Empathy', 'Passive-aggressive', 'Heated messages'], rows,
                       summary='View the checks as a table')
    return chart + table


def html(ms, a=None) -> dict:
    if not ms:
        return {}
    return {'charts': _charts(ms), 'table': _parts_table(ms),
            'health': figure(lambda w: bei_charts.health_meter(ms['health']['score'], ms['health']['tone'], width=w)),
            'health_score': measures.whole(ms['health']['score']), 'trail': _trail(ms),
            'icons': {tone: icon_svg(tone) for tone in TONES},
            'levels': {level: {'name': measures.LEVEL_NAMES[level], 'tone': measures.LEVEL_TONE[level]} for level in measures.LEVELS},
            'verdict_tone': {measures.WORDS['better']: 'good', measures.WORDS['worse']: 'warn', measures.WORDS['same']: 'info'}}
