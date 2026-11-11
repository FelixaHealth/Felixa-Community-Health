"""Charts for the 13-signal behaviour analysis, built with the same scene toolkit as Vibe Check
(inline SVG for the web, drawings for the PDF)."""
from vibe_check.charts import NARROW_W, W, Scene, nice_ticks, text_w
from vibe_check.common import fmt_int, js_round

FAMILY_ORDER = ('Positive', 'Tense', 'Toxic')
FAMILY_ROLE = {'Positive': 'good', 'Tense': 'warn', 'Toxic': 'crit'}      # status colours: the family name sits beside each key
FAMILY_TONE = {'Positive': 'good', 'Tense': 'warn', 'Toxic': 'bad'}
FAMILY_BLURB = {'Positive': 'Polite, funny, empathetic and encouraging communication',
                'Tense': 'Ignorant, egocentric, neurotic, sarcastic and agitated communication',
                'Toxic': 'Aggressive, rude, disrespectful and judgmental communication'}


def family_summary(metrics) -> list:
    """Average FAS per family and the strongest signal in it. Families with no data are left out."""
    out = []
    for fam in FAMILY_ORDER:
        ms = [m for m in metrics if m['family'] == fam]
        if ms:
            top = max(ms, key=lambda m: m['fas'])
            out.append({'name': fam, 'avg_fas': sum(m['fas'] for m in ms) / len(ms), 'top': top['attribute'], 'top_fas': top['fas'],
                        'tone': FAMILY_TONE[fam], 'blurb': FAMILY_BLURB[fam]})
    return out


def attribute_meters(metrics, *, width=W):
    """One meter per signal against the full 0-100 FAS scale, grouped by family. Small values look small on purpose."""
    groups = [(fam, sorted((m for m in metrics if m['family'] == fam), key=lambda m: (-m['fas'], m['attribute']))) for fam in FAMILY_ORDER]
    groups = [(fam, ms) for fam, ms in groups if ms]
    if not groups:
        return None
    narrow = width < 600
    head_h, row_h, between = 34, (54 if narrow else 32), 12
    height = sum(head_h + len(ms) * row_h + between for _, ms in groups) - between + 4
    top3 = sorted((m for _, ms in groups for m in ms), key=lambda m: -m['fas'])[:3]
    sc = Scene(width, height, 'Signal profile',
               'FAS for each of the 13 communication signals on a 0 to 100 scale, grouped as positive, tense and toxic. Strongest: '
               + ', '.join(f'{m["attribute"]} {m["fas"]:.1f}' for m in top3) + '.')
    y = 0.0
    name_w, x0 = 170, 186
    track_w = 350 if not narrow else width - 74
    for fam, ms in groups:
        role = FAMILY_ROLE[fam]
        avg = sum(m['fas'] for m in ms) / len(ms)
        sc.rect(0, y + 10, 10, 10, role, r=(2, 2, 2, 2))
        sc.text(18, y + 20, f'{fam} signals', size=14, fill='ink', weight=600)
        sc.text(width, y + 20, f'average FAS {avg:.1f}', size=12, fill='ink2', anchor='end')
        y += head_h
        for m in ms:
            tip = {'t': m['attribute'], 'r': [['FAS', f'{m["fas"]:.1f}', role], ['of units show it', f'{m["detection_rate"]:.0f}%', role],
                                             ['strength when present', f'{m["expression_strength"]:.0f}%', role], ['units rated', fmt_int(m['n']), role]]}
            if narrow:
                sc.text(0, y + 17, m['attribute'], fill='ink2')
                sc.text(width, y + 17, f'{m["detection_rate"]:.0f}% of units', size=12, fill='ink2', anchor='end')
                bx, by = 0, y + 26
                value_x = track_w + 12
                value_y = by + 11
            else:
                sc.text(name_w, y + row_h / 2 + 4.5, m['attribute'], anchor='end', fill='ink2')
                bx, by = x0, y + row_h / 2 - 6
                value_x = x0 + track_w + 14
                value_y = y + row_h / 2 + 4.5
            sc.rect(bx, by, track_w, 12, role, r=(6, 6, 6, 6), opacity=0.18, tip=tip if m['fas'] <= 0 else None)
            if m['fas'] > 0:
                sc.rect(bx, by, max(3.0, track_w * min(m['fas'], 100) / 100), 12, role, r=(6, 6, 6, 6), tip=tip)
            sc.text(value_x, value_y, f'{m["fas"]:.1f}', fill='ink', weight=600)
            if not narrow:
                sc.text(value_x + 52, value_y, f'{m["detection_rate"]:.0f}% of units', size=12, fill='ink2')
            y += row_h
        y += between
    return sc


def attribute_table_rows(metrics) -> list:
    return [[m['attribute'], m['family'], f'{m["fas"]:.1f}', f'{m["detection_rate"]:.1f}%', f'{m["expression_strength"]:.1f}%', fmt_int(m['n'])]
            for m in metrics]


