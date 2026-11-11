"""End to end tests of Measures over time, alerts, badges and "Since your last check", through the real app.

Every test builds a real analysis the way a person does: it uploads a CSV chat, runs a check (the fake model of tests/fake_ollama.py rates the
messages by keyword), and then reads what the app serves: the workplace page, the report page, the PDF (read back with pypdf) and the CSV. The fake model
says nothing about real chats. What it gives is chats with exactly known tones, so each test can say what the page must show.

The chats are small and made on purpose:

* FLARE   48 messages, 30 minutes apart. The third of six parts of the chat (messages 17 to 24) is mostly heated: 7 of its 8 messages, 5 of them hostile.
* CALM    48 messages, none heated.
* BOUNDS  the same chat cut to 23, 24, 47 and 48 messages (its last 16 are hostile), and 100 messages with 59 and with 60 dated.

What the classes cover:

    FlareUp        one heated moment, its alert, its badge and its cards, with the same numbers on both pages, in the PDF and in the CSV; times shown while Incognito is off;
                   the No serious flags tile held back beside the High priority alert (and not beside a Medium one)
    CalmChat       no alert to act on, Calm waters earned, and the sentence that an empty list is not an all clear
    Boundaries     23 and 24 messages, 47 and 48 messages, 59 and 60 in 100 dated, a check of one message
    TooEarly       Escalation reads Too early (no count: an empty cell in the CSV) and the rate cards and the health tile are grey while a check is too short to judge,
                   and the card note for a hot part that chance could give
    SeriousFlags   a threat and a harassment message (High and Medium priority), one message or several, the badge for no serious flags, and the safety sentence under
                   the flagged messages of the Vibe Check part (the pages, the PDF, and a Note row of the CSV)
    Checks         one record per check, Since your last check, the check-by-check chart, the same evidence again, a failing guidance, no Vibe Check (the Not checked tile),
                   Vibe Check alone, a cancel, a check made with another sensitivity or model, with a model whose name is long, or with two long names that differ
    Notes          every quiet note once, in its own section, on the pages, in the PDF and in the CSV; the sentence for an all clear (never for a silent chat, whatever its
                   size); the two fixed sentences and the caveats; the marks in words
    Privacy        what survives Delete evidence, and no time of day anywhere while Incognito is on, the check-by-check chart included
    Dates          no dates, 30 in 100 undated, too few dated for lines, strange dates, UTC
    Robustness     5,000 messages, a missing rating, a changed review period, a poisoned saved record, a record that lost its health score or is off the 0 to 100 scale,
                   a count above its total (Not known), a year of checks
    Sarcasm, Sampling, Thresholds   digs and banter, a sampled check, 30 messages for badges, a tense start, Low sensitivity and the note that says so
    PdfLayout      the PDF read back page by page: a first check is one page, the health score tile never starts a page or stands without its cards, the Evidence page
                   keeps the evidence, the model line and the whole method together with room to spare
    PageStructure  order of the sections, the key to the charts (ms['chart_key']), caveats outside every fold, words that stay out (looked for in the words a person
                   reads, never in the markup), symbols the PDF fonts cannot print, the Content-Security-Policy, sound markup, print and phone rules, the home list

What the four surfaces share is checked once, in the helpers of MeasuresCase (assert_everywhere): every note is printed once, in its own section (the notes about the
alerts under Alerts, the others under Measures over time), and no note says why two checks are not alike (ms['since']['why'] is printed once, with the comparison),
'Nothing needs urgent attention' only when ms['all_clear'] says so, the Not checked tile only while Vibe Check did not run and nothing stands, the two fixed sentences
of ms['texts'], the five caveats, ms['chart_key'] once above the four cards (page and PDF), the mark of each card and of the health tile (the icon on the pages, in
words with tone_word in the PDF and in the column Mark of the CSV), an empty count for a card that could not be judged, and that the health score tile is not alone
on a page of the PDF.

Dates are counted back from today (days_ago), so a chat is always dated inside the usable range, whatever day the tests run.
Run with:

    python3 -m unittest tests.test_measures_app -v
"""
import base64
import collections
import csv
import datetime
import hashlib
import io
import json
import pathlib
import re
import sys
import time
import unittest
from html import unescape
from html.parser import HTMLParser
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_app import AppCase, _Balance, css_rules, database_bytes, stock_sqlite  # noqa: E402

import fake_ollama  # noqa: E402
import measures  # noqa: E402
import report  # noqa: E402
from vibe_check.charts import icon_svg  # noqa: E402
from vibe_check.common import long_date, short_date  # noqa: E402
from vibe_check.engine import _PUNITIVE  # noqa: E402

# ---- the messages ---------------------------------------------------------------------------------------------------------------------
NEUTRAL = 'The schedule for tomorrow is posted in the pinned list'            # the fake model rates it 0 on every tone
CALM = ['Thanks for the update on the release', NEUTRAL, 'Please send the file when you can', 'I understand, sorry you had a rough week',
        'Great work on the new design', NEUTRAL]                                # polite, none, polite, care, encouraging, none
HOT = ['you absolute idiot, stupid take', 'wtf is going on here', 'you absolute idiot, stupid take', 'you always do this to the team']   # hostile, heated, hostile, hostile
INSULT = HOT[0]
DIG = 'oh brilliant, another meeting'                                           # Sarcastic 2 and nothing else (a new rule of tests/fake_ollama.py)
THREAT = "I'll find you after the match"                                        # Aggressive 3, and Vibe Check calls it a threat
HARASSMENT = 'I will report you to the admins'                                  # Aggressive 3, and Vibe Check calls it harassment

CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')                                        # an hour and a minute
MONTH_DAY = re.compile(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}\b')
FORBIDDEN = (r'\bFAS\b', r'(?i)\bstretch', r'(?i)\bepisode', r'(?i)\bbaseline', r'(?i)\bpeak', r'(?i)\btoxic', r'(?i)communication unit', r'(?i)standard error',
             r'(?i)z-score')           # words a person must never read in the new sections
SYMBOLS = ''.join(map(chr, (0x2713, 0x2714, 0x2717, 0x2718, 0x2265, 0x2264, 0x2212, 0x2022, 0x25b2, 0x25bc, 0x2605)))       # a tick, a cross, greater-or-equal,
#                  less-or-equal, a minus sign, a bullet, two arrows and a star: the standard PDF fonts print each of them as a question mark
SECTIONS = ('summary', 'alerts', 'over-time', 'badges')                          # the new sections of the page, in their order
BLOCKS = ('Alerts', 'Measures over time', 'Since your last check', 'Badges')    # the new blocks of the CSV, in their order
HOSTILE = '<script>alert("x")</script> <img src=x onerror=alert(1)> & "q"'      # the string tests/test_app.py uses to try to break out of a page
NOT_KNOWN = 'Not known'                                                         # what a row of Since your last check says for a count that the store had to drop
HELD_BACK = 'Held back while an alert about serious flags or a High priority alert stands.'      # why the No serious flags tile is not shown beside such an alert
GONE_NOTES = ('not_alike', 'opening')                                           # notes that no longer exist: the reason two checks are not alike is only ms['since']['why'], a tense start only a card note
NOTE_SECTIONS = ('alerts', 'measures')                                          # the two sections a note can belong to
MARK = 'Mark'                                                                   # the last column of the four cards in the CSV: the icon of the page, in words


def days_ago(n):
    """18:00 on a day n days back: always a usable date, whatever day the tests run."""
    return (datetime.datetime.now() - datetime.timedelta(days=n)).replace(hour=18, minute=0, second=0, microsecond=0)


def at(n, start, step=30):
    """n ISO stamps, `step` minutes apart, from `start`."""
    return [(start + datetime.timedelta(minutes=step * i)).isoformat() for i in range(n)]


def messages(n, hot=(0, 0), heated=HOT, calm=CALM):
    """n messages: the calm ones in turn, and from hot[0] up to (not including) hot[1] the heated ones in turn."""
    return [heated[(i - hot[0]) % len(heated)] if hot[0] <= i < hot[1] else calm[i % len(calm)] for i in range(n)]


def chat_csv(texts, stamps=None, sources=None) -> bytes:
    """A CSV chat. Every message gets a counter, because the importer drops a message it has already seen. `stamps=None` leaves out the date column."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow((['timestamp'] if stamps is not None else []) + ['source', 'text'])
    for i, text in enumerate(texts):
        writer.writerow(([stamps[i]] if stamps is not None else []) + [sources[i] if sources else 'general', f'{text} {i}'])
    return out.getvalue().encode()


def hostile_ending(n):
    """The BOUNDS chat: calm, and the last 16 of its n messages hostile."""
    return messages(n, hot=(n - 16, n), heated=[INSULT])


AGITATED = 'wtf is going on here'                                               # the fake model rates it Agitated 2 and nothing else: heated, and neither hostile nor a dig


def parts_of(counts, size, heated=AGITATED, calm=CALM):
    """len(counts) parts of `size` messages each: in part j the first counts[j] are the heated message, the rest calm ones."""
    return [heated if t < c else calm[(j * size + t) % len(calm)] for j, c in enumerate(counts) for t in range(size)]


# ---- reading what the app serves ------------------------------------------------------------------------------------------------------
def norm(text) -> str:
    return ' '.join(unescape(str(text)).split())


def squeeze(text) -> str:
    """Without any white space: a table cell of the PDF wraps its text at the width of the column, so a sentence comes back from it cut in pieces."""
    return re.sub(r'\s+', '', unescape(str(text)))


def strings(obj):
    """Every string inside a nested structure of dicts, lists and tuples."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from strings(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from strings(value)


class Page(HTMLParser):
    """What a reader of an HTML page takes in, split out so that a test can ask about it: the text outside the folds (<details>) and all of it in order, the words
    a screen reader or a hover adds (aria-label, title, alt and the tooltip data of the charts), the inline scripts, every on*= attribute, every address a tag points
    at, the title of each drawing, and the tooltips of each drawing by its title."""
    HOVER = ('aria-label', 'title', 'alt', 'data-vc-tip', 'placeholder')

    def __init__(self, html=''):
        super().__init__(convert_charrefs=True)
        self.fold = self.skip = 0
        self.flow, self.outside, self.hover = [], [], []
        self.scripts, self.events, self.addresses, self.titles, self.tips = [], [], [], [], {}
        self._script = self._title = None
        self.title = ''
        if html:
            self.feed(html)
            self.close()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            value = value or ''
            if name.lower().startswith('on'):
                self.events.append((tag, name))
            if name in ('src', 'href', 'action') and value:
                self.addresses.append((tag, name, value))
            if name == 'data-vc-tip':
                try:
                    tip = json.loads(value)
                except ValueError:
                    tip = {'t': value, 'r': []}
                self.tips.setdefault(self.title, []).append(tip)
                self.hover += [str(tip.get('t', ''))] + [str(cell) for row in tip.get('r', []) for cell in row[:2]]       # its words, not the tone name that colours a row
            elif name in self.HOVER and value:
                self.hover.append(value)
        if tag == 'script':
            self._script = [] if not dict(attrs).get('src') else None
            self.skip += 1
        elif tag == 'style':
            self.skip += 1
        elif tag == 'details':
            self.fold += 1
        elif tag == 'title':
            self._title = []

    def handle_endtag(self, tag):
        if tag == 'script':
            self.skip -= 1
            if self._script is not None:
                self.scripts.append(''.join(self._script))
                self._script = None
        elif tag == 'style':
            self.skip -= 1
        elif tag == 'details':
            self.fold -= 1
        elif tag == 'title' and self._title is not None:
            self.title = norm(''.join(self._title))
            self.titles.append(self.title)
            self._title = None

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
            return
        if self._title is not None:
            self._title.append(data)
        if self.skip:
            return
        self.flow.append(data)
        if not self.fold:
            self.outside.append(data)

    @property
    def text(self) -> str:
        return norm(' '.join(self.flow))

    @property
    def visible_outside_folds(self) -> str:
        return norm(' '.join(self.outside))

    @property
    def human(self) -> str:
        """Everything a person can read or hear on the page: its text and its hover and screen reader words."""
        return norm(' '.join(self.flow + self.hover))


class Tables(HTMLParser):
    """The tables of a piece of HTML, as lists of rows of cell text."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tables, self._row, self._cell = [], None, None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.tables.append([])
        elif tag == 'tr' and self.tables:
            self._row = []
            self.tables[-1].append(self._row)
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []
            self._row.append(self._cell)

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            self._cell = None
        elif tag == 'tr':
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    @property
    def rows(self) -> list:
        return [[[norm(''.join(cell)) for cell in row] for row in table] for table in self.tables]


def section(html, sid) -> str:
    """The markup of the <section> that holds id="sid"."""
    at_id = html.index(f'id="{sid}"')
    start = html.rindex('<section', 0, at_id)
    return html[start:html.index('</section>', at_id) + len('</section>')]


def section_text(html, sid) -> str:
    return Page(section(html, sid)).text


def section_tables(html, sid) -> list:
    return Tables(section(html, sid)).rows


def measures_markup(html) -> str:
    """The four new sections of a page, as markup (attributes, tooltips and drawings included)."""
    return ''.join(section(html, sid) for sid in SECTIONS)


def quiet_notes(html, sid) -> list:
    """The sentences of the quiet notes (the lists of class vc-quiet) of one section of a page, in order."""
    out = []
    for block in re.findall(r'<ul class="vc-quiet">(.*?)</ul>', section(html, sid), re.S):
        out += [norm(re.sub(r'<[^>]+>', '', item)) for item in re.findall(r'<li>(.*?)</li>', block, re.S)]
    return out


def cards_on_page(html) -> list:
    """The four measure cards of the page as [(icon, level, mark in words)]: the status icon, the level word, and the words that a screen reader hears after it."""
    found = re.findall(r'<p class="vc-level">(<svg.*?</svg>)<strong>(.*?)</strong><span class="sr-only">, (.*?)</span>', section(html, 'over-time'), re.S)
    return [(icon, norm(level), norm(word)) for icon, level, word in found]


def health_mark_on_page(html) -> str:
    """The status icon beside the status word of the health score tile, as markup (to be compared with icon_svg(tone)); '' when the tile has none."""
    found = re.search(r'<div class="vc-badge">(<svg.*?</svg>)<span>', section(html, 'over-time'), re.S)
    return found.group(1) if found else ''


def same_markup(html) -> str:
    """Markup with the numbers that make each drawing's ids new on every page load taken out, so two renderings of the same results compare equal."""
    return re.sub(r'vc\d+-', 'vcN-', html)


NEXT_PARTS = ('Your questions answered', 'Summary and guidance', 'Measured behaviour', 'Vibe Check', 'Evidence and how this report works')
FOOTER = re.compile(r'^Felixa Community Health \| \d{4}-\d{2}-\d{2}(?: Model-rated wording\. Not a verdict on people\.)? \d+ ')     # what every page opens with in the text


def pdf_pages(data) -> list:
    import pypdf
    return [norm(page.extract_text()) for page in pypdf.PdfReader(io.BytesIO(data)).pages]


BOTTOM_MARGIN, FOOTER_RULE = 60, 45          # points from the foot of the page: where the text column ends, and the line above the footer (the footer text is drawn at 32)


def lowest_body_y(data, page_no) -> float:
    """How far from the foot of a page of the PDF (in points) the lowest line of its text sits, the footer left out. The text column ends BOTTOM_MARGIN points up, so
    this less BOTTOM_MARGIN is the room the page has left under its text."""
    import pypdf
    found = []

    def visit(text, cm, tm, font_dict, font_size):
        if text.strip():
            found.append(tm[4] * cm[1] + tm[5] * cm[3] + cm[5])
    pypdf.PdfReader(io.BytesIO(data)).pages[page_no].extract_text(visitor_text=visit)
    return min(y for y in found if y > FOOTER_RULE)


def feature_bodies(pages) -> list:
    """The pages this feature adds to the PDF, each without its footer: from the page headed Alerts and badges up to the page that starts the next part of the report."""
    bodies = [FOOTER.sub('', text) for text in pages]
    start = next(i for i, text in enumerate(bodies) if text.startswith('Alerts and badges'))
    end = next((i for i in range(start + 1, len(bodies)) if bodies[i].startswith(NEXT_PARTS)), len(bodies))
    return bodies[start:end]


def measures_pdf(pages) -> str:
    """The text of the pages this feature adds to the PDF. The footer of each page (the text of a page opens with it) is left out."""
    return ' '.join(feature_bodies(pages))


def alerts_pdf(pages) -> str:
    """The part of those pages that holds Alerts and Badges: up to the page that opens Measures over time."""
    bodies = feature_bodies(pages)
    lines = next((i for i, text in enumerate(bodies) if text.startswith('Measures over time')), len(bodies))
    return ' '.join(bodies[:lines])


def lines_bodies(pages) -> list:
    """The pages that hold Measures over time and Since your last check, one text for each: from the page that opens Measures over time, footer left out."""
    bodies = feature_bodies(pages)
    return bodies[next((i for i, text in enumerate(bodies) if text.startswith('Measures over time')), len(bodies)):]


def lines_pdf(pages) -> str:
    """The part that holds Measures over time and Since your last check: from the page that opens Measures over time."""
    return ' '.join(lines_bodies(pages))


def csv_blocks(rows) -> dict:
    """The blocks of the CSV that belong to this feature, each as its rows from its title row on (without the empty rows that end it)."""
    present = [title for title in BLOCKS if [title] in rows]
    out = {}
    for i, title in enumerate(present):
        start = rows.index([title])
        end = rows.index([present[i + 1]]) if i + 1 < len(present) else next(j for j in range(start, len(rows)) if rows[j] == [])
        block = rows[start:end]
        while block and block[-1] == []:
            block.pop()
        out[title] = block
    return out


def rows_under(block, first_cell) -> list:
    """The rows of a table inside a block: those under its header row (the first row that opens with `first_cell`) that are as wide as the header."""
    at_header = next(i for i, row in enumerate(block) if row[:1] == [first_cell] and len(row) > 2)
    width, out = len(block[at_header]), []
    for row in block[at_header + 1:]:
        if len(row) != width:
            break
        out.append(row)
    return out


def count_cells(value) -> list:
    """The two numbers of a cell of the Since table as the CSV writes them: 'x of n' is [x, n], the health score (a whole number) is a number out of 100, and a
    count that the store had to drop ('Not known') stays those words with nothing out of it."""
    if isinstance(value, int):
        return [str(value), '100']
    if value == NOT_KNOWN:
        return [NOT_KNOWN, '']
    found = re.fullmatch(r'([\d,]+) of ([\d,]+)', str(value))
    return [found.group(1).replace(',', ''), found.group(2).replace(',', '')]


def measure_cells(rows):
    """Every cell of the blocks of this feature in a CSV, as text."""
    return [cell for block in csv_blocks(rows).values() for row in block for cell in row]


