"""The measures, alerts and badges in the PDF and the CSV.

`csv_rows(ms)` returns the rows for the CSV, `pdf_pages(ms, story, p, page, table, drawing, small)` adds the pages to the PDF story. Both take the result of
`measures.build`. Neither reads a message: the module works from numbers and from the sentences `ms` already holds (they are written in measures.py, which is
the one place for wording). What is written here is only the names of blocks and columns, and a few short headings.

The PDF gets two sections after the cover: Alerts and badges, then Measures over time. Every table is 492 points wide, the width of the text between the margins
(the page frame keeps 6 points of padding on each side of the 504 points between the margins). The charts are the Scenes of bei_charts, drawn with the `drawing`
helper of report_export (its scale fits a 720 unit chart to 492 points). The four small charts sit two by two, each as wide as its half and drawn at CHART_SHRINK of
their natural size, so that a first check (cards, health score tile and its sentence) fits one page when there are no notes.

The sentence that explains the lines and the bands of the four small charts is `ms['chart_key']`, printed as it is: this module has no wording of its own for it.
Each note is printed once, in the section it belongs to (`note['about']`: 'alerts' under Alerts, 'measures' under Measures over time). The reason two checks are
not alike is `ms['since']['why']`: it is printed once, in the Since your last check block.

A heading never stays alone at the foot of a page: a heading that opens a table that may run over several pages gets a CondPageBreak, sized from the real heights of
the heading and the first rows of the table, and the table itself splits between its rows with its header repeated. The badges are one block (heading, caveat,
notes and the seven rows): it starts on the page where it fits whole and moves to the next page when it does not, so that no page holds a few rows of it. The health
score tile never starts a page: the second row of cards stays with it, and on a first check 'Since your last check' and its sentence follow in the same block.

With Incognito on, no clock time may appear in any of it. This module never formats a time: the names of the parts of the chat come from measures.py
(`label`, `axis`), which leaves times out under Incognito. As a second guard, a name that still held one would be cut back to 'Part 3 of 9'. The days of the
checks in the CSV are dates only.
"""
import re

from reportlab.lib import colors
from reportlab.platypus import CondPageBreak, KeepTogether, Spacer, Table, TableStyle

import bei_charts
import measures
from vibe_check.charts import W, Scene
from vibe_check.common import parse_ts

HOW = 'How to read this'
MARK = 'Mark'                                   # the last column of the cards in the CSV: the card's mark in words (the CSV cannot show the icon)
CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')
RULE = colors.HexColor('#D8D8D8')               # the light line of the report's tables

# the PDF: widths in points. Each row of widths adds up to PAGE_W.
PAGE_W = 492                                     # the text between the margins: the 504 points of the page, less 6 points of padding on each side of the frame
ALERT_WIDTHS = [56, 64, 98, 132, 64, 78]
BADGE_WIDTHS = [92, 56, 344]
SINCE_WIDTHS = [156, 108, 108, 120]
SINCE_WIDTHS_NO_CALL = [204, 144, 144]
CARD_W, GUTTER = 238, 16                         # two cards side by side: 238 + 16 + 238
TILE_NUMBER_W = 110                              # the width of the big number of the health score tile
CHART_SHRINK = 0.8                               # the four small charts are drawn at this share of their natural size: 168 units tall come out at 92 points, 14 units of text at 7.7
SCENE_NARROW = 599                               # units: the widest chart that bei_charts still lays out in its phone layout (it changes layout at 600)
MAX_ROOM = 650                                   # a heading never asks for more room than this: a start taller than a page (666 points) could be met on no page at all
FIRST_ROWS = 120                                 # what a heading and the first rows of a table are taken to need when the real height cannot be read
OUT_OF_HEALTH = 100                              # the health score is a figure out of 100: that is the 'out of' of its row in the CSV
OF = re.compile(r'^\s*([\d,]+)\s+of\s+([\d,]+)\s*$')       # 'x of n' as measures.since_table prints it
MONTH_DAY = re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})\b')
NBSP = '\u00a0'


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


