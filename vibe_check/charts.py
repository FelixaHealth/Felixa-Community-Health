"""Vibe Check charts.

Each chart is built once as a `Scene` (rectangles, lines, text in a top-left coordinate
system) and rendered twice: `to_svg` for the web report, `to_drawing` for the PDF. Nothing is
fetched from outside, unlike the original bot, which built QuickChart URLs.

Form choices (dataviz method):
  sentiment split           tall segmented bar   (one whole, 3 parts; a pie is hard to read)
  friendliness score        meter + hero figure  (one ratio against a limit)
  toxicity types, forecast  ranked bars / 2-part segmented bar
  score and flagged trend   line, 2px, one series, no legend
  sentiment trend           stacked columns over time, legend of names
Colour: friendly / neutral / unfriendly use the documented blue / gray / red diverging pair
(checked with the palette validator). Every colour is a named role, so a host can re-theme
through CSS variables (--vc-<role>) and dark mode needs no code change.
"""
from __future__ import annotations

import html
import json
import math

from .common import fmt_int, fmt_score, pct, run_labels, shares, score_label, score_tier, truncate, type_label

W = 720
NARROW_W = 360   # second layout for phones: same data, geometry re-flowed so text stays readable
K = 0.5522847498  # quarter-circle Bézier constant

# Light values are the documented palette; the neutral and red differ in dark mode so the pair
# keeps a colour-vision separation of ΔE ≥ 8 (validated: 14.4 dark, 8.7 light).
ROLES = {
    'light': {
        'surface': '#ffffff', 'ink': '#0b0b0b', 'ink2': '#52514e', 'muted': '#898781',
        'grid': '#e1e0d9', 'axis': '#c3c2b7',
        'series1': '#1f66bf', 'pos': '#1f66bf', 'neu': '#898781', 'neg': '#c93838',
        'good': '#0ca30c', 'warn': '#fab219', 'serious': '#ec835a', 'crit': '#d03b3b',
        'on-fill': '#ffffff', 'on-neu': '#0b0b0b', 'on-warn': '#0b0b0b',
        'cat1': '#0072B2', 'cat2': '#E69F00', 'cat3': '#009E73', 'cat4': '#D55E00', 'cat5': '#CC79A7', 'cat6': '#56B4E9',      # one colour per workplace on the dashboard (colour-blind safe)
    },
    'dark': {
        'surface': '#1a1a19', 'ink': '#f0efec', 'ink2': '#c3c2b7', 'muted': '#898781',
        'grid': '#2c2c2a', 'axis': '#383835',
        'series1': '#3987e5', 'pos': '#3987e5', 'neu': '#c3c2b7', 'neg': '#e66767',
        'good': '#0ca30c', 'warn': '#fab219', 'serious': '#ec835a', 'crit': '#d03b3b',
        'on-fill': '#ffffff', 'on-neu': '#0b0b0b', 'on-warn': '#0b0b0b',
        'cat1': '#56A8E8', 'cat2': '#F0B429', 'cat3': '#2FC197', 'cat4': '#EF7A3A', 'cat5': '#E39BC4', 'cat6': '#7CC4F0',
    },
}


def tier_role(tier: str) -> str:
    """Meter fill carries severity: accent when healthy, warning, then critical."""
    return {'excellent': 'series1', 'good': 'series1', 'attention': 'warn', 'poor': 'crit'}[tier]


# ---- text measuring --------------------------------------------------------------
try:  # reportlab is only used for better width estimates; a fallback keeps this module dependency-free
    from reportlab.pdfbase.pdfmetrics import stringWidth as _sw
except Exception:  # pragma: no cover
    _sw = None


def text_w(s: str, size: float, bold: bool = False) -> float:
    """Estimated rendered width. Browser fonts differ from Helvetica, so add 8% headroom."""
    s = str(s)
    try:
        w = _sw(s, 'Helvetica-Bold' if bold else 'Helvetica', size) if _sw else None
    except Exception:
        w = None
    if w is None:
        w = len(s) * size * (0.60 if bold else 0.55)
    return w * 1.08