# ---- the harness ----------------------------------------------------------------------------------------------------------------------
class MeasuresCase(AppCase):
    """What every class of tests here shares: building a checked analysis, reading the four surfaces, and the checks that the four tell the same story."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.built = {}

    def scenario(self, name, make):
        """An analysis that many tests only read, made on first use."""
        if name not in self.built:
            self.built[name] = make()
        return self.built[name]

    # -- building ---------------------------------------------------------------------------------
    def check(self, aid, data, name='chat.csv', engine='', **settings):
        """One check, as the last step of New check runs it: the file goes in and a full run follows. Settings (Incognito and the like) are set first, because New
        check resets them. `engine` is the model the person picked on the last step ('' keeps the app's own)."""
        if settings:
            self.store.update_analysis(aid, **settings)
        self.upload(aid, (name, data, 'text/csv'))
        job = self.run_job(aid, path='check/6', engine=engine)
        self.assertEqual(job['status'], 'complete', job)
        return job

    def new_check(self, aid):
        self.assertEqual(self.client.post(f'/analysis/{aid}/check/new').status_code, 303)

    def checked(self, texts, stamps, name='chat.csv', sources=None, **settings) -> int:
        """A new analysis with one chat in it and one check run."""
        aid = self.new_analysis()
        self.check(aid, chat_csv(texts, stamps, sources), name, **settings)
        return aid

    def incognito_check(self, aid, data, name='chat.csv'):
        """A check with Incognito switched on through its own step of New check."""
        self.assertEqual(self.client.post(f'/analysis/{aid}/check/3', data={'incognito': 'on'}).status_code, 303)
        self.assertEqual(self.store.get_analysis(aid)['incognito'], 1)
        return self.check(aid, data, name)

    def many_notes(self) -> int:
        """A check with six quiet notes at once: Vibe Check did not run (a note about the alerts), Low sensitivity, 30 in 100 undated, one message with an incomplete
        rating, almost no tone found and four messages added after the check (notes about the measures)."""
        def make():
            stamps = ['' if i % 10 in (1, 4, 8) else s for i, s in enumerate(at(100, days_ago(40), step=20))]
            aid = self.new_analysis()
            self.upload(aid, ('chat.csv', chat_csv([NEUTRAL] * 100, stamps), 'text/csv'))
            self.store.update_analysis(aid, vibe_sensitivity='low')
            self.fake.fail_next('vibe', 99)
            job = self.run_job_fast(aid, path='check/6', engine='')
            self.fake.failures.clear()
            self.assertEqual(job['status'], 'complete')
            with self.store.db() as c:
                c.execute("DELETE FROM unit_ratings WHERE unit_id=? AND attribute='Rude'", (self.store.rated_units(aid)[7]['id'],))
            self.upload(aid, ('more.csv', chat_csv([NEUTRAL] * 4, at(4, days_ago(10))), 'text/csv'))
            return aid
        return self.scenario('six notes', make)

    def ms(self, aid) -> dict:
        ctx = report.build(aid)
        self.assertIsNotNone(ctx['measures'])
        return ctx['measures']

    # -- the four surfaces ------------------------------------------------------------------------
    def page(self, aid, what='results') -> str:
        """A page of the app. The results are now one screen per kind; the report is the whole of them on one page, so `results` reads the report."""
        response = self.client.get(f'/analysis/{aid}/{"report" if what == "results" else what}')
        self.assertEqual(response.status_code, 200, what)
        return response.text

    def pdf(self, aid) -> list:
        response = self.client.get(f'/analysis/{aid}/report.pdf')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:5], b'%PDF-')
        return pdf_pages(response.content)

    def csv(self, aid) -> list:
        return self.export_rows(aid)

    def surfaces_render(self, aid):
        """All four answer 200 (the pages and the PDF and the CSV are all there to open)."""
        for what in ('results', 'report', 'report.pdf', 'export'):
            self.assertEqual(self.client.get(f'/analysis/{aid}/{what}').status_code, 200, what)

    # -- the four surfaces tell the same story ----------------------------------------------------
    def assert_says(self, text, *parts, where=''):
        """Each part is in the text. Used where the numbers and the key words matter and the exact sentence is the wording engineer's to improve."""
        for part in parts:
            self.assertIn(part, text, where)

    def assert_plain_words(self, text, where):
        """The words that stay out of the new sections, and nothing that suggests a punishment or points at the people involved."""
        for pattern in FORBIDDEN:
            self.assertIsNone(re.search(pattern, text), f'{where}: {pattern}')
        self.assertIsNone(_PUNITIVE.search(text), f'{where}: punitive wording')
        self.assertIsNone(re.search(r'(?i)\bthe (?:members|people) (?:who|involved)\b', text), f'{where}: it points at the people involved')

    def assert_page_agrees(self, ms, html, where='page'):
        text = {sid: section_text(html, sid) for sid in SECTIONS}
        page = Page(html)
        for sid in SECTIONS:
            self.assert_plain_words(Page(section(html, sid)).human, f'{where} {sid}')                 # the text, the hover words and the screen reader words
        m, h, caveats = ms['m'], ms['health'], ms['caveats']
        look = ms['alert_counts']['high'] + ms['alert_counts']['medium']
        self.assertTrue(all(n['about'] in NOTE_SECTIONS for n in ms['notes']), f'{where}: every note belongs to the alerts or to the measures')
        self.assertEqual({n['key'] for n in ms['notes']} & set(GONE_NOTES), set(), f'{where}: a note that no longer exists')
        vibe_ran = 'no_vibe' not in {n['key'] for n in ms['notes']}                          # the note for a Vibe Check that did not run is the one sign of it that ms holds
        if look == 0 and not vibe_ran:                                                       # nothing to look at, but nothing was looked for either: not '0 things'
            self.assertIn('Not checked things to look at first', text['summary'], where)
            self.assertNotIn('0 things to look at first', text['summary'], where)
        else:
            self.assertIn(f'{look} thing{"" if look == 1 else "s"} to look at first', text['summary'], where)
            self.assertNotIn('Not checked', text['summary'], where)
        self.assertEqual('Not run Vibe Check score' in text['summary'], not vibe_ran, f'{where}: the tile for the Vibe Check score')
        self.assertIn(f'{ms["badges_earned"]} badge{"" if ms["badges_earned"] == 1 else "s"} earned', text['summary'], where)
        self.assertNotIn('/ 100', text['summary'], where)                                    # the headline stays the Vibe Check score; the health score is a tile below
        # the alerts, in the order of the list: the two priorities, then at most three lines of good news
        shown = [a for a in ms['alerts'] if a['level'] != 'good'] + [a for a in ms['alerts'] if a['level'] == 'good'][:3]
        last = -1
        for a in shown:
            for part in (a['title'], a['detail'], a['base'], f'Where in the chat: {a["when"]}' if a['when'] else '', f'Try this: {a["try"]}' if a['try'] else ''):
                if part:
                    self.assertIn(norm(part), text['alerts'], f'{where}: {a["key"]}')
            self.assertIn(measures.LEVEL_NAMES[a['level']], text['alerts'], where)
            found = text['alerts'].index(norm(a['title']))
            self.assertGreater(found, last, f'{where}: the alerts are not in the order of the list')
            last = found
        # the count line, and the sentence that says nothing needs attention only when ms says it may
        self.assertEqual('1 thing to look at first.' in text['alerts'], look == 1, where)
        self.assertEqual(f'{look} things worth a look.' in text['alerts'], look > 1, where)
        these = 'this message' if m['n'] == 1 else 'these {:,} messages'.format(m['n'])
        self.assertEqual(f'Nothing needs urgent attention in {these}.' in text['alerts'], ms['all_clear'], f'{where}: the sentence for an all clear')
        # the quiet notes: each once, in its own section (the notes about the alerts under Alerts, every other under Measures over time)
        for sid, about in (('alerts', 'alerts'), ('over-time', 'measures')):
            self.assertEqual(quiet_notes(html, sid), [norm(n['text']) for n in ms['notes'] if n['about'] == about], f'{where}: the notes of {sid}')
        for sid in ('summary', 'badges'):
            self.assertEqual(quiet_notes(html, sid), [], f'{where}: {sid} holds no quiet note')
        for note in ms['notes']:
            copies = text['alerts'].count(norm(note['text'])) + text['over-time'].count(norm(note['text']))
            self.assertEqual(copies, 1, f'{where}: {note["key"]} is printed once')
            self.assertNotIn(norm(note['text']), text['summary'] + ' ' + text['badges'], f'{where}: {note["key"]} sits in a section it is not about')
        # the four measures and the health score
        over = text['over-time']
        self.assertIn(norm(ms['basis_text']), over, where)
        # the key to the charts: ms['chart_key'] once, above the four cards and in no other section, and only while there are charts for it to explain
        key = norm(ms['chart_key'])
        if m['has_series']:
            self.assertEqual(over.count(key), 1, f'{where}: the key to the charts')
            self.assertLess(over.index(key), over.index(norm(ms['cards'][0]['meaning'])), f'{where}: the key to the charts is above the cards')
            self.assertIn(key, Page(section(html, 'over-time')).visible_outside_folds, f'{where}: the key to the charts is never folded')
        else:
            self.assertNotIn('Lines show messages per 100', over, f'{where}: a key to charts that are not there')
        for sid in ('summary', 'alerts', 'badges'):
            self.assertNotIn(key, text[sid], f'{where}: the key to the charts sits in {sid}')
        for card in ms['cards']:
            for part in (card['name'], card['level'], card['meaning'], card['headline'], card['tone_word'], card.get('trend'), card.get('note')):
                if part:
                    self.assertIn(norm(part), over, f'{where}: {card["key"]}')
            if card.get('latest'):
                self.assertIn(f'{card["latest_label"]}: {card["latest"]}', over, where)
        self.assertEqual(cards_on_page(html), [(icon_svg(c['tone']), norm(c['level']), norm(c['tone_word'])) for c in ms['cards']], f'{where}: the mark of each card')
        self.assertIn(f'{measures.whole(h["score"])} / 100', over, where)
        self.assertEqual(health_mark_on_page(html), icon_svg(h['tone']), f'{where}: the mark of the health score')
        for part in (h['name'], h['status'], h['caption'], h['trend']):
            if part:
                self.assertIn(norm(part), over, where)
        tables = section_tables(html, 'over-time')
        parts = [t for t in tables if t and t[0][:2] == ['Part of the chat', 'Messages']]
        if m['has_series']:
            w = measures.whole
            self.assertEqual(len(parts), 1, where)
            self.assertEqual(parts[0][1:], [[s['label'], f'{s["n"]:,}', str(w(s['rates']['hostile'])), str(w(s['rates']['care'])), str(w(s['rates']['dig'])),
                                             str(w(s['rates']['heated'])), str(w(s['health']))] for s in m['stretches']], where)
        else:
            self.assertEqual(parts, [], where)
            self.assertEqual(over.lower().count('no lines over time'), 1, f'{where}: that there are no lines is said once, by the note that explains why')
        # since your last check, and the check-by-check table (from the third check)
        since = [t for t in tables if t and t[0][:3] == ['Measure', 'Last check', 'This check']]
        if ms['since']:
            alike = ms['since']['comparable']
            self.assertEqual((ms['comparison']['comparable'], ms['comparison']['why']), (alike, ms['since']['why']), f'{where}: the table and the comparison give one reason')
            self.assertEqual(bool(ms['since']['why']), not alike, f'{where}: a reason only when the two checks are not alike')
            self.assertIn(norm(caveats['since']), Page(section(html, 'over-time')).visible_outside_folds, f'{where}: the caveat of the comparison is always shown')
            self.assertNotIn(norm(ms['texts']['first_check']), over, f'{where}: the sentence for a first check, after a second')
            self.assertEqual(len(since), 1, where)
            self.assertEqual(since[0][0], ['Measure', 'Last check', 'This check'] + (['Change'] if alike else []), where)
            self.assertEqual(since[0][1:], [[r['label'], str(r['before']), str(r['now'])] + ([r['verdict']] if alike else []) for r in ms['since']['rows']], where)
            if not alike:                                                                  # the reason is said once, with the comparison, and not as a note under Alerts
                self.assertIn('No comparison', over, where)
                self.assertEqual(over.count(norm(ms['since']['why'])), 1, f'{where}: the reason two checks are not alike, once')
                self.assertLess(over.index('Since your last check'), over.index(norm(ms['since']['why'])), f'{where}: the reason sits under the heading of the comparison')
                self.assertNotIn(norm(ms['since']['why']), text['alerts'], f'{where}: the reason two checks are not alike is no note under Alerts')
            else:
                self.assertNotIn('No comparison', over, where)
        else:
            self.assertEqual(since, [], where)
            self.assertIn(norm(ms['texts']['first_check']), Page(section(html, 'over-time')).visible_outside_folds, f'{where}: the sentence for a first check')
        trail = [t for t in tables if t and t[0][:2] == ['Check', 'Date']]
        if len(ms['trail']) >= 3:
            self.assertEqual(len(trail), 1, where)
            self.assertEqual([row[:4] for row in trail[0][1:]],
                             [[f'Check {i}' + (' (this check)' if i == len(ms['trail']) else ''), long_date(r['created_at']), f'{r["n"]:,}', str(measures.whole(r['health']))]
                              for i, r in enumerate(ms['trail'], 1)], where)
        else:
            self.assertEqual(trail, [], where)
            self.assertNotIn('Check by check', over, where)
        # the badges
        badges = text['badges']
        self.assertEqual(len(re.findall(r'\bEarned\b', badges)), ms['badges_earned'], where)
        self.assertEqual(norm(ms['texts']['no_badges']) in badges, ms['badges_earned'] == 0, f'{where}: the sentence for no badge, and only then')
        for b in ms['badges']:
            self.assertIn(b['name'], badges, where)
            self.assertIn(norm(b['evidence']) if b['earned'] else norm(b['how']), badges, f'{where}: {b["key"]}')
            if b['needs']:
                self.assertIn(norm(b['needs']), badges, where)
        if ms['badge_note']:
            self.assertIn(norm(ms['badge_note']), badges, where)
        # the sentences that must always be shown sit outside every fold
        outside = page.visible_outside_folds
        anywhere = norm(page.text)
        self.assertIn(norm(caveats['measures']), outside, f'{where}: the measures caveat is folded away or missing')
        for key in ('alerts', 'badges'):                      # the person asked for these two descriptions to be folded ("How alerts work", "How badges are meant")
            self.assertIn(norm(caveats[key]), anywhere, f'{where}: the {key} caveat is missing')
        self.assertEqual(norm(caveats['empty']) in anywhere, not shown, where)

    def assert_pdf_agrees(self, ms, pages, where='pdf'):
        ours = squeeze(measures_pdf(pages))
        self.assert_plain_words(measures_pdf(pages), where)

        def has(*parts):
            self.assertIn(squeeze(''.join(str(p) for p in parts)), ours, f'{where}: {" | ".join(str(p) for p in parts)[:120]}')
        h, caveats = ms['health'], ms['caveats']
        for key in ('alerts', 'measures', 'badges'):
            has(caveats[key])
        for a in ms['alerts']:
            has(measures.LEVEL_NAMES[a['level']])
            for part in ('title', 'detail', 'try', 'when', 'base'):
                has(a[part])
        if not ms['alerts']:
            has(caveats['empty'])
        # the quiet notes: each once, in its own part of the report (the notes about the alerts on the Alerts and badges page, the others on the Measures page)
        in_alerts, in_lines = squeeze(alerts_pdf(pages)), squeeze(lines_pdf(pages))
        for note in ms['notes']:
            own, other = (in_alerts, in_lines) if note['about'] == 'alerts' else (in_lines, in_alerts)
            self.assertIn(squeeze(note['text']), own, f'{where}: {note["key"]} is not in its own part')
            self.assertNotIn(squeeze(note['text']), other, f'{where}: {note["key"]} is in the other part as well')
            self.assertEqual(ours.count(squeeze(note['text'])), 1, f'{where}: {note["key"]} is printed more than once')
        self.assertEqual({n['key'] for n in ms['notes']} & set(GONE_NOTES), set(), f'{where}: a note that no longer exists')
        these = 'this message' if ms['m']['n'] == 1 else 'these {:,} messages'.format(ms['m']['n'])
        if not ms['all_clear']:
            self.assertNotIn(squeeze(f'Nothing needs urgent attention in {these}'), ours, f'{where}: an all clear that ms does not give')
        has(ms['basis_text'])
        # the key to the charts is ms['chart_key'], once, above the first card; with no charts there is no key
        key = squeeze(ms['chart_key'])
        if ms['m']['has_series']:
            self.assertEqual(ours.count(key), 1, f'{where}: the key to the charts')
            self.assertLess(ours.index(key), ours.index(squeeze(f'{ms["cards"][0]["name"]}: {ms["cards"][0]["level"]}')), f'{where}: the key to the charts is above the cards')
            self.assertEqual(squeeze(alerts_pdf(pages)).count(key), 0, f'{where}: the key to the charts sits with the lines, not with the alerts')
        else:
            self.assertNotIn(squeeze('Lines show messages per 100'), ours, f'{where}: a key to charts that are not there')
        # a card: its name and level, the mark in words (the PDF has no icon), what it means, what we saw, the trend and the note, in this order
        for card in ms['cards']:
            at = -1
            for part in (f'{card["name"]}: {card["level"]}', f'({card["tone_word"]})', card['meaning'], card['headline'], card.get('trend'), card.get('note')):
                if part:
                    found = ours.find(squeeze(part), at + 1)
                    self.assertGreater(found, -1, f'{where}: {card["key"]}: {part[:60]!r} is missing or out of order')
                    at = found
        has(f'{measures.whole(h["score"])} out of 100')
        has(f'{h["name"]}: {h["status"]} ({h["tone_word"]})')                                # the meter's own numbers sit between the status and the caption
        has(h['caption'], h['trend'])
        for b in ms['badges']:
            if b['earned']:
                has(b['name'], 'Earned', b['evidence'])
            else:
                has(b['name'], 'Not yet', b['needs'], b['how'])
        if ms['badge_note']:
            has(ms['badge_note'])
        self.assertEqual(squeeze(ms['texts']['no_badges']) in ours, ms['badges_earned'] == 0, f'{where}: the sentence for no badge, and only then')
        has('Since your last check')
        if ms['since']:
            has(caveats['since'])
            if ms['since']['comparable']:
                for r in ms['since']['rows']:
                    has(r['label'], r['before'], r['now'], r['verdict'])
            else:
                has('No comparison.', ms['since']['why'])
                self.assertEqual(ours.count(squeeze(ms['since']['why'])), 1, f'{where}: the reason two checks are not alike, once')
                self.assertLess(ours.index(squeeze('Since your last check')), ours.index(squeeze(ms['since']['why'])), f'{where}: the reason sits under the heading of the comparison')
                self.assertNotIn(squeeze(ms['since']['why']), squeeze(alerts_pdf(pages)), f'{where}: the reason two checks are not alike is no note under Alerts')
        else:
            has('Since your last check', ms['texts']['first_check'])                       # a first check says there is nothing to compare yet, and does not leave the heading out
        if len(ms['trail']) >= 3:
            has('Health score, check by check')
        else:
            self.assertNotIn(squeeze('Health score, check by check'), ours, where)
        # the health score tile never starts a page and never stands without the last row of cards: no page is left with the tile and a sentence
        tiles = [body for body in lines_bodies(pages) if 'out of 100' in body]
        self.assertEqual(len(tiles), 1, f'{where}: the tile is on one page')
        self.assertIsNone(re.match(r'^\d+ out of 100', tiles[0]), f'{where}: a page starts with the health score tile')
        self.assertIn(f'Passive-aggressive: {ms["cards"][3]["level"]}', tiles[0], f'{where}: the tile is not on the page of the last row of cards')
        self.assertLess(tiles[0].index('Passive-aggressive:'), tiles[0].index('out of 100'), f'{where}: the tile sits under the last row of cards')
        for symbol in SYMBOLS:
            self.assertNotIn(symbol, measures_pdf(pages), f'{where}: the PDF fonts turn {symbol!r} into a question mark')

    def assert_csv_agrees(self, ms, rows, where='csv'):
        self.assert_plain_words(' '.join(measure_cells(rows)), where)
        self.assertEqual([c for c in measure_cells(rows) if c.startswith("'")], [], f'{where}: a cell was defused that is no formula (a number written as text?)')
        blocks = csv_blocks(rows)
        self.assertEqual(list(blocks), list(BLOCKS), f'{where}: the four blocks, even before a second check')
        w, h, caveats, texts = measures.whole, ms['health'], ms['caveats'], ms['texts']
        a = blocks['Alerts']
        self.assertEqual(a[1], ['How to read this', caveats['alerts']], where)
        if ms['alerts']:
            self.assertEqual(rows_under(a, 'Priority'), [[measures.LEVEL_NAMES[x['level']], x['title'], x['detail'], x['try'], x['when'], x['base']] for x in ms['alerts']], where)
        else:
            self.assertIn(['No alerts', caveats['empty']], a, where)
        # the quiet notes: each once, in the block of its own section (the notes about the alerts with Alerts, the others with Measures over time)
        note_rows = lambda block: [r[1] for r in block if r[:1] == ['Note']]                   # noqa: E731
        self.assertEqual(note_rows(a), [n['text'] for n in ms['notes'] if n['about'] == 'alerts'], f'{where}: the notes of Alerts')
        self.assertEqual(note_rows(blocks['Measures over time']), [n['text'] for n in ms['notes'] if n['about'] == 'measures'], f'{where}: the notes of Measures over time')
        self.assertEqual(note_rows(blocks['Since your last check']), [], where)
        for note in ms['notes']:
            self.assertEqual(sum(1 for r in rows if r == ['Note', note['text']]), 1, f'{where}: {note["key"]} is printed once')
        if not ms['all_clear']:
            self.assertFalse([c for c in measure_cells(rows) if 'Nothing needs urgent attention' in c], f'{where}: an all clear that ms does not give')
        m = blocks['Measures over time']
        self.assertEqual(m[1], ['How to read this', caveats['measures']], where)
        self.assertIn(['Basis', ms['basis_text']], m, where)
        head = next(r for r in m if r[:1] == ['Measure'])
        self.assertEqual((len(head), head[:2], head[-1]), (10, ['Measure', 'Level'], MARK), f'{where}: the cards end with a column for the mark in words')
        cards = rows_under(m, 'Measure')
        self.assertEqual([r[:2] + r[4:] for r in cards],
                         [[c['name'], c['level'], c.get('trend') or '', c['headline'], c.get('note') or '', c['meaning'], c.get('latest') or '', c['tone_word']] for c in ms['cards']], where)
        # the count is a number of messages (of heated moments for Escalation); a card that could not be judged has an empty cell, never a 0 that reads as 'none'
        self.assertEqual([r[2:4] for r in cards], [['' if c.get('count') is None else str(c['count']), str(c.get('of', ''))] for c in ms['cards']], where)
        if ms['m']['has_series']:
            self.assertEqual(rows_under(m, 'Part of the chat'),
                             [[s['label'], str(s['n']), str(w(s['rates']['hostile'])), str(w(s['rates']['care'])), str(w(s['rates']['dig'])), str(w(s['rates']['heated'])),
                               ('Yes' if s['counted'] else 'No') if ms['m']['verdicts'] else '', str(w(s['health']))] for s in ms['m']['stretches']], where)
        else:
            self.assertNotIn(['Part of the chat'], [r[:1] for r in m], where)
        self.assertIn(['Health score', str(w(h['score'])), h['status'], h['tone_word']], m, where)             # the score, its status and its mark in words
        self.assertIn(['About the health score', h['caption']], m, where)
        s = blocks['Since your last check']
        self.assertEqual(s[1], ['How to read this', caveats['since']], where)
        if ms['since']:
            self.assertNotIn(['First check', texts['first_check']], s, where)
            # a count that the page writes as 'x of n' is two numbers in the CSV, and the health score is a number out of 100
            if ms['since']['comparable']:
                self.assertEqual(rows_under(s, 'Measure'), [[r['label'], *count_cells(r['before']), *count_cells(r['now']), r['verdict']] for r in ms['since']['rows']], where)
            else:
                self.assertIn(['No comparison', ms['since']['why']], s, where)
                self.assertEqual([r for r in rows if ms['since']['why'] in r], [['No comparison', ms['since']['why']]], f'{where}: the reason two checks are not alike, once')
                self.assertEqual(rows_under(s, 'Measure'), [[r['label'], *count_cells(r['before']), *count_cells(r['now'])] for r in ms['since']['rows']], where)
            if len(ms['trail']) >= 3:                                                                # the table behind the check-by-check chart: the day of each check, never the time
                cell = lambda r, key: '' if r.get(key) is None else str(r[key])                       # noqa: E731  (a count the store dropped is an empty cell)
                self.assertEqual([r[:9] for r in rows_under(s, 'Check')],
                                 [[f'Check {i}' + (' (this check)' if i == len(ms['trail']) else ''), r['created_at'][:10], str(r['n']), str(w(r['health'])),
                                   cell(r, 'hostile_n'), cell(r, 'care_n'), cell(r, 'dig_n'), cell(r, 'heated_n'), cell(r, 'episodes')]
                                  for i, r in enumerate(ms['trail'], 1)], where)
            else:
                self.assertNotIn(['Check'], [r[:1] for r in s], where)
        else:
            self.assertIn(['First check', texts['first_check']], s, where)
            self.assertNotIn(['Measure', 'Last check'], [r[:2] for r in s], where)
        b = blocks['Badges']
        self.assertEqual(b[1], ['How to read this', caveats['badges']], where)
        self.assertEqual(['Note', ms['badge_note']] in b, bool(ms['badge_note']), where)
        self.assertEqual(['No badges', texts['no_badges']] in b, ms['badges_earned'] == 0, f'{where}: the sentence for no badge, and only then')
        self.assertEqual([r[:3] for r in rows_under(b, 'Badge')],
                         [[x['name'], 'Earned', x['evidence']] if x['earned'] else [x['name'], 'Not yet', x['needs']] for x in ms['badges']], where)

    def assert_everywhere(self, aid, where=''):
        """The workplace page, the report page, the PDF and the CSV all say what the measures of the analysis say."""
        ms = self.ms(aid)
        self.assert_page_agrees(ms, self.page(aid, 'results'), f'{where} workspace')
        self.assert_page_agrees(ms, self.page(aid, 'report'), f'{where} report')
        self.assert_pdf_agrees(ms, self.pdf(aid), f'{where} pdf')
        self.assert_csv_agrees(ms, self.csv(aid), f'{where} csv')
        return ms


# =============================================================================================================================================
class FlareUp(MeasuresCase):
    """A 48 message chat, 30 minutes apart, whose third part is mostly heated: one heated moment, one alert, one badge, and the same story on every surface."""

    def flare(self):
        def make():
            start = days_ago(40)
            return self.checked(messages(48, hot=(16, 23)), at(48, start)), start
        return self.scenario('flare', make)

    def test_one_part_of_the_chat_is_one_heated_moment(self):
        aid, start = self.flare()
        m = self.ms(aid)['m']
        self.assertEqual((m['n'], m['timeline_n'], m['basis'], m['verdicts'], m['has_series'], m['left_off'], m['incomplete']), (48, 48, 'time', True, True, 0, 0))
        self.assertEqual([s['n'] for s in m['stretches']], [8] * 6)
        self.assertEqual([s['counts']['heated'] for s in m['stretches']], [0, 0, 7, 0, 0, 0])
        self.assertEqual([s['counted'] for s in m['stretches']], [False, False, True, False, False, False])
        self.assertEqual([(e['first'], e['last'], e['heated'], e['n']) for e in m['episodes']], [(2, 2, 7, 8)])        # one heated moment, in part 3
        self.assertEqual((m['overall']['hostile'], m['overall']['heated'], m['overall']['care'], m['overall']['dig']), (5, 7, 7, 0))

    def test_the_mood_heated_up_fast_is_the_only_alert_and_says_where_and_how_many(self):
        aid, start = self.flare()
        ms = self.ms(aid)
        self.assertEqual(ms['alert_counts'], {'high': 1, 'medium': 0, 'good': 0})
        self.assertEqual(ms['notes'], [])
        (alert,) = ms['alerts']
        day = short_date((start + datetime.timedelta(minutes=30 * 16)).isoformat())            # the day of messages 17 to 24
        self.assertEqual((alert['level'], alert['key'], alert['title']), ('high', 'heated', 'The mood heated up fast'))
        self.assert_says(alert['detail'], 'About a third of the way in', '7 of 8 messages', 'heated', 'almost none were')       # where, how many, and what it was before
        self.assert_says(alert['base'], 'Based on 8 messages', '7 of them')
        self.assertTrue(alert['when'].startswith('about a third of the way in'), alert['when'])
        self.assertIn(day, alert['when'])                                                          # the data is dated, so the day is named
        self.assertTrue(alert['try'])
        self.assertFalse(CLOCK.search(' '.join(str(v) for v in alert.values())), 'an alert never holds a clock time')

    def test_the_cards_say_what_happened_in_plain_sentences(self):
        aid, start = self.flare()
        cards = {c['key']: c for c in self.ms(aid)['cards']}
        self.assertEqual((cards['escalation']['level'], cards['escalation']['count']), ('Steep', 1))
        self.assertTrue(cards['escalation']['headline'].startswith('1 heated moment, about a third of the way in'), cards['escalation']['headline'])
        self.assertEqual((cards['conflict']['level'], cards['conflict']['headline'], cards['conflict']['trend'], cards['conflict']['latest']),
                         ('Some', '5 of 48 messages were openly hostile.', 'No clear change.', 'Rare'))              # the sentence MEASURES.md gives as its example
        self.assertEqual((cards['empathy']['level'], cards['empathy']['trend']), ('Often', 'No clear change.'))
        self.assertTrue(cards['empathy']['headline'].startswith('7 of 48 messages'), cards['empathy']['headline'])
        self.assertEqual(cards['passive']['level'], 'Rare')
        self.assertTrue(cards['passive']['headline'].startswith('None of the 48 messages'), cards['passive']['headline'])

    def test_the_cools_fast_badge_is_earned_and_calm_waters_is_not(self):
        aid, start = self.flare()
        ms = self.ms(aid)
        earned = {b['name']: b for b in ms['badges'] if b['earned']}
        self.assertEqual(sorted(earned), ['Cools fast'])                                         # No serious flags would be earned, but a High priority alert stands (see below)
        self.assertTrue(earned['Cools fast']['evidence'])
        self.assertEqual(ms['badges_earned'], 1)
        calm = next(b for b in ms['badges'] if b['key'] == 'calm')
        self.assertEqual((calm['earned'], calm['needs']), (False, ''))                           # not earned because of the heated moment, not for want of data

    def test_the_no_serious_flags_tile_is_held_back_beside_a_high_priority_alert_and_the_fold_says_so(self):
        """MEASURES.md: the No serious flags tile is held back while a High priority alert stands (it would read as praise beside it); the fold says so. Vibe Check
        flagged no serious message here (a threat, hate speech, harassment or bullying: its insults and curses are not serious flags) and read all 48 messages, so the
        badge would be earned on its own rule. The page, the PDF and the CSV list it as not yet, with the reason."""
        aid, start = self.flare()
        ms = self.ms(aid)
        self.assertEqual(ms['alert_counts']['high'], 1)
        self.assertEqual(ms['m']['n'], 48)
        noflags = next(b for b in ms['badges'] if b['key'] == 'noflags')
        self.assertEqual((noflags['earned'], noflags['evidence'], noflags['needs']), (False, '', HELD_BACK))
        vibe = self.store.latest_vibe(aid)
        self.assertEqual((measures.serious_flags(vibe)['total'], vibe['analyzed_n']), (0, 48))        # the rule alone would have earned it
        for what in ('results', 'report'):
            html = self.page(aid, what)
            badges = section_text(html, 'badges')
            self.assertNotIn('Earned No serious flags', badges, what)                              # no tile for it beside the alert
            self.assertEqual(badges.count(HELD_BACK), 1, what)
            self.assertIn(f'No serious flags {norm(noflags["how"])} {HELD_BACK}', badges, what)    # listed with its rule, and why it is held back, under the fold
            self.assertEqual(len(re.findall(r'<tr class="ab-on">', section(html, 'badges'))), 1, what)         # one earned row (Cools fast), and not the held back one
        pdf = squeeze(measures_pdf(self.pdf(aid)))
        self.assertIn(squeeze(f'No serious flags Not yet {HELD_BACK}'), pdf)
        self.assertEqual(pdf.count(squeeze(HELD_BACK)), 1)
        rows = [r for r in self.csv(aid) if r[:1] == ['No serious flags']]
        self.assertEqual([r[:3] for r in rows], [['No serious flags', 'Not yet', HELD_BACK]])
        self.assert_everywhere(aid)

    def test_a_medium_priority_alert_does_not_hold_the_no_serious_flags_tile_back(self):
        """Only a High priority alert holds the tile back. A quarter of the messages being digs raises a Medium alert, and Vibe Check flagged nothing."""
        aid = self.checked([DIG if i % 4 == 0 else CALM[i % 6] for i in range(48)], at(48, days_ago(40)))
        ms = self.ms(aid)
        self.assertEqual(ms['alert_counts'], {'high': 0, 'medium': 1, 'good': 0})
        noflags = next(b for b in ms['badges'] if b['key'] == 'noflags')
        self.assertEqual((noflags['earned'], noflags['needs']), (True, ''))
        self.assertTrue(noflags['evidence'])
        for what in ('results', 'report'):
            self.assertIn('Earned No serious flags', section_text(self.page(aid, what), 'badges'), what)
            self.assertNotIn(HELD_BACK, section_text(self.page(aid, what), 'badges'), what)
        self.assertIn(squeeze('No serious flags Earned'), squeeze(measures_pdf(self.pdf(aid))))
        self.assertNotIn(squeeze(HELD_BACK), squeeze(measures_pdf(self.pdf(aid))))
        self.assert_everywhere(aid)

    def test_the_workplace_page_and_the_report_page_show_it(self):
        aid, start = self.flare()
        ms = self.ms(aid)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assert_page_agrees(ms, html, what)
            self.assertIn('1 thing to look at first.', section_text(html, 'alerts'), what)
            self.assertIn('High priority', section_text(html, 'alerts'), what)
            self.assertIn('Earned Cools fast', section_text(html, 'badges'), what)
            self.assertIn('Escalation', section_text(html, 'over-time'))
            tips = Page(section(html, 'over-time')).tips['Escalation over time']                  # the hover text of the Escalation chart
            marked = {tip['t'] for tip in tips if any(row[0] == 'part of a heated moment' for row in tip['r'])}
            self.assertEqual(len(marked), 1, what)
            self.assertTrue(next(iter(marked)).startswith('Part 3 of 6: '), what)
            rows = [row for tip in tips for row in tip['r'] if row[0] == 'part of a heated moment']
            self.assertTrue(rows and all(re.fullmatch(r'up \d+ per 100 messages', row[1]) for row in rows), rows)       # a unit, and a plain word for the climb
            self.assertNotIn('Nothing needs urgent attention', section_text(html, 'alerts'), 'a High priority alert stands, so it is no all clear')

    def test_the_pdf_says_the_same(self):
        aid, start = self.flare()
        pages = self.pdf(aid)
        self.assert_pdf_agrees(self.ms(aid), pages)
        text = measures_pdf(pages)
        self.assertIn('The mood heated up fast', text)
        self.assertIn('Cools fast Earned', text)
        self.assertNotIn('Model-rated wording.', pages[0])                                         # the cover has no footer note, every page after it has one
        self.assertTrue(all('Model-rated wording. Not a verdict on people.' in page for page in pages[1:]))
        bodies = [FOOTER.sub('', page) for page in pages]
        first = next(i for i, page in enumerate(bodies) if page.startswith('Alerts and badges'))
        self.assertEqual(first, 1)                                                                 # right after the cover
        self.assertTrue(bodies[first + 1].startswith('Measures over time'))
        self.assertLess(first + 1, next(i for i, page in enumerate(bodies) if page.startswith('Summary and guidance')))

    def test_the_csv_says_the_same(self):
        aid, start = self.flare()
        rows = self.csv(aid)
        self.assert_csv_agrees(self.ms(aid), rows)
        escalation = next(r for r in rows if r[:1] == ['Escalation'])
        self.assertEqual(escalation[:3], ['Escalation', 'Steep', '1'])                               # name, level, number of heated moments
        self.assertEqual(escalation[-1], 'Look at this first')                                      # the last column is the mark of the page, in words
        conflict = next(r for r in rows if r[:1] == ['Conflict'])
        self.assertEqual((conflict[1], conflict[-1]), ('Some', 'Worth a look'))                     # a level word says how often; the mark says whether that is good
        self.assertIn(['Health score', '55', 'Fair', 'Neutral'], rows)                              # the health tile: score, status, mark
        self.assertEqual(next(r for r in rows if r[:1] == ['Cools fast'])[1], 'Earned')

    def test_every_part_of_the_chat_has_the_same_numbers_on_both_pages_and_in_the_csv(self):
        aid, start = self.flare()
        m = self.ms(aid)['m']
        csv_parts = rows_under(csv_blocks(self.csv(aid))['Measures over time'], 'Part of the chat')
        for what in ('results', 'report'):
            table = next(t for t in section_tables(self.page(aid, what), 'over-time') if t[0][:2] == ['Part of the chat', 'Messages'])
            self.assertEqual(len(table) - 1, 6)
            for page_row, csv_row, s in zip(table[1:], csv_parts, m['stretches']):
                self.assertEqual((page_row[0], page_row[1]), (csv_row[0], csv_row[1]))
                self.assertEqual(page_row[2:6], csv_row[2:6])                                       # conflict, empathy, passive-aggressive and heated per 100 messages
                self.assertEqual(page_row[6], csv_row[7])                                           # the health score
                self.assertEqual(csv_row[6], 'Yes' if s['counted'] else 'No')
        self.assertEqual([r[6] for r in csv_parts], ['No', 'No', 'Yes', 'No', 'No', 'No'])
        self.assertEqual([r[5] for r in csv_parts], ['0', '0', '88', '0', '0', '0'])                # 7 of 8 is 87.5, shown as 88

    def test_the_summary_strip_keeps_the_vibe_check_score_as_the_headline(self):
        aid, start = self.flare()
        vibe = self.store.latest_vibe(aid)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            summary = section_text(html, 'summary')
            self.assertIn(f'{vibe["score"]:g} / 10', summary)                                         # the Vibe Check score stays the headline
            self.assertIn('1 thing to look at first', summary)
            self.assertIn('1 badge earned', summary)                                                # Cools fast; No serious flags is held back beside the High priority alert
            self.assertNotIn('Health score', summary)
            self.assertIn('Health score', section_text(html, 'over-time'))
            self.assertRegex(section_text(html, 'over-time'), r'the two can differ')                # the caption of the health tile says the two scores can differ
        self.assertIn('the two can differ', ' '.join(self.pdf(aid)))

    def test_with_incognito_off_the_times_of_day_show_in_tooltips_and_tables(self):
        """Incognito off, timed data inside three days: a part of the chat is named by its times in the tooltip and the table, and the axis shows them. An alert
        still never holds a time."""
        aid, start = self.flare()
        ms = self.ms(aid)
        self.assertFalse(ms['m']['incognito'])
        first = ms['m']['stretches'][0]
        self.assertRegex(first['label'], r'^Part 1 of 6: [A-Z][a-z]{2} \d{1,2} 18:00 to 21:30$')
        html = self.page(aid, 'results')
        over = section(html, 'over-time')
        tips = [tip['t'] for tip in Page(over).tips['Conflict over time']]
        self.assertTrue(all(CLOCK.search(t) for t in tips), tips)                              # every tooltip of the chart names its times
        table = next(t for t in section_tables(html, 'over-time') if t[0][:2] == ['Part of the chat', 'Messages'])
        self.assertTrue(all(CLOCK.search(row[0]) for row in table[1:]))
        self.assertTrue(any(CLOCK.search(label) for label in re.findall(r'<text[^>]*>([^<]*)</text>', over)))     # the axis, too
        self.assertTrue(all(CLOCK.search(row[0]) for row in rows_under(csv_blocks(self.csv(aid))['Measures over time'], 'Part of the chat')))
        self.assertTrue(CLOCK.search(measures_pdf(self.pdf(aid))))
        self.assertFalse(CLOCK.search(section_text(html, 'alerts')), 'an alert never holds a clock time')