def _about(note) -> str:
    """The section a note belongs to, 'alerts' or 'measures'. measures.py says it (`about`); a note without the word is judged by its key, and one that is
    about anything else is shown with the measures, so that no note is lost."""
    about = note.get('about') or getattr(measures, 'NOTE_ABOUT', {'no_vibe': 'alerts'}).get(note.get('key'), 'measures')
    return about if about == 'alerts' else 'measures'


def _notes(ms, about) -> list:
    """The sentences of the notes of one section. Each note belongs to one section, so a surface prints it once."""
    return [n['text'] for n in ms.get('notes') or [] if _about(n) == about]


def _since_caveat(ms) -> str:
    return ms['caveats'].get('since') or ms['caveats']['measures']      # the comparison is of these same measures, so it reads with the same caveat should there be none of its own


def _first_check(ms) -> str:
    return (ms.get('texts') or {}).get('first_check') or measures.FIRST_CHECK


def _no_badges(ms) -> str:
    return (ms.get('texts') or {}).get('no_badges') or measures.NO_BADGES


def _tone(item) -> str:
    """The icon of a card or of the health tile in words, as measures.py names it: the PDF and the CSV cannot show the icon."""
    return item.get('tone_word') or measures.TONE_WORDS.get(item.get('tone'), '')


def _no_badge_earned(ms) -> bool:
    return not any(b.get('earned') for b in ms['badges'])


# ---- the CSV --------------------------------------------------------------------------------------
def csv_rows(ms) -> list:
    """The blocks of this feature as rows, each ending with an empty row: Alerts, Measures over time, Since your last check, Badges. Every block opens with
    its title row and a 'How to read this' row that carries its caveat. Figures are numbers, as the page prints them (whole numbers), and a count that the
    page writes as 'x of n' is two numbers (a count the store had to drop stays the words 'Not known'). A note is written once, in the block it belongs to. The
    icon of a card and of the health score is written in words in its last cell (the column Mark), from `tone_word`. The reason two checks are not alike is
    written once, in the Since your last check block."""
    return _alert_rows(ms) + _measure_rows(ms) + _since_rows(ms) + _badge_rows(ms)


def _alert_rows(ms) -> list:
    c = ms['caveats']
    rows = [['Alerts'], [HOW, c['alerts']]]
    if ms['alerts']:
        rows.append(['Priority', 'Alert', 'What we saw', 'Try this', 'Where', 'Based on'])
        rows += [[_priority(a['level']), a['title'], a['detail'], a['try'], a.get('when') or '', a['base']] for a in ms['alerts']]
    else:
        rows.append(['No alerts', c['empty']])
    rows += [['Note', text] for text in _notes(ms, 'alerts')]
    return rows + [[]]


def _measure_rows(ms) -> list:
    m = ms['m']
    rows = [['Measures over time'], [HOW, ms['caveats']['measures']], ['Basis', ms['basis_text']]]
    rows += [['Note', text] for text in _notes(ms, 'measures')]
    rows.append(['Measure', 'Level', 'Count (messages; heated moments for Escalation)', 'Out of (messages rated)', 'Trend', 'What we saw', 'Note', 'What it means',
                 'Level in the latest part', MARK])
    for c in ms['cards']:           # a card that could not be judged has no count (None): an empty cell, never a 0 that a spreadsheet would read as 'none'
        rows.append([c['name'], c['level'], _blank(c.get('count')), _blank(c.get('of')), c.get('trend') or '', c['headline'], c.get('note') or '', c['meaning'],
                     c.get('latest') or '', _tone(c)])
    if m['stretches']:
        rows += [[], ['Part of the chat', 'Messages', f'{measures.NAMES["conflict"]} per 100 messages', f'{measures.NAMES["empathy"]} per 100 messages',
                      f'{measures.NAMES["passive"]} per 100 messages', 'Heated per 100 messages', 'Counted as a heated moment', 'Health score']]
        for s in m['stretches']:
            r = s['rates']
            rows.append([_label(s, m.get('incognito')), s['n'], measures.whole(r['hostile']), measures.whole(r['care']), measures.whole(r['dig']), measures.whole(r['heated']),
                         'Yes' if s.get('counted') else 'No', measures.whole(s['health'])])
    h = ms['health']
    rows += [[], ['Health score', measures.whole(h['score']), h['status'], _tone(h)]]          # the score, its status word and its mark in words
    if h.get('trend'):
        rows.append(['Health score trend', h['trend']])
    rows.append(['About the health score', h['caption']])
    return rows + [[]]