def pdf_safe(s) -> str:
    """Standard PDF fonts cover Latin-1. Other scripts become '?' instead of a missing-glyph box."""
    s = str(s or '')
    for a, b in (('—', '-'), ('–', '-'), ('’', "'"), ('‘', "'"), ('“', '"'), ('”', '"'), ('…', '...'), ('→', '>'), ('·', '|')):
        s = s.replace(a, b)
    return s.encode('latin-1', 'replace').decode('latin-1')


def nice_ticks(vmax: float, count: int = 4) -> list[float]:
    vmax = max(float(vmax), 1.0)
    raw = vmax / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    top = math.ceil(vmax / step) * step
    return [round(i * step, 6) for i in range(int(round(top / step)) + 1)]


# ---- scene -----------------------------------------------------------------------
class Scene:
    def __init__(self, width: float, height: float, title: str, desc: str):
        self.width, self.height, self.title, self.desc = width, height, title, desc
        self.items: list[dict] = []

    def _add(self, kind: str, **kw):
        self.items.append({'kind': kind, **kw})

    def rect(self, x, y, w, h, fill, *, r=(0, 0, 0, 0), opacity=1.0, tip=None):
        self._add('rect', x=x, y=y, w=w, h=h, fill=fill, r=r, opacity=opacity, tip=tip)

    def line(self, x1, y1, x2, y2, stroke, width=1.0):
        self._add('line', x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, width=width)

    def poly(self, pts, *, stroke=None, width=2.0, fill=None, fill_opacity=1.0, close=False):
        self._add('poly', pts=pts, stroke=stroke, width=width, fill=fill, fill_opacity=fill_opacity, close=close)

    def circle(self, cx, cy, r, fill):
        self._add('circle', cx=cx, cy=cy, r=r, fill=fill)

    def text(self, x, y, s, *, size=13, fill='ink2', anchor='start', weight=400):
        self._add('text', x=x, y=y, s=str(s), size=size, fill=fill, anchor=anchor, weight=weight)

    def hit(self, x, y, w, h, tip, cx):          # hover/focus target (web only)
        self._add('hit', x=x, y=y, w=w, h=h, tip=tip, cx=cx)

    def cross(self, y1, y2):                      # crosshair (web only)
        self._add('cross', y1=y1, y2=y2)

    @property
    def interactive(self) -> bool:
        return any(i['kind'] == 'hit' or (i['kind'] == 'rect' and i.get('tip')) for i in self.items)


def rrect_ops(x, y, w, h, tl=0, tr=0, br=0, bl=0):
    """Rounded rectangle as M/L/C/Z path operations (shared by SVG and PDF)."""
    m = min(w, h) / 2
    tl, tr, br, bl = (max(0.0, min(float(v), m)) for v in (tl, tr, br, bl))
    ops = [('M', x + tl, y), ('L', x + w - tr, y)]
    if tr:
        ops.append(('C', x + w - tr + K * tr, y, x + w, y + tr - K * tr, x + w, y + tr))
    ops.append(('L', x + w, y + h - br))
    if br:
        ops.append(('C', x + w, y + h - br + K * br, x + w - br + K * br, y + h, x + w - br, y + h))
    ops.append(('L', x + bl, y + h))
    if bl:
        ops.append(('C', x + bl - K * bl, y + h, x, y + h - bl + K * bl, x, y + h - bl))
    ops.append(('L', x, y + tl))
    if tl:
        ops.append(('C', x, y + tl - K * tl, x + tl - K * tl, y, x + tl, y))
    ops.append(('Z',))
    return ops


# ---- SVG renderer ----------------------------------------------------------------
def _n(v) -> str:
    s = f'{v:.2f}'.rstrip('0').rstrip('.')
    return s if s not in ('-0', '') else '0'