# =============================================================================================================================================
class CalmChat(MeasuresCase):
    """A calm 48 message chat: no alert to act on, and the badge for calm waters."""

    def calm(self):
        return self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))

    def test_no_high_or_medium_alert_calm_waters_earned_and_no_heated_moment(self):
        ms = self.ms(self.calm())
        self.assertEqual(ms['alert_counts'], {'high': 0, 'medium': 0, 'good': 0})
        self.assertEqual(ms['alerts'], [])
        self.assertEqual((ms['m']['episodes'], ms['m']['verdicts']), ([], True))
        earned = [b['key'] for b in ms['badges'] if b['earned']]
        self.assertIn('calm', earned)
        self.assertEqual(sorted(earned), ['calm', 'courteous', 'noflags'])
        calm = next(b for b in ms['badges'] if b['key'] == 'calm')
        self.assert_says(calm['evidence'], '0 of 48')
        cards = {c['key']: c for c in ms['cards']}
        self.assertEqual((cards['escalation']['level'], cards['escalation']['count']), ('None', 0))
        self.assertTrue(cards['escalation']['headline'].startswith('No heated moment'), cards['escalation']['headline'])
        self.assertTrue(cards['conflict']['headline'].startswith('None of the 48 messages'), cards['conflict']['headline'])
        self.assertEqual({c['trend'] for c in ms['cards'] if c['key'] != 'escalation'}, {'No clear change.'})

    def test_the_pages_say_nothing_needs_urgent_attention_and_that_this_is_not_an_all_clear(self):
        aid = self.calm()
        ms = self.assert_everywhere(aid)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            alerts = section_text(html, 'alerts')
            self.assertIn('Nothing needs urgent attention in these 48 messages', alerts)
            self.assertIn(measures.CAVEAT_EMPTY, alerts)
            listed = alerts.replace(norm(ms['caveats']['alerts']), '')                              # the caveat names the two priorities; no alert is listed under them
            self.assertNotIn('High priority', listed)
            self.assertNotIn('Medium priority', listed)
            self.assertNotIn('Try this', listed)
            self.assertIn('Calm waters', Page(section(html, 'badges')).text)
            self.assertIn('0 things to look at first', section_text(html, 'summary'))
        self.assertIn(squeeze(ms['caveats']['empty']), squeeze(measures_pdf(self.pdf(aid))))
        self.assertIn(['No alerts', ms['caveats']['empty']], self.csv(aid))


# =============================================================================================================================================
class Boundaries(MeasuresCase):
    """The sizes at which the page starts to say more: 24 messages for lines over time, 48 for a direction and a heated moment, 3 in 5 dated for the clock."""

    def hostile_check(self, n):
        return self.checked(hostile_ending(n), at(n, days_ago(40)))

    def test_23_messages_have_no_timeline_and_24_have_one(self):
        short, long = self.hostile_check(23), self.hostile_check(24)
        a, b = self.ms(short), self.ms(long)
        self.assertEqual((a['m']['has_series'], a['m']['stretches'], a['m']['n']), (False, [], 23))
        self.assertEqual((b['m']['has_series'], len(b['m']['stretches']), [s['n'] for s in b['m']['stretches']]), (True, 3, [8, 8, 8]))
        self.assert_says(a['cards'][0]['headline'], 'at least 24 messages')                       # what the first card asks for
        self.assert_says(b['cards'][0]['headline'], 'at least 48 messages')
        self.assertEqual([n['key'] for n in a['notes']], ['few'])
        self.assert_says(a['notes'][0]['text'], 'only 23 messages', 'no lines over time', '24 or more', '48 or more')
        self.assertEqual([n['key'] for n in b['notes']], ['no_verdict'])
        self.assert_says(b['notes'][0]['text'], 'at least 48 messages', 'this one has 24')
        # the level alert needs 24 messages, so 16 hostile of 23 raises none and 16 of 24 raises one
        self.assertEqual((a['alerts'], [x['key'] for x in b['alerts']]), ([], ['conflict']))
        for aid, ms, lines in ((short, a, False), (long, b, True)):
            for what in ('results', 'report'):
                html = self.page(aid, what)
                titles = Page(section(html, 'over-time')).titles
                self.assertEqual('Conflict over time' in titles, lines, what)                   # a drawing for each measure only when there is a timeline
                self.assertEqual('View the four charts as a table' in section(html, 'over-time'), lines, what)
                self.assertEqual('no lines over time yet' in section_text(html, 'over-time'), not lines, what)       # said by the note that explains it, once
                self.assertEqual(norm(ms['chart_key']) in section_text(html, 'over-time'), lines, what)             # the key to the charts is ms['chart_key'], and only where there are charts
                self.assert_page_agrees(ms, html, f'{what} {ms["m"]["n"]}')
            pdf, rows = self.pdf(aid), self.csv(aid)
            self.assert_pdf_agrees(ms, pdf)
            self.assert_csv_agrees(ms, rows)
            self.assertEqual(squeeze(ms['chart_key']) in squeeze(measures_pdf(pdf)), lines)
            self.assertEqual('Lines show messages per 100.' in measures_pdf(pdf), lines)
            self.assertEqual(any(r[:1] == ['Part of the chat'] for r in rows), lines)

    def test_47_messages_have_no_direction_and_no_heated_moment_and_48_have_both(self):
        s47, s48 = self.hostile_check(47), self.hostile_check(48)
        a, b = self.ms(s47), self.ms(s48)
        self.assertEqual((a['m']['verdicts'], a['m']['episodes'], len(a['m']['stretches'])), (False, [], 5))
        self.assertEqual((b['m']['verdicts'], len(b['m']['episodes']), len(b['m']['stretches'])), (True, 1, 6))
        self.assertEqual({c['trend'] for c in a['cards'] if c['key'] != 'escalation'}, {measures.NO_CALL})
        self.assert_says(a['cards'][0]['headline'], 'at least 48 messages')
        self.assertTrue(b['cards'][0]['headline'].startswith('1 heated moment, about two thirds of the way in'), b['cards'][0]['headline'])
        self.assertTrue(b['cards'][1]['trend'].startswith('Rising:'), b['cards'][1]['trend'])                            # 0 in 100 early on, 100 in 100 lately
        self.assert_says(b['cards'][1]['trend'], '0 in 100', '100 in 100')
        self.assertEqual(b['cards'][2]['trend'], 'No clear change.')
        self.assertEqual([x['key'] for x in a['alerts']], ['conflict'])                           # a level alert needs only 24 messages
        self.assertEqual(sorted(x['key'] for x in b['alerts']), ['conflict', 'heated'])
        self.assertEqual(b['alert_counts'], {'high': 2, 'medium': 0, 'good': 0})
        self.assertEqual(a['health']['trend'], '')
        self.assertTrue(b['health']['trend'].startswith('Falling:'), b['health']['trend'])
        needs = {x['key']: x['needs'] for x in a['badges']}
        self.assert_says(needs['calm'], 'at least 48 messages')
        self.assert_says(needs['cools'], 'at least 48 messages')
        self.assertEqual({x['key']: x['needs'] for x in b['badges']}['calm'], '')
        for aid, ms in ((s47, a), (s48, b)):
            self.assert_page_agrees(ms, self.page(aid, 'results'))
            self.assert_pdf_agrees(ms, self.pdf(aid))
            self.assert_csv_agrees(ms, self.csv(aid))
        page47 = section_text(self.page(s47, 'results'), 'over-time')
        self.assertNotIn('Rising', page47)
        self.assertNotIn('Falling', page47)
        self.assertNotIn('1 heated moment', page47)
        self.assertIn(b['cards'][1]['trend'], section_text(self.page(s48, 'results'), 'over-time'))

    def test_a_check_of_one_message_reads_in_the_singular_and_every_surface_still_opens(self):
        """One rated message: 'the 1 message that was rated', 'only 1 message', 'has 1'. Vibe Check needs 5 messages, so this is not an all clear either, and the
        tile of the summary says Not checked."""
        aid = self.checked(['Thanks for the update on the release'], at(1, days_ago(40)))
        ms = self.ms(aid)
        self.assertEqual(ms['basis_text'], 'Built from the 1 message that was rated.')
        self.assertEqual([(n['key'], n['about']) for n in ms['notes']], [('no_vibe', 'alerts'), ('few', 'measures')])
        self.assert_says(ms['notes'][1]['text'], 'We rated only 1 message,', 'no lines over time yet')
        self.assertNotRegex(ms['notes'][1]['text'] + ms['basis_text'] + ms['badge_note'], r'\b1 messages|\b1 message that were|has 1 messages')
        self.assert_says(ms['badge_note'], 'Badges start at 30 messages', 'has 1.')
        self.assertEqual((ms['m']['n'], ms['m']['has_series'], ms['alerts'], ms['all_clear'], ms['badges_earned']), (1, False, [], False, 0))
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertIn(ms['basis_text'], section_text(html, 'over-time'), what)
            self.assertIn('Not checked things to look at first', section_text(html, 'summary'), what)
        self.assertIn(['Basis', 'Built from the 1 message that was rated.'], self.csv(aid))
        self.assert_everywhere(aid)

    def dated_hundred(self, dated):
        """100 calm messages 20 minutes apart. Every message whose number leaves 1 or 3 when divided by 5 has no date (40 of them), and with `dated` = 59 so has message 5."""
        stamps = at(100, days_ago(40), step=20)
        undated = {i for i in range(100) if i % 5 in (1, 3)} | ({4} if dated == 59 else set())
        self.assertEqual(100 - len(undated), dated)
        return self.checked(messages(100), ['' if i in undated else s for i, s in enumerate(stamps)])

    def test_59_percent_dated_follows_the_order_and_60_percent_follows_the_clock(self):
        order, clock = self.dated_hundred(59), self.dated_hundred(60)
        a, b = self.ms(order), self.ms(clock)
        self.assertEqual((a['m']['basis'], a['m']['timeline_n'], a['m']['left_off'], a['m']['n']), ('order', 100, 0, 100))
        self.assertEqual((b['m']['basis'], b['m']['timeline_n'], b['m']['left_off'], b['m']['n']), ('time', 60, 40, 100))
        self.assertEqual([n['key'] for n in a['notes']], ['order'])
        self.assertEqual([n['key'] for n in b['notes']], ['undated'])
        self.assert_says(b['notes'][0]['text'], 'Only 60 of 100 messages have a date', 'still count all 100')
        self.assert_says(a['notes'][0]['text'], 'Only 59 of 100 messages have a usable date', 'the order you added them')
        self.assertTrue(all(s['label'].startswith(f'Part {s["i"] + 1} of 12: rated messages ') for s in a['m']['stretches']))
        self.assertEqual([s['axis'] for s in a['m']['stretches'] if s['axis']], ['#1', '#17', '#34', '#51', '#67', '#84'])
        self.assertFalse(any(s['axis'].startswith('#') for s in b['m']['stretches']))
        self.assertTrue(all(MONTH_DAY.search(s['label']) for s in b['m']['stretches']))
        for aid, ms in ((order, a), (clock, b)):
            self.assert_page_agrees(ms, self.page(aid, 'results'))
            self.assert_pdf_agrees(ms, self.pdf(aid))
            self.assert_csv_agrees(ms, self.csv(aid))
        order_markup, clock_markup = section(self.page(order, 'results'), 'over-time'), section(self.page(clock, 'results'), 'over-time')
        self.assertIn('>#17</text>', order_markup)
        self.assertNotIn('>#17</text>', clock_markup)
        # the key to the charts is ms['chart_key']; where the lines follow the order it also says what the numbers under the charts are, and where they follow the clock it does not
        self.assert_says(a['chart_key'], 'Lines show messages per 100.', 'rated message numbers', 'the order you added them')
        self.assert_says(b['chart_key'], 'Lines show messages per 100.')
        self.assertNotIn('rated message numbers', b['chart_key'])
        self.assertIn(norm(a['chart_key']), Page(order_markup).text)
        self.assertIn(norm(b['chart_key']), Page(clock_markup).text)
        self.assertNotIn('rated message numbers', Page(clock_markup).text)


# =============================================================================================================================================
class TooEarly(MeasuresCase):
    """A check that is too short to judge. MEASURES.md: below 48 timeline messages nothing can be judged, so the Escalation card reads Too early with a neutral mark,
    never a green None; below 48 rated messages the mark of Conflict, Empathy and Passive-aggressive is neutral grey. And a part of the chat that chance alone could
    make hot gets a note on the Escalation card, not a heated moment."""

    SAID = 'Some parts were heated, but none climbed above the parts before it, so no part is flagged.'

    def thin(self):
        """30 messages whose middle (messages 11 to 18) is hostile and heated: more than the 24 that lines need, fewer than the 48 that a verdict needs."""
        return self.scenario('thin', lambda: self.checked(messages(30, hot=(10, 18)), at(30, days_ago(40))))

    def test_escalation_reads_too_early_in_grey_below_48_messages_on_the_pages_the_pdf_and_the_csv(self):
        aid = self.thin()
        ms = self.ms(aid)
        card = ms['cards'][0]
        self.assertEqual((card['level'], card['word'], card['tone'], card['tone_word'], card['count']), ('Too early', 'early', 'info', 'Neutral', None))       # not judged: no count, and not a 0
        self.assert_says(card['headline'], 'at least 48 messages')
        self.assertEqual((ms['m']['has_series'], ms['m']['verdicts']), (True, False))
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertEqual(cards_on_page(html)[0], (icon_svg('info'), 'Too early', 'Neutral'), what)               # the grey dot, and the words for a screen reader
            self.assertNotEqual(cards_on_page(html)[0][0], icon_svg('good'), 'never the green mark of a None')
            self.assertNotRegex(section_text(html, 'over-time'), r'Escalation None\b', what)
        text = squeeze(measures_pdf(self.pdf(aid)))
        self.assertIn(squeeze('Escalation: Too early (Neutral)'), text)
        self.assertNotIn(squeeze('Escalation: None'), text)
        escalation = next(r for r in self.csv(aid) if r[:1] == ['Escalation'])
        self.assertEqual(escalation[:4], ['Escalation', 'Too early', '', ''])                         # no count: an empty cell, never a 0 that a spreadsheet would read as 'none'
        self.assertEqual(escalation[-1], 'Neutral')                                                  # and its mark in words
        self.assertEqual([r[2] for r in rows_under(csv_blocks(self.csv(aid))['Measures over time'], 'Measure')[1:]], [str(c['count']) for c in ms['cards'][1:]])      # messages are still counted
        self.assert_everywhere(aid)

    def test_conflict_empathy_and_sarcasm_are_grey_below_48_rated_messages_and_not_from_48(self):
        short, long = self.hostile_check(47), self.hostile_check(48)
        a, b = self.ms(short), self.ms(long)
        # the same words at 47 and at 48 (16 of 47 and 16 of 48 are hostile, 5 show care, none is a dig): only the marks differ
        self.assertEqual([(c['key'], c['level'], c['tone'], c['tone_word']) for c in a['cards'][1:]],
                         [('conflict', 'Often', 'info', 'Neutral'), ('empathy', 'Often', 'info', 'Neutral'), ('passive', 'Rare', 'info', 'Neutral')])
        self.assertEqual([(c['key'], c['level'], c['tone'], c['tone_word']) for c in b['cards'][1:]],
                         [('conflict', 'Often', 'bad', 'Look at this first'), ('empathy', 'Often', 'good', 'A good sign'), ('passive', 'Rare', 'good', 'A good sign')])
        for aid, ms, marks in ((short, a, ('info', 'info', 'info')), (long, b, ('bad', 'good', 'good'))):
            for what in ('results', 'report'):
                icons = [icon for icon, _, _ in cards_on_page(self.page(aid, what))]
                self.assertEqual(icons[1:], [icon_svg(tone) for tone in marks], f'{what} {ms["m"]["n"]}')
            pdf = squeeze(measures_pdf(self.pdf(aid)))
            for card in ms['cards'][1:]:
                self.assertIn(squeeze(f'{card["name"]}: {card["level"]} ({card["tone_word"]})'), pdf)
            self.assert_everywhere(aid)

    def hostile_check(self, n):
        return self.scenario(f'hostile {n}', lambda: self.checked(hostile_ending(n), at(n, days_ago(40))))

    def test_the_health_tile_is_grey_below_48_rated_messages_and_has_the_mark_of_its_status_from_48(self):
        """MEASURES.md: the health tile's mark is neutral below 48 messages. Its status word (Good, Needs attention) is still said; only the mark is grey. A calm chat
        scores Good and an openly hostile one Needs attention, so at 48 the marks are the green and the red one, and at 47 both are grey."""
        chats = {('calm', n): self.scenario(f'calm {n}', lambda n=n: self.checked(messages(n), at(n, days_ago(40)))) for n in (47, 48)}
        chats.update({('rude', n): self.scenario(f'rude {n}', lambda n=n: self.checked([INSULT] * n, at(n, days_ago(40)))) for n in (47, 48)})
        want = {('calm', 47): ('Good', 'info', 'Neutral'), ('calm', 48): ('Good', 'good', 'A good sign'),
                ('rude', 47): ('Needs attention', 'info', 'Neutral'), ('rude', 48): ('Needs attention', 'bad', 'Look at this first')}
        for (kind, n), (status, tone, word) in want.items():
            aid, ms = chats[kind, n], self.ms(chats[kind, n])
            h = ms['health']
            self.assertEqual((ms['m']['n'], h['status'], h['tone'], h['tone_word']), (n, status, tone, word), (kind, n))
            for what in ('results', 'report'):
                html = self.page(aid, what)
                self.assertEqual(health_mark_on_page(html), icon_svg(tone), f'{what} {kind} {n}')                # the icon beside the status word
                self.assertIn(status, section_text(html, 'over-time'), f'{what} {kind} {n}')                     # the status word is said either way
            self.assertIn(squeeze(f'Health score: {status} ({word})'), squeeze(measures_pdf(self.pdf(aid))), (kind, n))
            self.assertIn(['Health score', str(measures.whole(h['score'])), status, word], self.csv(aid), (kind, n))
            self.assert_everywhere(aid, f'{kind} {n}')

    def test_the_rate_cards_judge_the_rated_messages_and_escalation_the_messages_on_the_timeline(self):
        stamps = at(70, days_ago(40))
        aid = self.checked(messages(70, hot=(20, 30)), [s if i < 45 else '' for i, s in enumerate(stamps)])         # 45 of 70 carry a date: the lines hold 45
        ms = self.ms(aid)
        self.assertEqual((ms['m']['n'], ms['m']['timeline_n'], ms['m']['basis'], ms['m']['has_series'], ms['m']['verdicts']), (70, 45, 'time', True, False))
        self.assertEqual((ms['cards'][0]['level'], ms['cards'][0]['tone']), ('Too early', 'info'))
        self.assertNotIn('info', [c['tone'] for c in ms['cards'][1:] if c['key'] != 'empathy'], '70 messages were rated, so the marks of Conflict and sarcasm are not grey')
        self.assertEqual(cards_on_page(self.page(aid, 'results'))[0], (icon_svg('info'), 'Too early', 'Neutral'))
        self.assertEqual([n['key'] for n in ms['notes']], ['no_verdict', 'undated'])
        self.assert_everywhere(aid)

    def test_a_judged_calm_chat_reads_none_with_the_green_mark(self):
        aid = self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))
        card = self.ms(aid)['cards'][0]
        self.assertEqual((card['level'], card['tone'], card['tone_word']), ('None', 'good', 'A good sign'))
        self.assertEqual(cards_on_page(self.page(aid, 'results'))[0], (icon_svg('good'), 'None', 'A good sign'))
        self.assertIn(squeeze('Escalation: None (A good sign)'), squeeze(measures_pdf(self.pdf(aid))))

    def busy(self):
        """96 messages in 12 parts of 8, heated in 2, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3 and 3 of them: a part at 75 in 100 in a chat that is often heated (38 in 100)."""
        return self.scenario('busy', lambda: self.checked(parts_of([2, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3, 3], 8), at(96, days_ago(40), step=20)))

    def test_a_hot_part_that_chance_alone_could_give_is_a_card_note_and_not_a_heated_moment(self):
        aid = self.busy()
        ms = self.ms(aid)
        m = ms['m']
        self.assertEqual((m['n'], m['verdicts'], m['episodes'], m['opening_heated']), (96, True, [], False))
        self.assertEqual([s['counts']['heated'] for s in m['stretches']], [2, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3, 3])
        escalation = ms['cards'][0]
        self.assertEqual((escalation['level'], escalation['count'], escalation['note']), ('None', 0, self.SAID))
        self.assertEqual([c['note'] for c in ms['cards'][1:]], ['', '', ''])
        self.assertEqual([a['key'] for a in ms['alerts'] if a['key'] == 'heated'], [], 'no heated moment, so no alert about one')
        self.assertEqual([n['key'] for n in ms['notes']], [], 'a card note and not a note of the page')
        for what in ('results', 'report'):
            self.assertEqual(section_text(self.page(aid, what), 'over-time').count(self.SAID), 1, what)
        self.assertEqual(squeeze(measures_pdf(self.pdf(aid))).count(squeeze(self.SAID)), 1)
        self.assertEqual([c for c in measure_cells(self.csv(aid)) if c == self.SAID], [self.SAID])
        self.assertEqual(next(r for r in self.csv(aid) if r[:1] == ['Escalation'])[6], self.SAID)             # the Note column of its row
        self.assert_everywhere(aid)

    def test_the_card_note_for_a_hot_part_is_not_there_when_a_heated_moment_passed_the_chance_test_or_the_chat_is_too_short(self):
        flare = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        young = self.thin()
        for aid in (flare, young):
            for what in ('results', 'report'):
                self.assertNotIn(self.SAID, section_text(self.page(aid, what), 'over-time'), what)
            self.assertEqual(self.ms(aid)['cards'][0]['note'], '')