# ---- measures over time ----------------------------------------------------------------------------
BAND_ROLES = {'hostile': ('good', 'warn', 'crit'), 'dig': ('good', 'warn', 'warn'), 'care': ('neu', 'neu', 'good')}      # the colour of Rare, Some and Often (a band is a faint wash, never the only signal)
BAND_WORDS = ('Rare', 'Some', 'Often')           # written inside a band where there is a clear spot for them, so the bands can be told apart in print and in grey
NAME_ASCENT, NAME_DESCENT, NAME_INK = 0.95, 0.25, 0.77      # in text sizes: the box a line of text may take above and below its baseline in a browser, and the height of its tallest letter
NAME_HEADROOM = 1.07                             # text_w measures Helvetica; the PDF's font and a Linux browser's run up to 6% wider
NAME_CLEAR = 1.5                                 # units of empty space between a band's name and the line, a marker or a shaded heated moment
BAND_WASH = 0.10                                 # a level band is a faint wash ...
MOMENT_WASH = 0.40                               # ... and a heated moment is clearly darker than any of them, in colour and in grey
SAID = {'hostile': ('messages that were openly hostile', 'were openly hostile'), 'care': ('messages that showed care', 'showed care'),
        'dig': ('messages that were sarcastic', 'were sarcastic'), 'heated': ('messages that were heated', 'were heated')}      # the tooltip row, and the words of the description


def _whole(x) -> int:
    return js_round(x + 1e-9)


def _sizes(width) -> tuple:
    """(tick and axis labels, which the names of the bands share, the value at the end of the line) in chart units. The phone layout is drawn 360 units wide and
    shown about 290 pixels wide, so its text is drawn larger to stay near 12 pixels."""
    return (14, 16) if width < 600 else (13, 14)


def _axis_labels(sc, items, size, y, gap=10) -> None:
    """Axis labels from left to right, each centred on its mark. One that would run off the drawing is pushed in from its edge, and one that would touch the label
    before it is left out (the table twin names every part)."""
    right = -1e9
    for x, label in items:
        if not label:
            continue
        w = text_w(label, size)
        left = min(max(x - w / 2, 0.0), max(sc.width - w, 0.0))
        if left < right + gap:
            continue
        sc.text(left + w / 2, y, label, size=size, fill='ink2', anchor='middle')
        right = left + w


def _marker_radii(count) -> tuple:
    """(outer ring, inner dot) of the markers of a line with `count` parts: smaller ones when there are many, so that they do not run together into a bead string."""
    return (4.5, 3) if count <= 12 else (3, 2)


def _markers(sc, pts) -> None:
    """A ringed dot for every part."""
    outer, inner = _marker_radii(len(pts))
    for x, y in pts:
        sc.circle(x, y, outer, 'surface')
        sc.circle(x, y, inner, 'series1')


def _segment_in_box(x1, y1, x2, y2, left, top, right, bottom) -> bool:
    """True when the straight segment from (x1, y1) to (x2, y2) touches the box (Liang and Barsky)."""
    t0, t1 = 0.0, 1.0
    dx, dy = x2 - x1, y2 - y1
    for p, q in ((-dx, x1 - left), (dx, right - x1), (-dy, y1 - top), (dy, bottom - y1)):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return True


def _name_spot(word, size, band, plot, pts, columns):
    """Where a band's name can go, as (left edge, baseline), or None when there is no clear spot. The name goes inside its band `band` = (top, bottom), anywhere
    in the plot `plot` = (left, right) that is at least 10 units from its edges, and its box is kept clear of the line, of every marker and of the shaded heated
    moments `columns` = [(left, right)]. The first spot found wins: from the left edge of the plot, and from the top of the band. A name that has no such spot
    is left out: the shading is still there, and the key to the charts says what the bands mean."""
    top, bottom = band
    left, right = plot
    wide = text_w(word, size) * NAME_HEADROOM
    above, below = NAME_ASCENT * size, NAME_DESCENT * size
    first, last = top + 1 + NAME_INK * size, bottom - 1             # the baselines that keep the letters themselves inside the band
    if last < first or right - left < wide + 20:
        return None
    radius = _marker_radii(len(pts))[0]
    reach = 1 + NAME_CLEAR                                           # half the width of the line, and the empty space around the name
    rows = min(12, int(last - first) + 1)
    lows = [first + (last - first) * k / max(1, rows - 1) for k in range(rows)]
    room = right - 10 - wide - (left + 10)
    cols = min(32, int(room / 4) + 1)
    scan = [left + 10 + room * k / max(1, cols - 1) for k in range(cols)]
    places = scan[:1] + scan[-1:] + scan[1:-1] if cols > 1 else scan          # the left end, the right end, then the places in between from the left
    for x in places:
        for y in lows:
            b_left, b_top, b_right, b_bottom = x, y - above, x + wide, y + below
            if any(c0 - 2 < b_right and c1 + 2 > b_left for c0, c1 in columns):
                continue
            if any(_segment_in_box(ax, ay, bx, by, b_left - reach, b_top - reach, b_right + reach, b_bottom + reach)
                   for (ax, ay), (bx, by) in zip(pts, pts[1:]) if max(ax, bx) >= b_left - reach and min(ax, bx) <= b_right + reach):
                continue
            if any(px + radius > b_left - NAME_CLEAR and px - radius < b_right + NAME_CLEAR and py + radius > b_top - NAME_CLEAR and py - radius < b_bottom + NAME_CLEAR
                   for px, py in pts):
                continue
            return x, y
    return None


def _runs(flags) -> list:
    """The runs of consecutive true flags as 1-based (first, last) part numbers."""
    out, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j + 1 < len(flags) and flags[j + 1]:
                j += 1
            out.append((i + 1, j + 1))
            i = j + 1
        else:
            i += 1
    return out