def _paint(prop: str, role: str) -> str:
    return f'{prop}:var(--vc-{role},{ROLES["light"][role]})'


def _tip_attr(tip) -> str:
    return html.escape(json.dumps(tip, ensure_ascii=False, separators=(',', ':')), quote=True)


def _tip_label(tip) -> str:
    """Accessible name of a chart mark, built from its tooltip text."""
    rows = ', '.join(f'{r[1]} {r[0]}' for r in (tip.get('r') or []))
    return ': '.join(x for x in (tip.get('t'), rows) if x)


def to_svg(scene: Scene, uid: str) -> str:
    esc = html.escape
    first = [True]

    def mark_attrs(tip) -> str:
        # One tab stop per chart: the first mark; the script moves it with the arrow keys.
        tab = '0' if first[0] else '-1'
        first[0] = False
        return f'tabindex="{tab}" role="img" aria-label="{esc(_tip_label(tip), quote=True)}" data-vc-tip="{_tip_attr(tip)}"'
    role = 'group' if scene.interactive else 'img'
    out = [f'<svg class="vc-svg" viewBox="0 0 {_n(scene.width)} {_n(scene.height)}" role="{role}" '
           f'aria-labelledby="{uid}-t {uid}-d" focusable="false" xmlns="http://www.w3.org/2000/svg">',
           f'<title id="{uid}-t">{esc(scene.title)}</title><desc id="{uid}-d">{esc(scene.desc)}</desc>']
    for it in scene.items:
        k = it['kind']
        if k == 'rect':
            st = _paint('fill', it['fill']) + (f';fill-opacity:{_n(it["opacity"])}' if it['opacity'] < 1 else '')
            extra = ''
            if it.get('tip'):
                extra = f' class="vc-mark" {mark_attrs(it["tip"])}'
            if any(it['r']):
                d = ''.join(op[0] + ' '.join(_n(v) for v in op[1:]) + ' ' if len(op) > 1 else 'Z'
                            for op in rrect_ops(it['x'], it['y'], it['w'], it['h'], *it['r']))
                out.append(f'<path d="{d.strip()}" style="{st}"{extra}/>')
            else:
                out.append(f'<rect x="{_n(it["x"])}" y="{_n(it["y"])}" width="{_n(it["w"])}" height="{_n(it["h"])}" style="{st}"{extra}/>')
        elif k == 'line':
            crisp = ' shape-rendering="crispEdges"' if it['width'] <= 1 else ''
            out.append(f'<line x1="{_n(it["x1"])}" y1="{_n(it["y1"])}" x2="{_n(it["x2"])}" y2="{_n(it["y2"])}" '
                       f'style="{_paint("stroke", it["stroke"])};stroke-width:{_n(it["width"])}"{crisp}/>')
        elif k == 'poly':
            pts = ' '.join(f'{_n(x)},{_n(y)}' for x, y in it['pts'])
            if it['fill']:
                st = _paint('fill', it['fill']) + f';fill-opacity:{_n(it["fill_opacity"])};stroke:none'
                out.append(f'<polygon points="{pts}" style="{st}"/>')
            else:
                st = (f'fill:none;{_paint("stroke", it["stroke"])};stroke-width:{_n(it["width"])};'
                      'stroke-linejoin:round;stroke-linecap:round')
                out.append(f'<polyline points="{pts}" style="{st}"/>')
        elif k == 'circle':
            out.append(f'<circle cx="{_n(it["cx"])}" cy="{_n(it["cy"])}" r="{_n(it["r"])}" style="{_paint("fill", it["fill"])}"/>')
        elif k == 'text':
            out.append(f'<text x="{_n(it["x"])}" y="{_n(it["y"])}" font-size="{_n(it["size"])}" font-weight="{it["weight"]}" '
                       f'text-anchor="{it["anchor"]}" style="{_paint("fill", it["fill"])}">{esc(it["s"])}</text>')
        elif k == 'hit':
            out.append(f'<rect class="vc-hit" x="{_n(it["x"])}" y="{_n(it["y"])}" width="{_n(it["w"])}" height="{_n(it["h"])}" '
                       f'style="fill:transparent" {mark_attrs(it["tip"])} data-vc-x="{_n(it["cx"])}"/>')
        elif k == 'cross':
            out.append(f'<line class="vc-cross" x1="0" y1="{_n(it["y1"])}" x2="0" y2="{_n(it["y2"])}" '
                       f'style="{_paint("stroke", "axis")};stroke-width:1;visibility:hidden;pointer-events:none"/>')
    out.append('</svg>')
    return ''.join(out)