# =============================================================================================================================================
class SeriousFlags(MeasuresCase):
    """A threat and a harassment message: the one alert that fires on a single message."""

    def threats(self):
        def make():
            texts = messages(48)
            texts[10], texts[30] = THREAT, HARASSMENT
            return self.checked(texts, at(48, days_ago(40)))
        return self.scenario('threats', make)

    def test_a_threat_gives_a_high_alert_that_says_to_read_the_quoted_messages(self):
        ms = self.ms(self.threats())
        (alert,) = ms['alerts']
        self.assertEqual((alert['level'], alert['key'], alert['title']), ('high', 'serious', 'Vibe Check flagged serious messages'))
        self.assert_says(alert['detail'], 'flagged messages for threats (1) and harassment (1)', '2 scored 7 or more out of 10', 'A model can be wrong',
                         'They are quoted under Flagged messages')                                  # what was flagged (each kind counted, never added up), and how sure to be
        self.assert_says(alert['base'], '48 messages')
        self.assert_says(alert['try'], 'Read the quoted messages')                                  # the alert sends the reader to the quotes, it does not repeat them
        self.assert_says(alert['try'], 'safety process', 'local emergency services')                # and, for a threat, to the community's safety process
        self.assertEqual(ms['alert_counts'], {'high': 1, 'medium': 0, 'good': 0})
        self.assertEqual(sum(1 for c in ms['m']['stretches'] if c['counted']), 0)                   # two hostile messages are no heated moment

    def test_the_clean_chat_badge_is_not_earned_and_the_alert_never_quotes_a_message(self):
        aid = self.threats()
        ms = self.ms(aid)
        noflags = next(b for b in ms['badges'] if b['name'] == 'No serious flags')
        self.assertEqual((noflags['earned'], noflags['evidence'], noflags['needs']), (False, '', ''))
        self.assertEqual(ms['badges_earned'], 0)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            badges = section_text(html, 'badges')
            self.assertNotIn('Earned No serious flags', badges)                                    # not among the earned tiles
            self.assertIn('No badges this time.', badges)
            self.assertIn('No serious flags Vibe Check read at least 30 messages', badges)         # but listed with its rule among the others
            self.assertIn('High priority', section_text(html, 'alerts'))
            self.assertIn('1 thing to look at first.', section_text(html, 'alerts'))
            for needle in ('find you', 'report you', 'admins', 'after the match'):
                self.assertNotIn(needle, measures_markup(html), what)                               # the alert and the other sections quote nothing
            self.assertIn('find you after the match', html)                                         # the Vibe Check list below quotes the message
            self.assertGreater(html.index('find you after the match'), html.index('id="vibe-check"'))
            self.assert_page_agrees(ms, html, what)
        pdf, rows = self.pdf(aid), self.csv(aid)
        self.assert_pdf_agrees(ms, pdf)
        self.assert_csv_agrees(ms, rows)
        self.assertNotIn('after the match', measures_pdf(pdf))
        self.assertNotIn('after the match', ' '.join(measure_cells(rows)))
        self.assertIn('No serious flags Not yet', measures_pdf(pdf))
        self.assertIn(['No serious flags', 'Not yet', ''], [r[:3] for r in rows])
        self.assertIn(['High priority', 'Vibe Check flagged serious messages'], [r[:2] for r in rows])

    def test_a_single_message_is_enough_even_in_a_chat_too_short_for_lines(self):
        texts = messages(12)
        texts[4] = THREAT
        aid = self.checked(texts, at(12, days_ago(40)))
        ms = self.ms(aid)
        self.assertEqual((ms['m']['has_series'], [a['key'] for a in ms['alerts']], [a['level'] for a in ms['alerts']]), (False, ['serious'], ['high']))
        self.assert_says(ms['alerts'][0]['detail'], 'flagged 1 message as a threat', 'One scored 7 or more out of 10', 'It is quoted under Flagged messages')       # one message: the singular
        self.assertNotRegex(ms['alerts'][0]['detail'], r'1 messages|as threats|\bThey\b')
        self.assertEqual([n['key'] for n in ms['notes']], ['few'])
        self.assert_says(ms['badge_note'], 'Badges start at 30 messages', 'has 12')
        self.assertEqual(ms['badges_earned'], 0)
        self.assert_everywhere(aid)

    SAFETY = "A model can be wrong. If a message describes a real risk to someone, follow your community's safety process or contact local emergency services."

    def hate_speech(self, texts):
        """A check whose Vibe Check calls every threat hate speech (the keyword stub has no rule for hate speech)."""
        stub = self.fake._vibe

        def relabel(system, prompt, schema=None):
            text, model = stub(system, prompt, schema)
            data = json.loads(text)
            if 'toxicityTypes' in data:
                for flagged in data.get('flaggedMessages', []):
                    flagged['type'] = 'hate_speech' if flagged['type'] == 'threat' else flagged['type']
                data['toxicityTypes']['hate_speech'], data['toxicityTypes']['threats'] = data['toxicityTypes']['threats'], 0
            return json.dumps(data), model
        with mock.patch.object(self.fake, '_vibe', relabel):
            return self.checked(texts, at(len(texts), days_ago(40)))

    def test_the_flagged_messages_carry_the_safety_sentence_for_a_threat_or_hate_speech_on_the_pages_in_the_pdf_and_in_the_csv(self):
        """A threat or hate speech among the flagged messages puts the safety sentence directly under the heading of the flagged messages, in the Vibe Check part of
        the page and of the PDF (the alert points there), and as a Note row of the CSV right before the flagged messages. Nothing is added for the other kinds."""
        threat = self.threats()
        hate = self.scenario('hate', lambda: self.hate_speech([THREAT if i == 10 else t for i, t in enumerate(messages(48))]))
        harassment = self.scenario('harassment only', lambda: self.checked([HARASSMENT if i == 30 else t for i, t in enumerate(messages(48))], at(48, days_ago(40))))
        for aid, count, kind, said in ((threat, 2, 'a threat', r'threats \(1\)'), (hate, 1, 'hate speech', r'1 message as hate speech')):
            self.assertRegex(self.ms(aid)['alerts'][0]['detail'], said)
            for what in ('results', 'report'):
                html = self.page(aid, what)
                text = Page(html[html.index('id="vibe-check"'):]).text
                self.assertEqual(text.count(self.SAFETY), 1, f'{what} {kind}')
                self.assertIn(f'Flagged messages ({count}) {self.SAFETY} Ordered by severity.', text, f'{what} {kind}')             # directly under its heading
            pdf = norm(' '.join(self.pdf(aid)))
            self.assertEqual(pdf.count(self.SAFETY), 1, kind)
            self.assertIn(f'Flagged messages ({count}) {self.SAFETY}', pdf, kind)
            rows = self.csv(aid)                                                                      # the CSV says it once, in a Note row, right before the flagged messages
            at_note = [i for i, row in enumerate(rows) if row == ['Note', self.SAFETY]]
            self.assertEqual(len(at_note), 1, kind)
            self.assertEqual(rows[at_note[0] + 1], ['Severity', 'Type', 'Source', 'Flagged message'], kind)
            quoted = rows[at_note[0] + 2:next((i for i in range(at_note[0] + 2, len(rows)) if rows[i] == []), len(rows))]
            self.assertEqual(len(quoted), count, kind)                                                # the quoted messages follow, as many as were flagged
        for what in ('results', 'report'):                                                          # harassment alone: the alert is High (a message scored 7), the sentence is not added
            html = self.page(harassment, what)
            self.assertNotIn(self.SAFETY, Page(html[html.index('id="vibe-check"'):]).text, what)
            self.assertEqual(self.ms(harassment)['alerts'][0]['level'], 'high')
        pdf = norm(' '.join(self.pdf(harassment)))
        self.assertNotIn(self.SAFETY, pdf)
        self.assertIn('Flagged messages (1)', pdf)
        rows = self.csv(harassment)
        self.assertNotIn(['Note', self.SAFETY], rows)
        self.assertIn(['Severity', 'Type', 'Source', 'Flagged message'], rows)                       # the flagged message is still listed, with no safety sentence before it

    def test_harassment_that_scored_below_seven_is_medium_priority(self):
        stub = self.fake._vibe

        def gentler(system, prompt, schema=None):
            text, model = stub(system, prompt, schema)
            data = json.loads(text)
            for flagged in data.get('flaggedMessages', []):
                flagged['severity'] = min(flagged['severity'], 5)                               # Vibe Check scores it 5, not 7 or more
            return json.dumps(data), model
        texts = messages(48)
        texts[30] = HARASSMENT
        with mock.patch.object(self.fake, '_vibe', gentler):
            aid = self.checked(texts, at(48, days_ago(40)))
        ms = self.ms(aid)
        (alert,) = ms['alerts']
        self.assertEqual((alert['level'], alert['key']), ('medium', 'serious'))
        self.assert_says(alert['detail'], 'flagged 1 message as harassment', 'A model can be wrong', 'It is quoted under Flagged messages')
        self.assertNotIn('7 or more', alert['detail'])                                          # none scored in the red band
        self.assert_says(alert['try'], 'Read the quoted messages')
        self.assertNotIn('emergency services', alert['try'])                                    # that line is for threats and hate speech
        self.assertEqual(ms['alert_counts'], {'high': 0, 'medium': 1, 'good': 0})
        self.assertIn('Medium priority', section_text(self.page(aid, 'results'), 'alerts'))
        self.assert_everywhere(aid)


# =============================================================================================================================================
class Checks(MeasuresCase):
    """One record per check, and what the pages say after each check: a second and a third check, the same evidence again, a guidance that fails, Vibe Check alone, a cancel."""

    def test_two_checks_two_records_and_the_table_since_the_last_check(self):
        aid = self.new_analysis()
        self.check(aid, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(90))))                # 12 heated messages in a row, 9 of them hostile
        self.assertEqual(len(self.store.measure_history(aid)), 1)
        first = self.ms(aid)
        self.assertIsNone(first['since'])
        for what in ('results', 'report'):
            self.assertIn('This is your first check.', section_text(self.page(aid, what), 'over-time'))
        self.new_check(aid)
        self.assertEqual(len(self.store.measure_history(aid)), 1, 'the record of the earlier check stays when a new check starts')
        self.assertIn('has no results yet', self.client.get(f'/analysis/{aid}/results').text, 'a new check has no results yet')
        self.check(aid, chat_csv(messages(48), at(48, days_ago(60))), 'two.csv')
        history = self.store.measure_history(aid)
        self.assertEqual(len(history), 2)                                                          # exactly two records
        self.assertEqual([(r['n'], r['hostile_n'], r['heated_n'], r['care_n']) for r in history], [(48, 9, 12, 6), (48, 0, 0, 8)])
        self.assertLess(history[0]['gen'], history[1]['gen'])
        ms = self.assert_everywhere(aid)
        self.assertTrue(ms['since']['comparable'])
        rows = ms['since']['rows']                                                                  # the health score, then conflict, empathy, sarcasm and heated messages
        self.assertEqual(len(rows), 5)
        self.assertEqual([(r['before'], r['now'], r['verdict']) for r in rows[1:]],
                         [('9 of 48', '0 of 48', 'Better'), ('6 of 48', '8 of 48', 'About the same'), ('0 of 48', '0 of 48', 'About the same'), ('12 of 48', '0 of 48', 'Better')])
        self.assertEqual(rows[0]['verdict'], 'Better')
        self.assertLess(rows[0]['before'], rows[0]['now'])                                          # the health score rose
        self.assertEqual([(a['level'], a['key']) for a in ms['alerts']], [('good', 'mood_up'), ('good', 'conflict_down')])
        for what in ('results', 'report'):
            html = self.page(aid, what)
            since = next(t for t in section_tables(html, 'over-time') if t[0][:3] == ['Measure', 'Last check', 'This check'])
            self.assertEqual([r[3] for r in since[1:]], ['Better', 'Better', 'About the same', 'About the same', 'Better'], what)
            self.assertIn('Good news', section_text(html, 'alerts'))
            self.assertNotIn('Check by check', section_text(html, 'over-time'), 'the line chart waits for the third check')
        self.assertNotIn('check by check', ' '.join(self.pdf(aid)))

    def test_a_third_check_adds_the_check_by_check_chart_and_a_worse_verdict(self):
        aid = self.new_analysis()
        for k, hot in enumerate(((16, 28), (0, 0), (30, 42))):
            if k:
                self.new_check(aid)
            self.check(aid, chat_csv(messages(48, hot=hot), at(48, days_ago(90 - 30 * k))), f'c{k}.csv')
            self.assertEqual(len(self.store.measure_history(aid)), k + 1)
            html = self.page(aid, 'results')
            titles = Page(section(html, 'over-time')).titles
            self.assertEqual('Health score, check by check' in titles, k == 2, f'check {k + 1}')                  # the line chart starts at the third check
            self.assertEqual('Check by check' in section_text(html, 'over-time'), k == 2, f'check {k + 1}')
            self.assertEqual('View the checks as a table' in section(html, 'over-time'), k == 2, f'check {k + 1}')    # and comes with its table twin
        ms = self.assert_everywhere(aid)
        rows = ms['since']['rows']
        self.assertEqual([(r['before'], r['now'], r['verdict']) for r in rows[1:]],
                         [('0 of 48', '9 of 48', 'Worse'), ('8 of 48', '6 of 48', 'About the same'), ('0 of 48', '0 of 48', 'About the same'), ('0 of 48', '12 of 48', 'Worse')])
        self.assertEqual(rows[0]['verdict'], 'Worse')
        self.assertGreater(rows[0]['before'], rows[0]['now'])
        self.assertIn('mood_down', [a['key'] for a in ms['alerts']])
        slipped = next(a for a in ms['alerts'] if a['key'] == 'mood_down')
        self.assertEqual(slipped['base'], 'Based on 48 messages in each check.')                   # two checks of the same size: not "Based on 48 and 48 messages"
        self.assertIn(slipped['base'], section_text(self.page(aid, 'results'), 'alerts'))
        self.assertIn(squeeze(slipped['base']), squeeze(alerts_pdf(self.pdf(aid))))
        self.assertEqual(ms['alert_counts'], {'high': 1, 'medium': 2, 'good': 0})
        self.assertEqual([r['hostile_n'] for r in ms['trail']], [9, 0, 9])
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertIn('3 things worth a look.', section_text(html, 'alerts'))
            self.assertIn('Check by check', section_text(html, 'over-time'))
            table = next(t for t in section_tables(html, 'over-time') if t[0][:2] == ['Check', 'Date'])
            self.assertEqual([r[0] for r in table[1:]], ['Check 1', 'Check 2', 'Check 3 (this check)'])
        self.assertIn('Health score, check by check', measures_pdf(self.pdf(aid)))
        trail = rows_under(csv_blocks(self.csv(aid))['Since your last check'], 'Check')
        self.assertEqual([r[0] for r in trail], ['Check 1', 'Check 2', 'Check 3 (this check)'])
        self.assertEqual([r[4] for r in trail], ['9', '0', '9'])

    def test_running_the_same_evidence_again_keeps_the_count_even_when_a_rating_changes(self):
        aid = self.new_analysis()
        self.check(aid, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(90))))
        self.new_check(aid)
        self.check(aid, chat_csv(messages(48, hot=(30, 42)), at(48, days_ago(60))), 'two.csv')
        before = self.store.measure_history(aid)
        self.assertEqual(len(before), 2)
        self.assertEqual(self.run_job(aid, path='run-ai')['status'], 'complete')                    # the same messages again
        again = self.store.measure_history(aid)
        self.assertEqual(len(again), 2)
        self.assertEqual([(r['gen'], r['n'], r['hostile_n'], r['sig']) for r in again], [(r['gen'], r['n'], r['hostile_n'], r['sig']) for r in before])
        self.assertEqual(len(self.ms(aid)['trail']), 2)

        def quiet(text):                                                                           # the model now rates every message 0
            return {a: 0 for a in fake_ollama.ATTRIBUTES}, {}
        with mock.patch.object(fake_ollama, 'rate', quiet):
            self.assertEqual(self.run_job(aid, path='run-ai')['status'], 'complete')
        changed = self.store.measure_history(aid)
        self.assertEqual(len(changed), 2, 'a changed rating is no new check')
        self.assertEqual(changed[0], before[0])                                                    # the earlier check is untouched
        self.assertEqual((changed[1]['gen'], changed[1]['hostile_n'], changed[1]['heated_n']), (before[1]['gen'], 0, 0))
        self.assertNotEqual(changed[1]['sig'], before[1]['sig'])
        self.assertEqual(self.ms(aid)['record']['hostile_n'], 0)
        self.assert_everywhere(aid)

    def test_a_guidance_that_fails_gives_finished_with_problems_and_still_exactly_one_record(self):
        aid = self.new_analysis()
        self.upload(aid, ('chat.csv', chat_csv(messages(48, hot=(16, 23)), at(48, days_ago(40))), 'text/csv'))
        self.fake.fail_next('guidance')
        job = self.run_job(aid, path='check/6', engine='')
        self.assertEqual(job['status'], 'complete')
        self.assertTrue(job['message'].startswith(self.app.pipeline.WITH_PROBLEMS), job['message'])
        self.assertIn('guidance could not be created', job['message'])
        self.assertEqual(len(self.store.measure_history(aid)), 1)
        self.assertIn(self.app.pipeline.WITH_PROBLEMS, self.page(aid, 'workspace'))
        html = self.page(aid, 'results')
        ms = self.assert_everywhere(aid, 'after the failure')                                     # the measures only need the ratings
        self.assertEqual([a['key'] for a in ms['alerts']], ['heated'])
        self.assertIn('The written guidance has not been created', html)
        before = self.store.measure_history(aid)
        job = self.run_job(aid, path='run-ai')
        self.assertEqual((job['status'], job['message'].startswith('Your report is ready')), ('complete', True), job)       # every part ran this time
        self.assertEqual(len(self.store.measure_history(aid)), 1)
        self.assertEqual(self.store.measure_history(aid)[0]['gen'], before[0]['gen'])
        self.assertNotIn('The written guidance has not been created', self.page(aid, 'results'))

    def test_without_a_vibe_check_an_empty_list_is_not_an_all_clear(self):
        """Vibe Check could not run: the 13 ratings still give measures, but a list with no alerts must not read as though every message was checked."""
        aid = self.new_analysis()
        self.upload(aid, ('chat.csv', chat_csv(messages(48), at(48, days_ago(40))), 'text/csv'))
        self.fake.fail_next('vibe', 99)
        job = self.run_job_fast(aid, path='check/6', engine='')
        self.fake.failures.clear()
        self.assertEqual(job['status'], 'complete')
        self.assertTrue(job['message'].startswith(self.app.pipeline.WITH_PROBLEMS), job['message'])
        ms = self.ms(aid)
        self.assertEqual([n['key'] for n in ms['notes']], ['no_vibe'])
        note = ms['notes'][0]['text']
        self.assert_says(note, 'Vibe Check', 'not an all clear')
        self.assertEqual(ms['alerts'], [])
        noflags = next(b for b in ms['badges'] if b['key'] == 'noflags')
        self.assertEqual((noflags['earned'], bool(noflags['needs'])), (False, True))
        self.assert_says(noflags['needs'], 'Vibe Check', 'at least 30 messages')
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertIn(note, section_text(html, 'alerts'))
            self.assertNotIn('Nothing needs urgent attention', section_text(html, 'alerts'))        # that line is only for a check that Vibe Check read as well
            summary = section_text(html, 'summary')
            self.assertIn('Not run Vibe Check score', summary)
            self.assertIn('Not checked things to look at first', summary)                           # nothing was looked for, so it is not '0 things to look at first'
            self.assertNotIn('0 things', summary)
        self.assert_everywhere(aid)
        self.assertIn(['Note', note], self.csv(aid))
        self.assertEqual(len(self.store.measure_history(aid)), 1)

    def test_without_a_vibe_check_the_summary_says_not_checked_only_while_no_alert_stands(self):
        """The strip's tile reads 'Not checked' when Vibe Check did not run and no alert stands (a count of 0 would read as an all clear). An alert from the ratings
        alone is a thing to look at whether Vibe Check ran or not, so then the tile counts."""
        aid = self.new_analysis()
        self.upload(aid, ('chat.csv', chat_csv(messages(48, hot=(16, 23)), at(48, days_ago(40))), 'text/csv'))
        self.fake.fail_next('vibe', 99)
        self.assertEqual(self.run_job_fast(aid, path='check/6', engine='')['status'], 'complete')
        self.fake.failures.clear()
        ms = self.ms(aid)
        self.assertEqual(([a['key'] for a in ms['alerts']], [n['key'] for n in ms['notes']]), (['heated'], ['no_vibe']))
        for what in ('results', 'report'):
            summary = section_text(self.page(aid, what), 'summary')
            self.assertIn('1 thing to look at first', summary, what)
            self.assertNotIn('Not checked', summary, what)
            self.assertIn('Not run Vibe Check score', summary, what)
        self.assert_everywhere(aid)
        with_vibe = self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))              # Vibe Check ran and nothing stands: a true 0
        for what in ('results', 'report'):
            summary = section_text(self.page(with_vibe, what), 'summary')
            self.assertIn('0 things to look at first', summary, what)
            self.assertNotIn('Not checked', summary, what)

    def test_a_check_made_with_another_sensitivity_or_another_model_is_compared_with_nothing(self):
        aid = self.checked(messages(48, hot=(16, 28)), at(48, days_ago(60)))
        self.new_check(aid)
        self.assertEqual(self.client.post(f'/analysis/{aid}/check/5', data={'vibe_sensitivity': 'low'}).status_code, 303)
        self.check(aid, chat_csv(messages(48), at(48, days_ago(30))), 'two.csv')
        ms = self.assert_everywhere(aid, 'other sensitivity')
        why = ms['since']['why']
        self.assertFalse(ms['since']['comparable'])
        self.assert_says(why, 'different sensitivity', 'not from the community')
        self.assertEqual([r['verdict'] for r in ms['since']['rows']], [''] * 5)
        self.assertEqual(([(n['key'], n['about']) for n in ms['notes']], ms['alerts']), ([('low_sensitivity', 'measures')], []))   # the reason two checks are not alike is no note; no Better or Worse, so no good news and no slipping mood
        for what in ('results', 'report'):
            html = self.page(aid, what)
            over = section_text(html, 'over-time')
            table = next(t for t in section_tables(html, 'over-time') if t[0][:3] == ['Measure', 'Last check', 'This check'])
            self.assertEqual(table[0], ['Measure', 'Last check', 'This check'], what)               # no Change column
            self.assertIn('No comparison', over)
            self.assertEqual(over.count(norm(why)), 1, what)                                        # the reason is said once, with the comparison ...
            self.assertNotIn(norm(why), section_text(html, 'alerts'), what)                         # ... and not as a note under Alerts
            for word in ('Better', 'Worse', 'About the same'):
                self.assertNotIn(word, over, what)
        text = measures_pdf(self.pdf(aid))
        self.assertIn('No comparison.', text)
        self.assertEqual(squeeze(text).count(squeeze(why)), 1)
        self.assertNotIn(squeeze(why), squeeze(alerts_pdf(self.pdf(aid))))
        self.assertEqual([r for r in self.csv(aid) if why in r], [['No comparison', why]])         # in the CSV too: once, in the block of the comparison, and not as a Note row
        for word in ('Better', 'Worse', 'About the same'):
            self.assertNotIn(word, text)
        # another model, with the sensitivity of the check before: still nothing to compare
        self.fake.extra_models = [{'name': 'tiny:1b', 'details': {'parameter_size': '1B'}}]
        try:
            self.new_check(aid)
            self.check(aid, chat_csv(messages(48), at(48, days_ago(10))), 'three.csv', engine='tiny:1b')
        finally:
            self.fake.extra_models = []
        ms = self.assert_everywhere(aid, 'other model')
        self.assertEqual([r['model'] for r in ms['trail']], ['qwen2.5:7b', 'qwen2.5:7b', 'tiny:1b'])
        self.assertFalse(ms['since']['comparable'])
        self.assert_says(ms['since']['why'], 'different model', 'not from the community')
        self.assertEqual(len(self.store.measure_history(aid)), 3)

    def test_two_checks_made_with_a_long_model_name_are_compared(self):
        """MEASURES.md: the model name kept in a record is up to 120 characters of letters, digits and . _ : / @ + - ( ) and spaces. A Hugging Face tag of 65 characters
        used to be dropped from the saved record while the record of the check being shown kept it, so two checks made with the same model read "Your last check used
        a different model", for every check from then on. Now both keep it, and a name too long for either is dropped from both. Each record also carries a short
        fingerprint of the name (model_key), so two checks made with the same long name compare (see the next test for two different ones)."""
        for name, kept in (('hf.co/mradermacher/Llama-3.1-Nemotron-70B-Instruct-HF-GGUF:Q4_K_M', True), ('hf.co/' + 'q' * 114, True), ('hf.co/' + 'q' * 115, False)):
            with self.subTest(length=len(name)):
                self.assertEqual(len(name) <= 120, kept)
                self.fake.extra_models = [{'name': name, 'details': {'parameter_size': '70B'}}]
                try:
                    aid = self.new_analysis()
                    self.check(aid, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(60))), engine=name)
                    self.new_check(aid)
                    self.check(aid, chat_csv(messages(48), at(48, days_ago(30))), 'two.csv', engine=name)
                finally:
                    self.fake.extra_models = []
                self.assertEqual(self.store.get_analysis(aid)['model_used'], name)
                if kept:                                                                                 # up to 120 characters the name is in both records
                    self.assertEqual([r.get('model') for r in self.store.measure_history(aid)], [name, name])
                ms = self.assert_everywhere(aid, f'a model name of {len(name)} characters')
                if kept:
                    self.assertEqual(ms['record'].get('model'), name)
                self.assertEqual(ms['record'].get('model'), self.store.measure_history(aid)[-1].get('model'), 'the record of this check follows the same rules as a saved one')
                keys = [r.get('model_key') for r in self.store.measure_history(aid)]                    # the fingerprint of the name is kept whether the name is or not
                self.assertTrue(all(re.fullmatch(r'[0-9a-f]{12}', key or '') for key in keys), keys)
                self.assertEqual(len(set(keys)), 1, 'the same model, the same fingerprint')
                self.assertEqual(ms['record'].get('model_key'), keys[-1])
                self.assertTrue(ms['since']['comparable'], ms['since']['why'])
                self.assertEqual(ms['notes'], [])

    def test_two_different_model_names_that_the_store_cannot_keep_are_never_taken_for_one_model(self):
        """Two names of 121 characters that differ in the last letter are both too long for a record, so neither is kept. Each record keeps the fingerprint of its
        name (model_key), and the comparison goes by the fingerprint: two checks made with different models are not alike, whether or not the names were kept."""
        first, second = 'hf.co/' + 'q' * 115, 'hf.co/' + 'q' * 114 + 'r'
        self.assertEqual((len(first), len(second)), (121, 121))
        self.fake.extra_models = [{'name': first, 'details': {'parameter_size': '70B'}}, {'name': second, 'details': {'parameter_size': '70B'}}]
        try:
            aid = self.new_analysis()
            self.check(aid, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(60))), engine=first)
            self.new_check(aid)
            self.check(aid, chat_csv(messages(48), at(48, days_ago(30))), 'two.csv', engine=second)
        finally:
            self.fake.extra_models = []
        history = self.store.measure_history(aid)
        self.assertEqual([r.get('model') for r in history], [None, None])                                   # neither name could be kept ...
        self.assertEqual(len({r['model_key'] for r in history}), 2)                                         # ... and the two fingerprints differ
        ms = self.assert_everywhere(aid, 'two long names')
        self.assertFalse(ms['since']['comparable'])
        self.assert_says(ms['since']['why'], 'different model', 'not from the community')
        self.assertEqual([r['verdict'] for r in ms['since']['rows']], [''] * 5)
        self.assertEqual(ms['alerts'], [])                                                                 # no Better or Worse, so no good news and no slipping mood

    def test_each_workplace_keeps_its_own_history(self):
        first, second = self.new_analysis(name='First place'), self.new_analysis(name='Second place')
        self.check(first, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(60))))
        self.new_check(first)
        self.check(first, chat_csv(messages(48), at(48, days_ago(30))), 'two.csv')
        self.check(second, chat_csv(messages(48), at(48, days_ago(30))))
        self.assertEqual((len(self.store.measure_history(first)), len(self.store.measure_history(second))), (2, 1))
        self.assertIsNotNone(self.ms(first)['since'])
        self.assertIsNone(self.ms(second)['since'])
        self.assertIn('This is your first check.', section_text(self.page(second, 'results'), 'over-time'))
        self.assertEqual(self.client.post(f'/analysis/{first}/clear-units').status_code, 303)
        self.assertEqual((len(self.store.measure_history(first)), len(self.store.measure_history(second))), (2, 1))

    def test_vibe_check_alone_on_a_fresh_analysis_shows_pages_pdf_and_csv_without_the_measures(self):
        """Vibe Check alone rates no tones, so there are no measures, no badges and no summary. The page, the PDF and the CSV still open, and say the ratings are
        missing. A panel of alerts is allowed to stand there if it says why it is empty (alerts need the ratings), but it must list nothing."""
        aid = self.new_analysis()
        self.upload(aid, ('chat.csv', chat_csv(messages(48, hot=(16, 23)), at(48, days_ago(40))), 'text/csv'))
        job = self.run_job(aid, path='run-vibe')
        self.assertEqual(job['status'], 'complete', job)
        self.assertIsNone(report.build(aid)['measures'])
        self.assertEqual(self.store.measure_history(aid), [])
        self.surfaces_render(aid)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            for sid in ('summary', 'over-time', 'badges'):
                self.assertNotIn(f'id="{sid}"', html, what)
            self.assertIn('id="vibe-check"', html)
            self.assertIn('The 13 communication signals have no ratings for this analysis', html, what)       # the page says the ratings are missing
            self.assertRegex(Page(html).text, r'(?i)alerts, badges and (?:the )?measures over time need (?:the|these) ratings too', what)       # and what else waits for them
            self.assertNotIn('High priority', html)
            self.assertNotIn('Try this', html)
            if 'id="alerts"' in html:
                self.assert_says(section_text(html, 'alerts'), 'ratings')
        text = ' '.join(self.pdf(aid))
        self.assertNotIn('Measures over time', text)
        self.assertNotIn('Cools fast', text)
        self.assertIn('Measured behaviour: not available.', text)
        self.assertIn('The 13 communication signals have no ratings for this analysis', text)
        self.assertRegex(text, r'(?i)alerts, badges and (?:the )?measures over time need (?:the|these) ratings too')
        rows = self.csv(aid)
        self.assertEqual(set(csv_blocks(rows)) - {'Alerts'}, set())
        self.assertIn(['Not available', report.NO_BEHAVIOUR[1]], rows)
        self.assertRegex(report.NO_BEHAVIOUR[1], r'(?i)alerts, badges and (?:the )?measures over time need (?:the|these) ratings too')

    def test_a_vibe_check_only_run_after_a_full_run_adds_no_record(self):
        aid = self.new_analysis()
        self.upload(aid, ('chat.csv', chat_csv(messages(48, hot=(16, 23)), at(48, days_ago(40))), 'text/csv'))
        self.assertEqual(self.run_job(aid, path='run-vibe')['status'], 'complete')                  # Vibe Check first: no ratings, no record
        self.assertEqual(self.store.measure_history(aid), [])
        self.assertEqual(self.run_job(aid, path='run-ai')['status'], 'complete')
        self.assertEqual(len(self.store.measure_history(aid)), 1)
        record = self.store.measure_history(aid)
        before = self.ms(aid)
        self.assertEqual(self.run_job(aid, path='run-vibe', vibe_sensitivity='medium')['status'], 'complete')
        self.assertEqual(self.store.measure_history(aid), record)
        after = self.ms(aid)
        self.assertEqual((after['m'], after['alerts'], after['cards']), (before['m'], before['alerts'], before['cards']))
        self.assert_everywhere(aid)

    def test_cancelling_after_the_ratings_keeps_the_record_and_before_them_leaves_none(self):
        pipeline = self.app.pipeline

        def real(instructions, text, schema=None):
            return pipeline.local_model_call(instructions, text, schema=schema)
        aid = self.new_analysis()
        self.upload(aid, ('chat.csv', chat_csv(messages(48, hot=(16, 23)), at(48, days_ago(40))), 'text/csv'))
        totalled = self.app.analysis.calculate_metrics

        def totalled_then_cancel(analysis_id):
            totalled(analysis_id)
            self.store.request_cancel(aid)                                                         # the user cancels as the ratings are totalled
        with mock.patch.object(self.app.analysis, 'calculate_metrics', totalled_then_cancel):
            pipeline.run_job(self.store.create_job(aid, 'full'), aid, 'full', call=real)
        self.assertEqual(self.store.latest_job(aid)['status'], 'cancelled')
        self.assertEqual(len(self.store.measure_history(aid)), 1)
        self.assertEqual(len(self.app.analysis.metrics_for(aid)), 13)
        self.assertEqual(self.store.measure_history(aid)[0]['n'], 48)
        self.assertIn('Cancelled', self.page(aid, 'workspace'))
        ms = self.assert_everywhere(aid, 'after a cancel')
        self.assertEqual([a['key'] for a in ms['alerts']], ['heated'])
        self.assertEqual(self.run_job(aid, path='run-ai')['status'], 'complete')                    # running it again replaces the record
        self.assertEqual(len(self.store.measure_history(aid)), 1)
        # a cancel before the ratings are saved leaves nothing at all
        other = self.new_analysis()
        self.upload(other, ('chat.csv', chat_csv(messages(48, hot=(16, 23)), at(48, days_ago(40))), 'text/csv'))
        calls = []

        def cancel_on_the_second_call(instructions, text, schema=None):
            calls.append(1)
            if len(calls) == 2:
                self.store.request_cancel(other)
            return real(instructions, text, schema)
        pipeline.run_job(self.store.create_job(other, 'full'), other, 'full', call=cancel_on_the_second_call)
        self.assertEqual(self.store.latest_job(other)['status'], 'cancelled')
        self.assertEqual((self.store.measure_history(other), report.build(other)['measures']), ([], None))
        gone = self.client.get(f'/analysis/{other}/report')                                       # no results, so no report to open: the Results page says so
        self.assertEqual((gone.status_code, gone.headers['location']), (303, f'/analysis/{other}/results'))


