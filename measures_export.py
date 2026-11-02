"""The measures, alerts and badges in the PDF and the CSV.

`csv_rows(ms)` returns the rows for the CSV, `pdf_pages(ms, story, p, page, table, drawing, small)` adds the pages to the PDF story. Both take the result of
`measures.build`. Neither reads a message: the module works from numbers and from the sentences `ms` already holds (they are written in measures.py, which is
the one place for wording). What is written here is only the names of blocks and columns and a one-line key for the charts.

The PDF gets two pages after the cover: Alerts and badges, then Measures over time. Every table is 504 points wide, the width of the page between its margins.
The charts are the Scenes of bei_charts, drawn with the `drawing` helper of report_export (its scale fits a 720 unit chart to 504 points). The four small
charts sit two by two, so they are built in the phone layout (360 units) and a little narrower still, so that each drawing is no wider than its half.

With Incognito on, no clock time may appear in any of it. This module never formats a time: the names of the parts of the chat come from measures.py
(`label`, `axis`), which leaves times out under Incognito. As a second guard, a name that still held one would be cut back to 'Part 3 of 9'.
"""
import re

from reportlab.lib import colors
from reportlab.platypus import KeepTogether, Spacer, Table, TableStyle

import bei_charts
import measures
from vibe_check.charts import NARROW_W, W, Scene
from vibe_check.common import long_date

HOW = 'How to read this'
CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')
NOTES_ABOUT_ALERTS = ('no_vibe', 'not_alike')   # notes about the alerts and about the comparison; every other note is about the basis of the lines over time
CHART_KEY = 'Lines show messages per 100. Bands mark Rare, Some and Often. A darker band is a heated moment.'     # one line: 438 of the 492 points of text width
RULE = colors.HexColor('#D8D8D8')               # the light line of the report's tables

# the PDF: widths in points. Each row of widths adds up to 504.
ALERT_WIDTHS = [56, 66, 100, 146, 48, 88]
BADGE_WIDTHS = [96, 56, 352]
SINCE_WIDTHS = [160, 110, 110, 124]
SINCE_WIDTHS_NO_CALL = [208, 148, 148]
CARD_W, GUTTER = 240, 24                         # two cards side by side: 240 + 24 + 240
PAGE_W = 504


def _priority(level) -> str:
    return measures.LEVEL_NAMES.get(level, str(level).title())


def _blank(value):
    """An empty string for None: an empty cell of a CSV row or of a table."""
    return '' if value is None else value


def _label(stretch, incognito) -> str:
    """The name of a part of the chat, as measures.py built it. A clock time never goes out under Incognito: should one turn up, only 'Part 3 of 9' is kept."""
    text = str(stretch.get('label') or '')
    return text.split(': ')[0] if incognito and CLOCK.search(text) else text


def _parts(m) -> list:
    """The parts of the chat for a chart. Under Incognito, an axis label with a clock time in it is left blank."""
    if not m.get('incognito'):
        return m['stretches']
    return [dict(s, axis='' if CLOCK.search(str(s.get('axis') or '')) else s.get('axis'), label=_label(s, True)) for s in m['stretches']]


def _notes_for_lines(ms) -> list:
    return [n['text'] for n in ms.get('notes') or [] if n.get('key') not in NOTES_ABOUT_ALERTS]


def _since_caveat(ms) -> str:
    return ms['caveats'].get('since') or ms['caveats']['measures']      # the comparison is of these same measures, so it reads with the same caveat


# ---- the CSV --------------------------------------------------------------------------------------
def csv_rows(ms) -> list:
    """The blocks of this feature as rows, each ending with an empty row: Alerts, Measures over time, Since your last check, Badges. Every block opens with its
    title row and a 'How to read this' row that carries its caveat. Figures are numbers, as the page prints them (whole numbers)."""
    return _alert_rows(ms) + _measure_rows(ms) + _since_rows(ms) + _badge_rows(ms)