# ---- PDF renderer ----------------------------------------------------------------
def to_drawing(scene: Scene, *, fonts=('Helvetica', 'Helvetica-Bold'), scale: float = 1.0, mode: str = 'light'):
    from reportlab.graphics.shapes import Circle, Drawing, Line, Path, PolyLine, Polygon, Rect, String
    from reportlab.lib.colors import HexColor

    pal = ROLES[mode]
    H = scene.height
    d = Drawing(scene.width * scale, scene.height * scale)
    X = lambda x: x * scale          # noqa: E731
    Y = lambda y: (H - y) * scale    # noqa: E731
    col = lambda role: HexColor(pal[role])   # noqa: E731
    for it in scene.items:
        k = it['kind']
        if k == 'rect':
            if any(it['r']):
                p = Path(fillColor=col(it['fill']), strokeColor=None, strokeWidth=0, fillOpacity=it['opacity'])
                for op in rrect_ops(it['x'], it['y'], it['w'], it['h'], *it['r']):
                    if op[0] == 'M':
                        p.moveTo(X(op[1]), Y(op[2]))
                    elif op[0] == 'L':
                        p.lineTo(X(op[1]), Y(op[2]))
                    elif op[0] == 'C':
                        p.curveTo(X(op[1]), Y(op[2]), X(op[3]), Y(op[4]), X(op[5]), Y(op[6]))
                    else:
                        p.closePath()
                d.add(p)
            else:
                d.add(Rect(X(it['x']), Y(it['y'] + it['h']), it['w'] * scale, it['h'] * scale, fillColor=col(it['fill']),
                           strokeColor=None, strokeWidth=0, fillOpacity=it['opacity']))
        elif k == 'line':
            d.add(Line(X(it['x1']), Y(it['y1']), X(it['x2']), Y(it['y2']), strokeColor=col(it['stroke']),
                       strokeWidth=max(it['width'] * scale, 0.5)))
        elif k == 'poly':
            flat = [c for x, y in it['pts'] for c in (X(x), Y(y))]
            if it['fill']:
                d.add(Polygon(flat, fillColor=col(it['fill']), fillOpacity=it['fill_opacity'], strokeColor=None, strokeWidth=0))
            else:
                d.add(PolyLine(flat, strokeColor=col(it['stroke']), strokeWidth=it['width'] * scale, strokeLineJoin=1,
                               strokeLineCap=1, fillColor=None))
        elif k == 'circle':
            d.add(Circle(X(it['cx']), Y(it['cy']), it['r'] * scale, fillColor=col(it['fill']), strokeColor=None, strokeWidth=0))
        elif k == 'text':
            d.add(String(X(it['x']), Y(it['y']), pdf_safe(it['s']), fontName=fonts[1] if it['weight'] >= 600 else fonts[0],
                         fontSize=it['size'] * scale, fillColor=col(it['fill']), textAnchor=it['anchor']))
    return d