# =============================================================================================================================================
class Notes(MeasuresCase):
    """What every surface prints from ms, so that none of them drifts: each quiet note once, in its own section; 'Nothing needs urgent attention' only when ms['all_clear']
    says so; the two fixed sentences (ms['texts']) and the caveats (ms['caveats']). The helpers of MeasuresCase check all of it in every scenario of this module; these
    tests put the cases together and say the sentences."""

    LOW = 'Your sensitivity is Low, so banter counts as normal. The bars for Conflict, Passive-aggressive and heated moments are higher.'
    FIRST = 'This is your first check. Run another after your next batch of messages and this panel can show what moved, if both checks used the same settings.'
    NO_BADGES = 'No badges this time. Each one is earned only when the messages clearly show it.'

    def test_every_note_is_printed_once_in_its_own_section_on_the_pages_the_pdf_and_the_csv(self):
        """Six notes at once: Vibe Check did not run (about the alerts), and Low sensitivity, 30 in 100 undated, one message with an incomplete rating, almost no tone
        and four messages added later (about the measures)."""
        aid = self.many_notes()
        ms = self.ms(aid)
        self.assertEqual({n['key']: n['about'] for n in ms['notes']},
                         {'no_vibe': 'alerts', 'undated': 'measures', 'incomplete': 'measures', 'low_sensitivity': 'measures', 'silent': 'measures', 'added': 'measures'})
        by_key = {n['key']: n['text'] for n in ms['notes']}
        self.assertEqual(by_key['low_sensitivity'], self.LOW)
        self.assert_says(by_key['silent'], 'Almost no tone was found in these messages', 'The model may not have read this text')
        self.assert_says(by_key['added'], '4 messages fall inside the review period but were not rated, so they are not included', 'Run the check again to include them')
        self.assert_says(by_key['incomplete'], '1 message had incomplete ratings', 'left out')
        self.assertRegex(by_key['undated'], r'^Only \d+ of 99 messages have a date\.')
        self.assert_says(by_key['no_vibe'], 'Vibe Check', 'not an all clear')
        self.assertFalse(ms['all_clear'])
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertEqual(quiet_notes(html, 'alerts'), [by_key['no_vibe']], what)
            self.assertEqual(sorted(quiet_notes(html, 'over-time')), sorted(v for k, v in by_key.items() if k != 'no_vibe'), what)
        pdf = squeeze(measures_pdf(self.pdf(aid)))
        for key, text in by_key.items():
            self.assertEqual(pdf.count(squeeze(text)), 1, f'pdf: {key}')
        self.assertIn(squeeze(by_key['no_vibe']), squeeze(alerts_pdf(self.pdf(aid))))
        csv_notes = [r[1] for r in self.csv(aid) if r[:1] == ['Note']]
        self.assertEqual(sorted(csv_notes), sorted(list(by_key.values()) + [ms['badge_note']]), 'one Note row for each, in the block of its own section, and the note of the badges')
        self.assert_everywhere(aid, 'six notes')

    def test_a_silent_chat_is_no_all_clear_and_says_so_with_the_measures_and_the_badges(self):
        aid = self.checked([NEUTRAL] * 60, at(60, days_ago(40)))
        ms = self.ms(aid)
        self.assertEqual([(n['key'], n['about']) for n in ms['notes']], [('silent', 'measures')])
        silent = ms['notes'][0]['text']
        self.assert_says(silent, 'Almost no tone was found in these messages', 'The model may not have read this text')
        self.assertEqual((ms['alerts'], ms['all_clear'], ms['badges_earned']), ([], False, 0))
        self.assert_says(ms['badge_note'], 'Almost no tone was found', 'no badges were given')
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertNotIn('Nothing needs urgent attention', section_text(html, 'alerts'), what)
            self.assertIn(ms['caveats']['empty'], section_text(html, 'alerts'), what)                      # the list is empty, and that is not an all clear
            self.assertIn(silent, section_text(html, 'over-time'), what)                                   # the warning is where the figures are, and not only under the badges
            self.assertNotIn(silent, section_text(html, 'alerts'), what)
            self.assertIn(ms['badge_note'], section_text(html, 'badges'), what)
            self.assertIn('0 things to look at first', section_text(html, 'summary'), what)          # Vibe Check ran, so this is a true 0 (not 'Not checked')
            # MEASURES.md: the marks are neutral when almost no tone was found. Nothing in a silent chat is good news: the green None of Escalation (60 messages are
            # enough to judge it), Rare for Conflict and sarcasm, and the green marks that go with them would say the chat is calm when the model read nothing.
            self.assertEqual(cards_on_page(html), [(icon_svg('info'), c['level'], 'Neutral') for c in ms['cards']], what)
            self.assertEqual(health_mark_on_page(html), icon_svg('info'), what)
        self.assertEqual([(c['key'], c['level'], c['tone'], c['tone_word']) for c in ms['cards']],
                         [('escalation', 'None', 'info', 'Neutral'), ('conflict', 'Rare', 'info', 'Neutral'), ('empathy', 'Rare', 'info', 'Neutral'), ('passive', 'Rare', 'info', 'Neutral')])
        self.assertEqual((ms['health']['status'], ms['health']['tone'], ms['health']['tone_word']), ('Fair', 'info', 'Neutral'))
        pdf = squeeze(measures_pdf(self.pdf(aid)))
        for line in ('Escalation: None (Neutral)', 'Conflict: Rare (Neutral)', 'Empathy: Rare (Neutral)', 'Passive-aggressive: Rare (Neutral)', 'Health score: Fair (Neutral)'):
            self.assertIn(squeeze(line), pdf)
        marks = [r[-1] for r in rows_under(csv_blocks(self.csv(aid))['Measures over time'], 'Measure')]
        self.assertEqual(marks, ['Neutral'] * 4)
        self.assert_everywhere(aid)

    def test_a_silent_chat_is_no_all_clear_whatever_its_size_and_the_note_about_it_needs_30_messages(self):
        """MEASURES.md: ms['all_clear'] counts silence at any number of messages (19 in 20 rated all 0 is silence); the note about it needs at least 30 messages."""
        for n in (12, 29, 30, 60):
            aid = self.scenario(f'silent {n}', lambda n=n: self.checked([NEUTRAL] * n, at(n, days_ago(40))))
            ms = self.ms(aid)
            self.assertEqual((ms['m']['n'], ms['all_clear'], ms['alerts'], ms['alert_counts']), (n, False, [], {'high': 0, 'medium': 0, 'good': 0}), n)
            self.assertEqual('silent' in [note['key'] for note in ms['notes']], n >= 30, n)                  # the note needs 30 messages; the refusal to say all clear does not
            self.assertTrue(ms['badge_note'], n)                                                            # below 30 messages: badges have not started; from 30: none was given, and why
            for what in ('results', 'report'):
                alerts = section_text(self.page(aid, what), 'alerts')
                self.assertNotIn('Nothing needs urgent attention', alerts, f'{what} {n}')
                self.assertIn(norm(ms['caveats']['empty']), alerts, f'{what} {n}')                          # the empty list is said not to be an all clear
            self.assertNotIn(squeeze('Nothing needs urgent attention'), squeeze(' '.join(self.pdf(aid))), n)
            self.assertFalse([c for c in measure_cells(self.csv(aid)) if 'Nothing needs urgent attention' in c], n)
            self.assertIn(['No alerts', ms['caveats']['empty']], self.csv(aid), n)
            self.assert_everywhere(aid, f'{n} silent messages')

    def test_the_sentence_for_an_all_clear_is_on_the_pages_only_for_a_check_that_may_say_it(self):
        calm = self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))
        flare = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        silent = self.scenario('silent 60', lambda: self.checked([NEUTRAL] * 60, at(60, days_ago(40))))
        said = 'Nothing needs urgent attention in these {} messages.'
        for aid, may in ((calm, True), (flare, False), (silent, False)):
            ms = self.ms(aid)
            self.assertEqual(ms['all_clear'], may)
            for what in ('results', 'report'):
                self.assertEqual(said.format(ms['m']['n']) in section_text(self.page(aid, what), 'alerts'), may, (what, ms['m']['n']))
            if not may:                                                                                     # the PDF and the CSV never say it for a check that may not
                self.assertNotIn(squeeze('Nothing needs urgent attention'), squeeze(' '.join(self.pdf(aid))))
                self.assertFalse([c for c in measure_cells(self.csv(aid)) if 'Nothing needs urgent attention' in c])
        self.assertEqual(self.ms(calm)['alert_counts'], {'high': 0, 'medium': 0, 'good': 0})

    def test_good_news_alone_still_lets_the_page_say_nothing_needs_urgent_attention(self):
        aid = self.new_analysis()
        self.check(aid, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(90))))
        self.new_check(aid)
        self.check(aid, chat_csv(messages(48), at(48, days_ago(60))), 'two.csv')
        ms = self.ms(aid)
        self.assertEqual({a['level'] for a in ms['alerts']}, {'good'})
        self.assertTrue(ms['all_clear'])
        for what in ('results', 'report'):
            alerts = section_text(self.page(aid, what), 'alerts')
            self.assertIn('Nothing needs urgent attention in these 48 messages.', alerts)
            self.assertIn('Good news', alerts)
            self.assertIn('Hostility is lower than at your last check', alerts)
        self.assert_everywhere(aid)

    def test_a_first_check_says_so_under_since_your_last_check_on_the_page_the_pdf_and_the_csv(self):
        aid = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        ms = self.ms(aid)
        self.assertEqual((ms['texts']['first_check'], ms['since']), (self.FIRST, None))
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertIn(self.FIRST, Page(section(html, 'over-time')).visible_outside_folds, what)
            self.assertEqual(section_text(html, 'over-time').count(self.FIRST), 1, what)
        self.assertIn(squeeze('Since your last check ' + self.FIRST), squeeze(lines_pdf(self.pdf(aid))))
        block = csv_blocks(self.csv(aid))['Since your last check']
        self.assertEqual(block, [['Since your last check'], ['How to read this', ms['caveats']['since']], ['First check', self.FIRST]])

    def test_no_badge_says_so_on_the_page_the_pdf_and_the_csv_and_only_then(self):
        threats = self.scenario('threats', lambda: self.checked([THREAT if i == 10 else HARASSMENT if i == 30 else t for i, t in enumerate(messages(48))], at(48, days_ago(40))))
        flare = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        ms = self.ms(threats)
        self.assertEqual((ms['badges_earned'], ms['texts']['no_badges']), (0, self.NO_BADGES))
        for what in ('results', 'report'):
            self.assertEqual(section_text(self.page(threats, what), 'badges').count(self.NO_BADGES), 1, what)
            self.assertNotIn(self.NO_BADGES, section_text(self.page(flare, what), 'badges'), what)
        self.assertEqual(squeeze(alerts_pdf(self.pdf(threats))).count(squeeze(self.NO_BADGES)), 1)
        self.assertNotIn(squeeze(self.NO_BADGES), squeeze(measures_pdf(self.pdf(flare))))
        self.assertIn(['No badges', self.NO_BADGES], csv_blocks(self.csv(threats))['Badges'])
        self.assertNotIn(['No badges', self.NO_BADGES], csv_blocks(self.csv(flare))['Badges'])

    def test_the_caveat_of_the_comparison_is_shown_from_the_second_check_on_every_surface(self):
        aid = self.new_analysis()
        self.check(aid, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(90))))
        self.new_check(aid)
        self.check(aid, chat_csv(messages(48), at(48, days_ago(60))), 'two.csv')
        ms = self.ms(aid)
        caveat = ms['caveats']['since']
        self.assert_says(caveat, 'the two checks were made alike', 'bigger than chance', 'wording, not people')
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertEqual(Page(section(html, 'over-time')).visible_outside_folds.count(caveat), 1, what)
            self.assertNotIn(self.FIRST, section_text(html, 'over-time'), what)
            self.assertNotIn(caveat, ' '.join(re.findall(r'<details.*?</details>', section(html, 'over-time'), re.S)), what)         # never inside a fold
        self.assertEqual(squeeze(lines_pdf(self.pdf(aid))).count(squeeze(caveat)), 1)
        self.assertEqual(csv_blocks(self.csv(aid))['Since your last check'][1], ['How to read this', caveat])
        self.assertNotIn(self.FIRST, ' '.join(measure_cells(self.csv(aid))))
        self.assertNotIn(squeeze(self.FIRST), squeeze(measures_pdf(self.pdf(aid))))

    def test_the_marks_of_the_cards_are_said_in_words_in_the_pdf(self):
        """The PDF cannot draw the icon, so each card and the health tile say their mark in words, from tone_word, in brackets after the level. (The CSV does it in a
        column of its own: see the next test.)"""
        aid = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        text = squeeze(measures_pdf(self.pdf(aid)))
        for line in ('Escalation: Steep (Look at this first)', 'Conflict: Some (Worth a look)', 'Empathy: Often (A good sign)', 'Passive-aggressive: Rare (A good sign)',
                     'Health score: Fair (Neutral)'):
            self.assertIn(squeeze(line), text)

    def test_the_marks_of_the_cards_are_said_in_words_in_the_csv_too(self):
        """MEASURES.md: a surface that cannot show the icon (the PDF and the CSV) says the mark in words, from tone_word on every card and on the health tile. In the
        CSV the last column of the four cards is Mark, and the Health score row ends with its mark. A level word says how often, and the mark says whether that is good:
        Some is Worth a look for Conflict, and Often is A good sign for Empathy."""
        aid = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        ms = self.ms(aid)
        block = csv_blocks(self.csv(aid))['Measures over time']
        self.assertEqual(next(row for row in block if row[:1] == ['Measure'])[-1], MARK)
        cards = rows_under(block, 'Measure')
        self.assertEqual([(row[0], row[1], row[-1]) for row in cards],
                         [('Escalation', 'Steep', 'Look at this first'), ('Conflict', 'Some', 'Worth a look'), ('Empathy', 'Often', 'A good sign'), ('Passive-aggressive', 'Rare', 'A good sign')])
        self.assertEqual([row[-1] for row in cards], [card['tone_word'] for card in ms['cards']])
        self.assertLessEqual({row[-1] for row in cards}, set(measures.TONE_WORDS.values()))        # always one of the four phrases of measures.py
        self.assertEqual([row for row in block if row[:1] == ['Health score'] and len(row) == 4], [['Health score', '55', 'Fair', ms['health']['tone_word']]])
        self.assertEqual(ms['health']['tone_word'], 'Neutral')