def _alert_rows(ms) -> list:
    c = ms['caveats']
    rows = [['Alerts'], [HOW, c['alerts']]]
    if ms['alerts']:
        rows.append(['Priority', 'Alert', 'What we saw', 'Try this', 'Where', 'Based on'])
        rows += [[_priority(a['level']), a['title'], a['detail'], a['try'], a.get('when') or '', a['base']] for a in ms['alerts']]
    else:
        rows.append(['No alerts', c['empty']])
    rows += [['Note', n['text']] for n in ms.get('notes') or []]
    return rows + [[]]


def _measure_rows(ms) -> list:
    m = ms['m']
    rows = [['Measures over time'], [HOW, ms['caveats']['measures']], ['Basis', ms['basis_text']]]
    rows += [['Note', text] for text in _notes_for_lines(ms)]
    rows.append(['Measure', 'Level', 'Count', 'Of', 'Trend', 'What we saw', 'Note', 'What it means', 'Latest part'])
    for c in ms['cards']:
        rows.append([c['name'], c['level'], _blank(c.get('count')), _blank(c.get('of')), c.get('trend') or '', c['headline'], c.get('note') or '', c['meaning'],
                     c.get('latest') or ''])
    if m['stretches']:
        rows += [[], ['Part of the chat', 'Messages', f'{measures.NAMES["conflict"]} per 100 messages', f'{measures.NAMES["empathy"]} per 100 messages',
                      f'{measures.NAMES["passive"]} per 100 messages', 'Heated per 100 messages', 'Counted as a heated moment', 'Health score']]
        for s in m['stretches']:
            r = s['rates']
            rows.append([_label(s, m.get('incognito')), s['n'], measures.whole(r['hostile']), measures.whole(r['care']), measures.whole(r['dig']), measures.whole(r['heated']),
                         'Yes' if s.get('counted') else 'No', measures.whole(s['health'])])
    h = ms['health']
    rows += [[], ['Health score', measures.whole(h['score']), h['status']]]
    if h.get('trend'):
        rows.append(['Health score trend', h['trend']])
    rows.append(['About the health score', h['caption']])
    return rows + [[]]


def _since_rows(ms) -> list:
    s = ms.get('since')
    if not s:
        return []                                              # the first check has nothing to compare with
    rows = [['Since your last check'], [HOW, _since_caveat(ms)]]
    if s['comparable']:
        rows.append(['Measure', 'Last check', 'This check', 'Change'])
        rows += [[r['label'], r['before'], r['now'], r['verdict']] for r in s['rows']]
    else:
        rows += [['No comparison', s['why']], ['Measure', 'Last check', 'This check']]
        rows += [[r['label'], r['before'], r['now']] for r in s['rows']]
    trail = ms.get('trail') or []
    if len(trail) >= 3:                                        # the table behind the check-by-check chart, which also starts at the third check
        rows += [[], ['Check', 'Date', 'Messages rated', 'Health score', f'{measures.NAMES["conflict"]} messages', f'{measures.NAMES["empathy"]} messages',
                      f'{measures.NAMES["passive"]} messages', 'Heated messages', 'Heated moments']]
        for i, r in enumerate(trail, 1):                         # the day the check was made, never the time of day
            rows.append([f'Check {i}' + (' (this check)' if i == len(trail) else ''), long_date(r.get('created_at')), _blank(r.get('n')),
                         measures.whole(r['health']) if r.get('health') is not None else '', _blank(r.get('hostile_n')), _blank(r.get('care_n')), _blank(r.get('dig_n')),
                         _blank(r.get('heated_n')), _blank(r.get('episodes'))])
    return rows + [[]]


def _badge_rows(ms) -> list:
    rows = [['Badges'], [HOW, ms['caveats']['badges']]]
    if ms.get('badge_note'):
        rows.append(['Note', ms['badge_note']])
    rows.append(['Badge', 'Status', 'Why or what it needs', 'How it works'])
    for b in ms['badges']:
        rows.append([b['name'], 'Earned' if b['earned'] else 'Not yet', (b['evidence'] if b['earned'] else b['needs']) or '', b['how']])
    return rows + [[]]