def _moments_said(runs, legacy=True) -> str:
    if not runs:
        return ''
    place = [f'part {a}' if a == b else f'parts {a} to {b}' for a, b in runs]
    word = 'Heated moment' if legacy else 'Flagged part'
    return f' {word}{"" if len(runs) == 1 else "s"}: ' + (place[0] if len(place) == 1 else ', '.join(place[:-1]) + ' and ' + place[-1]) + '.'


def measure_chart(card, stretches, bands=None, *, width=W):
    """A small line chart of one measure: the rate per 100 messages in each part of the chat. `card` is a card from measures.cards, `stretches` the parts
    from measures.analyse, `bands` the (low, high) cut-offs behind Rare, Some and Often (or None). The heated moments are shaded, darker than the bands. A band
    carries its name, at the size of the tick labels, only where there is a clear spot for it: never on the line, a marker or a shaded heated moment. One 2px line,
    value at the end, a hover target for every part (the targets tile the plot, so the right part always answers), and a table twin kept by the page."""
    values = [float(v) for v in card['series']]
    n = len(values)
    if n < 2:
        return None
    narrow = width < 600
    tick_size, end_size = _sizes(width)
    height = 168 if narrow else 150
    ml, mr, mt, mb = 40, 46, 16, 32
    pw, ph = width - ml - mr, height - mt - mb
    ticks = nice_ticks(max(values + [25.0]), 2)
    top = ticks[-1]
    X = lambda i: ml + pw * i / (n - 1)                       # noqa: E731
    Y = lambda v: mt + ph * (1 - min(max(v, 0.0), top) / top)    # noqa: E731
    kind = card['kind']
    name = card['name']
    said, pred = SAID[kind]
    first, last = _whole(values[0]), _whole(values[-1])
    peak = max(range(n), key=lambda i: (values[i], -i))
    legacy = 'flagged' not in card                      # a card built by hand, without the Track flags: shade what the analysis counted
    flagged = {i: 'yellow' for i, st in enumerate(stretches) if st.get('counted')} if legacy else (card.get('flagged') or {})
    counted = _runs([i in flagged for i in range(len(stretches))])
    desc = (f'How many of every 100 messages {pred}, in each of {n} parts of the chat. The first part had {first}, the last had {last}, '
            + (f'and the highest was part {peak + 1} with {_whole(values[peak])}.' if _whole(values[peak]) else 'and none had any.') + _moments_said(counted, legacy))
    sc = Scene(width, height, f'{name} over time', desc)
    named = []                                              # (top, height, word) of every band that can carry its name
    if bands and kind in BAND_ROLES:
        low, high = bands
        for k, (a, b) in enumerate(((0.0, low), (low, high), (high, top))):
            if b > a and a < top:
                b = min(b, top)
                sc.rect(ml, Y(b), pw, Y(a) - Y(b), BAND_ROLES[kind][k], opacity=BAND_WASH)
                named.append((Y(b), Y(a) - Y(b), BAND_WORDS[k]))
    step = pw / (n - 1)
    columns = []                                            # the shaded heated moments, as (left, right)
    for i, st in enumerate(stretches):
        if i in flagged:                                    # yellow or red, as on the Track page
            x0, x1 = max(ml, X(i) - step / 2), min(ml + pw, X(i) + step / 2)
            columns.append((x0, x1))
            sc.rect(x0, mt, x1 - x0, ph, 'crit' if flagged[i] == 'red' else 'warn', opacity=MOMENT_WASH)
    for t in ticks:
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + tick_size * 0.33, _whole(t), size=tick_size, fill='ink2', anchor='end')
    _axis_labels(sc, [(X(i), st.get('axis') or '') for i, st in enumerate(stretches)], tick_size, height - mb + tick_size + 7)
    pts = [(X(i), Y(v)) for i, v in enumerate(values)]
    for y0, h, word in named:                               # a name is written where the line, the markers and the shaded moments are all clear of it, else not at all
        spot = _name_spot(word, tick_size, (y0, y0 + h), (ml, ml + pw), pts, columns)
        if spot:
            sc.text(spot[0], spot[1], word, size=tick_size, fill='ink2')
    sc.poly(pts, stroke='series1', width=2)
    _markers(sc, pts)
    sc.text(pts[-1][0] + 10, pts[-1][1] + end_size * 0.35, _whole(values[-1]), size=end_size, fill='ink', weight=600)
    for i, st in enumerate(stretches):
        lo, hi = max(ml, X(i) - step / 2), min(width - mr, X(i) + step / 2)
        rows = [[said, f'{st["counts"][kind]:,} of {st["n"]:,}', 'series1'], ['per 100 messages', f'{_whole(values[i])}', 'series1']]
        if i in flagged and legacy:         # the climb is the climb of heated messages, so its number belongs to the Escalation chart; the others only say the part is shaded
            rows.append(['part of a heated moment', f'up {_whole(st["climb"])} per 100 messages', 'warn'] if kind == 'heated' else ['', 'part of a heated moment', 'warn'])
        elif i in flagged:                  # the part is flagged on the Track page: yellow or red
            rows.append(['Red flag' if flagged[i] == 'red' else 'Yellow flag', 'heat climbed in this part', 'crit' if flagged[i] == 'red' else 'warn'])
        sc.hit(lo, mt, hi - lo, ph, {'t': st['label'], 'r': rows}, X(i))
    sc.cross(mt, mt + ph)
    return sc


