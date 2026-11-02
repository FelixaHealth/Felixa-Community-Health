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


def _whole(x) -> int:
    return js_round(x + 1e-9)


def measure_chart(card, stretches, bands=None, *, width=W):
    """A small line chart of one measure: the rate per 100 messages in each stretch of the chat. `card` is a card from measures.cards, `stretches` the stretches
    from measures.analyse, `bands` the (low, high) cut-offs behind Rare, Some and Often (or None). The heated-moment stretches are shaded. One 2px line,
    value at the end, a hover target for every stretch (the targets tile the plot, so the right stretch always answers), and a table twin kept by the page."""
    values = [float(v) for v in card['series']]
    n = len(values)
    if n < 2:
        return None
    narrow = width < 600
    height = 168 if narrow else 150
    ml, mr, mt, mb = 40, 46, 16, 32
    pw, ph = width - ml - mr, height - mt - mb
    ticks = nice_ticks(max(values + [25.0]), 2)
    top = ticks[-1]
    X = lambda i: ml + pw * i / (n - 1)                       # noqa: E731
    Y = lambda v: mt + ph * (1 - min(max(v, 0.0), top) / top)    # noqa: E731
    kind = card['kind']
    name = card['name']
    sc = Scene(width, height, f'{name} over time', f'{name}: messages per 100 in each of {n} parts of the chat, from {_whole(values[0])} to {_whole(values[-1])}. '
                                                  f'The highest part is {_whole(max(values))}.')
    if bands and kind in BAND_ROLES:
        low, high = bands
        edges = [(0.0, low, BAND_ROLES[kind][0]), (low, high, BAND_ROLES[kind][1]), (high, top, BAND_ROLES[kind][2])]
        for a, b, role in edges:
            if b > a and a < top:
                b = min(b, top)
                sc.rect(ml, Y(b), pw, Y(a) - Y(b), role, opacity=0.10)
    elif kind == 'care' and bands:
        sc.rect(ml, Y(top), pw, Y(min(bands[1], top)) - Y(top), 'good', opacity=0.10) if bands[1] < top else None
    step = pw / (n - 1)
    for i, st in enumerate(stretches):
        if st.get('counted'):
            sc.rect(max(ml, X(i) - step / 2), mt, min(step, pw), ph, 'warn', opacity=0.22)
    for t in ticks:
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + 4, _whole(t), size=12, fill='ink2', anchor='end')
    last_x = -1e9
    for i, st in enumerate(stretches):
        label = st.get('axis') or ''
        if label and X(i) - last_x >= text_w(label, 12) + 10:
            sc.text(X(i), height - mb + 20, label, size=12, fill='ink2', anchor='middle')
            last_x = X(i)
    pts = [(X(i), Y(v)) for i, v in enumerate(values)]
    sc.poly(pts, stroke='series1', width=2)
    for x, y in pts:
        sc.circle(x, y, 4.5, 'surface')
        sc.circle(x, y, 3, 'series1')
    sc.text(pts[-1][0] + 10, pts[-1][1] + 4.5, _whole(values[-1]), size=13, fill='ink', weight=600)
    for i, st in enumerate(stretches):
        lo, hi = max(ml, X(i) - step / 2), min(width - mr, X(i) + step / 2)
        what = {'hostile': 'openly hostile', 'care': 'showed care', 'dig': 'sarcastic', 'heated': 'heated'}[kind]
        rows = [[f'messages that were {what}', f'{st["counts"][kind]:,} of {st["n"]:,}', 'series1'], ['per 100 messages', f'{_whole(values[i])}', 'series1']]
        if st.get('counted'):
            rows.append(['a heated moment', f'climb of {_whole(st["climb"])}', 'warn'])
        sc.hit(lo, mt, hi - lo, ph, {'t': st['label'], 'r': rows}, X(i))
    sc.cross(mt, mt + ph)
    return sc


def trail_chart(trail, key, *, title, series, width=W):
    """The health score check by check: one point per saved check, 0 to 100. Needs three checks, because a line through two says little."""
    from vibe_check.common import run_labels
    if len(trail) < 3:
        return None
    labels = [{'short': f'Check {i}', 'long': f'Check {i} \u00b7 ' + (x['long'].split(' \u00b7 ', 1)[1] if x['long'].startswith('Run ') else x['long'])} for i, x in enumerate(run_labels(trail), 1)]
    values = [float(r.get(key) or 0) for r in trail]
    n = len(values)
    narrow = width < 600
    height = 200 if narrow else 180
    ml, mr, mt, mb = 40, 46, 16, 36
    pw, ph = width - ml - mr, height - mt - mb
    X = lambda i: ml + pw * i / (n - 1)                       # noqa: E731
    Y = lambda v: mt + ph * (1 - min(max(v, 0.0), 100.0) / 100.0)    # noqa: E731
    sc = Scene(width, height, title, f'{series} in each of {n} checks, from {_whole(values[0])} to {_whole(values[-1])} out of 100.')
    for t in (0, 25, 50, 75, 100):
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, Y(t) + 4, t, size=12, fill='ink2', anchor='end')
    last_x = -1e9
    for i in range(n):
        if X(i) - last_x >= text_w(labels[i]['short'], 12) + 10:
            sc.text(X(i), height - mb + 22, labels[i]['short'], size=12, fill='ink2', anchor='middle')
            last_x = X(i)
    pts = [(X(i), Y(v)) for i, v in enumerate(values)]
    sc.poly(pts, stroke='series1', width=2)
    for x, y in pts:
        sc.circle(x, y, 4.5, 'surface')
        sc.circle(x, y, 3, 'series1')
    sc.text(pts[-1][0] + 10, pts[-1][1] + 4.5, _whole(values[-1]), size=13, fill='ink', weight=600)
    step = pw / (n - 1)
    for i in range(n):
        lo, hi = max(ml, X(i) - step / 2), min(width - mr, X(i) + step / 2)
        sc.hit(lo, mt, hi - lo, ph, {'t': labels[i]['long'], 'r': [[series, f'{_whole(values[i])} out of 100', 'series1'], ['messages rated', f'{trail[i].get("n", 0):,}', 'neu']]}, X(i))
    sc.cross(mt, mt + ph)
    return sc


def health_meter(score, tone, *, width=W):
    """The health score on its 0 to 100 track, with the three status edges (40, 60 and 80) marked."""
    role = {'good': 'series1', 'info': 'neu', 'bad': 'crit', 'warn': 'warn'}.get(tone, 'series1')
    sc = Scene(width, 42, 'Health score', f'{_whole(score)} out of 100.')
    th, y = 12, 4
    sc.rect(0, y, width, th, role, r=(6, 6, 6, 6), opacity=0.18)
    if score > 0:
        sc.rect(0, y, min(width, max(th, width * score / 100)), th, role, r=(6, 6, 6, 6))
    for v in (0, 40, 60, 80, 100):
        sc.text(width * v / 100, y + th + 20, v, size=13, fill='ink2', anchor='start' if v == 0 else 'end' if v == 100 else 'middle')
    return sc
