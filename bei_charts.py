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
BAND_ROLES = {'hostile': ('good', 'warn', 'crit'), 'dig': ('good', 'warn', 'warn')}      # the colour of Rare, Some and Often (a band is a faint wash, never the only signal)
BAND_WORDS = ('Rare', 'Some', 'Often')           # written inside a band that is tall enough, so the bands can be told apart in print and in grey
BAND_WASH = 0.10                                 # a level band is a faint wash ...
MOMENT_WASH = 0.40                               # ... and a heated moment is clearly darker than any of them, in colour and in grey
SAID = {'hostile': ('messages that were openly hostile', 'were openly hostile'), 'care': ('messages that showed care', 'showed care'),
        'dig': ('messages that were sarcastic', 'were sarcastic'), 'heated': ('messages that were heated', 'were heated')}      # the tooltip row, and the words of the description


def _whole(x) -> int:
    return js_round(x + 1e-9)


def _sizes(width) -> tuple:
    """(tick and axis labels, the value at the end of the line, band names) in chart units. The phone layout is drawn 360 units wide and shown about 290 pixels
    wide, so its text is drawn larger to stay near 12 pixels."""
    return (14, 16, 12) if width < 600 else (13, 14, 12)


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


def _markers(sc, pts) -> None:
    """A ringed dot for every part; smaller ones when there are many, so that they do not run together into a bead string."""
    outer, inner = (4.5, 3) if len(pts) <= 12 else (3, 2)
    for x, y in pts:
        sc.circle(x, y, outer, 'surface')
        sc.circle(x, y, inner, 'series1')


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


def _moments_said(runs) -> str:
    if not runs:
        return ''
    place = [f'part {a}' if a == b else f'parts {a} to {b}' for a, b in runs]
    return f' Heated moment{"" if len(runs) == 1 else "s"}: ' + (place[0] if len(place) == 1 else ', '.join(place[:-1]) + ' and ' + place[-1]) + '.'


def measure_chart(card, stretches, bands=None, *, width=W):
    """A small line chart of one measure: the rate per 100 messages in each part of the chat. `card` is a card from measures.cards, `stretches` the parts
    from measures.analyse, `bands` the (low, high) cut-offs behind Rare, Some and Often (or None). The heated moments are shaded, darker than the bands, and a band
    that is tall enough carries its name. One 2px line, value at the end, a hover target for every part (the targets tile the plot, so the right part always
    answers), and a table twin kept by the page."""
    values = [float(v) for v in card['series']]
    n = len(values)
    if n < 2:
        return None
    narrow = width < 600
    tick_size, end_size, band_size = _sizes(width)
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
    counted = _runs([bool(st.get('counted')) for st in stretches])
    desc = (f'How many of every 100 messages {pred}, in each of {n} parts of the chat. The first part had {first}, the last had {last}, '
            + (f'and the highest was part {peak + 1} with {_whole(values[peak])}.' if _whole(values[peak]) else 'and none had any.') + _moments_said(counted))
    sc = Scene(width, height, f'{name} over time', desc)
    named = []                                              # (top, height, word) of every band that can carry its name
    if bands and kind in BAND_ROLES:
        low, high = bands
        for k, (a, b) in enumerate(((0.0, low), (low, high), (high, top))):
            if b > a and a < top:
                b = min(b, top)
                sc.rect(ml, Y(b), pw, Y(a) - Y(b), BAND_ROLES[kind][k], opacity=BAND_WASH)
                named.append((Y(b), Y(a) - Y(b), BAND_WORDS[k]))
    elif kind == 'care' and bands and bands[1] < top:
        sc.rect(ml, Y(top), pw, Y(bands[1]) - Y(top), 'good', opacity=BAND_WASH)
        named.append((Y(top), Y(bands[1]) - Y(top), BAND_WORDS[2]))
    step = pw / (n - 1)
    for i, st in enumerate(stretches):
        if st.get('counted'):
            x0, x1 = max(ml, X(i) - step / 2), min(ml + pw, X(i) + step / 2)
            sc.rect(x0, mt, x1 - x0, ph, 'warn', opacity=MOMENT_WASH)
    for t in ticks:
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + tick_size * 0.33, _whole(t), size=tick_size, fill='ink2', anchor='end')
    _axis_labels(sc, [(X(i), st.get('axis') or '') for i, st in enumerate(stretches)], tick_size, height - mb + tick_size + 7)
    for y0, h, word in named:                               # a name hangs from the top edge of its band, away from a line that runs along the bottom of it
        if h >= band_size + 2:
            sc.text(ml + 10, y0 + band_size, word, size=band_size, fill='ink2')
    pts = [(X(i), Y(v)) for i, v in enumerate(values)]
    sc.poly(pts, stroke='series1', width=2)
    _markers(sc, pts)
    sc.text(pts[-1][0] + 10, pts[-1][1] + end_size * 0.35, _whole(values[-1]), size=end_size, fill='ink', weight=600)
    for i, st in enumerate(stretches):
        lo, hi = max(ml, X(i) - step / 2), min(width - mr, X(i) + step / 2)
        rows = [[said, f'{st["counts"][kind]:,} of {st["n"]:,}', 'series1'], ['per 100 messages', f'{_whole(values[i])}', 'series1']]
        if st.get('counted'):
            rows.append(['part of a heated moment', f'up {_whole(st["climb"])} per 100 messages', 'warn'])
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
    tick_size, end_size, _ = _sizes(width)
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