def check_names(trail) -> list:
    """The names of the saved checks on the check-by-check chart and in its table: 'Check 3' for the axis, 'Check 3 · Nov 2, 2026' for the hover. A check is told
    apart by its number, never by the time of day it ran, so that nothing here depends on Incognito."""
    from vibe_check.common import long_date
    out = []
    for i, r in enumerate(trail, 1):
        day = long_date(r.get('created_at')) if r.get('created_at') else ''
        out.append({'short': f'Check {i}', 'long': f'Check {i} \u00b7 {day}' if day else f'Check {i}'})
    return out


def trail_chart(trail, key, *, title, series, width=W):
    """The health score check by check: one point per saved check, 0 to 100. Needs three checks, because a line through two says little."""
    if len(trail) < 3:
        return None
    labels = check_names(trail)
    values = [float(r.get(key) or 0) for r in trail]
    n = len(values)
    narrow = width < 600
    tick_size, end_size = _sizes(width)
    height = 200 if narrow else 180
    ml, mr, mt, mb = 40, 46, 16, 36
    pw, ph = width - ml - mr, height - mt - mb
    X = lambda i: ml + pw * i / (n - 1)                       # noqa: E731
    Y = lambda v: mt + ph * (1 - min(max(v, 0.0), 100.0) / 100.0)    # noqa: E731
    sc = Scene(width, height, title, f'{series} in each of {n} checks, from {_whole(values[0])} to {_whole(values[-1])} out of 100.')
    for t in (0, 25, 50, 75, 100):
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + tick_size * 0.33, t, size=tick_size, fill='ink2', anchor='end')
    _axis_labels(sc, [(X(i), labels[i]['short']) for i in range(n)], tick_size, height - mb + tick_size + 8)
    pts = [(X(i), Y(v)) for i, v in enumerate(values)]
    sc.poly(pts, stroke='series1', width=2)
    _markers(sc, pts)
    sc.text(pts[-1][0] + 10, pts[-1][1] + end_size * 0.35, _whole(values[-1]), size=end_size, fill='ink', weight=600)
    step = pw / (n - 1)
    for i in range(n):
        lo, hi = max(ml, X(i) - step / 2), min(width - mr, X(i) + step / 2)
        sc.hit(lo, mt, hi - lo, ph, {'t': labels[i]['long'], 'r': [[series, f'{_whole(values[i])} out of 100', 'series1'], ['messages rated', f'{trail[i].get("n", 0):,}', 'neu']]}, X(i))
    sc.cross(mt, mt + ph)
    return sc


LABEL_GAP = 11.5           # units of empty space between two labels of the health meter: 6 points on the PDF page, which draws the 720 units of the wide layout 380 points wide (0.53 points a unit)
LABEL_HEADROOM = 1.07      # text_w measures Helvetica; the PDF's font and a Linux browser's run up to 6% wider


def _label_w(text, size) -> float:
    return text_w(text, size) * LABEL_HEADROOM


def _drop_crowded(labels) -> list:
    """The labels of one line, from the left, without those that would sit closer than LABEL_GAP to the next. Of two that are too close the one with the lower
    `rank` goes (the later one when the ranks are equal), until none is crowded."""
    kept = sorted(labels, key=lambda item: item['left'])
    while True:
        for a, b in zip(kept, kept[1:]):
            if b['left'] - a['right'] < LABEL_GAP:
                kept.remove(a if a['rank'] < b['rank'] else b)
                break
        else:
            return kept


def health_meter(score, tone, *, width=W):
    """The health score on its 0 to 100 track. Thin ticks mark where the status changes (40, 60 and 80), the numbers sit under them, and the four status words
    sit centred under their segments. On the phone layout (360), where the words do not all keep their distance from the numbers, they go on a line of their own.
    The wide layout (720, which the PDF draws scaled down to the width of its tile) and the 500 one have room for one line of labels only, so there a label that would
    sit closer than LABEL_GAP to the next is left out, the end number "100" before a word: the score is printed as "out of 100" beside the meter."""
    from measures import HEALTH_STATUS
    role = {'good': 'series1', 'info': 'neu', 'bad': 'crit', 'warn': 'warn'}.get(tone, 'series1')
    num, word = (16, 13) if width < 420 else (14, 12) if width < 600 else (16, 14)      # the phone layout (360), the PDF (500, drawn at 0.76 points a unit) and the wide layout (720)
    steps = sorted(HEALTH_STATUS)                                              # [(0, 'Needs attention'), (40, 'Fair'), (60, 'Good'), (80, 'Excellent')]
    edges = [floor for floor, _ in steps] + [100]
    th, y = 12, 6
    base1 = y + th + 7 + num * 0.85                                            # baseline of the numbers
    x_of = lambda v: width * v / 100                                           # noqa: E731

    def number(v):                                                             # the first starts at the left edge, the last ends at the right one; the end numbers go first when labels crowd
        w = _label_w(str(v), num)
        left, anchor = (0.0, 'start') if v == edges[0] else (width - w, 'end') if v == edges[-1] else (x_of(v) - w / 2, 'middle')
        return {'text': str(v), 'x': x_of(v), 'left': left, 'right': left + w, 'anchor': anchor, 'size': num, 'rank': 1 if v in (edges[0], edges[-1]) else 3}
    numbers = [number(v) for v in edges]

    def name(text, a, b):                                                      # a word is centred between its two ticks
        w, c = _label_w(text, word), x_of((a + b) / 2)
        return {'text': text, 'x': c, 'left': c - w / 2, 'right': c + w / 2, 'anchor': 'middle', 'size': word, 'rank': 2}
    names = [name(text, floor, edges[i + 1]) for i, (floor, text) in enumerate(steps)]
    together = numbers + names
    one_row = len(_drop_crowded(together)) == len(together) or width >= 420            # only the phone layout may put the words on a line of their own
    row1, row2 = (_drop_crowded(together), []) if one_row else (numbers, _drop_crowded(names))
    base2 = base1 if one_row else base1 + word + 5
    shown = _whole(score)
    status = next(text for floor, text in reversed(steps) if shown >= floor)
    sc = Scene(width, base2 + 6, 'Health score', f'{shown} out of 100, which is {status}. ' + ', '.join(
        f'{text} {f"under {edges[i + 1]}" if i == 0 else f"{floor} and over" if i == len(steps) - 1 else f"{floor} to {edges[i + 1] - 1}"}' for i, (floor, text) in enumerate(steps)) + '.')
    sc.rect(0, y, width, th, role, r=(6, 6, 6, 6), opacity=0.18)
    if score > 0:
        sc.rect(0, y, min(width, max(th, width * score / 100)), th, role, r=(6, 6, 6, 6))
    for v in edges[1:-1]:
        sc.line(x_of(v), y - 3, x_of(v), y + th + 5, 'ink2', 1)
    for item in row1:
        sc.text(item['x'], base1, item['text'], size=item['size'], fill='ink2', anchor=item['anchor'])
    for item in row2:
        sc.text(item['x'], base2, item['text'], size=item['size'], fill='ink2', anchor=item['anchor'])
    return sc