# =============================================================================================================================================
class Privacy(MeasuresCase):
    """What is kept after Delete evidence, and where a time of day may not appear."""

    CANARY = 'ZQMEASCANARY'

    def test_a_canary_in_the_messages_and_a_file_name_never_reaches_the_kept_records(self):
        canary = self.CANARY
        texts = [f'{canary} {t}' for t in messages(48, hot=(16, 23))]
        aid = self.new_analysis()
        with stock_sqlite():
            self.upload(aid, (f'{canary}-export.csv', chat_csv(texts, at(48, days_ago(40)), sources=[f'{canary}-room-{i % 2}' for i in range(48)]), 'text/csv'))
            self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
            self.assertIn(canary.encode(), database_bytes(self.tmp), 'the messages and the names are stored while they are needed')
            with self.store.db() as c:
                raw = [r['snapshot_json'] for r in c.execute('SELECT snapshot_json FROM measure_history WHERE analysis_id=?', (aid,))]
            self.assertEqual(len(raw), 1)
            self.assertNotIn(canary, raw[0])
            self.assertEqual(self.client.post(f'/analysis/{aid}/clear-units').status_code, 303)
            self.assertNotIn(canary.encode(), database_bytes(self.tmp), 'Delete evidence left a message or a name on disk')
        with self.store.db() as c:
            kept = [json.loads(r['snapshot_json']) for r in c.execute('SELECT snapshot_json FROM measure_history WHERE analysis_id=?', (aid,))]
        self.assertEqual(len(kept), 1, 'the record of the check stays')
        words = {'created_at', 'basis', 'sensitivity', 'model', 'model_key', 'period_start', 'period_end', 'sig'}      # the names of settings, a short fingerprint of the model name, and the date: the only text
        numbers = {'gen', 'n', 'total', 'sampled', 'stretches', 'hostile_n', 'care_n', 'dig_n', 'heated_n', 'episodes', 'health', 'health_sd', 'peak'}
        self.assertLessEqual(set(kept[0]), words | numbers, 'a new field in the kept record needs a look: only dates, settings and numbers may be kept')
        self.assertLessEqual({'created_at', 'n', 'health', 'hostile_n', 'care_n', 'dig_n', 'heated_n', 'episodes', 'peak', 'gen'}, set(kept[0]))
        for key, value in kept[0].items():
            self.assertIsInstance(value, str if key in words else (int, float), key)
            self.assertNotIsInstance(value, bool, key)
        self.assertEqual((kept[0]['n'], kept[0]['hostile_n'], kept[0]['heated_n'], kept[0]['episodes']), (48, 5, 7, 1))
        self.assertRegex(kept[0]['model_key'], r'^[0-9a-f]{12}$')                                   # a fingerprint of the model name (so two names the store cannot keep are told apart) ...
        self.assertNotEqual(kept[0]['model_key'], kept[0]['model'])                                  # ... and not the name
        self.assertEqual(self.store.measure_history(aid), kept)
        self.assertEqual(self.client.get(f'/analysis/{aid}/report').status_code, 303)
        self.assertEqual(self.client.get(f'/analysis/{aid}/workspace').status_code, 200)
        self.assertNotIn(canary, self.page(aid, 'workspace'))
        # the next check carries on from the kept record
        self.check(aid, chat_csv(messages(48), at(48, days_ago(20))), 'next.csv')
        self.assertEqual(len(self.store.measure_history(aid)), 2)
        self.assertTrue(self.ms(aid)['since']['comparable'])

    def clock_free(self, aid, where):
        """No hour and minute anywhere in the new sections of both pages, the new pages of the PDF and the new blocks of the CSV."""
        for what in ('results', 'report'):
            self.assertEqual(CLOCK.findall(measures_markup(self.page(aid, what))), [], f'{where} {what}')
        self.assertEqual(CLOCK.findall(measures_pdf(self.pdf(aid))), [], f'{where} pdf')
        self.assertEqual([c for c in measure_cells(self.csv(aid)) if CLOCK.search(c)], [], f'{where} csv')

    def test_incognito_on_leaves_no_time_of_day_in_the_page_the_pdf_or_the_csv(self):
        start = days_ago(40)
        aid = self.new_analysis()
        self.incognito_check(aid, chat_csv(messages(48, hot=(16, 23)), at(48, start)))
        ms = self.ms(aid)
        self.assertTrue(ms['m']['incognito'])
        self.assertEqual((ms['m']['basis'], len(ms['m']['stretches']), [a['key'] for a in ms['alerts']]), ('time', 6, ['heated']))
        self.assertTrue(all(not CLOCK.search(s['label'] + s['axis']) for s in ms['m']['stretches']))
        self.assertTrue(all(re.fullmatch(r'Part \d of 6: [A-Z][a-z]{2} \d{1,2}( to [A-Z][a-z]{2} \d{1,2})?', s['label']) for s in ms['m']['stretches']), ms['m']['stretches'])
        self.clock_free(aid, 'incognito')
        self.assert_everywhere(aid, 'incognito')                                                   # the numbers are the same: only the labels lost their times
        self.assertEqual(ms['cards'][0]['headline'], '1 heated moment, about a third of the way in.')
        # MEASURES.md: while Incognito is on, even the raw start and end of each part in the analysis are days, never times of day
        self.assertTrue(all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', s['start']) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', s['end']) for s in ms['m']['stretches']), ms['m']['stretches'])
        plain = self.checked(messages(48, hot=(16, 23)), at(48, start))                              # the control: the same chat with Incognito off keeps the time in the raw dates
        self.assertTrue(all(re.search(r'T\d{2}:\d{2}', s['start']) and re.search(r'T\d{2}:\d{2}', s['end']) for s in self.ms(plain)['m']['stretches']))
        self.assertEqual([s['start'][:10] for s in self.ms(plain)['m']['stretches']], [s['start'] for s in ms['m']['stretches']])

    def test_incognito_on_for_three_checks_keeps_the_check_by_check_chart_which_names_a_check_by_its_number_and_its_day_only(self):
        """MEASURES.md: a check is named by its number and its date, never by the time it ran, so the chart and its table are the same under Incognito. The chart
        used to be left out under Incognito, because its tooltips named the minute of two checks made on one day."""
        aid = self.new_analysis()
        for k, hot in enumerate(((16, 28), (0, 0), (30, 42))):                                     # three checks made today, one after the other: the same day
            if k:
                self.new_check(aid)
            self.incognito_check(aid, chat_csv(messages(48, hot=hot), at(48, days_ago(90 - 30 * k))), f'c{k}.csv')
        ms = self.ms(aid)
        self.assertEqual(len(ms['trail']), 3)
        self.assertTrue(ms['m']['incognito'])
        self.clock_free(aid, 'three checks')
        names = [f'Check {i} · {long_date(r["created_at"])}' for i, r in enumerate(ms['trail'], 1)]
        trail_figure = lambda over: next(f for f in re.findall(r'<figure class="vc-fig">.*?</figure>', over, re.S) if 'Health score, check by check' in f)       # noqa: E731
        kept = {}
        for what in ('results', 'report'):
            html = self.page(aid, what)
            over = section(html, 'over-time')
            self.assertIn('Check by check', section_text(html, 'over-time'))
            self.assertIn('View the checks as a table', over)
            tips = Page(over).tips['Health score, check by check']
            self.assertEqual([tip['t'] for tip in tips], names * 2, 'every check by its number and its day, in the wide drawing and in the phone one')
            self.assertFalse([tip['t'] for tip in tips if CLOCK.search(tip['t']) or 'UTC' in tip['t']])
            table = next(t for t in section_tables(html, 'over-time') if t[0][:2] == ['Check', 'Date'])
            self.assertEqual(len(table) - 1, 3)
            labels = [label for label in re.findall(r'<text[^>]*>([^<]*)</text>', trail_figure(over)) if label.startswith('Check')]
            self.assertEqual(labels, ['Check 1', 'Check 2', 'Check 3'] * 2, what)                    # under the points: the number of each check, in both layouts, and no time of day
            self.assertEqual([row[1] for row in table[1:]], [long_date(r['created_at']) for r in ms['trail']], what)           # the table names the day of a check, and no time
            kept[what] = (same_markup(trail_figure(over)), table)
        text = measures_pdf(self.pdf(aid))
        self.assertIn('Health score, check by check', text)
        self.assertIn(squeeze('Check 1 Check 2 Check 3'), squeeze(text))                              # the PDF draws the same chart, labelled by number
        trail = rows_under(csv_blocks(self.csv(aid))['Since your last check'], 'Check')
        self.assertEqual([row[1] for row in trail], [r['created_at'][:10] for r in ms['trail']])      # and its table in the CSV names the day only
        # the same three checks with Incognito off: the same chart and the same table
        self.store.update_analysis(aid, incognito=0)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            over = section(html, 'over-time')
            self.assertEqual(same_markup(trail_figure(over)), kept[what][0], what)
            self.assertEqual(next(t for t in section_tables(html, 'over-time') if t[0][:2] == ['Check', 'Date']), kept[what][1], what)
            self.assertEqual([tip['t'] for tip in Page(over).tips['Health score, check by check']], names * 2)

    def test_with_incognito_off_a_span_of_more_than_three_days_still_has_no_times(self):
        aid = self.checked(messages(48), at(48, days_ago(40), step=240))                          # 48 messages four hours apart: eight days
        ms = self.ms(aid)
        self.assertFalse(ms['m']['incognito'])
        self.assertTrue(all(MONTH_DAY.fullmatch(s['axis']) or s['axis'] == '' for s in ms['m']['stretches']), [s['axis'] for s in ms['m']['stretches']])
        self.clock_free(aid, 'eight days')


# =============================================================================================================================================
class Dates(MeasuresCase):
    """Chats with no dates, some dates and strange dates."""

    def test_a_chat_without_dates_follows_the_order_and_makes_no_date_claim(self):
        aid = self.checked(messages(48, hot=(16, 23)), None)
        ms = self.ms(aid)
        m = ms['m']
        self.assertEqual((m['basis'], m['timeline_n'], m['left_off'], m['has_series'], m['verdicts']), ('order', 48, 0, True, True))
        self.assertEqual([n['key'] for n in ms['notes']], ['order'])
        self.assertEqual([s['axis'] for s in m['stretches']], ['#1', '#9', '#17', '#25', '#33', '#41'])
        self.assertEqual([s['label'] for s in m['stretches']], [f'Part {i + 1} of 6: rated messages {8 * i + 1} to {8 * i + 8}' for i in range(6)])
        (alert,) = ms['alerts']
        self.assertEqual((alert['key'], alert['when']), ('heated', 'about a third of the way in'))      # where in the check, and no day: there are none
        self.assert_everywhere(aid)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            mine = measures_markup(html)
            self.assert_says(section_text(html, 'over-time'), 'no usable dates', 'the order you added them')                   # the note about the lines sits with the lines
            self.assertNotIn('no usable dates', section_text(html, 'alerts'), what)
            self.assertEqual(MONTH_DAY.findall(mine), [], what)                                    # no day of any month in the new sections
            self.assertEqual(CLOCK.findall(mine), [], what)
            self.assertIn('>#1</text>', mine)
            self.assertIn('>#41</text>', mine)
            tips = [tip['t'] for tip in Page(section(html, 'over-time')).tips['Conflict over time']]
            self.assertEqual(sorted(set(tips)), sorted(s['label'] for s in m['stretches']))
            self.assertNotIn('Where in the chat: about a third of the way in (', html)
        text = measures_pdf(self.pdf(aid))
        self.assertEqual(MONTH_DAY.findall(text) + CLOCK.findall(text), [])
        self.assertIn(squeeze('rated messages 17 to 24'), squeeze(' '.join(measure_cells(self.csv(aid)))))
        self.assertEqual([c for c in measure_cells(self.csv(aid)) if MONTH_DAY.search(c) or CLOCK.search(c)], [])

    def test_a_chat_with_thirty_percent_undated_says_so_and_still_counts_every_message(self):
        stamps = at(100, days_ago(40), step=20)
        stamps = ['' if i % 10 in (1, 4, 8) else s for i, s in enumerate(stamps)]
        aid = self.checked(messages(100, hot=(60, 66)), stamps)
        ms = self.assert_everywhere(aid)
        m = ms['m']
        self.assertEqual((m['basis'], m['n'], m['timeline_n'], m['left_off'], m['overall']['n']), ('time', 100, 70, 30, 100))
        self.assertEqual([n['key'] for n in ms['notes']], ['undated'])
        note = ms['notes'][0]['text']
        self.assert_says(note, 'Only 70 of 100 messages have a date', 'still count all 100')
        for what in ('results', 'report'):
            self.assertIn(note, section_text(self.page(aid, what), 'over-time'))
            self.assertNotIn(note, section_text(self.page(aid, what), 'alerts'))
        self.assertIn(squeeze(note), squeeze(measures_pdf(self.pdf(aid))))
        self.assertIn(['Note', note], self.csv(aid))
        self.assertTrue(all(MONTH_DAY.search(s['label']) for s in m['stretches']))                 # the 70 are named by their dates
        self.assertEqual(sum(s['n'] for s in m['stretches']), 70)
        self.assertEqual(m['overall']['n'], 100)                                                    # every message counts in the cards, dated or not
        self.assertTrue(ms['cards'][1]['headline'].startswith(f'{m["overall"]["hostile"]} of 100 messages' if m['overall']['hostile'] else 'None of the 100 messages'))

    def test_too_few_dated_messages_for_lines_say_so_with_both_counts_and_draw_no_line(self):
        """30 rated messages of which 20 carry a date: over 3 in 5, so the lines follow the clock, but 20 are fewer than the 24 that lines need. The page used to say
        only that a direction needs 48, as though lines were drawn."""
        stamps = at(30, days_ago(40))
        aid = self.checked(messages(30), [s if i < 20 else '' for i, s in enumerate(stamps)])
        ms = self.ms(aid)
        m = ms['m']
        self.assertEqual((m['n'], m['timeline_n'], m['basis'], m['left_off'], m['has_series'], m['verdicts']), (30, 20, 'time', 10, False, False))
        self.assertEqual([(n['key'], n['about']) for n in ms['notes']], [('few_dated', 'measures')])
        self.assert_says(ms['notes'][0]['text'], 'Only 20 of 30 messages have a date', 'no lines over time', 'at least 24 messages with a date')
        self.assertEqual((ms['cards'][0]['level'], ms['cards'][0]['tone']), ('Too early', 'info'))
        self.assert_says(ms['cards'][0]['headline'], 'at least 24 messages')
        for what in ('results', 'report'):
            html = self.page(aid, what)
            over = section_text(html, 'over-time')
            self.assertEqual(over.count('no lines over time yet'), 1, what)                         # said once, by the note that gives both counts
            self.assertNotIn('There are no lines over time yet.', over, what)                        # and not again as a sentence of its own
            self.assertEqual(quiet_notes(html, 'over-time'), [ms['notes'][0]['text']], what)
            self.assertNotIn('The lines show what happened', over, what)                            # the note about lines that are not drawn would mislead
            self.assertEqual(Page(section(html, 'over-time')).titles.count('Conflict over time'), 0, what)
        self.assert_everywhere(aid)

    def test_strange_dates_never_break_a_page_the_pdf_or_the_csv(self):
        odd = ['0001-01-01T00:00:00', '9999-12-31T23:59:59', '1970-01-01T00:00:00', 'not a date at all', '0001-01-01T00:00:00+05:00', '9999-12-31T23:59:59-05:00',
               '2026-13-45', '31/12/1999 23:59', 'yesterday', '2099-01-01T00:00:00', '']
        stamps = at(48, days_ago(40))
        for k, value in enumerate(odd[:9]):
            stamps[3 * k + 1] = value                                                              # nine of the 48 are odd: 39 are usable
        some = self.checked(messages(48), stamps)
        ms = self.ms(some)
        self.assertEqual((ms['m']['basis'], ms['m']['n'], ms['m']['timeline_n'], ms['m']['left_off']), ('time', 48, 39, 9))
        self.assertEqual({n['key'] for n in ms['notes']}, {'no_verdict', 'undated'})
        self.surfaces_render(some)
        self.assert_everywhere(some)
        for what in ('results', 'report'):
            mine = measures_markup(self.page(some, what))
            for year in ('0001', '9999', '1970', '2099', 'Jan 1, 1', 'Dec 31'):
                self.assertNotIn(year, mine, what)                                                 # nothing was drawn at a date that cannot be right
        everything = self.checked(messages(48, hot=(16, 23)), [odd[i % len(odd)] for i in range(48)])
        ms = self.ms(everything)
        self.assertEqual((ms['m']['basis'], ms['m']['timeline_n'], [n['key'] for n in ms['notes']]), ('order', 48, ['order']))
        self.surfaces_render(everything)
        self.assert_everywhere(everything, 'all odd')

    def test_times_are_called_utc_only_when_every_timestamp_carries_an_offset(self):
        start = days_ago(40)
        zulu = [s + 'Z' for s in at(48, start)]
        aid = self.checked(messages(48), zulu)
        labels = [s['label'] for s in self.ms(aid)['m']['stretches']]
        self.assertTrue(all(label.endswith(' UTC') for label in labels), labels)
        table = next(t for t in section_tables(self.page(aid, 'results'), 'over-time') if t[0][:2] == ['Part of the chat', 'Messages'])
        self.assertTrue(all(row[0].endswith(' UTC') for row in table[1:]))
        mixed = zulu[:]
        mixed[5] = at(48, start)[5]                                                                # one stamp without an offset
        other = self.checked(messages(48), mixed)
        self.assertFalse(any('UTC' in s['label'] for s in self.ms(other)['m']['stretches']))
        self.assertNotIn(' UTC', measures_markup(self.page(other, 'results')))


# =============================================================================================================================================
class Robustness(MeasuresCase):
    """Many messages, missing ratings, a changed review period and a bad saved record."""

    def test_5000_messages_render_the_page_in_under_two_seconds_and_build_the_pdf(self):
        self.page(self.checked(messages(24), at(24, days_ago(40))))                                  # the first page of a process compiles its templates: not what is timed here
        aid = self.new_analysis()
        n, start = 5000, days_ago(60)
        self.store.add_units(aid, [{'text': f'message number {i}', 'source': 'general', 'timestamp': (start + datetime.timedelta(minutes=7 * i)).isoformat()} for i in range(n)], 'UPLOAD')
        tones = self.app.analysis.ATTRIBUTES
        rows = []
        for k, unit in enumerate(self.store.units_for(aid)):
            given = {'Rude': 2, 'Disrespectful': 2} if 2000 <= k < 2300 and k % 3 else {'Polite': 3} if k % 5 == 0 else {'Empathetic': 2} if k % 9 == 0 else {}
            rows += [(unit['id'], tone, given.get(tone, 0), '') for tone in tones]
        with self.store.db() as c:
            c.executemany('INSERT INTO unit_ratings(unit_id,attribute,rating,rationale) VALUES(?,?,?,?)', rows)
        self.assertEqual(self.app.analysis.calculate_metrics(aid), n)
        self.store.update_analysis(aid, rated_units_total=n, model_used='qwen2.5:7b', status='analyzed')
        began = time.perf_counter()
        response = self.client.get(f'/analysis/{aid}/workspace')
        took = time.perf_counter() - began
        self.assertEqual(response.status_code, 200)
        self.assertLess(took, 2.0, f'the workplace page took {took:.2f} seconds for {n:,} messages')
        ms = report.build(aid)['measures']
        self.assertEqual((ms['m']['n'], len(ms['m']['stretches']), ms['m']['verdicts']), (n, 24, True))
        self.assertEqual(len(ms['m']['episodes']), 1)                                              # the 300 messages with a burst of insults
        began = time.perf_counter()
        results = self.client.get(f'/analysis/{aid}/report')                                       # the whole of the results on one page
        self.assertEqual(results.status_code, 200)
        self.assertLess(time.perf_counter() - began, 2.5, 'the results page took too long')
        self.assert_page_agrees(ms, results.text, 'results of 5,000')
        began = time.perf_counter()
        pdf = self.client.get(f'/analysis/{aid}/report.pdf')
        self.assertEqual((pdf.status_code, pdf.content[:5]), (200, b'%PDF-'))
        self.assertLess(time.perf_counter() - began, 20)
        self.assert_pdf_agrees(ms, pdf_pages(pdf.content), 'pdf of 5,000')
        self.assert_csv_agrees(ms, self.csv(aid), 'csv of 5,000')

    def test_one_missing_rating_row_leaves_the_pages_working_and_says_one_message_is_left_out(self):
        aid = self.checked(messages(60, hot=(20, 29)), at(60, days_ago(40)))
        self.assertEqual(self.ms(aid)['m']['n'], 60)
        victim = self.store.rated_units(aid)[7]['id']
        with self.store.db() as c:
            self.assertEqual(c.execute("DELETE FROM unit_ratings WHERE unit_id=? AND attribute='Rude'", (victim,)).rowcount, 1)
        ms = self.ms(aid)
        self.assertEqual((ms['m']['n'], ms['m']['incomplete']), (59, 1))
        (note,) = [n['text'] for n in ms['notes'] if n['key'] == 'incomplete']
        self.assert_says(note, '1 message', 'incomplete ratings', 'left out')
        self.surfaces_render(aid)
        self.assert_everywhere(aid)
        for what in ('results', 'report'):
            self.assertIn(note, section_text(self.page(aid, what), 'over-time'))
        self.assertIn(squeeze(note), squeeze(measures_pdf(self.pdf(aid))))
        self.assertIn(['Note', note], self.csv(aid))
        with self.store.db() as c:                                                                  # a message with no rating row at all is simply not a rated message
            c.execute('DELETE FROM unit_ratings WHERE unit_id=?', (self.store.rated_units(aid)[0]['id'],))
        again = self.ms(aid)
        self.assertEqual((again['m']['n'], again['m']['incomplete']), (58, 1))
        self.surfaces_render(aid)
        self.assert_everywhere(aid, 'two messages out')

    def test_changing_the_review_period_after_rating_does_not_change_the_measures(self):
        start = days_ago(40)
        aid = self.checked(messages(48, hot=(16, 23)), at(48, start))
        before = self.ms(aid)
        html = {what: self.page(aid, what) for what in ('results', 'report')}
        day = start.date().isoformat()                                                              # a period that keeps only the first 12 of the messages
        response = self.client.post(f'/analysis/{aid}/check/4', data={'period_start': day, 'period_end': day})
        self.assertEqual(response.status_code, 303)
        saved = self.store.get_analysis(aid)
        self.assertEqual((saved['period_start'], saved['period_end']), (day, day))
        ctx = report.build(aid)
        self.assertEqual((ctx['period']['kept'], ctx['period']['outside']), (12, 36))              # the period really does leave most of the chat out
        after = ctx['measures']
        for key in ('m', 'cards', 'health', 'alerts', 'alert_counts', 'badges', 'badges_earned', 'basis_text', 'since', 'caveats'):
            self.assertEqual(after[key], before[key], key)
        # the one thing that moves is a quiet note, said once in the section about the measures: the period now leaves out rated messages
        self.assertEqual([n for n in after['notes'] if n['key'] != 'period'], before['notes'])
        period_note = {'key': 'period', 'text': 'The review period now leaves out 36 messages that were rated. Run the check again to apply it.', 'about': 'measures'}
        self.assertEqual([n for n in after['notes'] if n['key'] == 'period'], [period_note])
        self.assertEqual([r['hostile_n'] for r in after['trail']], [r['hostile_n'] for r in before['trail']])
        self.assertEqual((before['record']['period_start'], after['record']['period_start']), ('', day))   # only the settings the record names changed
        for what in ('results', 'report'):
            now = self.page(aid, what)
            for sid in SECTIONS:
                if sid == 'over-time':                                                                # the same section, with the one note more
                    self.assertEqual(quiet_notes(now, sid), quiet_notes(html[what], sid) + [period_note['text']], what)
                    without = re.sub(r'<li>The review period now leaves out.*?</li>', '', section(now, sid)).replace('<ul class="vc-quiet"></ul>', '')
                    self.assertEqual(same_markup(without), same_markup(section(html[what], sid)), f'{what}: {sid}')
                    continue
                self.assertEqual(same_markup(section(now, sid)), same_markup(section(html[what], sid)), f'{what}: {sid}')
        self.assert_everywhere(aid, 'after the period changed')

    def test_a_poisoned_saved_record_is_dropped_or_shown_as_text_and_never_as_markup(self):
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        good = {'created_at': '2026-05-01T10:00:00', 'n': 48, 'total': 48, 'sampled': 0, 'basis': 'time', 'stretches': 6, 'model': 'qwen2.5:7b', 'sensitivity': 'medium',
                'health': 60.0, 'health_sd': 10.0, 'hostile_n': 1, 'care_n': 3, 'dig_n': 0, 'heated_n': 1, 'episodes': 0, 'peak': 0.0, 'sig': '48:1:1:1:1'}
        poisoned = [dict(good, gen=gen + 1, created_at=HOSTILE),                                   # no readable date: the whole record goes
                    dict(good, gen=gen + 2, model=HOSTILE, model_key=HOSTILE, sensitivity=HOSTILE, sig=HOSTILE, basis=HOSTILE, period_start=HOSTILE, period_end=HOSTILE,
                         extra='<script>alert(2)</script>', note='<b>x</b>', hostile_n='<script>alert(3)</script>'),
                    dict(good, gen=gen + 3, model='=HYPERLINK("http://evil.example","x")'),
                    dict(good, gen=gen + 4, model='@cmd|calc', care_n=-4, heated_n=10 ** 30, peak='<img src=x onerror=alert(4)>')]
        with self.store.db() as c:
            for row in poisoned:
                c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, row['created_at'], json.dumps(row), row['gen']))
            c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, 'x', '{this is not json', gen + 5))
            c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, 'x', json.dumps([HOSTILE]), gen + 6))
        kept = self.store.measure_history(aid)
        self.assertEqual([r['gen'] for r in kept], [gen, gen + 2, gen + 3, gen + 4], 'the records with no date, no JSON or no object are dropped')
        allowed = set(self.store.MEASURE_FIELDS)
        for record in kept:
            self.assertLessEqual(set(record), allowed)
            for value in record.values():
                self.assertNotRegex(str(value), r'[<>"=@]')
        self.assertEqual(kept[1].get('model'), None)
        self.assertNotIn('model_key', kept[1])                                                     # a fingerprint is twelve hex digits and nothing else
        self.assertEqual(kept[2].get('model'), None)
        self.assertNotIn('care_n', kept[3])
        self.assertNotIn('heated_n', kept[3])
        self.surfaces_render(aid)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            for needle in ('<script>alert', '<img src=x', 'onerror', 'HYPERLINK', '@cmd', 'evil.example'):
                self.assertNotIn(needle, html, f'{what}: {needle}')
            self.assertEqual(Page(html).events, [], what)
        for needle in ('script', 'onerror', 'HYPERLINK', '@cmd', 'evil.example', '<'):
            self.assertNotIn(needle, ' '.join(measure_cells(self.csv(aid))), needle)
        cells = measure_cells(self.csv(aid))
        self.assertEqual([c for c in cells if c[:1] in ('=', '+', '-', '@', '\t', '\r') and not re.fullmatch(r'-?\d+(\.\d+)?', c)], [], 'a cell would run as a formula')
        text = ' '.join(self.pdf(aid))
        self.assertNotIn('<script', text)
        self.assertNotIn('HYPERLINK', text)
        self.assert_everywhere(aid, 'with poisoned records')

    def test_a_year_of_weekly_checks_still_renders_everywhere(self):
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        live = self.ms(aid)['record']                                                               # the 52 earlier checks were made with this model and this sensitivity
        with self.store.db() as c:
            for k in range(52):
                row = {'created_at': f'2025-{1 + k // 5:02d}-{1 + (k % 5) * 5:02d}T10:00:00', 'gen': gen + 1 + k, 'n': 48, 'total': 48, 'sampled': 0, 'basis': 'time', 'stretches': 6,
                       'model': live['model'], 'model_key': live['model_key'], 'sensitivity': live['sensitivity'], 'health': 45.0 + k % 20, 'health_sd': 10.0, 'hostile_n': k % 5,
                       'care_n': 3 + k % 4, 'dig_n': 0, 'heated_n': k % 7, 'episodes': k % 2, 'peak': 0.0, 'sig': f'48:{k}:1:1:1'}
                c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, row['created_at'], json.dumps(row), row['gen']))
        ms = self.ms(aid)
        self.assertEqual(len(ms['trail']), 53)
        self.assertTrue(ms['since']['comparable'], ms['since']['why'])                              # the last of them is a check made like this one: the table has its Change column
        began = time.perf_counter()
        self.surfaces_render(aid)
        self.assertLess(time.perf_counter() - began, 20)
        self.assert_everywhere(aid, '53 checks')                                                    # the table behind the chart has all 53 rows on both pages and in the CSV
        self.assertEqual(len(next(t for t in section_tables(self.page(aid, 'results'), 'over-time') if t[0][:2] == ['Check', 'Date'])) - 1, 53)

    def test_a_saved_record_with_a_date_and_a_count_but_no_health_score_does_not_break_the_pages(self):
        """MEASURES.md: a record without a date, a count of messages or a health score, or with a health score off the 0 to 100 scale, is dropped as damaged. It used
        to be kept, and measures.since_table then raised a KeyError for it on every page of the analysis (the workplace page, the report, the PDF and the CSV), for
        good, because the records stay through Delete evidence. A record like that comes from a hand-edited database or an older build."""
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        base = {'created_at': '2026-05-01T10:00:00', 'n': 48, 'model': 'qwen2.5:7b', 'sensitivity': 'medium'}
        no_key = dict(base, gen=gen + 1)
        damaged = [dict(base, gen=gen + 2, health='not a number'), no_key, dict(base, gen=gen + 3, health=None), dict(base, gen=gen + 4, health=[50.0]),
                   dict(base, gen=gen + 5, health=1e300), dict(base, gen=gen + 6, health=True), dict(base, gen=gen + 7, health={'a': 1})]
        for k, off_the_scale in enumerate((-0.001, 100.001, -1, 101, 250, -1000, float('nan'), float('inf'))):       # a number, but not a score out of 100
            damaged.append(dict(base, gen=gen + 8 + k, health=off_the_scale))
        with self.store.db() as c:
            for row in damaged:
                c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, row['created_at'], json.dumps(row), row['gen']))
        self.assertEqual([r['gen'] for r in self.store.measure_history(aid)], [gen], f'not one of the {len(damaged)} comes back: they are left out as damaged')
        self.surfaces_render(aid)
        ms = self.assert_everywhere(aid, 'damaged records')
        self.assertEqual(len(ms['trail']), 1)
        self.assertIsNone(ms['since'], 'with nothing readable before it, this is a first check')
        self.assertIn(ms['texts']['first_check'], section_text(self.page(aid, 'results'), 'over-time'))
        # a check made afterwards compares with the real one, whatever damaged rows lie between
        self.new_check(aid)
        self.check(aid, chat_csv(messages(48), at(48, days_ago(10))), 'two.csv')
        ms = self.assert_everywhere(aid, 'after the next check')
        self.assertEqual(len(ms['trail']), 2)
        self.assertTrue(ms['since']['comparable'])

    def test_a_saved_record_at_either_end_of_the_health_scale_is_kept_and_compared(self):
        """The two ends of the 0 to 100 scale are real scores (an all-hostile chat scores near 0), so a record with a health score of exactly 0 or 100 is not damage:
        the store keeps it, and Since your last check reads it. The record carries the fingerprint of the model (model_key), so that it is a check made like this one."""
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        live = self.ms(aid)['record']
        base = {'created_at': '2026-05-01T10:00:00', 'n': 48, 'total': 48, 'model': live['model'], 'model_key': live['model_key'], 'sensitivity': live['sensitivity'],
                'health_sd': 10.0, 'hostile_n': 2, 'care_n': 4, 'dig_n': 0, 'heated_n': 2, 'episodes': 0, 'peak': 0.0, 'gen': gen + 1}
        for edge, verdict in ((100.0, 'Worse'), (0.0, 'Better')):
            with self.store.db() as c:
                c.execute('DELETE FROM measure_history WHERE analysis_id=? AND gen>?', (aid, gen))
                c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)',
                          (aid, base['created_at'], json.dumps(dict(base, health=edge)), base['gen']))
            self.assertEqual([r['health'] for r in self.store.measure_history(aid)][1:], [edge])
            ms = self.assert_everywhere(aid, f'a last check that scored {edge:g}')
            self.assertTrue(ms['since']['comparable'], ms['since']['why'])
            self.assertEqual((ms['since']['rows'][0]['label'], ms['since']['rows'][0]['before'], ms['since']['rows'][0]['verdict']), ('Health score', int(edge), verdict))
            table = next(t for t in section_tables(self.page(aid, 'results'), 'over-time') if t[0][:3] == ['Measure', 'Last check', 'This check'])
            self.assertEqual(table[1][:2], ['Health score', str(int(edge))])                          # a 0 is printed as a 0 and not left out as though it were empty

    def test_a_saved_record_with_a_count_above_its_total_keeps_its_other_numbers_and_the_pages_work(self):
        """MEASURES.md: a count of hostile, care, dig or heated messages that is higher than the number of messages is left out of its record. The comparison used to
        raise 'math domain error' for a count that passed its total by 2 or more."""
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        live = self.ms(aid)['record']                                                               # the saved check is made like this one: same model (and fingerprint), same sensitivity
        row = {'created_at': '2026-05-01T10:00:00', 'n': 48, 'total': 48, 'model': live['model'], 'model_key': live['model_key'], 'sensitivity': live['sensitivity'],
               'gen': gen + 1, 'health': 60.0, 'health_sd': 10.0, 'hostile_n': 500, 'care_n': 10 ** 9, 'dig_n': 2, 'heated_n': 50, 'episodes': 0, 'peak': 0.0}
        with self.store.db() as c:
            c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, row['created_at'], json.dumps(row), row['gen']))
        kept = self.store.measure_history(aid)[-1]
        self.assertEqual(kept['gen'], gen + 1)
        self.assertEqual({k: kept.get(k) for k in ('hostile_n', 'care_n', 'dig_n', 'heated_n', 'health', 'n')}, {'hostile_n': None, 'care_n': None, 'dig_n': 2, 'heated_n': None,
                                                                                                                 'health': 60.0, 'n': 48})
        self.surfaces_render(aid)
        ms = self.assert_everywhere(aid, 'a count above its total')
        self.assertTrue(ms['since']['comparable'], ms['since']['why'])                              # the two checks are alike, so a '' below is a number that is not there, not a refusal
        rows = {r['label']: r for r in ms['since']['rows']}
        self.assertEqual((rows['Health score']['before'], rows['Health score']['verdict']), (60, 'About the same'))
        self.assertEqual((rows['Conflict']['verdict'], rows['Empathy']['verdict'], rows['Heated messages']['verdict']), ('', '', ''), 'no call is made on a number that is not there')
        self.assertEqual(rows['Passive-aggressive']['before'], '2 of 48')
        self.assertEqual(rows['Passive-aggressive']['verdict'], 'About the same')
        # a count the store had to drop is said so in words ('Not known'), not as a 0 of 48 that would read as none
        self.assertEqual([rows[label]['before'] for label in ('Conflict', 'Empathy', 'Heated messages')], [NOT_KNOWN] * 3)
        self.assertEqual(rows['Conflict']['now'], f'{ms["m"]["overall"]["hostile"]} of 48')            # this check has its numbers
        for what in ('results', 'report'):
            table = next(t for t in section_tables(self.page(aid, what), 'over-time') if t[0][:3] == ['Measure', 'Last check', 'This check'])
            self.assertEqual([row[:2] for row in table[1:]], [['Health score', '60'], ['Conflict', NOT_KNOWN], ['Empathy', NOT_KNOWN], ['Passive-aggressive', '2 of 48'], ['Heated messages', NOT_KNOWN]], what)
            self.assertEqual([row[3] for row in table[1:]], ['About the same', '', '', 'About the same', ''], what)           # no verdict beside a count that is not known
        block = csv_blocks(self.csv(aid))['Since your last check']
        self.assertEqual([row[:3] for row in rows_under(block, 'Measure')],
                         [['Health score', '60', '100'], ['Conflict', NOT_KNOWN, ''], ['Empathy', NOT_KNOWN, ''], ['Passive-aggressive', '2', '48'], ['Heated messages', NOT_KNOWN, '']])
        self.assertIn(squeeze('Conflict Not known'), squeeze(lines_pdf(self.pdf(aid))))


