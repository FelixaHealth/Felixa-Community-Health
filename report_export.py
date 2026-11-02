"""Felixa Community Health branded PDF report: alerts, badges and measures over time (measures_export.py), summary and guidance, measured behaviour,
Vibe Check, method. Every page after the cover carries the footer note FOOTER_NOTE."""
import io
import os
import re
from html import escape
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import analysis
import bei_charts
import measures_export
from vibe_check import pdf_flowables
from vibe_check.charts import W as CHART_W
from vibe_check.charts import pdf_safe, to_drawing
from vibe_check.common import fmt_int

FONTS = ('FelixaSans', 'FelixaSansBold')
FOOTER_NOTE = 'Model-rated wording. Not a verdict on people.'      # printed in the footer of every page after the cover


def _register_fonts():
    fontdir = Path(reportlab.__file__).parent / 'fonts'
    for name, filename in (('FelixaSans', 'Vera.ttf'), ('FelixaSansBold', 'VeraBd.ttf')):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fontdir / filename)))


_COVER_CACHE = {}


def _cover_artwork(path: Path):
    """The cover artwork, shrunk to what a 330pt-wide print needs. The source file is 2.3 MB and would otherwise
    add that to every PDF. Returns a BytesIO of JPEG data, or None when there is no artwork."""
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime)
    if key not in _COVER_CACHE:
        try:
            from PIL import Image as PILImage
            with PILImage.open(path) as im:
                im = im.convert('RGB')
                im.thumbnail((900, 900))
                buf = io.BytesIO()
                im.save(buf, 'JPEG', quality=86, optimize=True)
                _COVER_CACHE[key] = buf.getvalue()
        except Exception:
            _COVER_CACHE[key] = path.read_bytes()
    return io.BytesIO(_COVER_CACHE[key])