# ---- the Track tab: a measure built up over the parts of a chat, with flags, and the three measures together ------------------------------------
TRACK_STEPS = {'escalation': (5, 10, 20, 35), 'conflict': (2, 5, 12, 25), 'empathy': (2, 4, 8, 14)}      # per 100 messages: where the colour moves to the next shade
TRACK_SCALE = {'escalation': 'heat', 'conflict': 'heat', 'empathy': 'calm'}
TRACK_LINE = {'escalation': 'warn', 'conflict': 'crit', 'empathy': 'cat3', 'deescalation': 'pos'}


def track_shade(key, value) -> str:
    """The role of the bar for a value: the shade moves from 1 (pale) to 5 (deep) as the rate climbs through TRACK_STEPS."""
    step = sum(1 for edge in TRACK_STEPS[key] if value >= edge)
    return f'{TRACK_SCALE[key]}{step + 1}'


def track_chart(key, name, stretches, flags, *, width=W, black=None):
    """One measure as bars, one per part of the chat, in a colour that deepens as the rate climbs. A red or yellow flag sits on the parts that carry one (the letter
    R or Y is in the flag, so the colour is not the only sign). For Escalation a thin line follows the recent level each part is measured against. A hover target for every part."""
    values = [f['value'] for f in flags]
    n = len(values)
    if n < 2:
        return None
    narrow = width < 600
    tick_size, _end = _sizes(width)
    height = 230 if narrow else 220
    ml, mr, mt, mb = 40, 16, 30 + (14 if black and any(black) else 0), 34
    pw, ph = width - ml - mr, height - mt - mb
    ticks = nice_ticks(max(values + [20.0]), 4)
    top = ticks[-1]
    step = pw / n
    bar_w = max(4.0, min(step * 0.72, 56.0))
    X = lambda i: ml + step * (i + 0.5)                          # noqa: E731
    Y = lambda v: mt + ph * (1 - min(max(v, 0.0), top) / top)    # noqa: E731
    red, yellow = sum(1 for f in flags if f['level'] == 'red'), sum(1 for f in flags if f['level'] == 'yellow')
    desc = (f'{name} in each of {n} bars, as messages per 100. The first part had {_whole(values[0])} and the last had {_whole(values[-1])}. '
            + ('No bar was flagged.' if not (red or yellow) else f'{red} bar{"" if red == 1 else "s"} carried a red flag and {yellow} a yellow flag.')
            if key != 'empathy' else f'{name} in each of {n} bars, as messages per 100. The first part had {_whole(values[0])} and the last had {_whole(values[-1])}.')
    sc = Scene(width, height, f'{name} built up over time', desc)
    for t in ticks:
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + tick_size * 0.33, _whole(t), size=tick_size, fill='ink2', anchor='end')
    _axis_labels(sc, [(X(i), st.get('axis') or '') for i, st in enumerate(stretches)], tick_size, height - mb + tick_size + 7)
    sc.group(key)
    for i, f in enumerate(flags):
        h = max(2.0, Y(0) - Y(f['value']))
        sc.rect(X(i) - bar_w / 2, Y(0) - h, bar_w, h, track_shade(key, f['value']), r=(3, 3, 0, 0))
    if key == 'escalation':
        pts = [(X(i), Y(f['base'])) for i, f in enumerate(flags) if f['base'] is not None]
        if len(pts) > 1:
            sc.poly(pts, stroke='ink2', width=1.5)
    for i, f in enumerate(flags):
        if f['level']:
            cy = max(mt - 10, Y(f['value']) - 13)
            sc.circle(X(i), cy, 9, 'crit' if f['level'] == 'red' else 'warn')
            sc.text(X(i), cy + 4, 'R' if f['level'] == 'red' else 'Y', size=11, fill='on-fill' if f['level'] == 'red' else 'on-warn', anchor='middle', weight=700)
    sc.group('highrisk')
    for i in range(n):
        if black and black[i]:
            black_flag(sc, X(i) - 1, 1)
    sc.group(None)
    for i, f in enumerate(flags):
        st = stretches[i]
        rows = [[SAID_PLAIN[key], f'{f["count"]:,} of {f["n"]:,}', TRACK_SCALE[key] + '3'], ['per 100 messages', f'{_whole(f["value"])}', TRACK_SCALE[key] + '3']]
        if key == 'escalation' and f['base'] is not None:
            rows.append(['recent level', f'{_whole(f["base"])} per 100', 'ink2'])
        if f['level']:
            rows.append(['flag', 'Red flag' if f['level'] == 'red' else 'Yellow flag', 'crit' if f['level'] == 'red' else 'warn'])
        if black and black[i]:
            rows.append(['High Risk flag', f'{black[i]} held', 'black'])
        sc.hit(max(ml, X(i) - step / 2), mt - 12, min(step, pw), ph + 12, {'t': st['label'], 'r': rows}, X(i))
    sc.cross(mt, mt + ph)
    return sc


