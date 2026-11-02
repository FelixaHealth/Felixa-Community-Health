"""End to end tests of Measures over time, alerts, badges and "Since your last check", through the real app.

Every test builds a real analysis the way a person does: it uploads a CSV chat, runs a check (the fake model of tests/fake_ollama.py rates the
messages by keyword), and then reads what the app serves: the workplace page, the report page, the PDF (read back with pypdf) and the CSV. The fake model
says nothing about real chats. What it gives is chats with exactly known tones, so each test can say what the page must show.

The chats are small and made on purpose:

* FLARE   48 messages, 30 minutes apart. The third of six parts of the chat (messages 17 to 24) is mostly heated: 7 of its 8 messages, 5 of them hostile.
* CALM    48 messages, none heated.
* BOUNDS  the same chat cut to 23, 24, 47 and 48 messages (its last 16 are hostile), and 100 messages with 59 and with 60 dated.

What the classes cover:

    FlareUp        one heated moment, its alert, its badge and its cards, with the same numbers on both pages, in the PDF and in the CSV; times shown while Incognito is off
    CalmChat       no alert to act on, Calm waters earned, and the sentence that an empty list is not an all clear
    Boundaries     23 and 24 messages, 47 and 48 messages, 59 and 60 in 100 dated
    TooEarly       Escalation reads Too early and the rate cards are grey while a check is too short to judge, and the card note for a hot part that chance could give
    SeriousFlags   a threat and a harassment message (High and Medium priority), one message or several, the badge for no serious flags, and the safety sentence under
                   the flagged messages of the Vibe Check part
    Checks         one record per check, Since your last check, the check-by-check chart, the same evidence again, a failing guidance, no Vibe Check,
                   Vibe Check alone, a cancel, a check made with another sensitivity or model, or with a model whose name is long
    Notes          every quiet note once, in its own section, on the pages, in the PDF and in the CSV; the sentence for an all clear; the two fixed sentences and the caveats
    Privacy        what survives Delete evidence, and no time of day anywhere while Incognito is on
    Dates          no dates, 30 in 100 undated, too few dated for lines, strange dates, UTC
    Robustness     5,000 messages, a missing rating, a changed review period, a poisoned saved record, a record that lost its health score, a year of checks
    Sarcasm, Sampling, Thresholds   digs and banter, a sampled check, 30 messages for badges, a tense start, Low sensitivity and the note that says so
    PageStructure  order of the sections, caveats outside every fold, words that stay out (looked for in the words a person reads, never in the markup), the
                   Content-Security-Policy, sound markup, print and phone rules, the home list

What the four surfaces share is checked once, in the helpers of MeasuresCase (assert_everywhere): every note is printed once, in its own section (the notes about the
alerts under Alerts, the others under Measures over time), 'Nothing needs urgent attention' only when ms['all_clear'] says so, the two fixed sentences of ms['texts'],
the five caveats, and, where there is no icon (the PDF), the mark of each card in words (tone_word).

One test is marked as an expected failure: Notes.test_the_marks_of_the_cards_are_said_in_words_in_the_csv_too holds a gap found while writing these tests (the CSV
does not say the marks in words, though MEASURES.md says it should; see its docstring). When the CSV does, the test starts to pass and unittest reports an unexpected
success: remove the decorator then.

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


def same_markup(html) -> str:
    """Markup with the numbers that make each drawing's ids new on every page load taken out, so two renderings of the same results compare equal."""
    return re.sub(r'vc\d+-', 'vcN-', html)


NEXT_PARTS = ('Summary and guidance', 'Measured behaviour', 'Vibe Check', 'Evidence and how this report works')
FOOTER = re.compile(r'^Felixa Community Health \| \d{4}-\d{2}-\d{2}(?: Model-rated wording\. Not a verdict on people\.)? \d+ ')     # what every page opens with in the text


def pdf_pages(data) -> list:
    import pypdf
    return [norm(page.extract_text()) for page in pypdf.PdfReader(io.BytesIO(data)).pages]


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


