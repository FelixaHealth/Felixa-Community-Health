"""Charts for the 13-signal behaviour analysis, built with the same scene toolkit as Vibe Check
(inline SVG for the web, drawings for the PDF)."""
from vibe_check.charts import NARROW_W, W, Scene
from vibe_check.common import fmt_int

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