# ---- charts ----------------------------------------------------------------------
def segmented_bar(parts, *, title, desc, width=W, bar_h=28, legend=True):
    """One whole split into parts. parts: [{'name','value','role','on'(label colour),'unit'}]."""
    shown = [p for p in parts if p['value'] > 0]
    total = sum(p['value'] for p in parts)
    if not shown or total <= 0:
        return None
    top, gap = 4, 2
    slots, cx, row = [], 0.0, 0
    for p in shown:                                   # legend layout first: it decides the scene height
        w = 16 + text_w(p['name'], 13)
        if cx > 0 and cx + w > width:
            cx, row = 0.0, row + 1
        slots.append((cx, row, p))
        cx += w + 24
    sc = Scene(width, top + bar_h + ((row + 1) * 22 + 10 if legend else 4), title, desc)
    x, last = 0.0, len(shown) - 1
    for i, p in enumerate(shown):
        full = width * p['value'] / total
        left = x + (gap / 2 if i else 0)
        right = x + full - (gap / 2 if i < last else 0)
        x += full
        if right - left < 0.5:
            continue
        share = pct(p['value'], total)
        tip = {'t': p['name'], 'r': [[p.get('unit', 'messages'), fmt_int(p['value']), p['role']], ['share', f'{share}%', p['role']]]}
        sc.rect(left, top, right - left, bar_h, p['role'], r=(4, 4, 4, 4), tip=tip)
        label = f'{share}%'
        if right - left >= text_w(label, 13, True) + 20:     # print only what fits, never clip
            sc.text(left + 10, top + bar_h / 2 + 4.5, label, size=13, fill=p.get('on', 'on-fill'), weight=600)
    if legend:
        for cx, row, p in slots:
            y = top + bar_h + 22 + row * 22
            sc.rect(cx, y - 9, 10, 10, p['role'], r=(2, 2, 2, 2))
            sc.text(cx + 16, y, p['name'], size=13, fill='ink2')
    return sc


def sentiment_bar(friendly, neutral, unfriendly, *, width=W):
    total = friendly + neutral + unfriendly
    pf, pn, pu = shares([friendly, neutral, unfriendly])
    desc = (f'Of {fmt_int(total)} messages, {fmt_int(friendly)} friendly ({pf}%), '
            f'{fmt_int(neutral)} neutral ({pn}%) and {fmt_int(unfriendly)} unfriendly ({pu}%).')
    return segmented_bar([
        {'name': 'Friendly', 'value': friendly, 'role': 'pos', 'on': 'on-fill'},
        {'name': 'Neutral', 'value': neutral, 'role': 'neu', 'on': 'on-neu'},
        {'name': 'Unfriendly', 'value': unfriendly, 'role': 'neg', 'on': 'on-fill'},
    ], title='Sentiment mix', desc=desc, width=width)


def forecast_bar(improve: int, worsen: int, *, width=W):
    return segmented_bar([
        {'name': 'Chance the community improves', 'value': improve, 'role': 'pos', 'unit': 'points'},
        {'name': 'Chance toxicity increases', 'value': worsen, 'role': 'neg', 'unit': 'points'},
    ], title='Behaviour forecast', desc=f'{improve}% chance the community improves, {worsen}% chance toxicity increases.',
        width=width, bar_h=28)


def score_meter(score: float, *, width=W):
    tier = score_tier(score)
    role = tier_role(tier)
    sc = Scene(width, 42, 'Friendliness score', f'{fmt_score(score)} out of 10: {score_label(score)}.')
    th, y = 12, 4
    sc.rect(0, y, width, th, role, r=(6, 6, 6, 6), opacity=0.18)
    if score > 0:
        sc.rect(0, y, min(width, max(th, width * score / 10)), th, role, r=(6, 6, 6, 6))
    for v, anchor, x in ((0, 'start', 0), (5, 'middle', width / 2), (10, 'end', width)):
        sc.text(x, y + th + 20, v, size=16, fill='ink2', anchor=anchor)    # 12 px or more in the narrowest box (543 px for 720 units, 274 px for 360)
    return sc