def _count_of(value) -> list:
    """[count, out of] for a cell of the Since table. The page prints 'x of n' for a count of messages, and a whole number for the health score (out of 100).
    Both become numbers, so a spreadsheet can sort and add them. A cell that is neither is kept as it is, with nothing out of it."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [value, OUT_OF_HEALTH]
    found = OF.match(str(value))
    if found:
        return [int(found.group(1).replace(',', '')), int(found.group(2).replace(',', ''))]
    return [_blank(value), '']


def _iso_day(value) -> str:
    """The day of a check as YYYY-MM-DD, never a time of day. (The Vibe Check blocks of the same file write ISO dates too.)"""
    d = parse_ts(value)
    return d.date().isoformat() if d else str(value or '')[:10]


def _since_rows(ms) -> list:
    s = ms.get('since')
    rows = [['Since your last check'], [HOW, _since_caveat(ms)]]
    if not s:
        return rows + [['First check', _first_check(ms)], []]          # the first check has nothing to compare with, and the page says so
    if s['comparable']:
        rows.append(['Measure', 'Last check', 'Last check: out of', 'This check', 'This check: out of', 'Change'])
        rows += [[r['label'], *_count_of(r['before']), *_count_of(r['now']), r['verdict']] for r in s['rows']]
    else:
        rows += [['No comparison', s['why']], ['Measure', 'Last check', 'Last check: out of', 'This check', 'This check: out of']]
        rows += [[r['label'], *_count_of(r['before']), *_count_of(r['now'])] for r in s['rows']]
    trail = ms.get('trail') or []
    if len(trail) >= 3:                                        # the table behind the check-by-check chart, which also starts at the third check
        rows += [[], ['Check', 'Date', 'Messages rated', 'Health score', f'{measures.NAMES["conflict"]} messages', f'{measures.NAMES["empathy"]} messages',
                      f'{measures.NAMES["passive"]} messages', 'Heated messages', 'Heated moments']]
        for i, r in enumerate(trail, 1):                         # the day the check was made, never the time of day
            rows.append([f'Check {i}' + (' (this check)' if i == len(trail) else ''), _iso_day(r.get('created_at')), _blank(r.get('n')),
                         measures.whole(r['health']) if r.get('health') is not None else '', _blank(r.get('hostile_n')), _blank(r.get('care_n')), _blank(r.get('dig_n')),
                         _blank(r.get('heated_n')), _blank(r.get('episodes'))])
    return rows + [[]]


def _badge_rows(ms) -> list:
    rows = [['Badges'], [HOW, ms['caveats']['badges']]]
    if ms.get('badge_note'):
        rows.append(['Note', ms['badge_note']])
    if _no_badge_earned(ms):
        rows.append(['No badges', _no_badges(ms)])
    rows.append(['Badge', 'Status', 'Why or what it needs', 'How it works'])
    for b in ms['badges']:
        rows.append([b['name'], 'Earned' if b['earned'] else 'Not yet', (b['evidence'] if b['earned'] else b['needs']) or '', b['how']])
    return rows + [[]]


# ---- the PDF --------------------------------------------------------------------------------------
def pdf_pages(ms, story, p, page, table, drawing, small=None) -> None:
    """Adds 'Alerts and badges' and 'Measures over time' to the story. `p(text, style, tail='')` makes a paragraph (and is the only way a string gets in; `tail` is
    set after the text in small grey type), `page(title)` starts a page with its heading, `table(rows, widths)` adds a table, `drawing(scene)` turns a chart into
    a drawing and `small(text)` makes a note in small type."""
    small = small or (lambda text: p(text, 'BeiSmall'))
    _alerts_page(ms, story, p, page, table, small)
    _lines_page(ms, story, p, page, table, _drawer(drawing), small)


def _drawer(drawing):
    """`draw(make, base, max_w, shrink=1.0)`: a chart as a drawing no wider than `max_w` points, or None when the chart has nothing to draw. `make(width)` builds its
    Scene. The scene is `base` units wide, or narrower when the drawing would come out wider than `max_w`: the text keeps its size and only the plot gets shorter.
    With `shrink` below 1 the whole drawing, its text too, is drawn at that share of its size, and the scene is made that much wider, so that the drawing still
    fills `max_w`: the chart comes out lower, not narrower. How many points a unit is comes from drawing a tiny empty scene once."""
    scale = drawing(Scene(100, 10, 'scale', '')).width / 100 or 1.0

    def draw(make, base, max_w, shrink=1.0):
        scene = make(min(base, max_w / (scale * shrink)))
        if scene is None:
            return None
        d = drawing(scene)
        if shrink != 1.0:
            d.scale(shrink, shrink)                                    # the drawing is scaled when it is drawn, and its size on the page follows
            d.width, d.height = d.width * shrink, d.height * shrink
        return d
    return draw


def _cells(rows, p) -> list:
    """The rows of a table with every cell made by p(): these tables are narrow, so their cells wrap at spaces (the style BeiCell), never in the middle of a word."""
    return [[p(text, 'BeiCell') for text in row] for row in rows]


def _keep_dates(text) -> str:
    """'(Oct 14 to Oct 17)' with a no-break space inside each date, so that a narrow column wraps between the dates and never inside one."""
    return MONTH_DAY.sub(lambda hit: f'{hit.group(1)}{NBSP}{hit.group(2)}', str(text or ''))


def _made(story, build) -> list:
    """The flowables that `build()` (a call of the table helper) adds to the story. They are taken out again, so that the caller places them after their heading."""
    mark = len(story)
    build()
    made = story[mark:]
    del story[mark:]
    return made


def _height(flowable) -> float:
    """The height a flowable takes in the text column, with the space the page leaves around it. A KeepTogether is as tall as what it holds (it tells the page it is
    endlessly tall, to force its own split)."""
    if isinstance(flowable, KeepTogether):
        return sum(_height(inner) for inner in flowable._content)
    return flowable.wrap(PAGE_W, 10_000)[1] + flowable.getSpaceBefore() + flowable.getSpaceAfter()


def _room(lead, made, rows=1) -> float:
    """The room, in points, that the `lead` flowables (a heading, its caveat, its notes) and the start of the table in `made` (its header row and its first `rows`
    rows) need. A heading that asks a CondPageBreak for this much room never stays alone at the foot of a page, whatever the first row is."""
    need = sum(_height(f) for f in lead)
    grid = next((f for f in made if isinstance(f, Table)), None)
    first = 0
    if grid is not None:
        grid.wrap(PAGE_W, 10_000)
        heights = list(getattr(grid, '_rowHeights', None) or [])
        first = sum(heights[:rows + 1]) or FIRST_ROWS
    return min(need + first + 4, MAX_ROOM)


def room_for(flowables) -> float:
    """The room, in points, that these flowables need one after the other: all of them, whole. The space after the last one is left out (a page does not ask
    for it), and a few points are added for the rounding of the layout. Never more than MAX_ROOM. A CondPageBreak of this height in front of a block moves the
    whole block to the next page when it does not fit, and does nothing when it does."""
    flowables = list(flowables)
    return min(sum(_height(f) for f in flowables) - (flowables[-1].getSpaceAfter() if flowables else 0) + 3, MAX_ROOM)


def _alerts_page(ms, story, p, page, table, small) -> None:
    """Nothing small trails a table: a note or a caveat set after a table can land alone on the next page. So the notes come before the alerts table (after the
    sentence for an empty list), and the caveat of the badges, its note and the sentence for no badges come before the badges table. The alerts table may run over
    a page: it splits between rows, and the header row repeats. The badges are one block that never splits (see `_badges`)."""
    c = ms['caveats']
    page('Alerts and badges')
    story.append(p(c['alerts'], 'BeiCaveat'))
    head = p('Alerts', 'BeiSubFree')
    notes = [small(text) for text in _notes(ms, 'alerts')]
    if ms['alerts']:
        rows = [['Priority', 'Alert', 'What we saw', 'Try this', 'Where', 'Based on']]
        rows += [[f'**{_priority(a["level"])}**', f'**{a["title"]}**', a['detail'], a['try'], _keep_dates(a.get('when')), a['base']] for a in ms['alerts']]
        made = _made(story, lambda: table(_cells(rows, p), ALERT_WIDTHS))
        story += [CondPageBreak(_room([head] + notes, made)), head] + notes + made
    else:
        lead = [head, p(c['empty'])] + notes
        story += [CondPageBreak(_room(lead, [])), *lead]
    _badges(ms, story, p, table, small)


def _badges(ms, story, p, table, small) -> None:
    """The badges: heading, caveat, the note, the sentence for no badge earned, then the table of seven rows, as one block. It starts on the page where all of it
    fits and moves to the next page when it does not. A table of seven rows has no good place to break: with at least 5 rows before the break and more than 3
    after it (so that the last rows are never left alone) it would need 9 rows, so no page ever holds a few rows of it."""
    head = p('Badges', 'BeiSubFree')
    lead = [head, p(ms['caveats']['badges'], 'BeiCaveat')]
    if ms.get('badge_note'):
        lead.append(small(ms['badge_note']))
    if _no_badge_earned(ms):
        lead.append(p(_no_badges(ms)))
    rows = [['Badge', 'Status', 'Why or what it needs']]
    for b in ms['badges']:
        if b['earned']:
            rows.append([f'**{b["name"]}**', '**Earned**', b['evidence']])                      # the figure behind it
        else:
            rows.append([b['name'], 'Not yet', ' '.join(x for x in (b['needs'], b['how']) if x)])      # what is missing, and the rule
    made = _made(story, lambda: table(_cells(rows, p), BADGE_WIDTHS))
    story += [CondPageBreak(room_for(lead + made))] + lead + made


def _bands(card, m):
    """The cut-offs behind Rare, Some and Often for a chart: (low, high) per 100 messages. Conflict and Passive-aggressive are raised under Low sensitivity. None for
    Escalation, which has no level words by rate."""
    low_high = measures.CUTS.get(card['kind'])
    if not low_high:
        return None
    scale = m['scale'] if card['kind'] in measures.SCALED else 1.0
    return low_high[0] * scale, low_high[1] * scale


def _lines_page(ms, story, p, page, table, draw, small) -> None:
    """Measures over time: the caveat, the basis, the notes, the key to the charts (`ms['chart_key']`, kept with the first row of cards), the four cards, the health
    score tile, Since your last check and, from the third check, the check-by-check chart. A first check with no notes fits one page."""
    m = ms['m']
    page('Measures over time')
    story.append(p(ms['caveats']['measures'], 'BeiCaveat'))
    story.append(p(ms['basis_text'], 'BeiCaveat'))
    story += [small(text) for text in _notes(ms, 'measures')]
    grids = _cards(ms, p, draw)
    tile, since = _health(ms, p, draw), _since(ms, story, p, table)
    if m['has_series'] and ms.get('chart_key') and grids:
        grids[0] = KeepTogether([p(ms['chart_key'], 'BeiKey'), grids[0]])             # the sentence for the lines and bands of the four charts, as measures.py wrote it
    story += grids[:-1]
    # The tile never starts a page: the last row of cards stays with it, and when it does not fit under the first row the two go to the next page together.
    if ms.get('since'):
        story += [KeepTogether(grids[-1:] + [tile]), KeepTogether(since)]      # the comparison follows on the same page when it fits, else it starts the next one
    else:
        story.append(KeepTogether(grids[-1:] + [tile] + since))               # a first check: 'Since your last check' and its one sentence follow the tile in the block
    d = draw(lambda w: bei_charts.trail_chart(ms.get('trail') or [], 'health', title='Health score, check by check', series='Health score', width=w), W, PAGE_W)
    if d is not None:
        story.append(KeepTogether([p('Health score, check by check', 'BeiSubBelow'), d]))


def _cards(ms, p, draw) -> list:
    """The four measures two by two. Each shows its name and level word with the tone in words (the PDF has no icon), what it means in small grey type, what we
    saw, the trend and a note, then its small chart. The words of a row sit in one table row and the charts of that row in the next, so the two charts of a row
    start at the same height whatever the words above them take. Each pair is a table of its own, which never splits."""
    m, parts, texts, charts = ms['m'], _parts(ms['m']), [], []
    for card in ms['cards']:
        tone = _tone(card)
        cell = [p(f'{card["name"]}: {card["level"]}', 'BeiCardHead', tail=f'({tone})' if tone else ''), p(card['meaning'], 'BeiCardNote'), p(card['headline'], 'BeiCardText')]
        texts.append(cell + [p(text, 'BeiCardNote') for text in (card.get('trend'), card.get('note')) if text])
        charts.append(draw(lambda w, card=card: bei_charts.measure_chart(card, parts, _bands(card, m), width=w), SCENE_NARROW, CARD_W, CHART_SHRINK) if m['has_series'] else None)
    grids = []
    for i in range(0, len(texts), 2):
        pair = list(range(i, min(i + 2, len(texts))))
        rows = [[texts[pair[0]], '', texts[pair[1]] if len(pair) > 1 else '']]
        style = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                 ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                 ('LINEABOVE', (0, 0), (0, 0), .4, RULE), ('LINEABOVE', (2, 0), (2, 0), .4, RULE), ('TOPPADDING', (0, 0), (-1, 0), 4)]
        if any(charts[j] is not None for j in pair):
            rows.append([_blank(charts[pair[0]]), '', _blank(charts[pair[1]]) if len(pair) > 1 else ''])
            style += [('NOSPLIT', (0, 0), (-1, 1)), ('TOPPADDING', (0, 1), (-1, 1), 1), ('BOTTOMPADDING', (0, 1), (-1, 1), 3)]
        else:
            style.append(('BOTTOMPADDING', (0, 0), (-1, 0), 6))
        grid = Table(rows, colWidths=[CARD_W, GUTTER, CARD_W], hAlign='LEFT')
        grid.setStyle(TableStyle(style))
        grids.append(grid)
    return grids


def _health(ms, p, draw):
    """The health score tile, set like a card: the number out of 100 on the left; on the right its name, status word and tone in words, the meter (drawn in the wide
    layout, so that all its labels show, and scaled to the width of the tile), the caption and the trend."""
    h = ms['health']
    tone = _tone(h)
    room = PAGE_W - TILE_NUMBER_W - 2
    meter = draw(lambda w: bei_charts.health_meter(h['score'], h['tone'], width=w), W, room, int(10_000 * room / PAGE_W) / 10_000)       # the wide layout (all its labels), as wide as the tile
    side = [p(f'{h["name"]}: {h["status"]}', 'BeiCardHead', tail=f'({tone})' if tone else '')] + ([meter, Spacer(1, 2)] if meter is not None else []) + [p(h['caption'], 'BeiCardNote')]
    if h.get('trend'):
        side.append(p(h['trend'], 'BeiCardNote'))
    tile = Table([[[p(measures.whole(h['score']), 'BeiBig'), p('out of 100', 'BeiCardNote')], side]], colWidths=[TILE_NUMBER_W, PAGE_W - TILE_NUMBER_W], hAlign='LEFT')
    tile.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 3), ('LINEABOVE', (0, 0), (-1, 0), .4, RULE)]))
    return tile


def _since(ms, story, p, table) -> list:
    """'Since your last check': the heading, then on a first check the sentence that says there is nothing to compare with yet, else the caveat of the comparison,
    the reason when the two checks were not made alike, and the table. The caller keeps them together on one page."""
    s = ms.get('since')
    out = [p('Since your last check', 'BeiSubBelow')]
    if not s:
        return out + [p(_first_check(ms), 'BeiCardText')]
    out.append(p(_since_caveat(ms), 'BeiCaveat'))
    if s['comparable']:
        rows = [['Measure', 'Last check', 'This check', 'Change']] + [[r['label'], str(r['before']), str(r['now']), r['verdict']] for r in s['rows']]
        return out + _made(story, lambda: table(_cells(rows, p), SINCE_WIDTHS))
    out.append(p(f'**No comparison.** {s["why"]}'))                # two checks that were not made alike: the reason, and no call on any change
    rows = [['Measure', 'Last check', 'This check']] + [[r['label'], str(r['before']), str(r['now'])] for r in s['rows']]
    return out + _made(story, lambda: table(_cells(rows, p), SINCE_WIDTHS_NO_CALL))