# =============================================================================================================================================
class Sarcasm(MeasuresCase):
    """A dig is sarcasm that clearly shows, with no insult in it and no joke: the fake model rates 'oh brilliant ...' Sarcastic 2."""

    def test_the_fake_model_has_a_sarcasm_rule_and_keeps_its_old_ones(self):
        rated = {text: {k: v for k, v in fake_ollama.rate(text)[0].items() if v} for text in (
            'oh brilliant, another meeting', 'what a surprise', 'haha oh brilliant, another meeting', 'oh brilliant, you absolute idiot',
            'yeah right, whatever you say', 'The schedule for tomorrow is posted in the pinned list')}
        self.assertEqual(rated['oh brilliant, another meeting'], {'Sarcastic': 2})
        self.assertEqual(rated['what a surprise'], {'Sarcastic': 2})
        self.assertEqual(rated['haha oh brilliant, another meeting'], {'Funny': 2, 'Sarcastic': 2})
        self.assertEqual(rated['oh brilliant, you absolute idiot'], {'Rude': 2, 'Disrespectful': 2, 'Sarcastic': 2})
        self.assertEqual(rated['yeah right, whatever you say'], {'Sarcastic': 1})                  # the old rule: a weak reading that never counts
        self.assertEqual(rated['The schedule for tomorrow is posted in the pinned list'], {})

    def test_a_quarter_of_the_messages_are_digs_raise_a_medium_alert(self):
        texts = [DIG if i % 4 == 0 else CALM[i % 6] for i in range(48)]
        aid = self.checked(texts, at(48, days_ago(40)))
        ms = self.ms(aid)
        card = next(c for c in ms['cards'] if c['key'] == 'passive')
        self.assertEqual((card['level'], card['count']), ('Often', 12))
        self.assertTrue(card['headline'].startswith('12 of 48 messages'), card['headline'])
        (alert,) = ms['alerts']
        self.assertEqual((alert['level'], alert['key'], alert['title']), ('medium', 'passive', 'Sarcastic wording is common'))
        self.assert_says(alert['detail'], '12 of the 48 messages', 'about 25 in 100', 'banter')       # the figure, and the warning that a joke can count
        self.assert_says(alert['base'], 'Based on 48 messages')
        self.assert_everywhere(aid)
        self.assertIn('Medium priority', section_text(self.page(aid, 'results'), 'alerts'))

    def test_a_joke_and_an_insult_are_not_digs(self):
        for text, what in (('haha ' + DIG, 'banter'), (DIG.replace('another meeting', 'you absolute idiot'), 'hostile')):
            aid = self.checked([text if i % 4 == 0 else CALM[i % 6] for i in range(48)], at(48, days_ago(40)))
            ms = self.ms(aid)
            passive = next(c for c in ms['cards'] if c['key'] == 'passive')
            self.assertEqual((passive['level'], passive['count']), ('Rare', 0), what)
            self.assertNotIn('passive', [a['key'] for a in ms['alerts']], what)
        self.assertEqual(self.ms(aid)['m']['overall']['hostile'], 12)                                # the insult counts once, as hostile
        self.assertEqual(self.ms(aid)['m']['overall']['dig'], 0)


# =============================================================================================================================================
class Sampling(MeasuresCase):
    """A chat larger than the volume of a check is rated in part, and what the absence badges say then."""

    def test_a_sample_and_messages_added_later_leave_the_absence_badges_undecided(self):
        n = 330
        texts = [INSULT if 100 <= i < 108 else CALM[i % 6] for i in range(n)]
        aid = self.checked(texts, at(n, days_ago(40), step=10))
        ms = self.assert_everywhere(aid, 'sampled')
        self.assertEqual((ms['m']['n'], ms['record']['total'], ms['record']['sampled']), (300, 330, 1))
        self.assert_says(ms['basis_text'], '300 messages', 'an even sample of 330')
        note = next(x for x in ms['notes'] if x['key'] == 'sampled')
        self.assert_says(note['text'], 'A short flare-up can fall between the samples', '"all of them"', 'every message')     # what a sample can miss, and how to see everything
        self.assertNotRegex(note['text'], r'330|300|an even sample')                              # the size of the sample is the basis line's to say (above), once
        over = section_text(self.page(aid, 'results'), 'over-time')
        self.assertEqual((over.count('an even sample of 330'), over.count(note['text'])), (1, 1))
        cant = "Can't tell yet: only part of the chat was read."
        needs = {b['key']: b['needs'] for b in ms['badges']}
        self.assertEqual((needs['calm'], needs['cools'], needs['noflags']), (cant, cant, cant))
        self.assertEqual([b['key'] for b in ms['badges'] if b['earned']], ['courteous', 'supportive', 'encouraging'])
        self.assertIn(cant, section_text(self.page(aid, 'results'), 'badges'))
        self.assertIn(note['text'], section_text(self.page(aid, 'report'), 'over-time'))
        # twenty more messages after the ratings: the check no longer matches the chat
        self.upload(aid, ('more.csv', chat_csv(messages(20), at(20, days_ago(10))), 'text/csv'))
        ctx = report.build(aid)
        self.assertEqual((ctx['added_since'], ctx['rated_n'], ctx['rated_total']), (20, 300, 330))
        self.assertEqual({b['key']: b['needs'] for b in ctx['measures']['badges']}['noflags'], cant)
        self.assertEqual(self.store.measure_history(aid)[0]['n'], 300)
        self.assertEqual(len(self.store.measure_history(aid)), 1, 'adding messages to a check is no new check')
        # and the measures say so in a note of their own, once, in the section about the measures
        added = '20 messages fall inside the review period but were not rated, so they are not included. Run the check again to include them.'
        ms = self.assert_everywhere(aid, 'messages added')
        self.assertEqual([(n['key'], n['text'], n['about']) for n in ms['notes'] if n['key'] == 'added'], [('added', added, 'measures')])
        for what in ('results', 'report'):
            self.assertIn(added, quiet_notes(self.page(aid, what), 'over-time'), what)
            self.assertNotIn(added, quiet_notes(self.page(aid, what), 'alerts'), what)
        self.assertEqual(squeeze(lines_pdf(self.pdf(aid))).count(squeeze(added)), 1)
        self.assertIn(['Note', added], csv_blocks(self.csv(aid))['Measures over time'])


# =============================================================================================================================================
class Thresholds(MeasuresCase):
    """Where the page starts to say more or says it differently: 30 messages for badges, a chat that starts tense, and the Low sensitivity."""

    def test_29_messages_earn_no_badge_and_30_can(self):
        short, long = self.checked(messages(29), at(29, days_ago(40))), self.checked(messages(30), at(30, days_ago(40)))
        a, b = self.ms(short), self.ms(long)
        self.assertEqual(a['badges_earned'], 0)
        self.assert_says(a['badge_note'], 'Badges start at 30 messages', 'has 29')
        self.assertEqual((b['badge_note'], sorted(x['key'] for x in b['badges'] if x['earned'])), ('', ['courteous', 'noflags']))
        for aid, ms in ((short, a), (long, b)):
            self.assert_everywhere(aid)
            for what in ('results', 'report'):
                badges = section_text(self.page(aid, what), 'badges')
                self.assertEqual('Badges start at 30 messages' in badges, ms is a, what)
                self.assertEqual('Earned Courteous' in badges, ms is b, what)

    def test_two_checks_are_compared_only_when_both_rated_48_and_neither_is_over_three_times_the_other(self):
        def second(first_n, second_n):
            aid = self.checked(messages(first_n, hot=(16, 28)), at(first_n, days_ago(90)))
            self.new_check(aid)
            self.check(aid, chat_csv(messages(second_n), at(second_n, days_ago(30))), 'two.csv')
            return aid, self.ms(aid)
        _, alike = second(48, 144)                                                                  # 144 is exactly three times 48
        self.assertEqual((alike['since']['comparable'], alike['since']['why']), (True, ''))
        self.assertEqual(alike['comparison']['previous']['n'], 48)
        aid, big = second(48, 145)
        self.assertFalse(big['since']['comparable'])
        self.assert_says(big['since']['why'], 'very different numbers of messages')
        self.assert_everywhere(aid, '48 and 145')
        aid, small = second(47, 60)
        self.assertFalse(small['since']['comparable'])
        self.assert_says(small['since']['why'], 'fewer than 48 messages')
        self.assertEqual([r['verdict'] for r in small['since']['rows']], [''] * 5)
        self.assert_everywhere(aid, '47 and 60')

    def test_a_chat_that_starts_tense_says_so_once_on_the_escalation_card_and_its_none_is_grey(self):
        """MEASURES.md: if the very first part is already tense (30 or more in 100 heated), a climb before the start cannot be seen. That is said once, on the
        Escalation card, and not again as a note of the page ('opening' no longer exists). A None that rests on a chat that was hot from its first part is not good
        news, so its mark is neutral and not the green one."""
        aid = self.checked(messages(48, hot=(0, 8)), at(48, days_ago(40)))
        ms = self.ms(aid)
        card = ms['cards'][0]
        self.assertTrue(ms['m']['opening_heated'])
        self.assertEqual([n['key'] for n in ms['notes']], [])                                        # no note: it is a note of the card
        self.assert_says(card['note'], 'already tense', 'only counts a rise')
        self.assertEqual((card['level'], card['tone'], card['tone_word'], card['count']), ('None', 'info', 'Neutral', 0))     # judged (48 messages) and no heated moment, but no green mark
        self.assertTrue(card['headline'].startswith('No heated moment'), card['headline'])
        self.assert_everywhere(aid)
        for what in ('results', 'report'):
            html = self.page(aid, what)
            self.assertEqual(section_text(html, 'over-time').count(card['note']), 1, what)           # once, on the card
            self.assertNotIn(card['note'], section_text(html, 'alerts'), what)
            self.assertEqual(quiet_notes(html, 'over-time'), [], what)
            self.assertEqual(cards_on_page(html)[0], (icon_svg('info'), 'None', 'Neutral'), what)
            self.assertNotEqual(cards_on_page(html)[0][0], icon_svg('good'), what)
        pdf = squeeze(measures_pdf(self.pdf(aid)))
        self.assertEqual(pdf.count(squeeze(card['note'])), 1)
        self.assertIn(squeeze('Escalation: None (Neutral)'), pdf)
        rows = self.csv(aid)
        escalation = next(r for r in rows if r[:1] == ['Escalation'])
        self.assertEqual((escalation[6], escalation[-1]), (card['note'], 'Neutral'))                 # the note sits in the Note cell of its row, with the mark in words after the rest
        self.assertEqual(sum(row.count(card['note']) for row in rows), 1)
        control = self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))      # the same words on a chat that did not start tense: the green mark
        self.assertEqual((self.ms(control)['cards'][0]['level'], self.ms(control)['cards'][0]['tone']), ('None', 'good'))

    def test_low_sensitivity_raises_the_cut_offs_for_conflict_and_sarcasm(self):
        texts = [INSULT if i % 6 == 0 else CALM[i % 6] for i in range(48)]                       # 8 hostile messages in 48: 17 in 100
        medium, low = self.checked(texts, at(48, days_ago(40)), vibe_sensitivity='medium'), self.checked(texts, at(48, days_ago(40)), vibe_sensitivity='low')
        a, b = self.ms(medium), self.ms(low)
        self.assertEqual((a['m']['sensitivity'], a['m']['scale']), ('medium', 1.0))
        self.assertEqual((b['m']['sensitivity'], b['m']['scale']), ('low', 1.5))
        self.assertEqual((a['cards'][1]['level'], b['cards'][1]['level']), ('Often', 'Some'))      # the same 8 messages: Often at Medium, Some at Low
        self.assertEqual(a['cards'][1]['headline'], b['cards'][1]['headline'])
        self.assertEqual((a['record']['sensitivity'], b['record']['sensitivity']), ('medium', 'low'))
        self.assert_everywhere(medium, 'medium')
        self.assert_everywhere(low, 'low')

    LOW_NOTE = 'Your sensitivity is Low, so banter counts as normal. The bars for Conflict, Passive-aggressive and heated moments are higher.'

    def test_the_measures_say_so_when_low_sensitivity_has_raised_the_cut_offs(self):
        """MEASURES.md: at Low sensitivity the Conflict and Passive-aggressive cut-offs, and the Medium heated-moment cut-offs, are raised, and a quiet note says so.
        A reader used to see Some where Medium would say Often, with no reason. The note belongs to the measures, and the page, the PDF and the CSV print it once."""
        texts = [INSULT if i % 6 == 0 else CALM[i % 6] for i in range(48)]
        low = self.checked(texts, at(48, days_ago(40)), vibe_sensitivity='low')
        medium = self.checked(texts, at(48, days_ago(40)), vibe_sensitivity='medium')
        ms = self.assert_everywhere(low, 'low')
        self.assertEqual([(n['key'], n['text'], n['about']) for n in ms['notes']], [('low_sensitivity', self.LOW_NOTE, 'measures')])
        for what in ('results', 'report'):
            html = self.page(low, what)
            self.assertEqual(quiet_notes(html, 'over-time'), [self.LOW_NOTE], what)                        # with the measures it is about ...
            self.assertEqual(quiet_notes(html, 'alerts'), [], what)                                       # ... and not with the alerts
            self.assertNotIn('sensitivity', section_text(self.page(medium, what), 'over-time').lower(), f'{what}: no such note at Medium')
        self.assertEqual(squeeze(measures_pdf(self.pdf(low))).count(squeeze(self.LOW_NOTE)), 1)
        self.assertIn(squeeze(self.LOW_NOTE), squeeze(lines_pdf(self.pdf(low))))
        self.assertEqual([r for r in self.csv(low) if r == ['Note', self.LOW_NOTE]], [['Note', self.LOW_NOTE]])
        self.assertIn(['Note', self.LOW_NOTE], csv_blocks(self.csv(low))['Measures over time'])
        medium_ms = self.assert_everywhere(medium, 'medium')
        self.assertEqual(medium_ms['notes'], [])
        self.assertNotIn(self.LOW_NOTE, ' '.join(measure_cells(self.csv(medium))))
        self.assertNotIn(squeeze(self.LOW_NOTE), squeeze(measures_pdf(self.pdf(medium))))


# =============================================================================================================================================
class PdfLayout(MeasuresCase):
    """Where things land in the PDF, read back with pypdf (the text of each page, and where its lowest line sits): the Measures over time part of a first check fits
    one page, the health score tile never starts a page and is never left without the last row of cards, and the Evidence page keeps the list of evidence, the model
    line and the whole method together with room to spare (a long list sends the whole method to the next page)."""

    def flare(self):
        return self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))

    def three(self):
        def make():
            aid = self.new_analysis()
            for k, hot in enumerate(((16, 28), (0, 0), (30, 42))):
                if k:
                    self.new_check(aid)
                self.check(aid, chat_csv(messages(48, hot=hot), at(48, days_ago(90 - 30 * k))), f'c{k}.csv')
            return aid
        return self.scenario('three', make)

    def test_a_first_check_without_a_note_is_one_page_in_the_order_of_the_page(self):
        aid = self.flare()
        ms = self.ms(aid)
        self.assertEqual((ms['notes'], ms['since']), ([], None))
        lines = lines_bodies(self.pdf(aid))
        self.assertEqual(len(lines), 1, 'Measures over time of a first check without a note fits one page')
        text = squeeze(lines[0])
        parts = ('Measures over time', ms['caveats']['measures'], ms['basis_text'], ms['chart_key'], 'Escalation: Steep', 'Conflict: Some', 'Empathy: Often', 'Passive-aggressive: Rare',
                 f'{measures.whole(ms["health"]["score"])} out of 100', f'Health score: {ms["health"]["status"]}', ms['health']['caption'], 'Since your last check', ms['texts']['first_check'])
        found = [text.find(squeeze(part)) for part in parts]
        self.assertNotIn(-1, found, [part for part, at in zip(parts, found) if at < 0])
        self.assertEqual(found, sorted(found), 'the caveat, the basis and the key to the charts above the four cards, the tile under them, then the sentence for a first check')

    def test_the_health_score_tile_never_starts_a_page_and_never_stands_without_the_last_row_of_cards(self):
        """Seen from the PDF a person reads: no page of Measures over time starts with the tile, or holds it without the Empathy and Passive-aggressive cards above it,
        whether the check is a first one or a third, is short, has no dates, or has six notes before the cards."""
        thin = self.scenario('thin', lambda: self.checked(messages(30, hot=(10, 18)), at(30, days_ago(40))))
        undated = self.scenario('undated', lambda: self.checked(messages(48, hot=(16, 23)), None))
        two = self.scenario('two', lambda: self.two_checks())
        scenarios = {'a first check': self.flare(), 'a short chat': thin, 'a chat without dates': undated, 'six notes': self.many_notes(), 'a second check': two,
                     'a third check': self.three()}
        for name, aid in scenarios.items():
            ms = self.ms(aid)
            lines = lines_bodies(self.pdf(aid))
            tiles = [i for i, body in enumerate(lines) if 'out of 100' in body]
            self.assertEqual(len(tiles), 1, name)
            body = lines[tiles[0]]
            self.assertNotRegex(body, r'^\d+ out of 100', f'{name}: a page starts with the tile')
            self.assertLess(body.index('Passive-aggressive:'), body.index('out of 100'), f'{name}: the last row of cards is above the tile')
            self.assertLess(body.index('Empathy:'), body.index('out of 100'), name)
            heading = next(i for i, text in enumerate(lines) if 'Since your last check' in text)
            if ms['since'] is None:
                self.assertEqual(heading, tiles[0], f'{name}: the sentence for a first check follows the tile on its page')
            else:
                self.assertIn(heading, (tiles[0], tiles[0] + 1), f'{name}: the comparison follows the tile on its page, else it starts the next')

    def two_checks(self):
        aid = self.new_analysis()
        self.check(aid, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(90))))
        self.new_check(aid)
        self.check(aid, chat_csv(messages(48), at(48, days_ago(60))), 'two.csv')
        return aid

    def evidence_of(self, sources) -> int:
        return self.scenario(f'{sources} sources', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)), sources=[f'#room-{i % sources}' for i in range(48)]))

    def test_the_evidence_page_keeps_the_list_the_model_line_and_the_whole_method_together_with_room_to_spare(self):
        """Up to four sources: the list of evidence, the Model line and all the entries of How this report works sit on the last page, in that order, with at least
        40 points left under the last line (so that a little more text, or a wider font, does not push the last entry alone onto a page of its own)."""
        import analysis
        for sources in (1, 2, 4):
            aid = self.evidence_of(sources)
            data = self.client.get(f'/analysis/{aid}/report.pdf').content
            pages = pdf_pages(data)
            self.assertEqual([i for i, text in enumerate(pages) if 'Evidence loaded' in text], [len(pages) - 1], f'{sources} sources: the evidence is on the last page')
            page = squeeze(FOOTER.sub('', pages[-1]))
            self.assertEqual(len(self.store.evidence_by_source(aid)), sources)
            order = [page.find(squeeze(part)) for part in ('Evidence and how this report works', 'Evidence loaded', 'Model: qwen2.5:7b.', 'How this report works')]
            self.assertNotIn(-1, order, sources)
            self.assertEqual(order, sorted(order), sources)
            at_last = -1
            for heading, text in analysis.HOW_IT_WORKS:                                                  # every entry of the method is whole on this page, in order
                found = page.find(squeeze(text))
                self.assertGreater(found, at_last, f'{sources} sources: {heading}')
                at_last = found
            spare = lowest_body_y(data, len(pages) - 1) - BOTTOM_MARGIN
            self.assertGreaterEqual(spare, 40, f'{sources} sources: the last page has {spare:.0f} points left')

    def test_a_long_list_of_evidence_sends_the_whole_method_to_a_page_of_its_own(self):
        import analysis
        aid = self.evidence_of(12)
        pages = pdf_pages(self.client.get(f'/analysis/{aid}/report.pdf').content)
        evidence = next(i for i, text in enumerate(pages) if 'Evidence loaded' in text)
        self.assertEqual(evidence, len(pages) - 2)
        self.assertIn('Model: qwen2.5:7b.', pages[evidence])                                             # the model line stays under the evidence it rated
        method = FOOTER.sub('', pages[-1])
        self.assertTrue(method.startswith('How this report works'), method[:80])                        # the method starts the page, heading first, and is not cut
        for heading, text in analysis.HOW_IT_WORKS:
            self.assertIn(squeeze(text), squeeze(method), heading)
            self.assertNotIn(squeeze(text), squeeze(pages[evidence]), heading)