# ---- the PDF --------------------------------------------------------------------------------------
def pdf_pages(ms, story, p, page, table, drawing, small=None) -> None:
    """Adds 'Alerts and badges' and 'Measures over time' to the story. `p(text, style)` makes a paragraph (and is the only way a string gets in), `page(title)` starts
    a page with its heading, `table(rows, widths)` adds a table, `drawing(scene)` turns a chart into a drawing and `small(text)` makes a note in small type."""
    small = small or (lambda text: p(text, 'BeiSmall'))
    _alerts_page(ms, story, p, page, table, small)
    _lines_page(ms, story, p, page, table, _drawer(drawing), small)


def _drawer(drawing):
    """`draw(make, base, max_w)`: a chart as a drawing no wider than `max_w` points, or None when the chart has nothing to draw. `make(width)` builds its Scene.
    The scene is `base` units wide, or narrower when the drawing would come out wider than `max_w`: the text keeps its size and only the plot gets shorter.
    How many points a unit is comes from drawing a tiny empty scene once."""
    scale = drawing(Scene(100, 10, 'scale', '')).width / 100 or 1.0

    def draw(make, base, max_w):
        scene = make(min(base, max_w / scale))
        return None if scene is None else drawing(scene)
    return draw


def _cells(rows, p) -> list:
    """The rows of a table with every cell made by p(): these tables are narrow, so their cells wrap at spaces (the style BeiCell), never in the middle of a word."""
    return [[p(text, 'BeiCell') for text in row] for row in rows]


def _alerts_page(ms, story, p, page, table, small) -> None:
    """Nothing small trails a table: a note or a caveat set after a table can land alone on the next page. So the notes come before the alerts table (after the
    sentence for an empty list), and the caveat of the badges and its note come before the badges table."""
    c = ms['caveats']
    page('Alerts and badges')
    story.append(p(c['alerts']))
    story.append(p('Alerts', 'BeiSub'))
    notes = [small(n['text']) for n in ms.get('notes') or []]
    if ms['alerts']:
        rows = [['Priority', 'Alert', 'What we saw', 'Try this', 'Where', 'Based on']]
        rows += [[f'**{_priority(a["level"])}**', f'**{a["title"]}**', a['detail'], a['try'], a.get('when') or '', a['base']] for a in ms['alerts']]
        story += notes
        table(_cells(rows, p), ALERT_WIDTHS)
    else:
        story += [p(c['empty'])] + notes
    first = len(story)
    story.append(p('Badges', 'BeiSub'))
    story.append(p(c['badges']))
    if ms.get('badge_note'):
        story.append(small(ms['badge_note']))
    rows = [['Badge', 'Status', 'Why or what it needs']]
    for b in ms['badges']:
        if b['earned']:
            rows.append([f'**{b["name"]}**', '**Earned**', b['evidence']])                      # the figure behind it
        else:
            rows.append([b['name'], 'Not yet', ' '.join(x for x in (b['needs'], b['how']) if x)])      # what is missing, and the rule
    table(_cells(rows, p), BADGE_WIDTHS)
    story[first:] = [KeepTogether(story[first:])]               # the badges stay on one page: a table of seven rows is read as a whole


def _bands(card, m):
    """The cut-offs behind Rare, Some and Often for a chart: (low, high) per 100 messages. Conflict and Passive-aggressive are raised under Low sensitivity. None for
    Escalation, which has no level words by rate."""
    low_high = measures.CUTS.get(card['kind'])
    if not low_high:
        return None
    scale = m['scale'] if card['kind'] in measures.SCALED else 1.0
    return low_high[0] * scale, low_high[1] * scale


def _lines_page(ms, story, p, page, table, draw, small) -> None:
    m = ms['m']
    page('Measures over time')
    story.append(p(ms['caveats']['measures']))
    story.append(p(ms['basis_text']))
    story += [small(text) for text in _notes_for_lines(ms)]
    if m['has_series']:
        story.append(small(CHART_KEY))
    story.append(_cards(ms, p, draw))
    story.append(_health(ms, p, draw))
    _since(ms, story, p, table)
    d = draw(lambda w: bei_charts.trail_chart(ms.get('trail') or [], 'health', title='Health score, check by check', series='Health score', width=w), W, PAGE_W)
    if d is not None:
        story.append(KeepTogether([p('Health score, check by check', 'BeiSub'), d]))