SAID_PLAIN = {'escalation': 'heated messages', 'conflict': 'openly hostile messages', 'empathy': 'caring messages'}


def black_flag(sc, x, y) -> None:
    """The black Safety flag: a pole with a small pennant, drawn with its top at y. It marks a part of the chat, a bar or a message that holds something the Safety module keeps for a person to read."""
    sc.line(x, y, x, y + 17, 'black', 2)
    sc.poly([(x, y), (x + 12, y + 4.5), (x, y + 9)], stroke='black', width=1, fill='black', close=True)


def track_compare(stretches, *, width=W, deescalation=None, black=None, green=None, conflict=True):
    """Escalation, Conflict and Empathy on one scale (messages per 100), one line each, so that a rise in one can be read against the others."""
    n = len(stretches)
    if n < 2:
        return None
    series = {k: [float(st['rates'][kind]) for st in stretches] for k, kind in (('escalation', 'heated'), ('conflict', 'hostile'), ('empathy', 'care'))}
    if not conflict:                                                 # the Trends page leaves Conflict to its own view
        series.pop('conflict')
    if deescalation and len(deescalation) == n:                      # the blue line: calming messages after heat, per 100
        series['deescalation'] = [float(v) for v in deescalation]
    narrow = width < 600
    tick_size, end_size = _sizes(width)
    height = 260 if narrow else 250
    ml, mr, mt, mb = 40, 46, 16 + (16 if (black and any(black)) or (green and any(green)) else 0), 34
    pw, ph = width - ml - mr, height - mt - mb
    ticks = nice_ticks(max(max(v) for v in series.values()) or 20.0, 4)
    top = ticks[-1]
    X = lambda i: ml + pw * i / (n - 1)                          # noqa: E731
    Y = lambda v: mt + ph * (1 - min(max(v, 0.0), top) / top)    # noqa: E731
    if conflict:
        desc = ('Escalation, conflict and empathy in each of %d bars, as messages per 100. At the end: escalation %d, conflict %d, empathy %d.'
                % (n, _whole(series['escalation'][-1]), _whole(series['conflict'][-1]), _whole(series['empathy'][-1])))
    else:
        desc = ('Escalation and empathy in each of %d bars, as messages per 100. At the end: escalation %d, empathy %d.' % (n, _whole(series['escalation'][-1]), _whole(series['empathy'][-1])))
    sc = Scene(width, height, 'Escalation, conflict and empathy together' if conflict else 'Escalation and empathy together', desc)
    for t in ticks:
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + tick_size * 0.33, _whole(t), size=tick_size, fill='ink2', anchor='end')
    _axis_labels(sc, [(X(i), st.get('axis') or '') for i, st in enumerate(stretches)], tick_size, height - mb + tick_size + 7)
    outer, inner = _marker_radii(n)
    ends = []
    for key in [k for k in ('deescalation', 'escalation', 'empathy', 'conflict') if k in series]:                # conflict last, so its red line is never hidden under another
        role = TRACK_LINE[key]
        sc.group(key)
        pts = [(X(i), Y(v)) for i, v in enumerate(series[key])]
        thick = key == 'escalation'                       # escalation is drawn wide and underneath, conflict narrow on top: when every heated message is openly hostile the two lines meet, and both colours stay visible
        sc.poly(pts, stroke=role, width=6 if thick else 2.5)
        for x, y in pts:
            sc.circle(x, y, outer + (2.5 if thick else 0), 'surface')
            sc.circle(x, y, inner + (2.5 if thick else 0), role)
        ends.append((pts[-1][1], role, _whole(series[key][-1]), key))
    sc.group('highrisk')
    for i in range(n):
        if black and black[i]:
            black_flag(sc, X(i) - 1, 1)
    sc.group('deescalation')
    for i in range(n):
        if green and green[i]:
            pole_flag(sc, X(i) + (9 if black and black[i] else 0) + 1, 1, 'green')
    sc.group(None)
    floor = Y(0) - 4                                            # the values at the end of the lines, kept apart and above the axis labels
    next_y = floor
    for y, role, val, gkey in sorted(ends, reverse=True):
        y = min(y, next_y)
        sc.group(gkey)
        sc.text(width - mr + 8, y + end_size * 0.35, val, size=end_size, fill='ink', weight=600)
        next_y = y - end_size - 2
    sc.group(None)
    step = pw / (n - 1)
    for i, st in enumerate(stretches):
        rows = [['Escalation', f'{_whole(series["escalation"][i])} per 100', TRACK_LINE['escalation']]]
        if 'conflict' in series:
            rows.append(['Conflict', f'{_whole(series["conflict"][i])} per 100', TRACK_LINE['conflict']])
        rows.append(['Empathy', f'{_whole(series["empathy"][i])} per 100', TRACK_LINE['empathy']])
        if 'deescalation' in series:
            rows.append(['Deescalation', f'{_whole(series["deescalation"][i])} per 100', TRACK_LINE['deescalation']])
        if green and green[i]:
            rows.append(['Deescalation flag', f'{green[i]} calmed', 'green'])
        if black and black[i]:
            rows.append(['High Risk flag', f'{black[i]} held', 'black'])
        sc.hit(max(ml, X(i) - step / 2), mt, min(step, pw), ph, {'t': st['label'], 'r': rows}, X(i))
    sc.cross(mt, mt + ph)
    return sc