def ranked_bars(items, *, title, desc, unit='messages', width=W, max_rows=7):
    """Nominal categories ranked by size: one hue, value at the tip, 4px rounded data end."""
    items = [(k, v) for k, v in sorted(items, key=lambda kv: (-kv[1], kv[0])) if v > 0][:max_rows]
    if not items:
        return None
    row_h, bar_h, top, label_w = 32, 16, 6, (176 if width >= 600 else 108)
    x0, x1 = label_w + 14, width - 64
    mx = max(v for _, v in items)
    sc = Scene(width, top + row_h * len(items) + 4, title, desc)
    sc.line(x0, top, x0, top + row_h * len(items), 'axis', 1)
    for i, (k, v) in enumerate(items):
        yc = top + i * row_h + row_h / 2
        length = max(4.0, (x1 - x0) * v / mx)
        sc.rect(x0, yc - bar_h / 2, length, bar_h, 'series1', r=(0, 4, 4, 0), tip={'t': type_label(k), 'r': [[unit, fmt_int(v), 'series1']]})
        sc.text(label_w, yc + 4.5, type_label(k), anchor='end', fill='ink2')
        sc.text(x0 + length + 8, yc + 4.5, fmt_int(v), fill='ink', weight=600)
    return sc


def toxicity_bars(types: dict, *, title='Toxicity types', width=W):
    items = [(k, v) for k, v in types.items() if v > 0]
    top = sorted(items, key=lambda kv: (-kv[1], kv[0]))[:7]
    desc = 'Toxicity types by count: ' + ', '.join(f'{type_label(k)} {v}' for k, v in top) + '.' if top else title
    return ranked_bars(items, title=title, desc=desc, unit='instances', width=width)


def source_meters(sources, *, width=W, max_rows=10):
    rows = sources[:max_rows]
    if not rows:
        return None
    narrow = width < 600
    row_h, top, name_w = (58 if narrow else 38), 4, 230
    x0, track_w = name_w + 16, 270
    sc = Scene(width, top + row_h * len(rows) + 4, 'Impact score by source',
               'Friendliness score of each source out of 10: ' + '; '.join(f'{s["name"]} {fmt_score(s["score"])}' for s in rows) + '.')
    for i, s in enumerate(rows):
        yc = top + i * row_h + row_h / 2
        role = tier_role(score_tier(s['score']))
        tip = {'t': s['name'], 'r': [['friendliness', f'{fmt_score(s["score"])}/10', role], ['messages', fmt_int(s['analyzed_n']), role]]}
        if narrow:
            ty, tw = top + i * row_h, width - 76
            sc.text(0, ty + 16, truncate(s['name'], 26), fill='ink2')
            sc.text(width, ty + 16, f'{fmt_int(s["analyzed_n"])} messages', size=12, fill='ink2', anchor='end')
            sc.rect(0, ty + 28, tw, 12, role, r=(6, 6, 6, 6), opacity=0.18)
            sc.rect(0, ty + 28, max(12.0, tw * s['score'] / 10), 12, role, r=(6, 6, 6, 6), tip=tip)
            sc.text(tw + 12, ty + 39, f'{fmt_score(s["score"])}/10', fill='ink', weight=600)
            continue
        sc.text(name_w, yc + 4.5, truncate(s['name'], 30), anchor='end', fill='ink2')
        sc.rect(x0, yc - 6, track_w, 12, role, r=(6, 6, 6, 6), opacity=0.18)
        sc.rect(x0, yc - 6, max(12.0, track_w * s['score'] / 10), 12, role, r=(6, 6, 6, 6), tip=tip)
        sc.text(x0 + track_w + 14, yc + 4.5, f'{fmt_score(s["score"])}/10', fill='ink', weight=600)
        sc.text(x0 + track_w + 70, yc + 4.5, f'{fmt_int(s["analyzed_n"])} messages', size=12, fill='ink2')
    return sc