def lines_pdf(pages) -> str:
    """The part that holds Measures over time and Since your last check: from the page that opens Measures over time."""
    bodies = feature_bodies(pages)
    lines = next((i for i, text in enumerate(bodies) if text.startswith('Measures over time')), len(bodies))
    return ' '.join(bodies[lines:])


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
    """The two numbers of a cell of the Since table as the CSV writes them: 'x of n' is [x, n], and the health score (a whole number) is a number out of 100."""
    if isinstance(value, int):
        return [str(value), '100']
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

    def ms(self, aid) -> dict:
        ctx = report.build(aid)
        self.assertIsNotNone(ctx['measures'])
        return ctx['measures']

    # -- the four surfaces ------------------------------------------------------------------------
    def page(self, aid, what='workspace') -> str:
        response = self.client.get(f'/analysis/{aid}/{what}')
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
        for what in ('workspace', 'report', 'report.pdf', 'export'):
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
        self.assertIn(f'{look} thing{"" if look == 1 else "s"} to look at first', text['summary'], where)
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
            self.assertEqual(copies, 2 if note['key'] == 'not_alike' else 1, f'{where}: {note["key"]}')      # a check that is not alike: its reason is also under Since your last check
        # the four measures and the health score
        over = text['over-time']
        self.assertIn(norm(ms['basis_text']), over, where)
        for card in ms['cards']:
            for part in (card['name'], card['level'], card['meaning'], card['headline'], card['tone_word'], card.get('trend'), card.get('note')):
                if part:
                    self.assertIn(norm(part), over, f'{where}: {card["key"]}')
            if card.get('latest'):
                self.assertIn(f'Latest part: {card["latest"]}', over, where)
        self.assertEqual(cards_on_page(html), [(icon_svg(c['tone']), norm(c['level']), norm(c['tone_word'])) for c in ms['cards']], f'{where}: the mark of each card')
        self.assertIn(f'{measures.whole(h["score"])} / 100', over, where)
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
            self.assertIn('There are no lines over time yet.', over, where)
        # since your last check, and the check-by-check table (from the third check)
        since = [t for t in tables if t and t[0][:3] == ['Measure', 'Last check', 'This check']]
        if ms['since']:
            alike = ms['since']['comparable']
            self.assertIn(norm(caveats['since']), Page(section(html, 'over-time')).visible_outside_folds, f'{where}: the caveat of the comparison is always shown')
            self.assertNotIn(norm(ms['texts']['first_check']), over, f'{where}: the sentence for a first check, after a second')
            self.assertEqual(len(since), 1, where)
            self.assertEqual(since[0][0], ['Measure', 'Last check', 'This check'] + (['Change'] if alike else []), where)
            self.assertEqual(since[0][1:], [[r['label'], str(r['before']), str(r['now'])] + ([r['verdict']] if alike else []) for r in ms['since']['rows']], where)
            if not alike:
                self.assertIn('No comparison', over, where)
                self.assertIn(norm(ms['since']['why']), over, where)
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
        for key in ('alerts', 'measures', 'badges'):
            self.assertIn(norm(caveats[key]), outside, f'{where}: the {key} caveat is folded away or missing')
        self.assertEqual(norm(caveats['empty']) in outside, not shown, where)

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
            if note['key'] != 'not_alike':                                                  # (its reason is also under Since your last check)
                self.assertNotIn(squeeze(note['text']), other, f'{where}: {note["key"]} is in the other part as well')
                self.assertEqual(ours.count(squeeze(note['text'])), 1, f'{where}: {note["key"]} is printed more than once')
        these = 'this message' if ms['m']['n'] == 1 else 'these {:,} messages'.format(ms['m']['n'])
        if not ms['all_clear']:
            self.assertNotIn(squeeze(f'Nothing needs urgent attention in {these}'), ours, f'{where}: an all clear that ms does not give')
        has(ms['basis_text'])
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
        else:
            has('Since your last check', ms['texts']['first_check'])                       # a first check says there is nothing to compare yet, and does not leave the heading out
        if len(ms['trail']) >= 3:
            has('Health score, check by check')
        else:
            self.assertNotIn(squeeze('Health score, check by check'), ours, where)
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
        self.assertEqual([r[:2] + r[4:6] + r[7:] for r in rows_under(m, 'Measure')],
                         [[c['name'], c['level'], c.get('trend') or '', c['headline'], c['meaning'], c.get('latest') or ''] for c in ms['cards']], where)
        self.assertEqual([r[2:4] for r in rows_under(m, 'Measure')], [[str(c.get('count', '')), str(c.get('of', ''))] for c in ms['cards']], where)
        if ms['m']['has_series']:
            self.assertEqual(rows_under(m, 'Part of the chat'),
                             [[s['label'], str(s['n']), str(w(s['rates']['hostile'])), str(w(s['rates']['care'])), str(w(s['rates']['dig'])), str(w(s['rates']['heated'])),
                               'Yes' if s['counted'] else 'No', str(w(s['health']))] for s in ms['m']['stretches']], where)
        else:
            self.assertNotIn(['Part of the chat'], [r[:1] for r in m], where)
        self.assertIn(['Health score', str(w(h['score'])), h['status']], m, where)
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
                self.assertEqual(rows_under(s, 'Measure'), [[r['label'], *count_cells(r['before']), *count_cells(r['now'])] for r in ms['since']['rows']], where)
            if len(ms['trail']) >= 3:                                                                # the table behind the check-by-check chart: the day of each check, never the time
                self.assertEqual([r[:4] for r in rows_under(s, 'Check')],
                                 [[f'Check {i}' + (' (this check)' if i == len(ms['trail']) else ''), r['created_at'][:10], str(r['n']), str(w(r['health']))]
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
        self.assert_page_agrees(ms, self.page(aid, 'workspace'), f'{where} workspace')
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
        self.assertEqual(sorted(earned), ['Cools fast', 'No serious flags'])
        self.assertTrue(earned['Cools fast']['evidence'])
        self.assertEqual(ms['badges_earned'], 2)
        calm = next(b for b in ms['badges'] if b['key'] == 'calm')
        self.assertEqual((calm['earned'], calm['needs']), (False, ''))                           # not earned because of the heated moment, not for want of data

    def test_the_workplace_page_and_the_report_page_show_it(self):
        aid, start = self.flare()
        ms = self.ms(aid)
        for what in ('workspace', 'report'):
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
        self.assertEqual(next(r for r in rows if r[:1] == ['Cools fast'])[1], 'Earned')

    def test_every_part_of_the_chat_has_the_same_numbers_on_both_pages_and_in_the_csv(self):
        aid, start = self.flare()
        m = self.ms(aid)['m']
        csv_parts = rows_under(csv_blocks(self.csv(aid))['Measures over time'], 'Part of the chat')
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            summary = section_text(html, 'summary')
            self.assertIn(f'{vibe["score"]:g} / 10', summary)                                         # the Vibe Check score stays the headline
            self.assertIn('1 thing to look at first', summary)
            self.assertIn('2 badges earned', summary)
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
        html = self.page(aid, 'workspace')
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
        for what in ('workspace', 'report'):
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
            for what in ('workspace', 'report'):
                html = self.page(aid, what)
                titles = Page(section(html, 'over-time')).titles
                self.assertEqual('Conflict over time' in titles, lines, what)                   # a drawing for each measure only when there is a timeline
                self.assertEqual('View the four charts as a table' in section(html, 'over-time'), lines, what)
                self.assertEqual('There are no lines over time yet.' in section_text(html, 'over-time'), not lines, what)
                self.assertEqual('Lines show messages per 100. Bands mark Rare, Some and Often. A darker band is a heated moment.' in section_text(html, 'over-time'), lines, what)       # the key to the charts
                self.assert_page_agrees(ms, html, f'{what} {ms["m"]["n"]}')
            pdf, rows = self.pdf(aid), self.csv(aid)
            self.assert_pdf_agrees(ms, pdf)
            self.assert_csv_agrees(ms, rows)
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
            self.assert_page_agrees(ms, self.page(aid, 'workspace'))
            self.assert_pdf_agrees(ms, self.pdf(aid))
            self.assert_csv_agrees(ms, self.csv(aid))
        page47 = section_text(self.page(s47, 'workspace'), 'over-time')
        self.assertNotIn('Rising', page47)
        self.assertNotIn('Falling', page47)
        self.assertNotIn('1 heated moment', page47)
        self.assertIn(b['cards'][1]['trend'], section_text(self.page(s48, 'workspace'), 'over-time'))

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
        self.assert_says(a['notes'][0]['text'], 'no usable dates', 'the order you added them')
        self.assertTrue(all(s['label'].startswith(f'Part {s["i"] + 1} of 12: rated messages ') for s in a['m']['stretches']))
        self.assertEqual([s['axis'] for s in a['m']['stretches'] if s['axis']], ['#1', '#17', '#34', '#51', '#67', '#84'])
        self.assertFalse(any(s['axis'].startswith('#') for s in b['m']['stretches']))
        self.assertTrue(all(MONTH_DAY.search(s['label']) for s in b['m']['stretches']))
        for aid, ms in ((order, a), (clock, b)):
            self.assert_page_agrees(ms, self.page(aid, 'workspace'))
            self.assert_pdf_agrees(ms, self.pdf(aid))
            self.assert_csv_agrees(ms, self.csv(aid))
        order_markup, clock_markup = section(self.page(order, 'workspace'), 'over-time'), section(self.page(clock, 'workspace'), 'over-time')
        self.assertIn('>#17</text>', order_markup)
        self.assertNotIn('>#17</text>', clock_markup)
        key = 'The numbers under each chart are rated message numbers, in the order you added them.'       # the key to the labels of the axis, where the lines follow the order
        self.assertIn(key, Page(order_markup).text)
        self.assertNotIn(key, Page(clock_markup).text)


# =============================================================================================================================================
class TooEarly(MeasuresCase):
    """A check that is too short to judge. MEASURES.md: below 48 timeline messages nothing can be judged, so the Escalation card reads Too early with a neutral mark,
    never a green None; below 48 rated messages the mark of Conflict, Empathy and Passive-aggressive is neutral grey. And a part of the chat that chance alone could
    make hot gets a note on the Escalation card, not a heated moment."""

    SAID = 'Some parts were heated, but not by more than chance alone would give.'

    def thin(self):
        """30 messages whose middle (messages 11 to 18) is hostile and heated: more than the 24 that lines need, fewer than the 48 that a verdict needs."""
        return self.scenario('thin', lambda: self.checked(messages(30, hot=(10, 18)), at(30, days_ago(40))))

    def test_escalation_reads_too_early_in_grey_below_48_messages_on_the_pages_the_pdf_and_the_csv(self):
        aid = self.thin()
        ms = self.ms(aid)
        card = ms['cards'][0]
        self.assertEqual((card['level'], card['word'], card['tone'], card['tone_word'], card['count']), ('Too early', 'early', 'info', 'Neutral', 0))
        self.assert_says(card['headline'], 'at least 48 messages')
        self.assertEqual((ms['m']['has_series'], ms['m']['verdicts']), (True, False))
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            self.assertEqual(cards_on_page(html)[0], (icon_svg('info'), 'Too early', 'Neutral'), what)               # the grey dot, and the words for a screen reader
            self.assertNotEqual(cards_on_page(html)[0][0], icon_svg('good'), 'never the green mark of a None')
            self.assertNotRegex(section_text(html, 'over-time'), r'Escalation None\b', what)
        text = squeeze(measures_pdf(self.pdf(aid)))
        self.assertIn(squeeze('Escalation: Too early (Neutral)'), text)
        self.assertNotIn(squeeze('Escalation: None'), text)
        escalation = next(r for r in self.csv(aid) if r[:1] == ['Escalation'])
        self.assertEqual(escalation[:3], ['Escalation', 'Too early', '0'])
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
            for what in ('workspace', 'report'):
                icons = [icon for icon, _, _ in cards_on_page(self.page(aid, what))]
                self.assertEqual(icons[1:], [icon_svg(tone) for tone in marks], f'{what} {ms["m"]["n"]}')
            pdf = squeeze(measures_pdf(self.pdf(aid)))
            for card in ms['cards'][1:]:
                self.assertIn(squeeze(f'{card["name"]}: {card["level"]} ({card["tone_word"]})'), pdf)
            self.assert_everywhere(aid)

    def hostile_check(self, n):
        return self.scenario(f'hostile {n}', lambda: self.checked(hostile_ending(n), at(n, days_ago(40))))

    def test_the_rate_cards_judge_the_rated_messages_and_escalation_the_messages_on_the_timeline(self):
        stamps = at(70, days_ago(40))
        aid = self.checked(messages(70, hot=(20, 30)), [s if i < 45 else '' for i, s in enumerate(stamps)])         # 45 of 70 carry a date: the lines hold 45
        ms = self.ms(aid)
        self.assertEqual((ms['m']['n'], ms['m']['timeline_n'], ms['m']['basis'], ms['m']['has_series'], ms['m']['verdicts']), (70, 45, 'time', True, False))
        self.assertEqual((ms['cards'][0]['level'], ms['cards'][0]['tone']), ('Too early', 'info'))
        self.assertNotIn('info', [c['tone'] for c in ms['cards'][1:] if c['key'] != 'empathy'], '70 messages were rated, so the marks of Conflict and sarcasm are not grey')
        self.assertEqual(cards_on_page(self.page(aid, 'workspace'))[0], (icon_svg('info'), 'Too early', 'Neutral'))
        self.assertEqual([n['key'] for n in ms['notes']], ['no_verdict', 'undated'])
        self.assert_everywhere(aid)

    def test_a_judged_calm_chat_reads_none_with_the_green_mark(self):
        aid = self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))
        card = self.ms(aid)['cards'][0]
        self.assertEqual((card['level'], card['tone'], card['tone_word']), ('None', 'good', 'A good sign'))
        self.assertEqual(cards_on_page(self.page(aid, 'workspace'))[0], (icon_svg('good'), 'None', 'A good sign'))
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
        for what in ('workspace', 'report'):
            self.assertEqual(section_text(self.page(aid, what), 'over-time').count(self.SAID), 1, what)
        self.assertEqual(squeeze(measures_pdf(self.pdf(aid))).count(squeeze(self.SAID)), 1)
        self.assertEqual([c for c in measure_cells(self.csv(aid)) if c == self.SAID], [self.SAID])
        self.assertEqual(next(r for r in self.csv(aid) if r[:1] == ['Escalation'])[6], self.SAID)             # the Note column of its row
        self.assert_everywhere(aid)

    def test_the_card_note_for_a_hot_part_is_not_there_when_a_heated_moment_passed_the_chance_test_or_the_chat_is_too_short(self):
        flare = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        young = self.thin()
        for aid in (flare, young):
            for what in ('workspace', 'report'):
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
        self.assert_says(alert['detail'], 'flagged 2 messages as a threat and harassment', '2 scored 7 or more out of 10', 'A model can be wrong',
                         'They are quoted under Flagged messages')                                  # what was flagged, in the plural for two messages, and how sure to be
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
        for what in ('workspace', 'report'):
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
        self.assert_says(ms['alerts'][0]['detail'], 'flagged 1 message as a threat', 'It scored 7 or more out of 10', 'It is quoted under Flagged messages')       # one message: the singular
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

    def test_the_flagged_messages_carry_the_safety_sentence_for_a_threat_or_hate_speech_on_the_pages_and_in_the_pdf(self):
        """A threat or hate speech among the flagged messages puts the safety sentence directly under the heading of the flagged messages, in the Vibe Check part of
        the page and of the PDF (the alert points there). Nothing is added for the other kinds."""
        threat = self.threats()
        hate = self.scenario('hate', lambda: self.hate_speech([THREAT if i == 10 else t for i, t in enumerate(messages(48))]))
        harassment = self.scenario('harassment only', lambda: self.checked([HARASSMENT if i == 30 else t for i, t in enumerate(messages(48))], at(48, days_ago(40))))
        for aid, count, kind in ((threat, 2, 'a threat'), (hate, 1, 'hate speech')):
            self.assertRegex(self.ms(aid)['alerts'][0]['detail'], kind)
            for what in ('workspace', 'report'):
                html = self.page(aid, what)
                text = Page(html[html.index('id="vibe-check"'):]).text
                self.assertEqual(text.count(self.SAFETY), 1, f'{what} {kind}')
                self.assertIn(f'Flagged messages ({count}) {self.SAFETY} Ordered by severity.', text, f'{what} {kind}')             # directly under its heading
            pdf = norm(' '.join(self.pdf(aid)))
            self.assertEqual(pdf.count(self.SAFETY), 1, kind)
            self.assertIn(f'Flagged messages ({count}) {self.SAFETY}', pdf, kind)
        for what in ('workspace', 'report'):                                                          # harassment alone: the alert is High (a message scored 7), the sentence is not added
            html = self.page(harassment, what)
            self.assertNotIn(self.SAFETY, Page(html[html.index('id="vibe-check"'):]).text, what)
            self.assertEqual(self.ms(harassment)['alerts'][0]['level'], 'high')
        pdf = norm(' '.join(self.pdf(harassment)))
        self.assertNotIn(self.SAFETY, pdf)
        self.assertIn('Flagged messages (1)', pdf)

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
        self.assertIn('Medium priority', section_text(self.page(aid, 'workspace'), 'alerts'))
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
        for what in ('workspace', 'report'):
            self.assertIn('This is your first check.', section_text(self.page(aid, what), 'over-time'))
        self.new_check(aid)
        self.assertEqual(len(self.store.measure_history(aid)), 1, 'the record of the earlier check stays when a new check starts')
        self.assertEqual(self.page(aid, 'workspace').count('id="over-time"'), 0, 'a new check has no results yet')
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
        for what in ('workspace', 'report'):
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
            html = self.page(aid, 'workspace')
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
        self.assertIn(slipped['base'], section_text(self.page(aid, 'workspace'), 'alerts'))
        self.assertIn(squeeze(slipped['base']), squeeze(alerts_pdf(self.pdf(aid))))
        self.assertEqual(ms['alert_counts'], {'high': 1, 'medium': 2, 'good': 0})
        self.assertEqual([r['hostile_n'] for r in ms['trail']], [9, 0, 9])
        for what in ('workspace', 'report'):
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
        html = self.page(aid, 'workspace')
        self.assertIn(self.app.pipeline.WITH_PROBLEMS, html)
        ms = self.assert_everywhere(aid, 'after the failure')                                     # the measures only need the ratings
        self.assertEqual([a['key'] for a in ms['alerts']], ['heated'])
        self.assertIn('The written guidance has not been created', html)
        before = self.store.measure_history(aid)
        job = self.run_job(aid, path='run-ai')
        self.assertEqual((job['status'], job['message'].startswith('Your report is ready')), ('complete', True), job)       # every part ran this time
        self.assertEqual(len(self.store.measure_history(aid)), 1)
        self.assertEqual(self.store.measure_history(aid)[0]['gen'], before[0]['gen'])
        self.assertNotIn('The written guidance has not been created', self.page(aid, 'workspace'))

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
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            self.assertIn(note, section_text(html, 'alerts'))
            self.assertNotIn('Nothing needs urgent attention', section_text(html, 'alerts'))        # that line is only for a check that Vibe Check read as well
            self.assertIn('Not run Vibe Check score', section_text(html, 'summary'))
        self.assert_everywhere(aid)
        self.assertIn(['Note', note], self.csv(aid))
        self.assertEqual(len(self.store.measure_history(aid)), 1)

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
        self.assertEqual((sorted(n['key'] for n in ms['notes']), ms['alerts']), (['low_sensitivity', 'not_alike'], []))        # no Better or Worse, so no good news and no slipping mood
        self.assertEqual({n['key']: n['about'] for n in ms['notes']}, {'not_alike': 'alerts', 'low_sensitivity': 'measures'})
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            over = section_text(html, 'over-time')
            table = next(t for t in section_tables(html, 'over-time') if t[0][:3] == ['Measure', 'Last check', 'This check'])
            self.assertEqual(table[0], ['Measure', 'Last check', 'This check'], what)               # no Change column
            self.assertIn('No comparison', over)
            self.assertIn(norm(why), section_text(html, 'alerts'))                                  # the note under Alerts says it as well
            for word in ('Better', 'Worse', 'About the same'):
                self.assertNotIn(word, over, what)
        text = measures_pdf(self.pdf(aid))
        self.assertIn('No comparison.', text)
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
        a different model", for every check from then on. Now both keep it, and a name too long for either is dropped from both."""
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
                self.assertTrue(ms['since']['comparable'], ms['since']['why'])
                self.assertEqual([n['key'] for n in ms['notes'] if n['key'] == 'not_alike'], [])

    def test_each_workplace_keeps_its_own_history(self):
        first, second = self.new_analysis(name='First place'), self.new_analysis(name='Second place')
        self.check(first, chat_csv(messages(48, hot=(16, 28)), at(48, days_ago(60))))
        self.new_check(first)
        self.check(first, chat_csv(messages(48), at(48, days_ago(30))), 'two.csv')
        self.check(second, chat_csv(messages(48), at(48, days_ago(30))))
        self.assertEqual((len(self.store.measure_history(first)), len(self.store.measure_history(second))), (2, 1))
        self.assertIsNotNone(self.ms(first)['since'])
        self.assertIsNone(self.ms(second)['since'])
        self.assertIn('This is your first check.', section_text(self.page(second, 'workspace'), 'over-time'))
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
        for what in ('workspace', 'report'):
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
        html = self.page(aid, 'workspace')
        self.assertIn('Cancelled', html)
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
        self.assertEqual(self.client.get(f'/analysis/{other}/report').status_code, 404)           # no results, so no report to open


