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
from reportlab.platypus import CondPageBreak, Flowable, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import analysis
import bei_charts
import measures_export
from vibe_check import pdf_flowables
from vibe_check.charts import W as CHART_W
from vibe_check.charts import pdf_safe, to_drawing
from vibe_check.common import fmt_int

FONTS = ('FelixaSans', 'FelixaSansBold')
FOOTER_NOTE = 'Model-rated wording. Not a verdict on people.'      # printed in the footer of every page after the cover
TEXT_W = measures_export.PAGE_W                                     # 492 points: the text between the margins (504), less 6 points of frame padding on each side
KEEP_MAX = 280                                                      # the last rows of a table stay together only when they take no more than this many points
ART_W, ART_GAP, ART_MIN = 330, 10, 150                              # the cover artwork: its width, the air above it, and the least height (points) at which it is still shown


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


class CoverArt(Flowable):
    """The cover artwork, set under the cover text. It takes the room the cover has left: it is shrunk to fit it, and it is left out when that room is under ART_MIN
    points, so that it can never be pushed alone onto a page of its own (a page 2 with a picture and nothing else, when the cover text is long). `reserve` is
    room (points) kept free under it for what follows on the cover."""

    def __init__(self, art, reserve=0):
        super().__init__()
        self.reader = ImageReader(art)
        iw, ih = self.reader.getSize()
        self.ratio, self.reserve, self.hAlign, self.size = ih / iw, reserve, 'LEFT', None

    def wrap(self, availWidth, availHeight):
        full = ART_W * self.ratio
        room = availHeight - ART_GAP - self.reserve - 1
        if room < ART_MIN:
            self.size = None                                    # not enough room left on this page: no artwork
            return 0, 0
        scale = min(1.0, room / full)
        self.size = (ART_W * scale, full * scale)
        return self.size[0], self.size[1] + ART_GAP

    def draw(self):
        if self.size:
            self.canv.drawImage(self.reader, 0, 0, self.size[0], self.size[1])


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
    # the same heading without keepWithNext: reportlab would glue a keepWithNext heading and the table after it into one block that jumps to the next page whole
    # when it does not fit. A heading that opens a table that may run over a page sits behind a CondPageBreak instead (measures_export), and the table splits.
    styles.add(ParagraphStyle('BeiSubFree', parent=styles['BeiSub'], keepWithNext=False))
    # the headings inside Measures over time (Since your last check, Health score check by check): a little smaller than the heading of a part of the report, with
    # some air above them, which a page leaves out by itself when the heading starts the page
    styles.add(ParagraphStyle('BeiSubBelow', fontName=FONTS[1], fontSize=12, leading=16, spaceBefore=8, spaceAfter=4, textColor=ink, keepWithNext=True))
    styles.add(ParagraphStyle('BeiContact', parent=styles['BeiBody'], spaceBefore=10))        # the contact line of the cover: the air above it is its own, not a Spacer
    styles.add(ParagraphStyle('BeiSmall', fontName=FONTS[0], fontSize=8.5, leading=12, spaceAfter=8, textColor=soft, wordWrap='CJK'))
    styles.add(ParagraphStyle('BeiCaveat', fontName=FONTS[0], fontSize=8.5, leading=11.5, spaceAfter=6, textColor=ink))      # the caveats, and the line of what a section is built from
    # Measures over time sits in table cells and narrow columns, so its type is set tighter than the body text: a first check (four cards with their charts, the
    # health score tile and its sentence) has to fit one page. These styles wrap at spaces: the CJK wrap of BeiSmall cuts a word in two when the line before it is
    # short, which a 40 point column makes happen all the time.
    styles.add(ParagraphStyle('BeiCell', fontName=FONTS[0], fontSize=8.5, leading=12, spaceAfter=8, textColor=soft))
    styles.add(ParagraphStyle('BeiCardHead', fontName=FONTS[1], fontSize=11, leading=13.5, spaceAfter=2, textColor=ink))
    styles.add(ParagraphStyle('BeiCardText', fontName=FONTS[0], fontSize=9, leading=11.5, spaceAfter=2, textColor=ink))
    styles.add(ParagraphStyle('BeiCardNote', fontName=FONTS[0], fontSize=8.5, leading=10.5, spaceAfter=2, textColor=soft))
    styles.add(ParagraphStyle('BeiKey', parent=styles['BeiCardNote'], spaceAfter=5))        # the sentence for the lines and bands of the charts: a little air before the first card
    styles.add(ParagraphStyle('BeiBig', fontName=FONTS[1], fontSize=32, leading=36, spaceAfter=2, textColor=ink))
    styles.add(ParagraphStyle('BeiMethodHead', fontName=FONTS[1], fontSize=10, leading=14, spaceAfter=1, textColor=ink))        # an entry of the method text: close to its text
    styles.add(ParagraphStyle('BeiMethod', fontName=FONTS[0], fontSize=9.5, leading=13.5, spaceAfter=7, textColor=ink))        # and its text, a little tighter than the body: the whole method keeps to one page
    story = []

    def p(text, style='BeiBody', tail=''):
        """A paragraph. Every string comes through here: it is escaped first, and the only tags are the ones this function adds. `**bold**` in the text is set in
        the bold face. `tail` is set after the text, in the small grey type of a note (the tone in words beside the name of a measure)."""
        t = pdf_safe(escape('' if text is None else str(text))).replace('\n', '<br/>')       # a 0 is text too: `text or ''` would drop it
        t = re.sub(r'\*\*(.+?)\*\*', rf'<font name="{FONTS[1]}">\1</font>', t)
        if tail:
            t += f' <font name="{FONTS[0]}" size="8.5" color="#555555">{pdf_safe(escape(str(tail)))}</font>'
        return Paragraph(t, styles[style])

    def page(text):
        story.append(PageBreak())
        story.append(p(text, 'BeiHead'))

    def table(rows, widths, tight=False):
        """A table. A cell is text (set in the small style) or a flowable already made with p(). A table that runs over a page splits between its rows and repeats
        its header row. Its last rows (up to three) stay together, so that a repeated header is never left over a single row. They are kept together only when
        they take no more than KEEP_MAX points: a group taller than a page could not be placed at all. A `tight` table has less air in its rows (the list of
        evidence, which shares its page with the whole method text)."""
        data = [[x if isinstance(x, Flowable) else p(x, 'BeiSmall') for x in row] for row in rows]
        style = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F2F2F2')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                 ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#F27435')),
                 ('LINEBELOW', (0, 1), (-1, -1), .4, colors.HexColor('#D8D8D8')),
                 ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                 ('TOPPADDING', (0, 0), (-1, -1), 3 if tight else 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 3 if tight else 5)]

        def make(extra=()):
            grid = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT', spaceAfter=8 if tight else 12)       # space after, not a Spacer: a Spacer that does not fit makes an empty page
            grid.setStyle(TableStyle(style + list(extra)))
            return grid
        t, n = make(), len(rows)
        if n >= 4:
            t.wrap(TEXT_W, 10_000)                          # the row heights are known after a wrap; a table is laid out afresh when it is wrapped again
            heights = list(getattr(t, '_rowHeights', None) or [])
            kept = min(3, n - 2)
            while kept >= 2 and len(heights) == n and sum(heights[n - kept:]) > KEEP_MAX:
                kept -= 1
            if kept >= 2 and len(heights) == n:
                t = make([('NOSPLIT', (0, n - kept), (-1, n - 1))])
        story.append(t)

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
    contact = os.getenv('FELIXA_REPORT_CONTACT', '').strip()
    contact_line = p(contact, 'BeiContact') if contact else None
    art = _cover_artwork(base / 'static' / 'report_cover.png')
    if art:           # the artwork takes the room the cover has left (less the contact line), or none at all: it is never pushed alone onto page 2
        story.append(CoverArt(art, reserve=contact_line.wrap(TEXT_W, 10_000)[1] + 10 + 10 if contact_line else 0))
    if contact_line:
        story.append(contact_line)

    # alerts, badges and the measures over time
    if ctx.get('measures'):
        measures_export.pdf_pages(ctx['measures'], story, p, page, table, lambda scene: to_drawing(scene, fonts=FONTS, scale=TEXT_W / CHART_W), small=lambda t: p(t, 'BeiSmall'))

    # the questions that were asked, answered
    if ctx.get('answers'):
        page('Your questions answered')
        story.append(p('These answers use only the numbers in this report. Each says what the messages show and what they cannot tell.', 'BeiSmall'))
        for ans in ctx['answers']:
            block = [p(ans['question'], 'BeiSub'), p('**' + ans['verdict'] + '**')]
            block += [p('• ' + x) for x in ans['points']]
            block.append(p(ans['caution'], 'BeiSmall'))
            story += [KeepTogether(block[:3])] + block[3:]

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
            table([['Family', 'Average FAS', 'Strongest signal']] + [[f['name'], f'{f["avg_fas"]:.1f}', f'{f["top"]} ({f["top_fas"]:.1f})'] for f in ctx['families']], [136, 98, 258])
        scene = bei_charts.attribute_meters(ctx['metrics'])
        if scene:
            story.append(to_drawing(scene, fonts=FONTS, scale=TEXT_W / CHART_W))
            story.append(Spacer(1, 10))
        table([['Signal', 'Family', 'FAS', 'Detection', 'Strength', 'N']] + bei_charts.attribute_table_rows(ctx['metrics']), [108, 78, 68, 88, 88, 62])

    # Vibe Check
    if vibe:
        story.append(PageBreak())
        story.extend(pdf_flowables(vibe, fonts=FONTS, width=TEXT_W, notice=ctx['vibe_notice']))

    # evidence and method
    page('Evidence and how this report works')
    if ctx['evidence']:
        story.append(p('Evidence loaded', 'BeiSub'))
        table([['Source', 'Type', 'Units']] + [[e['source_label'], e['source_type'], fmt_int(e['n'])] for e in ctx['evidence']], [296, 98, 98], tight=True)
    if a['model_used']:
        story.append(p(f'Model: {a["model_used"]}.', 'BeiSmall'))      # under the evidence it rated: after the method text it was left alone on a last page
    # The method is one block: it stays on the page of the evidence when all of it fits (up to about five sources), else it moves to the next page whole, so that
    # the last entry never lands alone on a page of its own. An entry is read whole: its heading and text never part.
    method = [p('How this report works', 'BeiSub')] + [KeepTogether([p(heading, 'BeiMethodHead'), p(text, 'BeiMethod')]) for heading, text in analysis.HOW_IT_WORKS]
    story += [CondPageBreak(measures_export.room_for(method))] + method

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