def _x_label_indexes(xs: list[float], min_gap: float = 56) -> list[int]:
    """Labels left to right, skipping any that would touch; the last point is always labelled."""
    n = len(xs)
    keep, last_x = [], -1e9
    for i in range(n):
        if xs[i] - last_x >= min_gap:
            keep.append(i)
            last_x = xs[i]
    if keep and keep[-1] != n - 1:
        if xs[n - 1] - xs[keep[-1]] < min_gap:
            keep.pop()
        keep.append(n - 1)
    return keep


def line_chart(reports, values, *, title, desc, series, y_min=0.0, y_max=10.0, ticks=None, fmt=fmt_score, unit='', width=W, height=None):
    """Single-series trend: 2px line, area wash, ringed markers, value at the end, hover crosshair."""
    n = len(values)
    if n < 2:
        return None
    height = height or (260 if width >= 600 else 232)
    ml, mr, mt, mb = 48, 64, 16, 44
    pw, ph = width - ml - mr, height - mt - mb
    ticks = ticks or [0, 2, 4, 6, 8, 10]
    X = lambda i: ml + pw * i / (n - 1)                                  # noqa: E731
    Y = lambda v: mt + ph * (1 - (v - y_min) / ((y_max - y_min) or 1))   # noqa: E731
    sc = Scene(width, height, title, desc)
    for t in ticks:
        sc.line(ml, Y(t), width - mr, Y(t), 'axis' if t == y_min else 'grid', 1)
        sc.text(ml - 8, Y(t) + 4, fmt(t), size=12, fill='ink2', anchor='end')
    labels = run_labels(reports)
    for i in _x_label_indexes([X(i) for i in range(n)]):
        sc.text(X(i), height - mb + 22, labels[i]['short'], size=12, fill='ink2', anchor='middle')
    pts = [(X(i), Y(v)) for i, v in enumerate(values)]
    sc.poly(pts + [(X(n - 1), Y(y_min)), (X(0), Y(y_min))], fill='series1', fill_opacity=0.10, close=True)
    sc.poly(pts, stroke='series1', width=2)
    for i in (range(n) if n <= 24 else [n - 1]):
        sc.circle(pts[i][0], pts[i][1], 6, 'surface')     # 2px surface ring
        sc.circle(pts[i][0], pts[i][1], 4, 'series1')
    sc.text(pts[-1][0] + 12, pts[-1][1] + 4.5, fmt(values[-1]), size=13, fill='ink', weight=600)
    step = pw / (n - 1)
    for i in range(n):
        tip = {'t': labels[i]['long'], 'r': [[series, f'{fmt(values[i])}{unit}', 'series1']]}
        hw = max(24.0, step)
        lo, hi = max(ml, X(i) - hw / 2), min(width - mr, X(i) + hw / 2)
        sc.hit(lo, mt, hi - lo, ph, tip, X(i))
    sc.cross(mt, mt + ph)
    return sc


def score_trend(reports, multi: bool, *, width=W):
    values = [r['score'] for r in reports]
    name = 'Impact score' if multi else 'Friendliness score'
    return line_chart(reports, values, title=f'{name} over time',
                      desc=f'{name} across {len(values)} Vibe Checks, from {fmt_score(values[0])} to {fmt_score(values[-1])} out of 10.',
                      series=name, unit='/10', width=width)


def flagged_trend(reports, *, width=W):
    values = [r.get('flagged_count') or 0 for r in reports]
    ticks = nice_ticks(max(values + [1]))
    return line_chart(reports, values, title='Flagged messages over time',
                      desc=f'Flagged messages across {len(values)} Vibe Checks, from {values[0]} to {values[-1]}.',
                      series='Flagged messages', y_max=ticks[-1], ticks=ticks, fmt=lambda v: fmt_int(round(v)), width=width)