def pdf(ctx, base: Path) -> bytes:
    _register_fonts()
    a, vibe = ctx['a'], ctx['vibe']
    out = io.BytesIO()
    styles = getSampleStyleSheet()
    ink, soft = colors.HexColor('#212121'), colors.HexColor('#555555')
    styles.add(ParagraphStyle('BeiBody', fontName=FONTS[0], fontSize=10.5, leading=15, spaceAfter=10, textColor=ink))
    styles.add(ParagraphStyle('BeiTitle', fontName=FONTS[1], fontSize=32, leading=37, spaceAfter=20, textColor=ink))
    styles.add(ParagraphStyle('BeiHead', fontName=FONTS[1], fontSize=23, leading=29, spaceAfter=16, textColor=ink, keepWithNext=True))
    styles.add(ParagraphStyle('BeiSub', fontName=FONTS[1], fontSize=13, leading=18, spaceAfter=8, textColor=ink, keepWithNext=True))
    styles.add(ParagraphStyle('BeiSmall', fontName=FONTS[0], fontSize=8.5, leading=12, spaceAfter=8, textColor=soft, wordWrap='CJK'))
    # Measures over time sits in table cells and narrow columns, so its type is set tighter than the body text. These styles wrap at spaces: the CJK wrap of
    # BeiSmall cuts a word in two when the line before it is short, which a 40 point column makes happen all the time.
    styles.add(ParagraphStyle('BeiCell', fontName=FONTS[0], fontSize=8.5, leading=12, spaceAfter=8, textColor=soft))
    styles.add(ParagraphStyle('BeiCardHead', fontName=FONTS[1], fontSize=11, leading=14, spaceAfter=3, textColor=ink))
    styles.add(ParagraphStyle('BeiCardText', fontName=FONTS[0], fontSize=9, leading=12.5, spaceAfter=3, textColor=ink))
    styles.add(ParagraphStyle('BeiCardNote', fontName=FONTS[0], fontSize=8.5, leading=11.5, spaceAfter=3, textColor=soft))
    styles.add(ParagraphStyle('BeiBig', fontName=FONTS[1], fontSize=32, leading=36, spaceAfter=2, textColor=ink))
    story = []

    def p(text, style='BeiBody'):
        t = pdf_safe(escape('' if text is None else str(text))).replace('\n', '<br/>')       # a 0 is text too: `text or ''` would drop it
        t = re.sub(r'\*\*(.+?)\*\*', rf'<font name="{FONTS[1]}">\1</font>', t)
        return Paragraph(t, styles[style])

    def page(text):
        story.append(PageBreak())
        story.append(p(text, 'BeiHead'))

    def table(rows, widths):
        """A table. A cell is text (set in the small style) or a flowable already made with p()."""
        data = [[x if isinstance(x, Flowable) else p(x, 'BeiSmall') for x in row] for row in rows]
        t = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F2F2F2')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#F27435')),
                               ('LINEBELOW', (0, 1), (-1, -1), .4, colors.HexColor('#D8D8D8')),
                               ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                               ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        story.extend([t, Spacer(1, 12)])

    # cover
    logo = base / 'static' / 'logo_stacked.png'
    if logo.exists():
        iw, ih = ImageReader(str(logo)).getSize()
        im = Image(str(logo), width=75, height=75 * ih / iw)
        im.hAlign = 'LEFT'
        story.append(im)
    story += [Spacer(1, 24), p('FELIXA COMMUNITY HEALTH', 'BeiSub'), p(a['person_name'] or 'Untitled analysis', 'BeiTitle')]
    if ctx['facts']:
        story.append(p(' | '.join(ctx['facts'])))
    if a['user_name']:
        story.append(p('Looked after by ' + ', '.join(x for x in (a['user_name'], a['user_role']) if x)))
    story += [p('Communication behaviour report'), Spacer(1, 12),
              p(f'Prepared {ctx["generated"]} | Analysis {ctx["aid"]}')]
    if ctx['period_text']:
        story.append(p(ctx['period_text']))
    if ctx['goals'] or a['goal_text']:
        story.append(p('Goals: ' + ', '.join(ctx['goals'] + ([a['goal_text']] if a['goal_text'] else []))))
    story.append(p('Evidence-based working report. Ratings come from a language model and need review before anyone acts on them.'))
    story += [p(f'**{title}: not available.** {reason}') for title, reason in ctx['gaps']]      # a part with no results is said so, not left out silently
    art = _cover_artwork(base / 'static' / 'report_cover.png')
    if art:
        iw, ih = ImageReader(art).getSize()
        art.seek(0)
        im = Image(art, width=330, height=330 * ih / iw)
        im.hAlign = 'LEFT'
        story += [Spacer(1, 10), im]
    contact = os.getenv('FELIXA_REPORT_CONTACT', '').strip()
    if contact:
        story += [Spacer(1, 10), p(contact)]

    # alerts, badges and the measures over time
    if ctx.get('measures'):
        measures_export.pdf_pages(ctx['measures'], story, p, page, table, lambda scene: to_drawing(scene, fonts=FONTS, scale=504 / CHART_W), small=lambda t: p(t, 'BeiSmall'))

    # summary and guidance
    g = ctx['guidance']
    if g:
        page('Summary and guidance')
        if g.get('summary'):
            story.append(p(g['summary']))
        for title, key in (('What appears to be working', 'working'), ('What needs investigation', 'limitations')):
            items = [x for x in g.get(key) or [] if isinstance(x, dict)]
            story.append(p(title, 'BeiSub'))
            story += [p(f'**{x.get("title", "")}** {x.get("body", "")}') for x in items] or [p('No supported finding was generated.', 'BeiSmall')]
        story.append(p('What to do next', 'BeiSub'))
        for i, x in enumerate([x for x in g.get('recommendations') or [] if isinstance(x, dict)], 1):
            story.append(p(f'{i}. **{x.get("action", "")}**\n{x.get("why", "")} Measure: {x.get("measure", "")}'))
        if g.get('confidence'):
            story.append(p(f'Evidence confidence: {g["confidence"]}. {g.get("confidence_reason", "")}', 'BeiSmall'))

    # measured behaviour
    if ctx['metrics']:
        page('Measured behaviour')
        coverage = (f'{fmt_int(ctx["rated_n"])} of {fmt_int(ctx["rated_total"])} communication units were rated (sampled evenly).' if ctx['sampled']
                    else f'{fmt_int(ctx["rated_n"])} communication units were rated.')
        if ctx['added_since']:
            coverage += f' {fmt_int(ctx["added_since"])} added after the signals were last rated are not included; run FELIXA again.'
        story.append(p(coverage))
        if ctx['families']:
            table([['Family', 'Average FAS', 'Strongest signal']] + [[f['name'], f'{f["avg_fas"]:.1f}', f'{f["top"]} ({f["top_fas"]:.1f})'] for f in ctx['families']], [140, 100, 264])
        scene = bei_charts.attribute_meters(ctx['metrics'])
        if scene:
            story.append(to_drawing(scene, fonts=FONTS, scale=504 / CHART_W))
            story.append(Spacer(1, 10))
        table([['Signal', 'Family', 'FAS', 'Detection', 'Strength', 'N']] + bei_charts.attribute_table_rows(ctx['metrics']), [110, 80, 70, 90, 90, 64])

    # Vibe Check
    if vibe:
        story.append(PageBreak())
        story.extend(pdf_flowables(vibe, fonts=FONTS, notice=ctx['vibe_notice']))

    # evidence and method
    page('Evidence and how this report works')
    if ctx['evidence']:
        story.append(p('Evidence loaded', 'BeiSub'))
        table([['Source', 'Type', 'Units']] + [[e['source_label'], e['source_type'], fmt_int(e['n'])] for e in ctx['evidence']], [300, 100, 104])
    story.append(p('How this report works', 'BeiSub'))
    for heading, text in analysis.HOW_IT_WORKS:
        story += [p(f'**{heading}**'), p(text)]
    if a['model_used']:
        story.append(p(f'Model: {a["model_used"]}.', 'BeiSmall'))

    def furniture(canvas, doc):
        canvas.setStrokeColor(colors.HexColor('#F27435'))
        canvas.line(54, 45, 558, 45)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#555555'))
        canvas.drawString(54, 32, f'Felixa Community Health | {ctx["generated"]}')
        if doc.page > 1:
            canvas.drawCentredString(306, 32, pdf_safe(FOOTER_NOTE))       # every page after the cover says the wording was rated by a model
        canvas.drawRightString(558, 32, str(doc.page))

    SimpleDocTemplate(out, pagesize=(612, 792), rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=60,
                      title=pdf_safe('Felixa Community Health | ' + (a['person_name'] or 'Report')), author='Felixa Community Health').build(story, onFirstPage=furniture, onLaterPages=furniture)
    return out.getvalue()