def compare_chart(cats, cur, prev, *, width=W):
    """The same headline numbers for two checks side by side: one point per number (0 to 100), the current check in dark green with a soft fill, the earlier one in gold.
    `prev` is None when there is no earlier check, and only the current line is drawn. A hover target for every number."""
    n = len(cats)
    if n < 2:
        return None
    narrow = width < 600
    tick_size, _end = _sizes(width)
    height = 300 if narrow else 290
    ml, mr, mt, mb = 52, 20, 18, 40
    pw, ph = width - ml - mr, height - mt - mb
    X = lambda i: ml + pw * (i + 0.5) / n                           # noqa: E731
    Y = lambda v: mt + ph * (1 - min(max(v, 0.0), 100.0) / 100.0)   # noqa: E731
    desc = 'The headline numbers of this check' + (' and the earlier one' if prev else '') + ': ' + ', '.join(f'{c} {_whole(v)}%' for c, v in zip(cats, cur)) + '.'
    sc = Scene(width, height, 'Trend comparison', desc)
    for t in (0, 20, 40, 60, 80, 100):
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + tick_size * 0.33, f'{t}%', size=tick_size, fill='ink2', anchor='end')
    for i, c in enumerate(cats):
        sc.text(X(i), height - mb + tick_size + 9, c, size=tick_size, fill='ink2', anchor='middle')
    pts = [(X(i), Y(v)) for i, v in enumerate(cur)]
    sc.poly(pts + [(pts[-1][0], Y(0)), (pts[0][0], Y(0))], fill='good', fill_opacity=0.12, close=True)
    if prev:
        run = []                                                    # the earlier check may lack some numbers: a line is drawn through each unbroken run of them
        for i, v in enumerate(list(prev) + [None]):
            if v is not None:
                run.append((X(i), Y(v)))
                continue
            if len(run) > 1:
                sc.poly(run, stroke='gold', width=2.6)
            for x, y in run:
                sc.circle(x, y, 5.5, 'surface')
                sc.circle(x, y, 3.5, 'gold')
            run = []
    sc.poly(pts, stroke='forest', width=2.6)
    for x, y in pts:
        sc.circle(x, y, 5.5, 'surface')
        sc.circle(x, y, 3.5, 'forest')
    step = pw / n
    for i, c in enumerate(cats):
        rows = [['Current', f'{_whole(cur[i])}%', 'forest']] + ([['Earlier', f'{_whole(prev[i])}%', 'gold']] if prev and prev[i] is not None else [])
        sc.hit(ml + step * i, mt, step, ph, {'t': c, 'r': rows}, X(i))
    sc.cross(mt, mt + ph)
    return sc


def pole_flag(sc, x, y, role) -> None:
    """A pole with a pennant in a role colour (red for conflict, yellow for escalation), drawn with its top at y."""
    sc.line(x, y, x, y + 17, 'ink2', 1.6)
    sc.poly([(x, y), (x + 12, y + 4.5), (x, y + 9)], stroke=role, width=1, fill=role, close=True)