# =============================================================================================================================================
class Notes(MeasuresCase):
    """What every surface prints from ms, so that none of them drifts: each quiet note once, in its own section; 'Nothing needs urgent attention' only when ms['all_clear']
    says so; the two fixed sentences (ms['texts']) and the caveats (ms['caveats']). The helpers of MeasuresCase check all of it in every scenario of this module; these
    tests put the cases together and say the sentences."""

    LOW = 'Your sensitivity is Low, so banter counts as normal. The bars for Conflict, Passive-aggressive and heated moments are higher.'
    FIRST = 'This is your first check. Run another after your next batch of messages and this panel will show what moved.'
    NO_BADGES = 'No badges this time. Each one is earned only when the messages clearly show it.'

    def test_every_note_is_printed_once_in_its_own_section_on_the_pages_the_pdf_and_the_csv(self):
        """Six notes at once: Vibe Check did not run (about the alerts), and Low sensitivity, 30 in 100 undated, one message with an incomplete rating, almost no tone
        and four messages added later (about the measures)."""
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
        ms = self.ms(aid)
        self.assertEqual({n['key']: n['about'] for n in ms['notes']},
                         {'no_vibe': 'alerts', 'undated': 'measures', 'incomplete': 'measures', 'low_sensitivity': 'measures', 'silent': 'measures', 'added': 'measures'})
        by_key = {n['key']: n['text'] for n in ms['notes']}
        self.assertEqual(by_key['low_sensitivity'], self.LOW)
        self.assert_says(by_key['silent'], 'Almost no tone was found in these messages', 'The model may not have read this text')
        self.assert_says(by_key['added'], '4 messages were added after this check and are not included', 'Run the check again to include them')
        self.assert_says(by_key['incomplete'], '1 message had incomplete ratings', 'left out')
        self.assertRegex(by_key['undated'], r'^Only \d+ of 99 messages have a date\.')
        self.assert_says(by_key['no_vibe'], 'Vibe Check', 'not an all clear')
        self.assertFalse(ms['all_clear'])
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            self.assertNotIn('Nothing needs urgent attention', section_text(html, 'alerts'), what)
            self.assertIn(ms['caveats']['empty'], section_text(html, 'alerts'), what)                      # the list is empty, and that is not an all clear
            self.assertIn(silent, section_text(html, 'over-time'), what)                                   # the warning is where the figures are, and not only under the badges
            self.assertNotIn(silent, section_text(html, 'alerts'), what)
            self.assertIn(ms['badge_note'], section_text(html, 'badges'), what)
            self.assertIn('0 things to look at first', section_text(html, 'summary'), what)
        self.assert_everywhere(aid)

    def test_the_sentence_for_an_all_clear_is_on_the_pages_only_for_a_check_that_may_say_it(self):
        calm = self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))
        flare = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        silent = self.scenario('silent 60', lambda: self.checked([NEUTRAL] * 60, at(60, days_ago(40))))
        said = 'Nothing needs urgent attention in these {} messages.'
        for aid, may in ((calm, True), (flare, False), (silent, False)):
            ms = self.ms(aid)
            self.assertEqual(ms['all_clear'], may)
            for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
            alerts = section_text(self.page(aid, what), 'alerts')
            self.assertIn('Nothing needs urgent attention in these 48 messages.', alerts)
            self.assertIn('Good news', alerts)
            self.assertIn('Hostility is easing', alerts)
        self.assert_everywhere(aid)

    def test_a_first_check_says_so_under_since_your_last_check_on_the_page_the_pdf_and_the_csv(self):
        aid = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        ms = self.ms(aid)
        self.assertEqual((ms['texts']['first_check'], ms['since']), (self.FIRST, None))
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            self.assertEqual(Page(section(html, 'over-time')).visible_outside_folds.count(caveat), 1, what)
            self.assertNotIn(self.FIRST, section_text(html, 'over-time'), what)
            self.assertNotIn(caveat, ' '.join(re.findall(r'<details.*?</details>', section(html, 'over-time'), re.S)), what)         # never inside a fold
        self.assertEqual(squeeze(lines_pdf(self.pdf(aid))).count(squeeze(caveat)), 1)
        self.assertEqual(csv_blocks(self.csv(aid))['Since your last check'][1], ['How to read this', caveat])
        self.assertNotIn(self.FIRST, ' '.join(measure_cells(self.csv(aid))))
        self.assertNotIn(squeeze(self.FIRST), squeeze(measures_pdf(self.pdf(aid))))

    def test_the_marks_of_the_cards_are_said_in_words_in_the_pdf(self):
        """The PDF cannot draw the icon, so each card and the health tile say their mark in words, from tone_word (the CSV does not yet: see the known gap below)."""
        aid = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        text = squeeze(measures_pdf(self.pdf(aid)))
        for line in ('Escalation: Steep (Look at this first)', 'Conflict: Some (Worth a look)', 'Empathy: Often (A good sign)', 'Passive-aggressive: Rare (A good sign)',
                     'Health score: Fair (Neutral)'):
            self.assertIn(squeeze(line), text)

    @unittest.expectedFailure
    def test_the_marks_of_the_cards_are_said_in_words_in_the_csv_too(self):
        """KNOWN GAP, reported with this change. MEASURES.md and DEV.md say a surface that cannot show the icon (the PDF and the CSV) says the mark in words, from
        tone_word on every card and on the health tile. The PDF does; the CSV has no column or row for it (the Measures over time block holds the level word, Rare or
        Some or Often, which says how often and not whether that is good). When the CSV says the marks the test starts to pass: remove the decorator then."""
        aid = self.scenario('flare', lambda: self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40))))
        ms = self.ms(aid)
        block = csv_blocks(self.csv(aid))['Measures over time']
        cells = [cell for row in block for cell in row]
        for card in ms['cards']:
            self.assertIn(card['tone_word'], cells, card['key'])
        self.assertIn(ms['health']['tone_word'], cells)


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
        words = {'created_at', 'basis', 'sensitivity', 'model', 'period_start', 'period_end', 'sig'}      # the names of settings and the date: the only text
        numbers = {'gen', 'n', 'total', 'sampled', 'stretches', 'hostile_n', 'care_n', 'dig_n', 'heated_n', 'episodes', 'health', 'health_sd', 'peak'}
        self.assertLessEqual(set(kept[0]), words | numbers, 'a new field in the kept record needs a look: only dates, settings and numbers may be kept')
        self.assertLessEqual({'created_at', 'n', 'health', 'hostile_n', 'care_n', 'dig_n', 'heated_n', 'episodes', 'peak', 'gen'}, set(kept[0]))
        for key, value in kept[0].items():
            self.assertIsInstance(value, str if key in words else (int, float), key)
            self.assertNotIsInstance(value, bool, key)
        self.assertEqual((kept[0]['n'], kept[0]['hostile_n'], kept[0]['heated_n'], kept[0]['episodes']), (48, 5, 7, 1))
        self.assertEqual(self.store.measure_history(aid), kept)
        self.assertEqual(self.client.get(f'/analysis/{aid}/report').status_code, 404)
        self.assertEqual(self.client.get(f'/analysis/{aid}/workspace').status_code, 200)
        self.assertNotIn(canary, self.page(aid, 'workspace'))
        # the next check carries on from the kept record
        self.check(aid, chat_csv(messages(48), at(48, days_ago(20))), 'next.csv')
        self.assertEqual(len(self.store.measure_history(aid)), 2)
        self.assertTrue(self.ms(aid)['since']['comparable'])

    def clock_free(self, aid, where):
        """No hour and minute anywhere in the new sections of both pages, the new pages of the PDF and the new blocks of the CSV."""
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            over = section(html, 'over-time')
            self.assertIn('Check by check', section_text(html, 'over-time'))
            self.assertIn('View the checks as a table', over)
            tips = Page(over).tips['Health score, check by check']
            self.assertEqual([tip['t'] for tip in tips], names * 2, 'every check by its number and its day, in the wide drawing and in the phone one')
            self.assertFalse([tip['t'] for tip in tips if CLOCK.search(tip['t']) or 'UTC' in tip['t']])
            table = next(t for t in section_tables(html, 'over-time') if t[0][:2] == ['Check', 'Date'])
            self.assertEqual(len(table) - 1, 3)
            kept[what] = (same_markup(trail_figure(over)), table)
        self.assertIn('Health score, check by check', measures_pdf(self.pdf(aid)))
        # the same three checks with Incognito off: the same chart and the same table
        self.store.update_analysis(aid, incognito=0)
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            over = section_text(html, 'over-time')
            self.assertIn('There are no lines over time yet.', over, what)
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
        for what in ('workspace', 'report'):
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
        table = next(t for t in section_tables(self.page(aid, 'workspace'), 'over-time') if t[0][:2] == ['Part of the chat', 'Messages'])
        self.assertTrue(all(row[0].endswith(' UTC') for row in table[1:]))
        mixed = zulu[:]
        mixed[5] = at(48, start)[5]                                                                # one stamp without an offset
        other = self.checked(messages(48), mixed)
        self.assertFalse(any('UTC' in s['label'] for s in self.ms(other)['m']['stretches']))
        self.assertNotIn(' UTC', measures_markup(self.page(other, 'workspace')))


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
        self.assert_page_agrees(ms, response.text, 'workspace of 5,000')
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
        for what in ('workspace', 'report'):
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
        html = {what: self.page(aid, what) for what in ('workspace', 'report')}
        day = start.date().isoformat()                                                              # a period that keeps only the first 12 of the messages
        response = self.client.post(f'/analysis/{aid}/check/4', data={'period_start': day, 'period_end': day})
        self.assertEqual(response.status_code, 303)
        saved = self.store.get_analysis(aid)
        self.assertEqual((saved['period_start'], saved['period_end']), (day, day))
        ctx = report.build(aid)
        self.assertEqual((ctx['period']['kept'], ctx['period']['outside']), (12, 36))              # the period really does leave most of the chat out
        after = ctx['measures']
        for key in ('m', 'cards', 'health', 'alerts', 'alert_counts', 'notes', 'badges', 'badges_earned', 'basis_text', 'since', 'caveats'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual([r['hostile_n'] for r in after['trail']], [r['hostile_n'] for r in before['trail']])
        self.assertEqual((before['record']['period_start'], after['record']['period_start']), ('', day))   # only the settings the record names changed
        for what in ('workspace', 'report'):
            now = self.page(aid, what)
            for sid in SECTIONS:
                self.assertEqual(same_markup(section(now, sid)), same_markup(section(html[what], sid)), f'{what}: {sid}')
        self.assert_everywhere(aid, 'after the period changed')

    def test_a_poisoned_saved_record_is_dropped_or_shown_as_text_and_never_as_markup(self):
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        good = {'created_at': '2026-05-01T10:00:00', 'n': 48, 'total': 48, 'sampled': 0, 'basis': 'time', 'stretches': 6, 'model': 'qwen2.5:7b', 'sensitivity': 'medium',
                'health': 60.0, 'health_sd': 10.0, 'hostile_n': 1, 'care_n': 3, 'dig_n': 0, 'heated_n': 1, 'episodes': 0, 'peak': 0.0, 'sig': '48:1:1:1:1'}
        poisoned = [dict(good, gen=gen + 1, created_at=HOSTILE),                                   # no readable date: the whole record goes
                    dict(good, gen=gen + 2, model=HOSTILE, sensitivity=HOSTILE, sig=HOSTILE, basis=HOSTILE, period_start=HOSTILE, period_end=HOSTILE,
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
        self.assertEqual(kept[2].get('model'), None)
        self.assertNotIn('care_n', kept[3])
        self.assertNotIn('heated_n', kept[3])
        self.surfaces_render(aid)
        for what in ('workspace', 'report'):
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
        with self.store.db() as c:
            for k in range(52):
                row = {'created_at': f'2025-{1 + k // 5:02d}-{1 + (k % 5) * 5:02d}T10:00:00', 'gen': gen + 1 + k, 'n': 48, 'total': 48, 'sampled': 0, 'basis': 'time', 'stretches': 6,
                       'model': 'qwen2.5:7b', 'sensitivity': 'medium', 'health': 45.0 + k % 20, 'health_sd': 10.0, 'hostile_n': k % 5, 'care_n': 3 + k % 4, 'dig_n': 0,
                       'heated_n': k % 7, 'episodes': k % 2, 'peak': 0.0, 'sig': f'48:{k}:1:1:1'}
                c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, row['created_at'], json.dumps(row), row['gen']))
        ms = self.ms(aid)
        self.assertEqual(len(ms['trail']), 53)
        began = time.perf_counter()
        self.surfaces_render(aid)
        self.assertLess(time.perf_counter() - began, 20)
        self.assert_everywhere(aid, '53 checks')                                                    # the table behind the chart has all 53 rows on both pages and in the CSV
        self.assertEqual(len(next(t for t in section_tables(self.page(aid, 'workspace'), 'over-time') if t[0][:2] == ['Check', 'Date'])) - 1, 53)

    def test_a_saved_record_with_a_date_and_a_count_but_no_health_score_does_not_break_the_pages(self):
        """MEASURES.md: a record without a date, a count of messages or a health score is dropped as damaged. It used to be kept, and measures.since_table then raised
        a KeyError for it on every page of the analysis (the workplace page, the report, the PDF and the CSV), for good, because the records stay through Delete
        evidence. A record like that comes from a hand-edited database or an older build."""
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        base = {'created_at': '2026-05-01T10:00:00', 'n': 48, 'model': 'qwen2.5:7b', 'sensitivity': 'medium'}
        no_key = dict(base, gen=gen + 1)
        damaged = [dict(base, gen=gen + 2, health='not a number'), no_key, dict(base, gen=gen + 3, health=None), dict(base, gen=gen + 4, health=[50.0]),
                   dict(base, gen=gen + 5, health=1e300), dict(base, gen=gen + 6, health=True), dict(base, gen=gen + 7, health={'a': 1})]
        with self.store.db() as c:
            for row in damaged:
                c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, row['created_at'], json.dumps(row), row['gen']))
        self.assertEqual([r['gen'] for r in self.store.measure_history(aid)], [gen], 'not one of the seven comes back: they are left out as damaged')
        self.surfaces_render(aid)
        ms = self.assert_everywhere(aid, 'damaged records')
        self.assertEqual(len(ms['trail']), 1)
        self.assertIsNone(ms['since'], 'with nothing readable before it, this is a first check')
        self.assertIn(ms['texts']['first_check'], section_text(self.page(aid, 'workspace'), 'over-time'))
        # a check made afterwards compares with the real one, whatever damaged rows lie between
        self.new_check(aid)
        self.check(aid, chat_csv(messages(48), at(48, days_ago(10))), 'two.csv')
        ms = self.assert_everywhere(aid, 'after the next check')
        self.assertEqual(len(ms['trail']), 2)
        self.assertTrue(ms['since']['comparable'])

    def test_a_saved_record_with_a_count_above_its_total_keeps_its_other_numbers_and_the_pages_work(self):
        """MEASURES.md: a count of hostile, care, dig or heated messages that is higher than the number of messages is left out of its record. The comparison used to
        raise 'math domain error' for a count that passed its total by 2 or more."""
        aid = self.checked(messages(48, hot=(16, 23)), at(48, days_ago(40)))
        gen = self.store.evidence_generation(aid)
        row = {'created_at': '2026-05-01T10:00:00', 'n': 48, 'total': 48, 'model': 'qwen2.5:7b', 'sensitivity': 'medium', 'gen': gen + 1, 'health': 60.0, 'health_sd': 10.0,
               'hostile_n': 500, 'care_n': 10 ** 9, 'dig_n': 2, 'heated_n': 50, 'episodes': 0, 'peak': 0.0}
        with self.store.db() as c:
            c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, row['created_at'], json.dumps(row), row['gen']))
        kept = self.store.measure_history(aid)[-1]
        self.assertEqual(kept['gen'], gen + 1)
        self.assertEqual({k: kept.get(k) for k in ('hostile_n', 'care_n', 'dig_n', 'heated_n', 'health', 'n')}, {'hostile_n': None, 'care_n': None, 'dig_n': 2, 'heated_n': None,
                                                                                                                 'health': 60.0, 'n': 48})
        self.surfaces_render(aid)
        ms = self.assert_everywhere(aid, 'a count above its total')
        rows = {r['label']: r for r in ms['since']['rows']}
        self.assertEqual(rows['Health score']['before'], 60)
        self.assertEqual((rows['Conflict']['verdict'], rows['Empathy']['verdict'], rows['Heated messages']['verdict']), ('', '', ''), 'no call is made on a number that is not there')
        self.assertEqual(rows['Passive-aggressive']['before'], '2 of 48')


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
        self.assertIn('Medium priority', section_text(self.page(aid, 'workspace'), 'alerts'))

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
        self.assert_says(note['text'], '300 of 330 messages', 'an even sample', 'all of them')
        cant = "Can't tell yet: only part of the chat was read."
        needs = {b['key']: b['needs'] for b in ms['badges']}
        self.assertEqual((needs['calm'], needs['cools'], needs['noflags']), (cant, cant, cant))
        self.assertEqual([b['key'] for b in ms['badges'] if b['earned']], ['courteous', 'supportive', 'encouraging'])
        self.assertIn(cant, section_text(self.page(aid, 'workspace'), 'badges'))
        self.assertIn(note['text'], section_text(self.page(aid, 'report'), 'over-time'))
        # twenty more messages after the ratings: the check no longer matches the chat
        self.upload(aid, ('more.csv', chat_csv(messages(20), at(20, days_ago(10))), 'text/csv'))
        ctx = report.build(aid)
        self.assertEqual((ctx['added_since'], ctx['rated_n'], ctx['rated_total']), (20, 300, 330))
        self.assertEqual({b['key']: b['needs'] for b in ctx['measures']['badges']}['noflags'], cant)
        self.assertEqual(self.store.measure_history(aid)[0]['n'], 300)
        self.assertEqual(len(self.store.measure_history(aid)), 1, 'adding messages to a check is no new check')
        # and the measures say so in a note of their own, once, in the section about the measures
        added = '20 messages were added after this check and are not included. Run the check again to include them.'
        ms = self.assert_everywhere(aid, 'messages added')
        self.assertEqual([(n['key'], n['text'], n['about']) for n in ms['notes'] if n['key'] == 'added'], [('added', added, 'measures')])
        for what in ('workspace', 'report'):
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
            for what in ('workspace', 'report'):
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

    def test_a_chat_that_starts_tense_says_a_climb_before_it_cannot_be_seen(self):
        aid = self.checked(messages(48, hot=(0, 8)), at(48, days_ago(40)))
        ms = self.ms(aid)
        self.assertTrue(ms['m']['opening_heated'])
        self.assertIn('opening', [n['key'] for n in ms['notes']])
        self.assert_says(ms['cards'][0]['note'], 'already tense', 'only counts a rise')
        self.assertEqual(ms['cards'][0]['level'], 'None')
        self.assertTrue(ms['cards'][0]['headline'].startswith('No heated moment'), ms['cards'][0]['headline'])
        self.assert_everywhere(aid)
        note = next(n['text'] for n in ms['notes'] if n['key'] == 'opening')
        self.assert_says(note, 'already tense', 'cannot be seen')
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            self.assertIn(note, section_text(html, 'over-time'), what)
            self.assertIn(ms['cards'][0]['note'], section_text(html, 'over-time'), what)
        self.assertIn(['Note', note], self.csv(aid))

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
        for what in ('workspace', 'report'):
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
            for what in ('workspace', 'report'):
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
            for what in ('workspace', 'report'):
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
        for what in ('workspace', 'report'):
            html = self.page(aid, what)
            ids = ('summary', 'alerts', 'over-time', 'badges', 'behaviour', 'guidance', 'vibe-check')
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
        for what in ('workspace', 'report'):
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

    def test_print_rules_keep_each_new_component_whole_and_a_phone_layout_exists(self):
        aid = self.flare()
        html = self.page(aid, 'workspace')
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

    def test_on_the_workplace_page_the_results_come_before_the_plan_and_without_results_the_plan_is_alone(self):
        aid = self.flare()
        html = self.page(aid, 'workspace')
        plan = html.index('<div class="eyebrow">Plan</div>')
        for sid in SECTIONS + ('behaviour', 'guidance', 'vibe-check'):
            self.assertLess(html.index(f'id="{sid}"'), plan, sid)
        self.assertLess(plan, html.index('<div class="eyebrow">Engine</div>'))
        fresh = self.new_analysis()
        html = self.page(fresh, 'workspace')
        self.assertIn('<div class="eyebrow">Plan</div>', html)
        for sid in SECTIONS:
            self.assertNotIn(f'id="{sid}"', html)

    def test_the_caveats_are_always_visible_and_never_inside_a_fold(self):
        for aid in (self.flare(), self.scenario('calm', lambda: self.checked(messages(48), at(48, days_ago(40))))):
            ms = self.ms(aid)
            for what in ('workspace', 'report'):
                html = self.page(aid, what)
                page = Page(html)
                outside = page.visible_outside_folds
                for key, sid in (('alerts', 'alerts'), ('measures', 'over-time'), ('badges', 'badges')):
                    self.assertIn(norm(ms['caveats'][key]), outside, f'{what}: the {key} caveat')
                    self.assertIn(norm(ms['caveats'][key]), Page(section(html, sid)).visible_outside_folds, f'{what}: the {key} caveat is not in its own section')
                self.assertEqual(norm(ms['caveats']['empty']) in outside, not ms['alerts'], what)
                for fold in re.findall(r'<details.*?</details>', html, re.S):
                    for key in ('alerts', 'measures', 'badges'):
                        self.assertNotIn(ms['caveats'][key][:60], unescape(fold), f'{what}: the {key} caveat sits in a fold')
        self.assertIn('Alerts are prompts to look, not findings.', measures.CAVEAT_ALERTS)

    def test_the_new_sections_use_none_of_the_words_that_stay_out_and_nothing_punitive(self):
        """The same words are also checked inside every other test here that compares the four surfaces (assert_everywhere), over the alerts each of them raises.
        This test holds the chat shapes together in one place: a flare-up, a timeline of 24, a chat with no dates and a threat."""
        aid = self.flare()
        texts = {}
        for what in ('workspace', 'report'):
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
        html = self.page(self.flare(), 'workspace')
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
        for what in ('workspace', 'report'):
            response = self.client.get(f'/analysis/{aid}/{what}')
            page = Page(response.text)
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
            self.assertNotRegex(measures_markup(response.text), r'(?i)(?:src|href)="(?:https?:)?//')
            self.assertNotRegex(measures_markup(response.text), r'<script|<iframe|javascript:')

    def test_the_home_list_shows_the_vibe_score_and_no_health_score_alert_or_badge(self):
        aid = self.flare()
        html = self.client.get('/').text
        main = html[html.index('<main'):html.index('</main>')]
        text = Page(main).human
        vibe = self.store.latest_vibe(aid)
        self.assertIn(f'{vibe["score"]:g} / 10 vibe', text)
        for needle in ('Health score', '/ 100', 'High priority', 'Medium priority', 'Good news', 'Earned', 'Cools fast', 'No serious flags', 'The mood heated up fast',
                       'Escalation', 'Conflict', 'Empathy', 'Passive-aggressive', 'thing to look at'):
            self.assertNotIn(needle, text, needle)
        for sid in SECTIONS:
            self.assertNotIn(f'id="{sid}"', html)
        self.assertNotIn('class="panel vc"', html)


if __name__ == '__main__':
    unittest.main()