def _cards(ms, p, draw):
    """The four measures two by two: its name and level word, what we saw and the trend, then its small chart. (What each measure means is in the caveat above
    them, and in the CSV.) The words of a row sit in one table row and the charts of that row in the next, so the two charts of a row start at the same height
    whatever the words above them take."""
    m, parts, texts, charts = ms['m'], _parts(ms['m']), [], []
    for card in ms['cards']:
        cell = [p(f'{card["name"]}: {card["level"]}', 'BeiCardHead'), p(card['headline'], 'BeiCardText')]
        texts.append(cell + [p(text, 'BeiCardNote') for text in (card.get('trend'), card.get('note')) if text])
        charts.append(draw(lambda w, card=card: bei_charts.measure_chart(card, parts, _bands(card, m), width=w), NARROW_W, CARD_W) if m['has_series'] else None)
    rows, style = [], [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                       ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]
    for i in range(0, len(texts), 2):
        pair = list(range(i, min(i + 2, len(texts))))
        top = len(rows)
        rows.append([texts[pair[0]], '', texts[pair[1]] if len(pair) > 1 else ''])
        style += [('LINEABOVE', (0, top), (0, top), .4, RULE), ('LINEABOVE', (2, top), (2, top), .4, RULE), ('TOPPADDING', (0, top), (-1, top), 5)]
        if any(charts[j] is not None for j in pair):
            rows.append([_blank(charts[pair[0]]), '', _blank(charts[pair[1]]) if len(pair) > 1 else ''])
            style += [('NOSPLIT', (0, top), (-1, top + 1)), ('TOPPADDING', (0, top + 1), (-1, top + 1), 2), ('BOTTOMPADDING', (0, top + 1), (-1, top + 1), 4)]
        else:
            style.append(('BOTTOMPADDING', (0, top), (-1, top), 6))
    grid = Table(rows, colWidths=[CARD_W, GUTTER, CARD_W], hAlign='LEFT')
    grid.setStyle(TableStyle(style))
    return grid


def _health(ms, p, draw):
    """The health score tile, set like a card: the number out of 100 on the left; on the right its name and status word, the meter, the caption and the trend."""
    h = ms['health']
    meter = draw(lambda w: bei_charts.health_meter(h['score'], h['tone'], width=w), 500, 380)
    side = [p(f'{h["name"]}: {h["status"]}', 'BeiCardHead')] + ([meter, Spacer(1, 2)] if meter is not None else []) + [p(h['caption'], 'BeiCardNote')]
    if h.get('trend'):
        side.append(p(h['trend'], 'BeiCardNote'))
    tile = Table([[[p(measures.whole(h['score']), 'BeiBig'), p('out of 100', 'BeiCardNote')], side]], colWidths=[110, PAGE_W - 110], hAlign='LEFT')
    tile.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 4), ('LINEABOVE', (0, 0), (-1, 0), .4, RULE)]))
    return tile


def _since(ms, story, p, table) -> None:
    s = ms.get('since')
    if not s:
        return
    first = len(story)
    story.append(p('Since your last check', 'BeiSub'))
    if s['comparable']:
        rows = [['Measure', 'Last check', 'This check', 'Change']] + [[r['label'], str(r['before']), str(r['now']), r['verdict']] for r in s['rows']]
        table(_cells(rows, p), SINCE_WIDTHS)
    else:                                                       # two checks that were not made alike: the reason, and no call on any change
        story.append(p(f'**No comparison.** {s["why"]}'))
        rows = [['Measure', 'Last check', 'This check']] + [[r['label'], str(r['before']), str(r['now'])] for r in s['rows']]
        table(_cells(rows, p), SINCE_WIDTHS_NO_CALL)
    story[first:] = [KeepTogether(story[first:])]               # the heading, the reason and the five rows stay on one page