def incident_chart(bars, *, width=W, lane=None, tones=None):
    """One incident as stacked bars, a bar for each message or each stretch of minutes: openly hostile (red) on top of heated (yellow) on top of calm (grey).
    The yellow flag marks the bar where escalation starts and the red flag the bar where it turns into conflict. `lane` ([(behaviour, {bar index: messages})]) adds a row of dots
    for each High Risk behaviour under the bars, in the same columns. None without bars."""
    n = len(bars)
    if not n:
        return None
    lane = lane or []
    tones = tones or []
    narrow = width < 600
    tick_size, _end = _sizes(width)
    base_h = 190 if narrow else 210
    flags_on = any(b.get('start') or b.get('turn') or b.get('calm_mark') for b in bars)
    row_h = 34
    height = base_h + (row_h * (len(lane) + len(tones)) + 10 if (lane or tones) else 0)
    ml, mr, mt, mb = 30, 10, 12 + (18 if (flags_on or any(x.get('black') for x in bars)) else 0), 30
    pw, ph = width - ml - mr, base_h - mt - mb
    top = max(max(b['n'] for b in bars), 1)
    ticks = nice_ticks(float(top), 3)
    top = max(ticks[-1], 1)
    slot = pw / n
    bw = max(2.0, min(40.0, slot * 0.72))
    desc = 'Messages in each %s of the incident: %d openly hostile, %d heated, %d calm in all.' % ('bar', sum(b['hostile'] for b in bars), sum(b['heated'] for b in bars), sum(b['calm'] for b in bars))
    if flags_on:
        desc += ' A yellow flag marks where escalation starts, a red flag where it turns into conflict and a green flag where someone calms it.'
    if lane:
        desc += ' Under the bars, a row for each High Risk behaviour shows where it appeared: ' + ', '.join(name for name, _ in lane) + '.'
    if tones:
        desc += ' Another row for each tone that feeds escalation shows where it appeared: ' + ', '.join(t[0] for t in tones) + '.'
    sc = Scene(width, height, 'The incident, bar by bar', desc)
    for t in ticks:
        y = mt + ph * (1 - t / top)
        sc.line(ml, y, width - mr, y, 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 6, y + tick_size * 0.33, _whole(t), size=tick_size, fill='ink2', anchor='end')
    want = 4 if narrow else 8
    every = max(1, -(-n // want))
    for k, b in enumerate(bars):
        x = ml + slot * k + (slot - bw) / 2
        base = mt + ph
        tip_rows = [['Openly hostile', str(b['hostile']), 'crit'], ['Heated', str(b['heated']), 'warn'], ['Calm', str(b['calm']), 'grid']] + ([['High Risk flag', f"{b['black']} held", 'black']] if b.get('black') else [])
        for key, role, g in (('hostile', 'crit', 'conflict'), ('heated', 'warn', 'escalation'), ('calm', 'grid', None)):
            h = ph * b[key] / top
            if h > 0:
                sc.group(g)
                sc.rect(x, base - h, bw, max(1.0, h - 1), role, tip={'t': b['label'], 'r': tip_rows})
                base -= h
        sc.group(None)
        mid = x + bw / 2
        both = (b.get('start') or b.get('turn')) and b.get('black')
        if b.get('start'):
            sc.group('escalation')
            pole_flag(sc, mid - (7 if both else 1), 1, 'warn')
        if b.get('turn'):
            sc.group('conflict')
            pole_flag(sc, mid - (7 if both else 1) + (9 if b.get('start') else 0), 1, 'crit')
        if b.get('calm_mark'):
            sc.group('deescalation')
            pole_flag(sc, mid + (1 if not (b.get('start') or b.get('turn')) else 10), 1, 'green')
        if b.get('black'):
            sc.group('highrisk')
            black_flag(sc, mid + (8 if both else -1) + (19 if b.get('calm_mark') else 0), 1)
        sc.group(None)
        if k % every == 0:
            sc.text(x + bw / 2, base_h - mb + tick_size + 6, b['label'], size=tick_size, fill='ink2', anchor='middle')
    sc.group('highrisk')
    for r, (name, where) in enumerate(lane):
        y0 = base_h + 4 + r * row_h
        sc.text(ml, y0 + tick_size + 1, name, size=tick_size, fill="ink2")
        sc.line(ml, y0 + row_h - 6, width - mr, y0 + row_h - 6, 'grid', 1)
        for k, count in where.items():
            sc.circle(ml + slot * k + slot / 2, y0 + row_h - 6, 5.5 if count < 2 else 7, 'ink')
    sc.group('tones')                                            # the tones that feed escalation: open hostility in red, agitation and sarcasm in yellow
    for r, (name, role, where) in enumerate(tones):
        y0 = base_h + 4 + (len(lane) + r) * row_h
        sc.text(ml, y0 + tick_size + 1, name, size=tick_size, fill="ink2")
        sc.line(ml, y0 + row_h - 6, width - mr, y0 + row_h - 6, 'grid', 1)
        for k, count in where.items():
            sc.circle(ml + slot * k + slot / 2, y0 + row_h - 6, 5.5 if count < 2 else 7, role)
    sc.group(None)
    return sc


def phase_chart(rows, *, title, width=W):
    """A pair of bars for each row [{'name', 'esc_rate', 'de_rate', 'esc_n', 'de_n'}]: how often it shows in the escalating part (yellow) and in the calming part (green), per 100 messages of that part.
    The two series carry their names so a reader can switch either off."""
    if not rows:
        return None
    narrow = width < 600
    label_w = 96 if narrow else 118
    row_h, bar_h, top = 40, 13, 4
    top_v = max([5.0] + [max(r['esc_rate'], r['de_rate']) for r in rows])
    top_v = nice_ticks(top_v, 4)[-1]
    x0, x1 = label_w + 8, width - 62
    height = top + row_h * len(rows) + 4
    sc = Scene(width, height, title, f'{title}: for each row, how many messages per 100 show it in the escalating part and in the calming part of the incidents.')
    sc.line(x0, top, x0, top + row_h * len(rows), 'axis', 1)
    for i, r in enumerate(rows):
        yc = top + i * row_h + row_h / 2
        sc.text(label_w, yc + 4.5, r['name'], size=12, anchor='end', fill='ink2')
        tip = {'t': r['name'], 'r': [['Escalation', f"{r['esc_rate']:.0f} per 100 ({r['esc_n']} messages)", 'warn'], ['Deescalation', f"{r['de_rate']:.0f} per 100 ({r['de_n']} messages)", 'green']]}
        for k, (key, role, g) in enumerate((('esc', 'warn', 'escalation'), ('de', 'green', 'deescalation'))):
            v = r[f'{key}_rate']
            y = yc - bar_h - 1 + k * (bar_h + 2)
            length = max(2.0, (x1 - x0) * min(v, top_v) / top_v)
            sc.group(g)
            sc.rect(x0, y, length, bar_h, role, r=(0, 4, 4, 0), tip=tip)
            sc.text(x0 + length + 6, y + bar_h - 2.5, f'{v:.0f}', size=11, fill='ink', weight=600)
        sc.group(None)
    return sc