def health_meter(score, tone, *, width=W):
    """The health score on its 0 to 100 track. Thin ticks mark where the status changes (40, 60 and 80), the numbers sit under them, and the four status words
    sit under their segments: on the same line when they all fit between the numbers, else on a line of their own."""
    from measures import HEALTH_STATUS
    role = {'good': 'series1', 'info': 'neu', 'bad': 'crit', 'warn': 'warn'}.get(tone, 'series1')
    num, word = (16, 13) if width < 420 else (14, 12) if width < 600 else (16, 14)      # the phone layout (360), the PDF (500, drawn at 0.7 points a unit) and the wide layout (720)
    steps = sorted(HEALTH_STATUS)                                              # [(0, 'Needs attention'), (40, 'Fair'), (60, 'Good'), (80, 'Excellent')]
    edges = [floor for floor, _ in steps] + [100]
    th, y = 12, 6
    base1 = y + th + 7 + num * 0.85                                            # baseline of the numbers
    x_of = lambda v: width * v / 100                                           # noqa: E731

    def number_span(v):                                                        # the room a number takes: the first starts at the left edge, the last ends at the right one
        w = text_w(str(v), num)
        return (0.0, w) if v == edges[0] else (width - w, width) if v == edges[-1] else (x_of(v) - w / 2, x_of(v) + w / 2)
    numbers = [number_span(v) for v in edges]
    widths = [text_w(text, word) for _, text in steps]
    room = [(numbers[i][1] + 5, numbers[i + 1][0] - 5) for i in range(len(steps))]            # the space between the two numbers that frame a segment
    one_row = all(room[i][1] - room[i][0] >= widths[i] for i in range(len(steps)))
    base2 = base1 if one_row else base1 + word + 5
    centre = [min(max(x_of((a + b) / 2), room[i][0] + widths[i] / 2), room[i][1] - widths[i] / 2) if one_row else x_of((a + b) / 2)       # a word may lean a little to fit between its numbers
              for i, ((a, _), b) in enumerate(zip(steps, edges[1:]))]
    shown = _whole(score)
    status = next(text for floor, text in reversed(steps) if shown >= floor)
    sc = Scene(width, base2 + 6, 'Health score', f'{shown} out of 100, which is {status}. ' + ', '.join(
        f'{text} {f"under {edges[i + 1]}" if i == 0 else f"{floor} and over" if i == len(steps) - 1 else f"{floor} to {edges[i + 1] - 1}"}' for i, (floor, text) in enumerate(steps)) + '.')
    sc.rect(0, y, width, th, role, r=(6, 6, 6, 6), opacity=0.18)
    if score > 0:
        sc.rect(0, y, min(width, max(th, width * score / 100)), th, role, r=(6, 6, 6, 6))
    for v in edges[1:-1]:
        sc.line(x_of(v), y - 3, x_of(v), y + th + 5, 'ink2', 1)
    for v in edges:
        sc.text(x_of(v), base1, v, size=num, fill='ink2', anchor='start' if v == edges[0] else 'end' if v == edges[-1] else 'middle')
    for i, (_, text) in enumerate(steps):
        sc.text(centre[i], base2, text, size=word, fill='ink2', anchor='middle')
    return sc