def sentiment_stack(reports, *, width=W, height=None):
    n = len(reports)
    if n < 2:
        return None
    height = height or (290 if width >= 600 else 260)
    ml, mr, mt, mb, gap = 48, 16, 40, 44, 2
    pw, ph = width - ml - mr, height - mt - mb
    band = pw / n
    col_w = min(24.0, band * 0.6)
    sc = Scene(width, height, 'Sentiment evolution',
               f'Share of friendly, neutral and unfriendly messages in each of {n} Vibe Checks, as a percentage.')
    names = (('friendly', 'Friendly', 'pos'), ('neutral', 'Neutral', 'neu'), ('unfriendly', 'Unfriendly', 'neg'))
    cx = 0.0
    for _, label, role in names:                                   # legend: names in stack order
        sc.rect(cx, 7, 10, 10, role, r=(2, 2, 2, 2))
        sc.text(cx + 16, 16, label, size=13, fill='ink2')
        cx += 16 + text_w(label, 13) + 24
    for t in (0, 25, 50, 75, 100):
        y = mt + ph * (1 - t / 100)
        sc.line(ml, y, width - mr, y, 'axis' if t == 0 else 'grid', 1)
        sc.text(ml - 8, y + 4, f'{t}%', size=12, fill='ink2', anchor='end')
    centers = [ml + band * (i + 0.5) for i in range(n)]
    labels = run_labels(reports)
    for i in _x_label_indexes(centers):
        sc.text(centers[i], height - mb + 22, labels[i]['short'], size=12, fill='ink2', anchor='middle')
    for i, r in enumerate(reports):
        counts = [r.get(k) or 0 for k, _, _ in names]
        total = sum(counts) or 1
        segs = [(label, role, c / total * 100, s) for (k, label, role), c, s in zip(names, counts, shares(counts)) if c > 0]
        y_cursor = mt + ph
        for j, (label, role, share, _) in enumerate(segs):
            top_y = y_cursor - ph * share / 100
            is_top, is_bottom = j == len(segs) - 1, j == 0
            y0 = top_y + (0 if is_top else gap / 2)
            y1 = y_cursor - (0 if is_bottom else gap / 2)
            if y1 - y0 > 0.5:
                sc.rect(centers[i] - col_w / 2, y0, col_w, y1 - y0, role, r=(4, 4, 0, 0) if is_top else (0, 0, 0, 0))
            y_cursor = top_y
        tip = {'t': labels[i]['long'],
               'r': [[label, f'{s}%', role] for (_, label, role), c, s in zip(names, counts, shares(counts))]}
        sc.hit(centers[i] - max(24.0, band) / 2, mt, max(24.0, band), ph, tip, centers[i])
    sc.cross(mt, mt + ph)
    return sc


# ---- small inline icons (status colour + glyph, never colour alone) -----------------
_ICONS = {
    'good': ('good', '<path d="M4.6 8.3l2.2 2.2 4.6-4.9" fill="none" stroke="{g}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'),
    'warn': ('warn', '<path d="M8 4.4v4.3" stroke="{g}" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="11.4" r="1" fill="{g}"/>'),
    'bad': ('crit', '<path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" stroke="{g}" stroke-width="1.8" stroke-linecap="round"/>'),
}


def icon_svg(tone: str, size: int = 16) -> str:
    if tone not in _ICONS:   # 'info' and anything unknown: a plain bullet, no status implied
        return (f'<svg class="vc-icon" width="{size}" height="{size}" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
                f'<circle cx="8" cy="8" r="3" style="{_paint("fill", "muted")}"/></svg>')
    role, glyph = _ICONS[tone]
    g = f'var(--vc-{"on-warn" if tone == "warn" else "on-fill"},{"#0b0b0b" if tone == "warn" else "#ffffff"})'
    return (f'<svg class="vc-icon" width="{size}" height="{size}" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
            f'<circle cx="8" cy="8" r="8" style="{_paint("fill", role)}"/>{glyph.format(g=g)}</svg>')