# =============================================================================================================================================
class PageStructure(MeasuresCase):
    """The order of the sections, what is always shown, the words that stay out, and the safeguards of the page."""

    def flare(self):
        return self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))

    def three_checks(self):
        def make():
            aid = self.new_analysis()
            for k, hot in enumerate(((16, 28), (0, 0), (30, 42))):
                if k:
                    self.new_check(aid)
                self.check(aid, chat_csv(messages(48, hot=hot), at(48, days_ago(90 - 30 * k))), f'c{k}.csv')
            return aid
        return self.scenario('three', make)

    def test_the_markup_is_sound_ids_are_unique_and_every_reference_points_somewhere(self):
        for aid in (self.flare(), self.three_checks()):
            for what in ('results', 'report'):
                html = self.page(aid, what)
                balance = _Balance()
                balance.feed(html)
                self.assertEqual(balance.errors, [], what)
                ids = re.findall(r'\sid="([^"]+)"', html)
                self.assertEqual([i for i, n in collections.Counter(ids).items() if n > 1], [], f'{what}: an id is used twice')
                for reference in re.findall(r'aria-labelledby="([^"]+)"', html):
                    for target in reference.split():
                        self.assertIn(f'id="{target}"', html, f'{what}: aria-labelledby points at nothing')
                for target in re.findall(r'href="#([^"]+)"', html):
                    self.assertIn(f'id="{target}"', html, f'{what}: the link to #{target} leads nowhere')

    def poisoned_build(self, mark, extra_alerts=0):
        """measures.build with `mark` put on every piece of text that is shown (and `extra_alerts` more alerts added, to open the fold under the first five)."""
        real = measures.build

        def build(*args, **kwargs):
            ms = real(*args, **kwargs)
            for i in range(extra_alerts):
                ms['alerts'].append(dict(ms['alerts'][0], key=f'extra{i}', level='medium', title=f'Extra alert {i}'))
                ms['alert_counts']['medium'] += 1
            for a in ms['alerts']:
                for key in ('title', 'detail', 'try', 'base', 'when'):
                    a[key] = mark(a[key])
            ms['notes'] += [{'key': 'planted', 'text': 'A planted note'}]
            for n in ms['notes']:
                n['text'] = mark(n['text'])
            for c in ms['cards']:
                for key in ('name', 'meaning', 'headline', 'trend', 'note', 'level', 'latest'):
                    if c.get(key):
                        c[key] = mark(c[key])
            for key in ('name', 'status', 'trend', 'caption'):
                ms['health'][key] = mark(ms['health'][key])
            for b in ms['badges']:
                for key in ('name', 'how', 'evidence', 'needs'):
                    if b[key]:
                        b[key] = mark(b[key])
            ms['badge_note'] = mark('A planted badge note')
            ms['basis_text'] = mark(ms['basis_text'])
            ms['caveats'] = {k: mark(v) for k, v in ms['caveats'].items()}
            for r in (ms['since'] or {}).get('rows', []):
                for key in ('label', 'before', 'now'):
                    r[key] = mark(r[key])
            for s in ms['m']['stretches']:
                s['label'], s['axis'] = mark(s['label']), mark(s['axis'] or 'x')
            return ms
        return mock.patch.object(measures, 'build', build)

    def test_hostile_text_in_any_shown_field_is_escaped_on_the_pages_the_pdf_and_the_csv(self):
        """Nothing in the measures comes from a message, a name or a file name, so no hostile string can reach them today. This puts one into every text that is shown,
        as a later change might, and reads what the real templates, charts, PDF and CSV make of it: text on the pages and in the PDF, a defused cell in the CSV."""
        aid = self.three_checks()
        with self.poisoned_build(lambda text: f'{text} {HOSTILE}', extra_alerts=6):
            for what in ('results', 'report'):
                html = self.page(aid, what)
                for needle in ('<script>alert', '<img src=x'):
                    self.assertNotIn(needle, html, f'{what}: {needle} came through as markup')
                self.assertEqual(Page(html).events, [], what)
                balance = _Balance()
                balance.feed(html)
                self.assertEqual(balance.errors, [], what)
                for sid in ('alerts', 'over-time', 'badges'):
                    self.assertIn('&lt;script&gt;', section(html, sid), f'{what}: {sid} lost the planted text')
                    self.assertIn(HOSTILE, Page(section(html, sid)).text, f'{what}: {sid} does not show the planted text as text')
                alerts = section(html, 'alerts')
                self.assertEqual(len(re.findall(r'<strong class="vc-alert-title">', alerts)), 9, what)       # 3 alerts and 6 more: five in the list, the rest under the fold
                self.assertRegex(alerts, r'<details><summary>Show 4 more</summary>')
            text = squeeze(measures_pdf(self.pdf(aid)))
            self.assertIn(squeeze(HOSTILE), text)                                                    # shown as it is written, not as a tag and not as &lt;
            self.assertNotIn('&lt;', text)
            self.assertNotIn('&amp;', text)
        with self.poisoned_build(lambda text: f'={text}'):
            rows = self.csv(aid)
        cells = measure_cells(rows)
        self.assertEqual([c for c in cells if c[:1] in ('=', '+', '-', '@', '\t', '\r') and not re.fullmatch(r'-?\d+(\.\d+)?', c)], [], 'a cell would run as a formula')
        self.assertGreater(len([c for c in cells if c.startswith("'=")]), 40)                       # the planted cells were defused with an apostrophe, one by one

    def test_the_new_templates_mark_only_builder_html_as_safe_and_hold_no_script_or_event_attribute(self):
        """Every branch of the four templates, including those a page only shows sometimes (the fold under five alerts, a note, a failed comparison)."""
        for name in ('_summary.html', '_alerts.html', '_over_time.html', '_badges.html'):
            source = (ROOT / 'templates' / name).read_text(encoding='utf-8')
            for expression in re.findall(r'\{\{\s*([^}|]*?)\s*\|\s*safe\s*\}\}', source):
                self.assertRegex(expression, r'^(?:icons|charts|mh\.)', f'{name}: {expression} is marked safe but is not the output of a chart, table or icon builder')
            self.assertNotRegex(source, r'(?i)<script|<iframe|javascript:', name)
            self.assertNotRegex(re.sub(r'\{#.*?#\}', '', source, flags=re.S), r'(?i)<[a-z][^>]*\son[a-z]+\s*=', f'{name}: an on*= attribute')
            self.assertNotRegex(source, r'(?i)(?:src|href)="(?:https?:)?//', name)

    def test_the_method_text_of_the_report_explains_the_new_sections(self):
        import analysis
        heading, text = next((h, t) for h, t in analysis.HOW_IT_WORKS if 'alerts' in h.lower())
        aid = self.flare()
        page = Page(self.page(aid, 'report'))
        self.assertIn(heading, page.text)
        self.assertIn(norm(text), page.text)
        self.assertIn(squeeze(text), squeeze(' '.join(self.pdf(aid))))
        self.assert_plain_words(heading + ' ' + text, 'the method text')
        self.assertNotRegex(heading + ' ' + text, r'(?i)\.md\b|\.py\b|\.html\b|\.csv\b|\.json\b', 'the method text is for a reader of the report, who has no file to open')

    def test_the_sections_come_in_the_order_summary_alerts_measures_badges_then_the_signal_profile(self):
        aid = self.flare()
        for what in ('report',):
            html = self.page(aid, what)
            # the report keeps one long page in this order; the results are a screen for each kind of result
            ids = ('summary', 'alerts', 'over-time', 'badges', 'behaviour', 'guidance', 'vibe-check') if what == 'report' else ('summary', 'over-time', 'behaviour', 'vibe-check', 'guidance', 'alerts', 'badges')
            places = [html.index(f'id="{sid}"') for sid in ids]
            self.assertEqual(places, sorted(places), what)
            self.assertEqual(len(set(places)), len(ids))
            for sid in ids:
                self.assertEqual(html.count(f'id="{sid}"'), 1, f'{what}: {sid}')
            # the strip's links go to parts that are on the page
            for target in re.findall(r'<a class="chip-btn" href="#([^"]+)"', section(html, 'summary')):
                self.assertIn(f'id="{target}"', html, f'{what}: the link to #{target} leads nowhere')

    def test_every_new_section_is_a_vc_panel_and_every_chart_has_two_layouts_a_name_and_a_table_twin(self):
        aid = self.flare()
        for what in ('results', 'report'):
            html = self.page(aid, what)
            for sid in SECTIONS:
                self.assertRegex(section(html, sid), r'^<section[^>]*\bclass="[^"]*\bvc\b', f'{what}: {sid} is not inside class vc')       # brings the chart colours and dark mode
            over = section(html, 'over-time')
            figures = re.findall(r'<figure class="vc-fig">.*?</figure>', over, re.S)
            self.assertEqual(len(figures), 5, what)                                                  # the four measures and the health meter
            for figure in figures:
                self.assertEqual((figure.count('class="vc-chart vc-wide"'), figure.count('class="vc-chart vc-narrow"')), (1, 1), what)     # the wide and the phone layout
                self.assertEqual((figure.count('<svg class="vc-svg"'), figure.count('<title'), figure.count('<desc')), (2, 2, 2), what)   # each has a name and a description
            self.assertEqual(over.count('<details class="vc-table"><summary>'), 1, what)            # one twin for the four charts; the meter is a number printed beside it
            self.assertIn('<summary>View the four charts as a table</summary>', over, what)
            names = list(dict.fromkeys(Page(over).titles))                                           # each drawing is there twice, wide and narrow
            self.assertEqual(names[:4], ['Escalation over time', 'Conflict over time', 'Empathy over time', 'Passive-aggressive over time'])
            self.assertEqual(len(names), 5, names)
            self.assertIn('Health score', names[4])

    CHART_KEY = 'Lines show messages per 100. Shaded bands show where Rare, Some and Often begin. A darker band is a heated moment.'

    def test_the_key_to_the_charts_is_the_sentence_of_measures_above_the_cards_on_the_page_and_in_the_pdf(self):
        """MEASURES.md: ms['chart_key'] is the one sentence that explains the four small charts, for the page and the PDF. It is the base sentence, followed where it
        matters by the sentence that says the numbers under the charts are rated message numbers (lines that follow the order the messages were added in) or by
        'Times are UTC.'. It stands once above the four cards, outside every fold, and where there are no charts there is no key."""
        start = days_ago(40)
        dated = self.flare()
        order = self.scenario('undated', lambda: self.checked(messages(48, hot=(16, 23)), None))
        utc = self.scenario('utc', lambda: self.checked(messages(48), [s + 'Z' for s in at(48, start)]))
        short = self.scenario('short 23', lambda: self.checked(hostile_ending(23), at(23, start)))
        self.assertEqual(self.ms(dated)['chart_key'], self.CHART_KEY)
        self.assertEqual(self.ms(utc)['chart_key'], self.CHART_KEY + ' Times are UTC.')
        self.assertTrue(self.ms(order)['chart_key'].startswith(self.CHART_KEY + ' '), self.ms(order)['chart_key'])
        self.assert_says(self.ms(order)['chart_key'][len(self.CHART_KEY):], 'rated message numbers', 'the order you added them')      # what the numbers under the charts are
        for aid in (dated, order, utc):
            ms = self.ms(aid)
            key = ms['chart_key']
            for what in ('results', 'report'):
                html = self.page(aid, what)
                found = re.findall(r'<p class="vc-note vc-chart-key">(.*?)</p>', html, re.S)
                self.assertEqual([unescape(text) for text in found], [key], what)                       # once, as ms wrote it
                self.assertLess(html.index('vc-chart-key'), html.index('class="vc-cols vc-measures"'), f'{what}: the key stands above the four cards')
                self.assertNotIn('Bands mark', html, what)                                                # (the old wording of the page)
            text = squeeze(measures_pdf(self.pdf(aid)))
            self.assertEqual(text.count(squeeze(key)), 1)
            self.assertLess(text.index(squeeze(key)), text.index(squeeze('Escalation:')))
            self.assertNotIn('Bands mark', text)
        self.assertEqual(self.ms(short)['m']['has_series'], False)
        for what in ('results', 'report'):
            self.assertNotIn('vc-chart-key', self.page(short, what), what)                              # nothing to explain
        self.assertNotIn(squeeze('Lines show messages per 100'), squeeze(' '.join(self.pdf(short))))

    def test_a_planted_key_to_the_charts_is_printed_as_it_is_and_no_surface_has_wording_of_its_own(self):
        aid = self.flare()
        planted = 'A planted key to the charts, which no template and no page of the PDF writes itself.'
        real = measures.build

        def build(*args, **kwargs):
            ms = real(*args, **kwargs)
            ms['chart_key'] = planted
            return ms
        with mock.patch.object(measures, 'build', build):
            pages = {what: self.page(aid, what) for what in ('results', 'report')}
            pdf = measures_pdf(self.pdf(aid))
        for what, html in pages.items():
            text = section_text(html, 'over-time')
            self.assertEqual(text.count(planted), 1, what)
            self.assertNotIn('Lines show messages per 100', text, what)
            self.assertNotIn('Shaded bands', text, what)
        self.assertEqual(squeeze(pdf).count(squeeze(planted)), 1)
        self.assertNotIn('Lines show messages per 100', pdf)
        self.assertNotIn('Shaded bands', pdf)

    def wording_of_real_checks(self):
        """(name, ms) of a flare-up, three checks, a threat and six notes: the strings of these results are all the wording the pages, the PDF and the CSV print."""
        threats = self.scenario('threats', lambda: self.checked([THREAT if i == 10 else HARASSMENT if i == 30 else t for i, t in enumerate(messages(48))], at(48, days_ago(40))))
        out = [(name, self.ms(aid)) for name, aid in (('flare', self.flare()), ('three checks', self.three_checks()), ('threats', threats), ('six notes', self.many_notes()))]
        for name, ms in out:
            self.assertGreater(len(list(strings(ms))), 100, name)                                      # (the control: there is wording to look at)
        return out

    def test_no_sentence_of_the_measures_holds_a_symbol_the_pdf_fonts_cannot_print(self):
        """No tick, no cross, no inequality sign, no minus sign, bullet, arrow or star in any string that reaches the PDF or the CSV (the standard PDF fonts print each
        as a question mark). Read from the strings of the result, the one place the wording is written."""
        for name, ms in self.wording_of_real_checks():
            for text in strings(ms):
                self.assertEqual(set(text) & set(SYMBOLS), set(), f'{name}: {text[:80]!r}')

    def test_no_sentence_of_the_measures_is_longer_than_22_words(self):
        """House rule for the new sections: short direct sentences, at most 22 words. Read from the strings of real checks, so that the numbers and the words built into a
        sentence (the counts of an alert, the place of a heated moment) are counted too."""
        for name, ms in self.wording_of_real_checks():
            for text in strings(ms):
                for sentence in re.split(r'(?<=[.!?])\s+', text.strip()):
                    self.assertLessEqual(len(sentence.split()), 22, f'{name}: {sentence[:80]!r}')

    def test_print_rules_keep_each_new_component_whole_and_a_phone_layout_exists(self):
        aid = self.flare()
        html = self.page(aid, 'results')
        css = '\n'.join([self.client.get('/static/style.css').text] + re.findall(r'<style[^>]*>(.*?)</style>', html, re.S))
        kept = [selector for selectors, declarations in css_rules(css, 'print') if declarations.get('break-inside') == 'avoid' for selector in selectors]
        for component in ('.vc-flags li', '.award', '.vc-measure', '.vc-health', '.vc-since', '.vc-card', '.vc-fig'):                      # alerts, badges, cards, the tile, the table, charts
            self.assertTrue(any(component in selector for selector in kept), f'{component} can be split across two printed pages')
        phone = []                                                                                  # the bodies of the @media (max-width ...) blocks
        for found in re.finditer(r'@media\s*\(max-width:[^)]*\)\s*\{', css):
            depth, i = 1, found.end()
            while depth and i < len(css):
                depth += {'{': 1, '}': -1}.get(css[i], 0)
                i += 1
            phone.append(css[found.end():i])
        self.assertTrue(any('.vc-since' in block and '.vc-summary-kpis' in block for block in phone), 'no phone layout for the table and the summary strip')

    def test_the_workplace_page_holds_no_results_and_the_report_has_every_section(self):
        aid = self.flare()
        html = self.page(aid, 'workspace')
        self.assertNotIn('<div class="eyebrow">Plan</div>', html, 'the pathway panel is gone')
        self.assertNotIn('id="results"', html, 'the results are not in the workplace space')
        self.assertIn(f'href="/analysis/{aid}/results"', html)
        for sid in SECTIONS:
            self.assertNotIn(f'id="{sid}"', html, 'the parts of the results are on the results screens')
        page = self.page(aid, 'results')                     # the whole of the results on one page: the report
        for sid in SECTIONS + ('behaviour', 'guidance', 'vibe-check'):
            self.assertIn(f'id="{sid}"', page, sid)
        fresh = self.new_analysis()
        html = self.page(fresh, 'workspace')
        self.assertNotIn('<div class="eyebrow">Plan</div>', html)
        self.assertNotIn('id="results"', html)
        for sid in SECTIONS:
            self.assertNotIn(f'id="{sid}"', html)

    def test_the_caveats_are_always_visible_and_never_inside_a_fold(self):
        for aid in (self.flare(), self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))):
            ms = self.ms(aid)
            for what in ('results', 'report'):
                html = self.page(aid, what)
                page = Page(html)
                outside = page.visible_outside_folds
                self.assertIn(norm(ms['caveats']['measures']), Page(section(html, 'over-time')).visible_outside_folds, f'{what}: the measures caveat')
                for key, sid in (('alerts', 'alerts'), ('badges', 'badges')):          # folded on purpose: "How alerts work", "How badges are meant"
                    self.assertIn(norm(ms['caveats'][key]), norm(Page(section(html, sid)).text), f'{what}: the {key} caveat is not in its own section')
                self.assertEqual(norm(ms['caveats']['empty']) in norm(page.text), not ms['alerts'], what)
                for fold in re.findall(r'<details.*?</details>', html, re.S):
                    self.assertNotIn(ms['caveats']['measures'][:60], unescape(fold), f'{what}: the measures caveat sits in a fold')
        self.assertIn('Alerts are prompts to look, not findings.', measures.CAVEAT_ALERTS)

    def test_the_new_sections_use_none_of_the_words_that_stay_out_and_nothing_punitive(self):
        """The same words are also checked inside every other test here that compares the four surfaces (assert_everywhere), over the alerts each of them raises.
        This test holds the chat shapes together in one place: a flare-up, a timeline of 24, a chat with no dates and a threat."""
        aid = self.flare()
        texts = {}
        for what in ('results', 'report'):
            html = self.page(aid, what)
            for sid in SECTIONS:
                texts[f'{what} {sid}'] = Page(section(html, sid)).human                          # the text, the hover words and the screen reader words
        texts['pdf'] = measures_pdf(self.pdf(aid))
        texts['csv'] = ' '.join(measure_cells(self.csv(aid)))
        self.assertTrue(all(texts.values()))
        for where, text in texts.items():
            self.assert_plain_words(text, where)
        for aid in (self.checked(hostile_ending(24), at(24, days_ago(40))), self.checked(messages(48, hot=(16, 23)), None),
                    self.checked([THREAT if i == 3 else t for i, t in enumerate(messages(48))], at(48, days_ago(40)))):
            html = self.page(aid, 'report')
            for sid in SECTIONS:
                self.assert_plain_words(Page(section(html, sid)).human, sid)
            self.assert_plain_words(measures_pdf(self.pdf(aid)) + ' ' + ' '.join(measure_cells(self.csv(aid))), 'pdf and csv')

    def test_the_words_that_stay_out_are_looked_for_in_what_a_person_reads_and_never_in_the_markup(self):
        """The Summary strip links to the Content categories of Vibe Check by the address #vc-toxicity, and the class names of the markup hold words such as warn.
        Neither is something a person reads, so the scans for the words that stay out and for punitive wording look at the visible text (and the words a screen
        reader or a hover adds) with every tag, address, id and class name stripped first."""
        html = self.page(self.flare(), 'report')                                                    # the report keeps the strip of links; the results page has tabs instead
        summary = section(html, 'summary')
        self.assertIn('href="#vc-toxicity"', summary)                                               # the word is in the markup of the strip ...
        markup = measures_markup(html)
        self.assertIsNotNone(re.search(r'(?i)toxic', markup))
        self.assertIsNotNone(_PUNITIVE.search(markup), 'the control: the filter of punitive wording does find a word in the class names of the markup')
        for sid in SECTIONS:
            read = Page(section(html, sid)).human                                                 # ... and nobody reads it
            self.assertIsNone(re.search(r'(?i)\btoxic|vc-', read), sid)
            self.assert_plain_words(read, sid)
        self.assert_plain_words(Page('<a class="warn" href="#toxic-fas" id="peak" data-x="stretch">Alerts</a><p>All good</p>').human, 'markup only')
        for planted in ('<p>This is toxic</p>', '<p>FAS rose</p>', '<p title="a stretch of calm">x</p>', '<p>Time to warn them</p>', '<p>the members who posted</p>'):
            with self.assertRaises(AssertionError, msg=planted):
                self.assert_plain_words(Page(planted).human, 'planted')

    def test_every_inline_script_is_named_by_the_content_security_policy_and_no_tag_has_an_event_attribute(self):
        aid = self.flare()
        for what in ('results/health', 'report'):
            response = self.client.get(f'/analysis/{aid}/{what}')
            page = Page(response.text)
            has_sections = what == 'report'
            policy = response.headers['content-security-policy']
            script_src = next(part for part in policy.split(';') if part.strip().startswith('script-src'))
            self.assertNotIn('unsafe-inline', script_src)
            self.assertTrue(page.scripts, 'the charts ship one small script of their own')
            for script in page.scripts:
                digest = "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
                self.assertIn(digest, policy, f'{what}: an inline script the policy does not name')
            self.assertEqual(page.events, [], f'{what}: an on*= attribute')
            self.assertNotRegex(response.text, r'(?i)<[a-z][^>]*\son[a-z]+\s*=')
            for tag, name, address in page.addresses:                                              # nothing is fetched from another site
                if tag in ('img', 'script', 'link', 'source', 'iframe'):
                    self.assertFalse(re.match(r'(?i)(?:https?:)?//', address), f'{what}: {tag} {name}={address}')
            if has_sections:
                self.assertNotRegex(measures_markup(response.text), r'(?i)(?:src|href)="(?:https?:)?//')
            if has_sections:
                self.assertNotRegex(measures_markup(response.text), r'<script|<iframe|javascript:')

    def test_the_home_dashboard_shows_four_headline_cards_and_none_of_the_result_sections(self):
        """Home is a glance: Vibe Check, health score, sentiment, the four modules, then the two doors. The detail stays on the results page."""
        aid = self.flare()
        html = self.client.get(f'/?w={aid}').text
        main = html[html.index('<main'):html.index('</main>')]
        text = Page(main).human
        vibe = self.store.latest_vibe(aid)
        self.assertIn(f'Vibe Check {round(vibe["score"] * 10)} percent', main)
        for needle in ('Vibe Check', 'Health Score', 'Sentiment', 'Escalation', 'Conflict', 'Empathy', 'Passive-aggressive', 'Badges & Alerts', 'Flagged messages'):
            self.assertIn(needle, text, needle)
        for needle in ('High priority', 'Medium priority', 'Good news', 'Earned', 'Cools fast', 'No serious flags', 'The mood heated up fast', 'thing to look at'):
            self.assertNotIn(needle, text, needle)
        for sid in SECTIONS:
            self.assertNotIn(f'id="{sid}"', html)
        self.assertNotIn('class="panel vc"', html)


if __name__ == '__main__':
    unittest.main()
