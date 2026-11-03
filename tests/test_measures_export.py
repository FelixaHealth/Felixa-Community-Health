"""The PDF pages and the CSV rows of Measures over time, alerts and badges (measures_export.py, and the footer, styles and tables of report_export.py).

Each test builds a real analysis through the app (with the fake model of tests/fake_ollama.py), runs checks, and reads the PDF and the CSV that the app serves.
The fake model rates by keyword, so the numbers say nothing about real chats. They exercise the pages, the tables and the wording. Some tests plant their own
alerts, notes and badges in a copy of the report context and lay the PDF out for real, to prove where things land: reportlab's `afterFlowable` hook tells which
page a flowable went to and how much room was left under it. Run with:

    python3 -m unittest tests.test_measures_export -v
"""
import copy
import csv
import datetime
import io
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_app import AppCase, pdf_text  # noqa: E402

import measures  # noqa: E402

CALM = ['Thanks for the update on the release', 'The schedule for tomorrow is posted in the pinned list', 'Please send the file when you can',
        'I understand, sorry you had a rough week', 'Great work on the new design', 'The schedule for tomorrow is posted in the pinned list']
HOT = ['you absolute idiot, stupid take', 'wtf is going on here', 'you absolute idiot, stupid take', 'The schedule for tomorrow is posted in the pinned list',
       'you always do this to the team']
CARING = ['I understand, sorry you had a rough week', 'The schedule for tomorrow is posted in the pinned list']
NEUTRAL = ['The schedule for tomorrow is posted in the pinned list']

CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')
BANNED = (r'\bFAS\b', r'(?i)\bstretch', r'(?i)\bepisode', r'(?i)\bbaseline', r'(?i)\bpeak', r'(?i)\btoxic', r'(?i)communication unit', r'(?i)standard error', r'(?i)z-score')
BLOCKS = ('Alerts', 'Measures over time', 'Since your last check', 'Badges')
MEASURE_HEAD = ['Measure', 'Level', 'Count (messages; heated moments for Escalation)', 'Out of (messages rated)', 'Trend', 'What we saw', 'Note', 'What it means',
                'Level in the latest part', 'Mark']
PARTS_HEAD = ['Part of the chat', 'Messages', 'Conflict per 100 messages', 'Empathy per 100 messages', 'Passive-aggressive per 100 messages', 'Heated per 100 messages',
              'Counted as a heated moment', 'Health score']
SINCE_HEAD = ['Measure', 'Last check', 'Last check: out of', 'This check', 'This check: out of', 'Change']
TRAIL_HEAD = ['Check', 'Date', 'Messages rated', 'Health score', 'Conflict messages', 'Empathy messages', 'Passive-aggressive messages', 'Heated messages', 'Heated moments']
ALERT_HEAD = ['Priority', 'Alert', 'What we saw', 'Try this', 'Where', 'Based on']
BADGE_HEAD = ['Badge', 'Status', 'Why or what it needs', 'How it works']
FOOTER = 'Model-rated wording. Not a verdict on people.'
PRIORITY = {'high': 'High priority', 'medium': 'Medium priority', 'good': 'Good news'}
SINCE_KINDS = {'Conflict': 'hostile', 'Empathy': 'care', 'Passive-aggressive': 'dig', 'Heated messages': 'heated'}
ISO_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MONTH_END = re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$')


def days_ago(n):
    """18:00 on a day n days back: always a usable date, whatever day the tests run."""
    return (datetime.datetime.now() - datetime.timedelta(days=n)).replace(hour=18, minute=0, second=0, microsecond=0)


def chat(n=72, hot=(0, 0), start=None, step=2, calm=CALM, heated=HOT, first=None, dated=True, utc=False, sources=('general',)):
    """A CSV chat of `n` messages two minutes apart. The messages in `hot` come from `heated`, the first `first[0]` from `first[1]`, the rest from `calm`. Without
    `dated` there is no timestamp column at all (the lines then follow the order the messages were added in); with `utc` every timestamp carries a 'Z'. The
    messages come from the `sources`, in turn."""
    start = start or days_ago(90)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow((['timestamp'] if dated else []) + ['source', 'text'])
    for i in range(n):
        pool = heated if hot[0] <= i < hot[1] else first[1] if first and i < first[0] else calm
        stamp = (start + datetime.timedelta(minutes=step * i)).isoformat() + ('Z' if utc else '')
        writer.writerow(([stamp] if dated else []) + [sources[i % len(sources)], f'{pool[i % len(pool)]} {i}'])
    return out.getvalue().encode()


def strings(obj):
    """Every string inside a nested structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from strings(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from strings(value)


def squeeze(text):
    """Without any white space: a table cell wraps its text at the width of the column, so a sentence comes back from the PDF cut in pieces."""
    return re.sub(r'\s+', '', text)


def page_texts(data):
    import pypdf
    return [' '.join(page.extract_text().split()) for page in pypdf.PdfReader(io.BytesIO(data)).pages]


def our_start(texts):
    return next(i for i, t in enumerate(texts) if re.search(r'Alerts and badges Alerts are prompts to look', t))


def our_pages(texts):
    """The pages of this feature: from the one headed Alerts and badges to the page before the summary (or the measured behaviour, when there is no summary)."""
    start = our_start(texts)
    end = next((i for i, t in enumerate(texts) if i > start and ('Your questions answered' in t or 'Summary and guidance' in t or 'Measured behaviour' in t)), len(texts))
    return texts[start:end]


def block(rows, title):
    """The rows of one block of the CSV: from its title row to the title row of the next block (the last one, Badges, to its empty row). A block holds an
    empty row between its tables, and ends with one; that last one is not counted."""
    present = [t for t in BLOCKS if [t] in rows]
    start, i = rows.index([title]), present.index(title)
    end = rows.index([present[i + 1]]) if i + 1 < len(present) else next(j for j in range(start, len(rows)) if rows[j] == [])
    out = rows[start:end]
    while out and out[-1] == []:
        out.pop()
    return out


def measure_rows(rows):
    """Everything this feature wrote into the CSV: from the title row of Alerts to the end of Badges."""
    return rows[rows.index(['Alerts']):rows.index(['Badges']) + len(block(rows, 'Badges'))]


def table_after(rows, head):
    """The rows under a header row, up to the next empty row."""
    start = rows.index(head) + 1
    return rows[start:next((i for i in range(start, len(rows)) if rows[i] == []), len(rows))]


def inside(flowable):
    """The flowable and everything nested in it: the content of a KeepTogether, and the cells of a table."""
    yield flowable
    for inner in getattr(flowable, '_content', None) or []:
        yield from inside(inner)
    for row in getattr(flowable, '_cellvalues', None) or []:
        for cell in row:
            for item in (cell if isinstance(cell, (list, tuple)) else [cell]):
                if hasattr(item, 'wrap'):
                    yield from inside(item)


def text_of(flowable) -> str:
    return flowable.getPlainText() if hasattr(flowable, 'getPlainText') else ''


class MeasuresExport(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.built = {}

    # -- scenarios, each built once ------------------------------------------------------------------
    def check(self, aid, data, name='chat.csv', **settings):
        """One check. A new check resets its settings, so they are set again before the messages come in."""
        self.store.update_analysis(aid, **settings)
        self.upload(aid, (name, data, 'text/csv'))
        job = self.run_job(aid, path='check/6', engine='')
        self.assertEqual(job['status'], 'complete', job)

    def new_check(self, aid):
        self.client.post(f'/analysis/{aid}/check/new')

    def scenario(self, name):
        """The id of a demo analysis, built on first use: 'three' (three checks, with alerts), 'incognito' (the same with Incognito on, and the three checks made
        on one day), 'calm' (a first check: no alerts, badges), 'fading' (an alert whose next step holds a question mark), 'few' (10 messages: no badges),
        'unlike' (two checks made with different sensitivity), 'improved' (two checks that are alike, the second calmer: good news only), 'undated' (no dates at all:
        the lines follow the order the messages were added in), 'utc' (every timestamp carries a UTC offset) and 'thin' (36 messages: lines over time, but no
        direction and no heated moment yet, so Escalation reads Too early)."""
        if name in self.built:
            return self.built[name]
        settings = {'incognito': 1} if name == 'incognito' else {}
        aid = self.new_analysis(**settings)
        if name in ('three', 'incognito'):
            self.check(aid, chat(hot=(24, 48), start=days_ago(90)), **settings)
            self.new_check(aid)
            self.check(aid, chat(start=days_ago(60)), 'two.csv', **settings)
            self.new_check(aid)
            self.check(aid, chat(hot=(40, 56), start=days_ago(30)), 'three.csv', **settings)
        elif name == 'calm':
            self.check(aid, chat())
        elif name == 'fading':
            self.check(aid, chat(n=96, calm=NEUTRAL, first=(36, CARING)))
        elif name == 'few':
            self.check(aid, chat(n=10, hot=(3, 6)))
        elif name == 'unlike':
            self.check(aid, chat(hot=(24, 48)))
            self.new_check(aid)
            self.check(aid, chat(start=days_ago(60)), 'two.csv', vibe_sensitivity='low')
        elif name == 'improved':
            self.check(aid, chat(hot=(24, 48)))
            self.new_check(aid)
            self.check(aid, chat(start=days_ago(60)), 'two.csv')
        elif name == 'undated':
            self.check(aid, chat(hot=(24, 48), dated=False))
        elif name == 'utc':
            self.check(aid, chat(hot=(24, 48), utc=True))
        elif name == 'thin':
            self.check(aid, chat(n=36, hot=(12, 20)))
        else:
            raise KeyError(name)
        self.built[name] = aid
        return aid

    def context(self, name):
        import report
        return report.build(self.scenario(name))

    def ms(self, name):
        return self.context(name)['measures']

    def edited(self, name):
        """A copy of the report context of a scenario with its own copy of the measures, to be changed. (The rest of the context is shared: it holds database
        rows, which cannot be copied.)"""
        ctx = dict(self.context(name))
        ctx['measures'] = copy.deepcopy(ctx['measures'])
        return ctx

    def csv(self, name):
        return self.export_rows(self.scenario(name))

    def pdf(self, name):
        data = self.client.get(f'/analysis/{self.scenario(name)}/report.pdf').content
        return data, page_texts(data)

    def laid_out(self, ctx):
        """(pdf bytes, story, seen) for a report context. `story` is the list of flowables that went to the document, `seen` holds (page, room left on the page under
        the flowable, flowable) for every flowable reportlab laid out, in order: a group that fits shows as one, one that had to be split shows as its parts."""
        import report_export
        from reportlab.platypus import SimpleDocTemplate
        seen, stories = [], []
        real_build = SimpleDocTemplate.build

        def after(doc, flowable):
            frame = doc.frame                                      # (none while a page break is being handled)
            seen.append((doc.page, None if frame is None else frame._y - frame._y1p, flowable))

        def build(doc, story, **kwargs):
            stories.append(list(story))
            return real_build(doc, story, **kwargs)
        with mock.patch.object(SimpleDocTemplate, 'afterFlowable', after), mock.patch.object(SimpleDocTemplate, 'build', build):
            data = report_export.pdf(ctx, ROOT)
        return data, stories[0], seen

    # -- the CSV -------------------------------------------------------------------------------------
    def test_the_csv_has_the_four_blocks_each_with_its_caveat(self):
        """After the guidance and before the Vibe Check come the blocks Alerts, Measures over time, Since your last check and Badges. Each opens with its
        title row and a 'How to read this' row that carries the caveat sentence of measures.py, word for word."""
        ms = self.ms('three')
        rows = self.csv('three')
        for title, key in (('Alerts', 'alerts'), ('Measures over time', 'measures'), ('Since your last check', 'since'), ('Badges', 'badges')):
            self.assertEqual(rows[rows.index([title]) + 1], ['How to read this', ms['caveats'][key]], title)
        order = [rows.index([title]) for title in BLOCKS]
        self.assertEqual(order, sorted(order))
        self.assertLess(rows.index(['Guidance']), order[0])
        self.assertLess(order[-1], rows.index(['Vibe Check']))
        for title in BLOCKS:
            self.assertGreater(len(block(rows, title)), 3, title)
            self.assertEqual(rows[rows.index([title]) - 1], [], title)                               # each block starts after an empty row
        self.assertEqual(rows[rows.index(['Badges']) + len(block(rows, 'Badges'))], [])

    def test_the_alerts_block_lists_every_alert_in_the_order_of_the_page(self):
        ms = self.ms('three')
        self.assertTrue(ms['alerts'])
        rows = block(self.csv('three'), 'Alerts')
        at = rows.index(ALERT_HEAD)
        shown = rows[at + 1:at + 1 + len(ms['alerts'])]
        self.assertEqual(shown, [[PRIORITY[a['level']], a['title'], a['detail'], a['try'], a['when'], a['base']] for a in ms['alerts']])
        self.assertEqual([r[0] for r in shown], sorted((r[0] for r in shown), key=['High priority', 'Medium priority', 'Good news'].index))
        self.assertEqual(rows[at + 1 + len(ms['alerts']):], [['Note', n['text']] for n in ms['notes'] if n['about'] == 'alerts'])

    def test_with_no_alerts_the_csv_says_that_is_not_an_all_clear(self):
        ms = self.ms('calm')
        self.assertEqual(ms['alerts'], [])
        rows = block(self.csv('calm'), 'Alerts')
        self.assertIn(['No alerts', ms['caveats']['empty']], rows)
        self.assertNotIn(ALERT_HEAD, rows)

    def test_the_measures_block_has_a_row_per_measure_a_row_per_part_and_the_health_score(self):
        ms = self.ms('three')
        m = ms['m']
        rows = block(self.csv('three'), 'Measures over time')
        self.assertIn(['Basis', ms['basis_text']], rows)
        self.assertEqual([c['name'] for c in ms['cards']], ['Escalation', 'Conflict', 'Empathy', 'Passive-aggressive'])
        cards = table_after(rows, MEASURE_HEAD)
        self.assertEqual(len(cards), 4)
        for card, row in zip(ms['cards'], cards):
            self.assertEqual(len(row), len(MEASURE_HEAD))
            self.assertEqual(row[:2], [card['name'], card['level']])
            self.assertEqual(row[2:4], ['' if card.get('count') is None else str(card['count']), str(card.get('of', ''))])
            self.assertEqual(row[4:6], [card['trend'], card['headline']])
            self.assertEqual(row[7:], [card['meaning'], card.get('latest', ''), card['tone_word']])         # the last cell is the mark, in words
        parts = table_after(rows, PARTS_HEAD)
        self.assertEqual([r[0] for r in parts], [s['label'] for s in m['stretches']])
        self.assertEqual([r[1] for r in parts], [str(s['n']) for s in m['stretches']])
        self.assertEqual([r[6] for r in parts], ['Yes' if s['counted'] else 'No' for s in m['stretches']])
        self.assertIn('Yes', [r[6] for r in parts])                                                    # the demo has a heated moment
        self.assertIn(['Health score', str(measures.whole(ms['health']['score'])), ms['health']['status'], ms['health']['tone_word']], rows)       # its mark in words last
        self.assertIn(['Health score trend', ms['health']['trend']], rows)
        self.assertIn(['About the health score', ms['health']['caption']], rows)

    def test_the_count_column_says_what_it_counts_and_the_latest_column_says_it_is_a_level(self):
        """Escalation counts heated moments and the other three count messages: the header says both. 'Latest part' is the level word of the latest part."""
        rows = block(self.csv('three'), 'Measures over time')
        head = next(r for r in rows if r[:1] == ['Measure'])
        self.assertEqual(head, MEASURE_HEAD)
        self.assertIn('messages', head[2])
        self.assertIn('heated moments', head[2])
        self.assertEqual(head[8], 'Level in the latest part')
        self.assertEqual(head[9], 'Mark')                                                              # the last column: the icon of the page, in words
        self.assertTrue({r[8] for r in table_after(rows, MEASURE_HEAD)} <= {'', 'Rare', 'Some', 'Often'})

    def test_the_group_size_is_printed_in_the_csv_and_the_pdf_when_checks_name_it(self):
        ctx = self.edited('three')
        ms = ctx['measures']
        ms['trail'] = [dict(r, members=m) for r, m in zip(ms['trail'], (200, 220, 300))]
        ms['members'] = measures.members_view(ms['trail'])
        self.assertIn('quarter or more', ms['members']['note'])
        import measures_export
        rows = measures_export.csv_rows(ms)
        self.assertIn(['Members', 'Members: 220 at your last check, 300 now (80 more).'], rows)
        self.assertIn(['Note', ms['members']['note']], rows)
        head = next(r for r in rows if r[:2] == ['Check', 'Date'])
        self.assertEqual(head[:5], ['Check', 'Date', 'Messages rated', 'Members', 'Health score'])
        self.assertEqual([r[3] for r in rows[rows.index(head) + 1:rows.index(head) + 4]], [200, 220, 300])
        data, _story, _seen = self.laid_out(ctx)
        text = squeeze(' '.join(our_pages(page_texts(data))))
        self.assertEqual(text.count(squeeze('Members: 220 at your last check, 300 now (80 more).')), 1)
        self.assertEqual(text.count(squeeze(ms['members']['note'])), 1)
        plain = measures_export.csv_rows(self.ms('three'))                                          # no members named: no column, no row
        self.assertNotIn(['Members'], [r[:1] for r in plain])
        self.assertEqual(next(r for r in plain if r[:2] == ['Check', 'Date'])[:4], ['Check', 'Date', 'Messages rated', 'Health score'])

    def test_a_part_that_could_not_be_judged_says_neither_yes_nor_no_in_the_csv(self):
        """Under 48 messages no heated moment is judged, so 'Counted as a heated moment' is empty: a 'No' would say that every part was judged and none counted."""
        ms = self.ms('thin')
        self.assertFalse(ms['m']['verdicts'])
        rows = block(self.csv('thin'), 'Measures over time')
        parts = table_after(rows, PARTS_HEAD)
        self.assertEqual(len(parts), len(ms['m']['stretches']))
        self.assertEqual({r[6] for r in parts}, {''})
        judged = table_after(block(self.csv('three'), 'Measures over time'), PARTS_HEAD)
        self.assertTrue({r[6] for r in judged} <= {'Yes', 'No'})

    def test_when_the_lines_follow_the_order_added_the_level_is_that_of_the_last_part_not_the_latest(self):
        ms = self.ms('undated')
        self.assertEqual(ms['m']['basis'], 'order')
        shown = [c for c in ms['cards'] if c.get('latest')]
        self.assertTrue(shown)
        self.assertEqual({c['latest_label'] for c in shown}, {'Last part'})
        head = next(r for r in block(self.csv('undated'), 'Measures over time') if r[:1] == ['Measure'])
        self.assertEqual(head[8], 'Level in the last part')
        data, texts = self.pdf('undated')
        text = ' '.join(our_pages(texts))
        for c in shown:
            self.assertIn(f'Last part: {c["latest"]}', text, c['key'])
        self.assertNotIn('Latest part', text)
        self.assertEqual({c['latest_label'] for c in self.ms('three')['cards'] if c.get('latest')}, {'Latest part'})

    def test_the_pdf_prints_the_level_of_the_latest_part_as_the_page_and_the_csv_do(self):
        ms = self.ms('three')
        shown = [c for c in ms['cards'] if c.get('latest')]
        self.assertEqual([c['key'] for c in shown], ['conflict', 'empathy', 'passive'])
        text = ' '.join(our_pages(self.pdf('three')[1]))
        for c in shown:
            self.assertIn(f'Latest part: {c["latest"]}', text, c['key'])
        self.assertNotIn('Latest part: None', text)

    def test_every_card_and_the_health_score_say_their_mark_in_words_in_the_csv(self):
        """The CSV cannot show the icon of the page. The last cell of each card row (the column Mark) and the fourth cell of the Health score row say it in the
        words of measures.py, so a reader can tell that Often is a good sign for Empathy and a look-at-this sign for Conflict."""
        import measures_export
        marks = set()
        for name in ('three', 'calm', 'few', 'thin', 'incognito'):
            ms = self.ms(name)
            rows = block(self.csv(name), 'Measures over time')
            cards = table_after(rows, MEASURE_HEAD)
            self.assertEqual([r[-1] for r in cards], [c['tone_word'] for c in ms['cards']], name)
            self.assertTrue({r[-1] for r in cards} <= set(measures.TONE_WORDS.values()), name)
            self.assertEqual([r for r in rows if r[:1] == ['Health score'] and len(r) == 4],
                             [['Health score', str(measures.whole(ms['health']['score'])), ms['health']['status'], ms['health']['tone_word']]], name)
            marks |= {r[-1] for r in cards} | {ms['health']['tone_word']}
        self.assertGreater(len(marks), 2)                                                              # the demos show several marks, so this is not an empty check
        ctx = self.edited('three')['measures']
        ctx['cards'][1]['tone_word'], ctx['health']['tone_word'] = 'Planted card mark', 'Planted tile mark'
        rows = measures_export.csv_rows(ctx)
        self.assertEqual(table_after(rows, MEASURE_HEAD)[1][-1], 'Planted card mark')
        self.assertIn(['Health score', measures.whole(ctx['health']['score']), ctx['health']['status'], 'Planted tile mark'], rows)
        for item in ctx['cards'] + [ctx['health']]:                                                    # a card without the words falls back to the words of its tone
            item.pop('tone_word')
        rows = measures_export.csv_rows(ctx)
        self.assertEqual([r[-1] for r in table_after(rows, MEASURE_HEAD)], [measures.TONE_WORDS[c['tone']] for c in ctx['cards']])
        self.assertIn(['Health score', measures.whole(ctx['health']['score']), ctx['health']['status'], measures.TONE_WORDS[ctx['health']['tone']]], rows)

    def test_a_card_that_could_not_be_judged_has_an_empty_count_in_the_csv(self):
        """Escalation reads Too early below 48 messages on the timeline, and then its count is None (not 0): a 0 in a spreadsheet would say 'no heated moments'.
        The cell is empty. A judged Escalation counts its heated moments, and the other three cards count messages."""
        import measures_export
        for name in ('few', 'thin'):                                                                    # no lines at all (10 messages), and lines but no verdicts (36)
            ms = self.ms(name)
            self.assertEqual(ms['cards'][0]['level'], 'Too early', name)
            self.assertIsNone(ms['cards'][0]['count'], name)
            cards = table_after(block(self.csv(name), 'Measures over time'), MEASURE_HEAD)
            self.assertEqual(cards[0][:4], ['Escalation', 'Too early', '', ''], name)
            self.assertEqual([r[2] for r in cards[1:]], [str(c['count']) for c in ms['cards'][1:]], name)    # messages are still counted
            rows = measures_export.csv_rows(ms)                                                        # and the rows of the module hold no 0 and no None there
            first = table_after(rows, MEASURE_HEAD)[0]
            self.assertEqual(first[2], '', name)
            self.assertNotIn(None, [c for row in rows for c in row], name)
        judged = table_after(block(self.csv('three'), 'Measures over time'), MEASURE_HEAD)[0]
        self.assertEqual(judged[2], str(self.ms('three')['cards'][0]['count']))
        self.assertNotEqual(judged[2], '')                                                              # judged: a number, even when it is 0

    def test_the_since_block_gives_last_this_and_the_verdict_and_the_check_by_check_table(self):
        """The counts the page writes as 'x of n' are two numbers each, so a spreadsheet can sort and add them; the health score is a number out of 100."""
        ms = self.ms('three')
        self.assertTrue(ms['since']['comparable'])
        rows = block(self.csv('three'), 'Since your last check')
        shown = table_after(rows, SINCE_HEAD)
        prev, cur = ms['comparison']['previous'], ms['record']
        expected = []
        for r in ms['since']['rows']:
            if r['label'] == 'Health score':
                numbers = [measures.whole(prev['health']), 100, measures.whole(cur['health']), 100]
            else:
                kind = SINCE_KINDS[r['label']]
                numbers = [prev[f'{kind}_n'], prev['n'], cur[f'{kind}_n'], cur['n']]
                self.assertEqual(f'{numbers[0]:,} of {numbers[1]:,}', r['before'])                       # the same figures as the page prints
                self.assertEqual(f'{numbers[2]:,} of {numbers[3]:,}', r['now'])
            expected.append([r['label'], *map(str, numbers), r['verdict']])
        self.assertEqual(shown, expected)
        self.assertTrue({'Better', 'Worse', 'About the same'} >= {r[5] for r in shown})
        trail = table_after(rows, TRAIL_HEAD)
        self.assertEqual([r[0] for r in trail], ['Check 1', 'Check 2', 'Check 3 (this check)'])
        self.assertEqual([r[2] for r in trail], [str(r['n']) for r in ms['trail']])
        self.assertEqual([r[3] for r in trail], [str(measures.whole(r['health'])) for r in ms['trail']])
        for row, record in zip(trail, ms['trail']):
            self.assertRegex(row[1], ISO_DAY)                                                          # YYYY-MM-DD, like the Vibe Check blocks of the same file
            self.assertEqual(row[1], record['created_at'][:10])
            self.assertNotRegex(row[1], CLOCK)

    def test_two_checks_that_are_not_alike_give_the_reason_and_no_verdict(self):
        ms = self.ms('unlike')
        self.assertFalse(ms['since']['comparable'])
        rows = block(self.csv('unlike'), 'Since your last check')
        self.assertIn(['No comparison', ms['since']['why']], rows)
        self.assertIn(SINCE_HEAD[:5], rows)
        self.assertEqual(rows[1], ['How to read this', ms['caveats']['since']])
        self.assertFalse(any('Change' in row or 'Better' in row or 'Worse' in row for row in rows))
        data, texts = self.pdf('unlike')
        text = ' '.join(our_pages(texts))
        self.assertIn(squeeze('No comparison. ' + ms['since']['why']), squeeze(text))
        self.assertIn(squeeze(ms['caveats']['since']), squeeze(text))
        self.assertNotIn('Better', text)
        self.assertNotIn('Change', text)

    def test_the_reason_two_checks_are_not_alike_is_printed_once_with_the_comparison(self):
        """It lives in ms['since']['why'] only (measures.py has no note for it any more), and every surface writes it once, in the Since your last check block:
        the CSV in one 'No comparison' row and no other cell, the PDF in one sentence. The same holds for the other reasons (a different model, too few messages)."""
        ms = self.ms('unlike')
        why = ms['since']['why']
        self.assertTrue(why)
        self.assertEqual(ms['comparison']['why'], why)
        self.assertNotIn('not_alike', [n['key'] for n in ms['notes']])
        self.assertFalse([n for n in ms['notes'] if why in n['text']])
        rows = self.csv('unlike')
        self.assertEqual([(row, cell) for row in rows for cell in row if why in cell], [(['No comparison', why], why)])
        self.assertIn(['No comparison', why], block(rows, 'Since your last check'))
        data, texts = self.pdf('unlike')
        self.assertEqual(squeeze(pdf_text(data)).count(squeeze(why)), 1)
        mine = our_pages(texts)
        since = next(t for t in mine if 'Sinceyourlastcheck' in squeeze(t))
        for t in mine:
            if t is not since:
                self.assertNotIn(squeeze(why), squeeze(t))                                              # not with the alerts, not with the badges
        self.assertLess(squeeze(since).index('Sinceyourlastcheck'), squeeze(since).index(squeeze(why)))
        for other in ('three', 'improved'):                                                            # checks that were made alike say nothing of the kind
            self.assertEqual(self.ms(other)['since']['why'], '')
            self.assertNotIn('No comparison', ' '.join(c for row in self.csv(other) for c in row))
            self.assertNotIn('Nocomparison', squeeze(' '.join(our_pages(self.pdf(other)[1]))))

    def test_the_badges_block_says_earned_or_not_yet_with_the_figure_or_what_is_missing(self):
        ms = self.ms('three')
        rows = block(self.csv('three'), 'Badges')
        shown = table_after(rows, BADGE_HEAD)
        self.assertEqual(len(shown), 7)
        for badge, row in zip(ms['badges'], shown):
            self.assertEqual(row, [badge['name'], 'Earned' if badge['earned'] else 'Not yet', badge['evidence'] if badge['earned'] else badge['needs'], badge['how']])
        self.assertIn('Earned', [r[1] for r in shown])
        self.assertIn('Not yet', [r[1] for r in shown])
        self.assertNotIn(['No badges', ms['texts']['no_badges']], rows)                                # some were earned: that sentence would be false
        few = block(self.csv('few'), 'Badges')
        self.assertIn(['Note', self.ms('few')['badge_note']], few)                                     # 'Badges start at 30 messages'
        self.assertEqual({r[1] for r in table_after(few, BADGE_HEAD)}, {'Not yet'})

    def test_when_no_badge_is_earned_the_csv_and_the_pdf_say_so_in_the_fixed_sentence(self):
        ms = self.ms('few')
        self.assertEqual(ms['badges_earned'], 0)
        self.assertEqual(ms['texts']['no_badges'], measures.NO_BADGES)
        rows = block(self.csv('few'), 'Badges')
        self.assertIn(['No badges', ms['texts']['no_badges']], rows)
        self.assertLess(rows.index(['No badges', ms['texts']['no_badges']]), rows.index(BADGE_HEAD))
        text = squeeze(' '.join(our_pages(self.pdf('few')[1])))
        self.assertEqual(text.count(squeeze(ms['texts']['no_badges'])), 1)
        self.assertNotIn(squeeze(ms['texts']['no_badges']), squeeze(' '.join(our_pages(self.pdf('three')[1]))))      # earned badges: the sentence is left out

    def test_a_first_check_says_so_under_since_your_last_check_in_the_pdf_and_the_csv(self):
        ms = self.ms('calm')
        self.assertIsNone(ms['since'])
        self.assertEqual(ms['texts']['first_check'], measures.FIRST_CHECK)
        rows = self.csv('calm')
        since = block(rows, 'Since your last check')
        self.assertEqual(since, [['Since your last check'], ['How to read this', ms['caveats']['since']], ['First check', ms['texts']['first_check']]])
        self.assertLess(rows.index(['Measures over time']), rows.index(['Since your last check']))
        self.assertLess(rows.index(['Since your last check']), rows.index(['Badges']))
        mine = our_pages(self.pdf('calm')[1])
        text = squeeze(' '.join(mine))
        self.assertEqual(text.count('Sinceyourlastcheck'), 1)
        self.assertEqual(text.count(squeeze(ms['texts']['first_check'])), 1)
        self.assertLess(text.index('Sinceyourlastcheck'), text.index(squeeze(ms['texts']['first_check'])))
        self.assertNotIn('MeasureLastcheckThischeck', text)                                           # no table of two checks yet
        page = next(t for t in mine if 'Sinceyourlastcheck' in squeeze(t))
        self.assertIn(f'{measures.whole(ms["health"]["score"])}outof100', squeeze(page))              # the sentence stays with the health score tile

    def test_numbers_in_the_csv_are_numbers(self):
        """The rows that `csv_rows` hands over hold ints where a cell is a figure, so a spreadsheet can add them up."""
        import measures_export
        ms = self.ms('three')
        rows = measures_export.csv_rows(ms)
        for row in rows:
            for cell in row:
                self.assertIsInstance(cell, (str, int, float), row)
        cards = table_after(rows, MEASURE_HEAD)
        self.assertEqual(len(cards), 4)
        for row in cards:
            self.assertIsInstance(row[2], int)
        self.assertEqual([r[3] for r in cards], ['', 72, 72, 72])                                       # Escalation counts heated moments, out of nothing
        parts = table_after(rows, PARTS_HEAD)
        self.assertEqual(len(parts), len(ms['m']['stretches']))
        for row in parts:
            self.assertTrue(all(isinstance(row[i], int) for i in (1, 2, 3, 4, 5, 7)), row)
            self.assertIn(row[6], ('Yes', 'No'))
        score = next(r for r in rows if r[:1] == ['Health score'] and len(r) == 4)
        self.assertEqual(score, ['Health score', measures.whole(ms['health']['score']), ms['health']['status'], ms['health']['tone_word']])
        since = table_after(rows, SINCE_HEAD)
        self.assertEqual(len(since), 5)
        for row in since:
            self.assertTrue(all(isinstance(row[i], int) for i in (1, 2, 3, 4)), row)                    # last check and this check, each as a count and an 'out of'
            self.assertIn(row[5], ('Better', 'Worse', 'About the same'))
        self.assertEqual(since[0][2], 100)                                                             # the health score is out of 100
        self.assertEqual(since[1][2], ms['comparison']['previous']['n'])
        trail = table_after(rows, TRAIL_HEAD)
        self.assertEqual(len(trail), 3)
        for row in trail:
            self.assertTrue(all(isinstance(x, int) for x in row[2:]), row)
            self.assertRegex(row[1], ISO_DAY)

    def test_a_count_the_store_had_to_drop_stays_the_words_not_known_in_the_since_table(self):
        """When a saved count was damaged the store leaves it out, and the table says 'Not known' for it (and calls no change). The CSV keeps those words in the
        cell of the count, with nothing out of it and no verdict, and the other rows stay numbers. The PDF prints the same words."""
        import measures_export
        ctx = self.edited('three')
        row = next(r for r in ctx['measures']['since']['rows'] if r['label'] == 'Conflict')
        row.update(before='Not known', verdict='')
        shown = table_after(measures_export.csv_rows(ctx['measures']), SINCE_HEAD)
        conflict = next(r for r in shown if r[0] == 'Conflict')
        now = measures_export._count_of(row['now'])
        self.assertEqual(conflict, ['Conflict', 'Not known', '', *now, ''])                             # the words, nothing out of them, this check's two numbers, no verdict
        for other in shown:
            if other is not conflict:
                self.assertTrue(all(isinstance(other[i], int) for i in (1, 2, 3, 4)), other)
        text = squeeze(' '.join(our_pages(page_texts(self.laid_out(ctx)[0]))))
        self.assertIn(squeeze(f'Conflict Not known {row["now"]}'), text)

    def test_no_cell_of_the_csv_can_run_as_a_formula(self):
        """report.csv_rows runs every cell through csv_safe, so a cell that starts with = + - or @ gets an apostrophe. The rows of this feature are no exception."""
        import report
        rows = measure_rows(self.csv('three'))
        self.assertTrue(rows)
        for row in rows:
            for cell in row:
                self.assertNotIn(cell[:1], ('=', '+', '-', '@', '\t', '\r'), row)
        ctx = self.edited('three')
        ctx['measures']['alerts'][0]['title'] = '=SUM(1+1)'
        ctx['measures']['badges'][0]['how'] = '-2+3'
        ctx['measures']['texts']['first_check'] = '+cmd'
        out = report.csv_rows(ctx)
        self.assertTrue(any(row[:2] == ['High priority', "'=SUM(1+1)"] for row in out))
        self.assertTrue(any(row[-1:] == ["'-2+3"] for row in out))

    def test_every_note_is_written_once_in_the_block_it_belongs_to(self):
        """A note says which section it is about: alerts under Alerts, measures under Measures over time. It is not written twice."""
        import measures_export
        ctx = self.edited('three')
        ctx['measures']['notes'] = [{'key': 'no_vibe', 'about': 'alerts', 'text': 'Zeta says something about the alerts.'},
                                    {'key': 'order', 'about': 'measures', 'text': 'Omega says something about the lines.'},
                                    {'key': 'planted', 'text': 'Sigma has no word for its section.'}]
        rows = measures_export.csv_rows(ctx['measures'])
        alerts, lines = block(rows, 'Alerts'), block(rows, 'Measures over time')
        self.assertEqual([r for r in alerts if r[:1] == ['Note']], [['Note', 'Zeta says something about the alerts.']])
        self.assertEqual([r for r in lines if r[:1] == ['Note']], [['Note', 'Omega says something about the lines.'], ['Note', 'Sigma has no word for its section.']])
        for note in ctx['measures']['notes']:
            self.assertEqual(sum(row == ['Note', note['text']] for row in rows), 1, note['key'])

    # -- the PDF -------------------------------------------------------------------------------------
    def test_the_pdf_has_the_alerts_and_badges_page_and_the_measures_page_after_the_cover(self):
        data, texts = self.pdf('three')
        ms = self.ms('three')
        whole_text = pdf_text(data)
        for needle in ('Alerts and badges', 'Measures over time', 'Since your last check', 'Earned', 'Not yet'):
            self.assertIn(needle, whole_text)
        self.assertNotRegex(texts[0], r'Alerts and badges Alerts are prompts')
        self.assertEqual(our_start(texts), 1)                                                          # right after the cover
        mine = our_pages(texts)
        self.assertIn('Alerts and badges', mine[0])
        self.assertTrue(any('Measures over time' in t for t in mine))
        text = squeeze(' '.join(mine))
        for alert in ms['alerts']:
            for field in ('title', 'detail', 'try', 'base'):
                self.assertIn(squeeze(alert[field]), text, alert['key'])
        for badge in ms['badges']:
            self.assertIn(squeeze(badge['name']), text)
            if badge['earned']:
                self.assertIn(squeeze(badge['evidence']), text)
        for key in ('alerts', 'measures', 'badges', 'since'):
            self.assertIn(squeeze(ms['caveats'][key]), text, key)
        self.assertIn(squeeze(ms['basis_text']), text)
        self.assertIn(squeeze(ms['chart_key']), text)                                                   # the sentence under the charts, as measures.py wrote it
        for card in ms['cards']:
            self.assertIn(squeeze(f'{card["name"]}: {card["level"]}'), text)
            self.assertIn(squeeze(card['headline']), text)
            self.assertIn(squeeze(card['meaning']), text)                                               # what the measure means, under its name
            if card['trend']:
                self.assertIn(squeeze(card['trend']), text)
        self.assertIn(squeeze(ms['health']['caption']), text)
        self.assertIn(f'{measures.whole(ms["health"]["score"])}outof100', text)
        for row in ms['since']['rows']:
            self.assertIn(squeeze(row['label']), text)
            self.assertIn(squeeze(row['verdict']), text)
        self.assertIn('Healthscore,checkbycheck', text)

    def test_the_tone_of_each_measure_is_said_in_words_after_its_level(self):
        """The web page marks a card with an icon. The PDF has none, so each card and the health tile say the tone in words, right after the level word."""
        ms = self.ms('three')
        text = squeeze(' '.join(our_pages(self.pdf('three')[1])))
        for card in ms['cards']:
            self.assertIn(card['tone_word'], measures.TONE_WORDS.values())
            self.assertIn(squeeze(f'{card["name"]}: {card["level"]} ({card["tone_word"]})'), text)
        h = ms['health']
        self.assertIn(squeeze(f'{h["name"]}: {h["status"]} ({h["tone_word"]})'), text)
        self.assertGreater(len({c['tone_word'] for c in ms['cards']} | {h['tone_word']}), 1)            # the demo shows more than one tone, so this is not an empty check

    def test_a_tone_word_is_escaped_like_every_other_string(self):
        """The tone word is set in its own small font by the paragraph helper, which escapes it first: markup in it is shown, never run."""
        ctx = self.edited('three')
        ctx['measures']['cards'][1]['tone_word'] = '<i>odd</i> & <font size="40">big</font>'
        ctx['measures']['health']['tone_word'] = '</para><b>x</b>'
        text = squeeze(' '.join(our_pages(page_texts(self.laid_out(ctx)[0]))))
        self.assertIn(squeeze('<i>odd</i> & <font size="40">big</font>'), text)
        self.assertIn(squeeze('</para><b>x</b>'), text)

    def test_the_caveats_come_first_on_their_pages(self):
        ms = self.ms('three')
        mine = our_pages(self.pdf('three')[1])
        self.assertTrue(squeeze(mine[0].split('Alerts and badges', 1)[1]).startswith(squeeze(ms['caveats']['alerts'])))
        page = next(t for t in mine if 'Measures over time' in t)
        self.assertTrue(squeeze(page.split('Measures over time', 1)[1]).startswith(squeeze(ms['caveats']['measures'])))

    def test_the_key_to_the_charts_is_the_sentence_of_measures_and_none_of_our_own(self):
        """ms['chart_key'] is the sentence for the four small charts on the page and in the PDF: this module has no wording of its own for it. It is printed once,
        with the charts, and only when charts are drawn. Without dates it says that the numbers under the charts are rated message numbers; with UTC times it says
        that the times are UTC (the CSV names its parts in UTC too, and the PDF had no way to say so)."""
        keys = {}
        for name in ('three', 'undated', 'utc', 'calm', 'incognito', 'thin'):
            ms = self.ms(name)
            keys[name] = ms['chart_key']
            self.assertTrue(ms['chart_key'].startswith('Lines show messages per 100. Shaded bands show where Rare, Some and Often begin.'), name)
            self.assertEqual(squeeze(' '.join(our_pages(self.pdf(name)[1]))).count(squeeze(ms['chart_key'])), 1, name)
        self.assertEqual(self.ms('undated')['m']['basis'], 'order')
        self.assertTrue(self.ms('utc')['m']['utc'])                                                    # the scenarios do hold what they are named for
        self.assertIn('rated message numbers', keys['undated'])
        self.assertIn('Times are UTC.', keys['utc'])
        for other in ('three', 'calm', 'incognito', 'thin'):
            self.assertNotIn('Times are UTC.', keys[other], other)
            self.assertNotIn('rated message numbers', keys[other], other)
        ctx = self.edited('three')                                                                     # the words are never our own: change them and the PDF follows
        ctx['measures']['chart_key'] = 'Zeta key sentence <i>from</i> measures & <font size="40">more</font>.'                  # (and escaped like every other string)
        text = squeeze(' '.join(our_pages(page_texts(self.laid_out(ctx)[0]))))
        self.assertEqual(text.count(squeeze('Zeta key sentence <i>from</i> measures & <font size="40">more</font>.')), 1)
        self.assertNotIn('Linesshowmessagesper100', text)
        self.assertNotIn('Bandsmark', text)                                                            # nor the older line of this module
        ctx = self.edited('three')
        del ctx['measures']['chart_key']                                                               # no key from measures.py: none is made up
        self.assertNotIn('Linesshowmessagesper100', squeeze(' '.join(our_pages(page_texts(self.laid_out(ctx)[0])))))
        few = self.ms('few')                                                                           # no charts, no key to them
        self.assertFalse(few['m']['has_series'])
        self.assertNotIn(squeeze(few['chart_key']), squeeze(' '.join(our_pages(self.pdf('few')[1]))))

    def test_every_page_after_the_cover_says_the_wording_was_rated_by_a_model(self):
        data, texts = self.pdf('three')
        self.assertGreater(len(texts), 3)
        self.assertNotIn(FOOTER, texts[0])
        for number, text in enumerate(texts[1:], 2):
            self.assertIn(FOOTER, text, f'page {number}')
        self.assertTrue(all(FOOTER in text for text in self.pdf('few')[1][1:]))

    def test_no_symbol_in_the_pdf_turns_into_a_question_mark(self):
        """The standard PDF fonts cannot draw a tick, a minus sign or a greater-than-or-equal sign: each would come out as a question mark. The only question
        marks on these pages are the ones inside the sentences of measures.py (a next step asks 'What helped you this week?')."""
        for name in ('three', 'fading', 'calm', 'few'):
            text = squeeze(' '.join(our_pages(self.pdf(name)[1])))
            for known in sorted((squeeze(s) for s in strings(self.ms(name)) if '?' in s), key=len, reverse=True):
                text = text.replace(known, '')
            self.assertNotIn('?', text, name)
        self.assertIn('empathy', [a['key'] for a in self.ms('fading')['alerts']])                       # the alert with the question in its next step is there
        self.assertIn(squeeze("such as 'What helped you this week?', and answer"), squeeze(' '.join(our_pages(self.pdf('fading')[1]))))
        for symbol in ('✓', '✔', '≥', '≤', '−', '→', '·'):
            for name in ('three', 'fading'):
                self.assertNotIn(symbol, ' '.join(our_pages(self.pdf(name)[1])))
            self.assertFalse(any(symbol in c for row in measure_rows(self.csv('three')) for c in row))

    def test_the_question_mark_check_does_catch_a_symbol(self):
        """The control for the test above: put a greater-than-or-equal sign into an alert and the PDF does print a question mark, which that test would find."""
        import report_export
        ctx = self.edited('three')
        ctx['measures']['alerts'][0]['detail'] += ' (≥ 5 in 100)'
        text = ' '.join(our_pages(page_texts(report_export.pdf(ctx, ROOT))))
        self.assertIn('(? 5 in 100)', text)

    def test_the_pages_use_plain_words_and_none_of_the_jargon(self):
        data, texts = self.pdf('three')
        self.assertRegex(pdf_text(data), r'\bFAS\b')                                                    # the older pages of the report do use it: only ours are checked
        for name in ('three', 'fading', 'few', 'calm'):
            text = ' '.join(our_pages(self.pdf(name)[1]))
            for word in BANNED:
                self.assertNotRegex(text, word, name)
        rows = ' '.join(str(c) for name in ('three', 'calm', 'few') for row in measure_rows(self.csv(name)) for c in row)
        for word in BANNED:
            self.assertNotRegex(rows, word)

    def test_every_table_and_every_drawing_of_the_whole_report_fits_the_text_column(self):
        """The page frame keeps 6 points of padding on each side of the 504 points between the margins, so the text is 492 points wide. A table or a drawing wider
        than that ran 6 points past the right margin and the footer rule."""
        import report_export
        from reportlab.graphics.shapes import Drawing
        from reportlab.platypus import Table
        self.assertEqual(report_export.TEXT_W, 492)
        _data, story, _seen = self.laid_out(self.context('three'))
        tables = [f for top in story for f in inside(top) if isinstance(f, Table)]
        drawings = [f for top in story for f in inside(top) if isinstance(f, Drawing)]
        self.assertGreater(len(tables), 8)                                                             # ours, the older tables of the report, and Vibe Check's
        self.assertGreater(len(drawings), 8)
        for t in tables:
            self.assertLessEqual(sum(t._colWidths), 492.01, t)
        for d in drawings:
            self.assertLessEqual(d.width, 492.01)

    def test_tables_and_charts_fit_the_page_and_the_four_small_charts_sit_two_by_two(self):
        """Every table of this feature is 492 points wide at most (the text between the margins), and each of the four small charts fills its half of that and no
        more. The small charts are drawn smaller than their natural size (CHART_SHRINK), so that a first check fits one page: lower, not narrower."""
        import measures_export
        import report_export
        ctx = self.context('three')
        seen = []
        real = measures_export.pdf_pages

        def spy(ms, story, p, page, table, drawing, small=None):
            def table_of(rows, widths):
                seen.append(('table', sum(widths), len(rows[0]), len(widths)))
                return table(rows, widths)

            def drawing_of(scene):
                d = drawing(scene)
                seen.append(('drawing', d, scene.title))                                              # the drawing itself: it may still be scaled, so its size is read after the build
                return d
            return real(ms, story, p, page, table_of, drawing_of, small)
        with mock.patch.object(measures_export, 'pdf_pages', spy):
            self.assertTrue(report_export.pdf(ctx, ROOT).startswith(b'%PDF'))
        tables = [s for s in seen if s[0] == 'table']
        self.assertEqual(len(tables), 3)                                                               # alerts, badges, since
        for _kind, total, cells, columns in tables:
            self.assertEqual(total, 492)
            self.assertEqual(cells, columns)
        for widths in (measures_export.ALERT_WIDTHS, measures_export.BADGE_WIDTHS, measures_export.SINCE_WIDTHS, measures_export.SINCE_WIDTHS_NO_CALL):
            self.assertEqual(sum(widths), 492)
        self.assertEqual(2 * measures_export.CARD_W + measures_export.GUTTER, 492)
        self.assertGreaterEqual(measures_export.ALERT_WIDTHS[4], 64)                                   # the Where column has room for a day and a month
        charts = [(s[1].width, s[1].height, s[2]) for s in seen if s[0] == 'drawing' and s[2] != 'scale']        # (one empty scene is drawn to learn the scale)
        small = [c for c in charts if c[2].endswith('over time')]
        self.assertEqual(len(small), 4)
        for width, height, _title in small:
            self.assertAlmostEqual(width, measures_export.CARD_W, delta=0.5)                           # as wide as its half ...
            self.assertLessEqual(width, measures_export.CARD_W + 0.01)
            self.assertLessEqual(height, 100)                                                          # ... and lower than the 115 points of a drawing at natural size
        for width, _height, _title in charts:
            self.assertLessEqual(width, 492.01)
        meter = [c for c in charts if c[2] == 'Health score']
        self.assertEqual(len(meter), 1)
        self.assertLessEqual(meter[0][0], 492 - measures_export.TILE_NUMBER_W)                         # the meter fits the right half of the tile
        self.assertGreater(meter[0][0], 360)                                                           # and uses it
        self.assertEqual(len([c for c in charts if c[2] == 'Health score, check by check']), 1)

    def test_no_heading_or_caveat_is_left_alone_on_a_page(self):
        """A caveat or a heading set after a table, or at the foot of a page, can land alone on the next page. Each one has to share its page with what it
        belongs to: the badges caveat with the start of the badges table, 'Since your last check' with its table or its sentence, the health score tile with the
        cards or with the comparison, the check-by-check heading with its chart."""
        for name in ('three', 'improved', 'calm', 'few', 'fading', 'unlike', 'incognito'):
            ms = self.ms(name)
            for number, text in enumerate(our_pages(self.pdf(name)[1])):
                flat = squeeze(text)
                where = f'{name}, page {number}'
                if squeeze(ms['caveats']['badges']) in flat:
                    self.assertIn('Whyorwhatitneeds', flat, where)
                    self.assertTrue('Earned' in flat or 'Notyet' in flat, where)                        # and at least one row of the table
                if 'Sinceyourlastcheck' in flat:
                    self.assertTrue('MeasureLastcheckThischeck' in flat or squeeze(ms['texts']['first_check']) in flat, where)
                if 'Healthscore,checkbycheck' in flat:
                    self.assertIn('Check1Check2Check3', flat, where)
                if f'{measures.whole(ms["health"]["score"])}outof100' in flat and squeeze(f'{ms["health"]["name"]}: {ms["health"]["status"]}') in flat:      # the tile
                    self.assertIn('Passive-aggressive:', flat, where)                                  # the last row of cards is on its page: the tile never starts one
                if 'Alertsandbadges' not in flat:
                    self.assertNotIn(squeeze(ms['caveats']['alerts']), flat, where)             # the first caveat is on the page with its heading, never apart from it

    def test_nothing_small_trails_a_table(self):
        """The structure behind the test above: a note or a caveat that follows a table can land alone at the top of the next page, so none of the tables of
        these pages (nor a group that holds one) is followed by a paragraph of body, caveat or small type."""
        import measures_export
        import report_export
        from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table
        real = measures_export.pdf_pages
        seen = {}

        def spy(ms, story, p, page, table, drawing, small=None):
            before = len(story)
            real(ms, story, p, page, table, drawing, small)
            seen['story'] = list(story[before:])

        def holds_table(flowable):
            return isinstance(flowable, Table) or (isinstance(flowable, KeepTogether) and any(isinstance(c, Table) for c in flowable._content))
        for name in ('three', 'improved', 'calm', 'few', 'unlike'):
            with mock.patch.object(measures_export, 'pdf_pages', spy):
                report_export.pdf(self.context(name), ROOT)
            story = seen['story']
            self.assertGreater(len(story), 8, name)
            tables = 0
            for i, flowable in enumerate(story):
                if holds_table(flowable):
                    tables += 1
                    after = next((g for g in story[i + 1:] if not isinstance(g, Spacer)), None)             # (a CondPageBreak is a kind of Spacer)
                    if isinstance(after, Paragraph):
                        self.assertNotIn(after.style.name, ('BeiBody', 'BeiSmall', 'BeiCaveat'), f'{name}: a paragraph trails a table')
            self.assertGreaterEqual(tables, 3, name)

    def test_the_alerts_heading_sits_behind_a_conditional_break_and_does_not_glue_itself_to_the_table(self):
        """A heading that keeps with the next flowable is joined by reportlab to the table after it into one block, and a block that does not fit jumps to the next
        page whole, leaving the page of the caveat empty. The Alerts and Badges headings keep with nothing; a CondPageBreak sized from the heading and the first
        rows of the table stands before each, and the table splits between its rows."""
        import measures_export
        import report_export
        from reportlab.platypus import CondPageBreak, Paragraph, Table
        real = measures_export.pdf_pages
        seen = {}

        def spy(ms, story, p, page, table, drawing, small=None):
            before = len(story)
            real(ms, story, p, page, table, drawing, small)
            seen['story'] = list(story[before:])
        for name in ('three', 'calm', 'few'):
            with mock.patch.object(measures_export, 'pdf_pages', spy):
                report_export.pdf(self.context(name), ROOT)
            story = seen['story']
            for title in ('Alerts', 'Badges'):
                at = next(i for i, f in enumerate(story) if isinstance(f, Paragraph) and text_of(f) == title)
                self.assertEqual(story[at].style.name, 'BeiSubFree', f'{name}: {title}')
                self.assertFalse(story[at].getKeepWithNext(), f'{name}: {title}')
                self.assertIsInstance(story[at - 1], CondPageBreak, f'{name}: {title}')
                self.assertGreater(story[at - 1].height, 40, f'{name}: {title}')                         # more than the heading alone
            badges_at = next(i for i, f in enumerate(story) if isinstance(f, Paragraph) and text_of(f) == 'Badges')
            table_at = next(i for i, f in enumerate(story) if i > badges_at and isinstance(f, Table))
            table = story[table_at]
            self.assertEqual(list(table._colWidths), measures_export.BADGE_WIDTHS)
            self.assertEqual(table.repeatRows, 1)                                                      # a table that runs over a page repeats its header
            self.assertAlmostEqual(story[badges_at - 1].height, measures_export.room_for(story[badges_at:table_at + 1]), places=3)      # the room of the whole block ...
            self.assertGreater(story[badges_at - 1].height, table.wrap(492, 10_000)[1] + 40, name)    # ... all of its table and its lead, not the first rows

    def test_a_list_of_alerts_that_is_too_tall_for_the_room_under_the_caveat_starts_on_that_page(self):
        """The Alerts heading and the table used to jump together to the next page, which left the first page with a title and a caveat only."""
        ctx = self.edited('three')
        alerts = [dict(ctx['measures']['alerts'][0], key=f'a{i}', title=f'Alert number {i}') for i in range(6)]
        ctx['measures']['alerts'], ctx['measures']['notes'] = alerts, []
        data, story, seen = self.laid_out(ctx)
        mine = our_pages(page_texts(data))
        self.assertIn('Alert number 0', mine[0])                                                       # on the page of the heading and the caveat
        self.assertIn(squeeze('Alerts and badges'), squeeze(mine[0]))
        shown = [t for t in mine if 'Alert number' in t]
        self.assertGreaterEqual(len(shown), 2)                                                         # six of them do not fit on one page: the table splits
        for t in shown:
            self.assertIn('PriorityAlertWhatwesaw', squeeze(t))                                         # and its header comes again on each page
        text = squeeze(' '.join(mine))
        for i in range(6):
            self.assertEqual(text.count(f'Alertnumber{i}'), 1)

    def test_the_badges_are_one_block_that_starts_where_it_fits_whole_and_never_splits(self):
        """The badges (heading, caveat, notes and the table of seven rows) start on the page where all of them fit, else on the next page. A table of seven rows
        has no good place to break (at least 5 rows before the break and more than 3 after it would need 9), and a split used to leave its last three rows alone on
        a page a fifth full. So the table is never in two parts, and every one of the seven badges is printed once, under its header, on the page of its heading."""
        import measures_export
        from reportlab.platypus import Paragraph, Table
        base = self.edited('three')
        starts_here = starts_next = 0
        for count in (1, 2, 3, 4, 5, 6):
            for notes in (0, 2, 5):
                ctx = copy.deepcopy(base['measures'])
                ctx['alerts'] = [dict(ctx['alerts'][0], key=f'a{i}', title=f'Alert number {i}') for i in range(count)]
                ctx['notes'] = [{'key': f'n{i}', 'about': 'alerts', 'text': f'Quiet line number {i} about the alerts.'} for i in range(notes)]
                _data, story, seen = self.laid_out(dict(base, measures=ctx))
                alert_parts = [(page, room) for page, room, f in seen if isinstance(f, Table) and list(f._colWidths) == measures_export.ALERT_WIDTHS]
                heading = next(page for page, _room, f in seen if isinstance(f, Paragraph) and text_of(f) == 'Badges' and f.style.name == 'BeiSubFree')
                at = next(i for i, f in enumerate(story) if isinstance(f, Paragraph) and text_of(f) == 'Badges' and f.style.name == 'BeiSubFree')
                need = story[at - 1].height                                                              # what the conditional break before the heading asks for
                page, room = alert_parts[-1]
                where = f'{count} alerts, {notes} notes, {room:.0f} points left under them, {need:.0f} asked for'
                self.assertEqual(heading, page if room >= need else page + 1, where)                    # on that page with the room for all of it, else on the next
                if room >= need:
                    starts_here += 1
                else:
                    starts_next += 1
                parts = [(page, f) for page, _room, f in seen if isinstance(f, Table) and list(f._colWidths) == measures_export.BADGE_WIDTHS]
                self.assertEqual(len(parts), 1, where)                                                   # one table, never in two parts
                self.assertEqual(len(parts[0][1]._cellvalues) - 1, 7, where)                             # every one of the seven badges once, under its header
                self.assertEqual(parts[0][0], heading, where)                                            # on the page of its heading, with its caveat
        self.assertGreater(starts_here, 0)                                                              # the demo covers both kinds of case
        self.assertGreater(starts_next, 0)

    def test_a_table_that_runs_over_a_page_never_leaves_a_header_over_a_single_row(self):
        """The header of a table comes again on the next page. It must not come again over one last row: the last rows of a table (up to three) stay together. Swept
        across every place where the page can end in a table of seven rows."""
        import io as _io
        import measures_export
        import report_export
        from reportlab.platypus import SimpleDocTemplate, Spacer, Table
        made = {}

        def spy(ms, story, p, page, table, drawing, small=None):
            made['table'], made['story'] = table, story
        with mock.patch.object(measures_export, 'pdf_pages', spy):
            report_export.pdf(self.context('three'), ROOT)
        rule = 'A rule that takes a line, or two lines when it has more to say about what the badge needs from the chat. ' * 2
        rows = [['Badge', 'Status', 'Why or what it needs']] + [[f'Badge {i}', 'Not yet', rule[:60 + 37 * (i % 3) * 2]] for i in range(7)]
        made['story'].clear()
        made['table'](rows, measures_export.BADGE_WIDTHS)
        height = made['story'][0].wrap(492, 10_000)[1]
        ends = set()
        for filler in range(int(666 - height), 666 - 20, 3):                                            # the page ends in the first row, then in the second, and so on
            parts = []

            def after(doc, flowable):
                if isinstance(flowable, Table) and flowable._colWidths == measures_export.BADGE_WIDTHS:
                    parts.append(len(flowable._cellvalues))
            made['story'].clear()
            made['table'](rows, measures_export.BADGE_WIDTHS)
            with mock.patch.object(SimpleDocTemplate, 'afterFlowable', after):
                SimpleDocTemplate(_io.BytesIO(), pagesize=(612, 792), rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=60).build([Spacer(1, filler)] + list(made['story']))
            self.assertEqual(sum(n - 1 for n in parts), 7, filler)
            if len(parts) > 1:
                ends.add(parts[-1] - 1)
                for n in parts[1:]:
                    self.assertGreaterEqual(n - 1, 3, f'filler {filler}: parts of {parts}')               # three rows at least under a repeated header
        self.assertGreater(len(ends), 1)                                                                # the sweep did put the end of the table in several places

    def test_the_health_score_tile_never_starts_a_page_and_a_first_check_fits_one_page(self):
        """The tile is set with the last row of cards, in one block: when it does not fit under the first row, the second row of cards and the tile go to the next
        page together, so that no page starts with the tile (a page a fifth full, with the tile and one sentence, was the usual look of a first check). On a first
        check 'Since your last check' and its sentence follow in the same block. With no notes (or one) the whole section of a first check, its caveat, the four
        cards with their charts, the tile and the sentence, fits one page. After the first check the comparison follows the tile, on its page when it fits."""
        import measures_export
        from reportlab.platypus import Paragraph, Table
        extra = [{'key': f'x{i}', 'about': 'measures', 'text': f'Quiet line number {i} about the basis of the figures, long enough to take a line or two. ' * 2} for i in range(6)]
        tile_widths = [measures_export.TILE_NUMBER_W, measures_export.PAGE_W - measures_export.TILE_NUMBER_W]
        card_widths = [measures_export.CARD_W, measures_export.GUTTER, measures_export.CARD_W]
        one_page = split = 0
        for name in ('three', 'calm', 'few', 'improved', 'unlike', 'undated', 'thin'):
            first_check = self.ms(name)['since'] is None
            for with_notes in (False, True):
                ctx = self.edited(name)
                if with_notes:
                    ctx['measures']['notes'] = ctx['measures']['notes'] + extra
                _data, _story, seen = self.laid_out(ctx)

                def page_of(test):
                    return next((page for page, _room, f in seen if any(test(x) for x in inside(f))), None)
                tile = page_of(lambda x: isinstance(x, Table) and list(x._colWidths) == tile_widths)
                heading = page_of(lambda x: isinstance(x, Paragraph) and text_of(x) == 'Measures over time')
                rows = [page for page, _room, f in seen for x in inside(f) if isinstance(x, Table) and list(x._colWidths) == card_widths]
                since = page_of(lambda x: isinstance(x, Paragraph) and text_of(x) == 'Since your last check')
                where = f'{name}, {"with" if with_notes else "without"} six more notes'
                self.assertIsNotNone(tile, where)
                self.assertIsNotNone(since, where)
                self.assertEqual(tile, rows[-1], where)                                                # the last row of cards is on the page of the tile, above it
                firsts = {}
                for page, _room, f in seen:                                                             # the first thing that is laid out on each page
                    if type(f).__name__ in ('Paragraph', 'Table', 'KeepTogether'):
                        firsts.setdefault(page, f)
                for page, f in firsts.items():
                    first_table = next((x for x in inside(f) if isinstance(x, Table)), None)
                    self.assertFalse(first_table is not None and list(first_table._colWidths) == tile_widths, f'{where}: page {page} starts with the tile')
                if first_check:
                    self.assertEqual(tile, since, where)                                               # a first check: the sentence follows the tile on its page
                    if not with_notes and name in ('calm', 'few', 'undated', 'thin'):
                        self.assertEqual(heading, tile, where)                                         # with no notes (or the one of the basis), it is all on one page
                        one_page += 1
                    split += heading != tile
                else:
                    self.assertIn(since, (tile, tile + 1), where)                                      # the comparison follows on its page, else it starts the next
        self.assertEqual(one_page, 4)
        self.assertGreater(split, 0)                                                                    # with six more notes the section did run over, and the tile went with its cards

    def test_many_notes_are_each_printed_once(self):
        """Eight notes, half about the alerts and half about the measures: the pages that carry them still hold together, and no note is lost or repeated."""
        ctx = self.edited('three')
        ctx['measures']['notes'] = [{'key': f'n{i}', 'about': 'alerts' if i % 2 else 'measures', 'text': f'Note number {i} says one thing about the basis of the figures.'} for i in range(8)]
        data, _story, _seen = self.laid_out(ctx)
        text = squeeze(' '.join(our_pages(page_texts(data))))
        for i in range(8):
            self.assertEqual(text.count(f'Notenumber{i}says'), 1, i)

    def test_every_note_is_printed_once_in_the_pdf_in_the_section_it_is_about(self):
        ctx = self.edited('three')
        ctx['measures']['notes'] = [{'key': 'no_vibe', 'about': 'alerts', 'text': 'Zeta says something about the alerts.'},
                                    {'key': 'order', 'about': 'measures', 'text': 'Omega says something about the lines.'},
                                    {'key': 'planted', 'text': 'Sigma has no word for its section.'}]
        data, _story, _seen = self.laid_out(ctx)
        mine = our_pages(page_texts(data))
        at = next(i for i, t in enumerate(mine) if squeeze('Measures over time' + ctx['measures']['caveats']['measures'][:30]) in squeeze(t))
        before, after = squeeze(' '.join(mine[:at])), squeeze(' '.join(mine[at:]))
        self.assertEqual(before.count('Zetasays'), 1)
        self.assertEqual(after.count('Zetasays'), 0)                                                   # an alerts note is not repeated with the measures
        for needle in ('Omegasays', 'Sigmahas'):
            self.assertEqual(after.count(needle), 1, needle)
            self.assertEqual(before.count(needle), 0, needle)                                          # nor a measures note with the alerts

    def test_the_where_column_never_breaks_a_date_in_two(self):
        """'(Oct 14 to Oct 17)' is set with a no-break space inside each date, so a narrow column wraps between the dates and not inside one."""
        import measures_export
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph
        when = 'about two thirds of the way in (Oct 14 to Oct 17)'
        kept = measures_export._keep_dates(when)
        self.assertEqual(kept, 'about two thirds of the way in (Oct\u00a014 to Oct\u00a017)')
        self.assertEqual(measures_export._keep_dates('about a third of the way in'), 'about a third of the way in')
        self.assertEqual(measures_export._keep_dates(None), '')
        style = ParagraphStyle('x', fontName='Helvetica', fontSize=8.5, leading=12)
        width = measures_export.ALERT_WIDTHS[4] - 16                                                    # the column, less its padding

        def lines(text):
            para = Paragraph(text, style)
            para.wrap(width, 1000)
            return [' '.join(getattr(line, 'words', None) or line[1]) if not isinstance(line, str) else line for line in para.blPara.lines]
        for line in lines(kept):
            self.assertNotRegex(line.strip(), MONTH_END, line)                                         # no line ends with a month and leaves its day for the next
        self.assertTrue(any(MONTH_END.search(line.strip()) for line in lines(when)), lines(when))       # the control: with plain spaces a line does end with a month
        data, _story, _seen = self.laid_out(self.context('three'))
        import pypdf
        for page in pypdf.PdfReader(io.BytesIO(data)).pages[1:3]:
            for line in page.extract_text().splitlines():
                self.assertNotRegex(line.strip(), MONTH_END, line)

    def test_the_bands_behind_the_level_words_follow_the_sensitivity(self):
        """The faint bands of a chart are the cut-offs of Rare, Some and Often: measures.CUTS, with Conflict and Passive-aggressive raised at Low sensitivity."""
        import measures_export
        self.assertEqual(measures_export._bands({'kind': 'hostile'}, {'scale': 1.0}), (5, 12))
        self.assertEqual(measures_export._bands({'kind': 'hostile'}, {'scale': 1.5}), (7.5, 18.0))
        self.assertEqual(measures_export._bands({'kind': 'dig'}, {'scale': 1.5}), (7.5, 18.0))
        self.assertEqual(measures_export._bands({'kind': 'care'}, {'scale': 1.5}), (3, 10))             # care is not raised
        self.assertIsNone(measures_export._bands({'kind': 'heated'}, {'scale': 1.0}))                   # Escalation shades its heated moments instead

    def test_a_first_check_has_no_comparison_and_no_check_by_check_chart(self):
        ms = self.ms('calm')
        self.assertIsNone(ms['since'])
        text = ' '.join(our_pages(self.pdf('calm')[1]))
        self.assertNotIn('check by check', text)
        self.assertNotIn('Last check', text)                                                            # no table of two checks
        self.assertNotIn(['Check'], self.csv('calm'))
        self.assertNotIn(SINCE_HEAD, self.csv('calm'))
        self.assertIn(squeeze(ms['caveats']['empty']), squeeze(text))                                    # no alerts: the sentence that says it is not an all clear
        self.assertGreaterEqual(len([b for b in ms['badges'] if b['earned']]), 1)
        self.assertIn('Earned', text)

    def test_with_too_few_messages_there_are_no_charts_and_the_pages_say_why(self):
        ms = self.ms('few')
        self.assertFalse(ms['m']['has_series'])
        text = squeeze(' '.join(our_pages(self.pdf('few')[1])))
        for note in ms['notes']:
            self.assertIn(squeeze(note['text']), text)
        self.assertIn(squeeze(ms['cards'][0]['headline']), text)                                       # 'Needs at least 24 messages ...'
        self.assertIn(squeeze(f'{ms["cards"][0]["name"]}: {ms["cards"][0]["level"]} ({ms["cards"][0]["tone_word"]})'), text)      # 'Too early', in the neutral tone
        self.assertIn(squeeze(ms['badge_note']), text)
        self.assertFalse(any(row[:1] == ['Part of the chat'] for row in self.csv('few')))
        self.assertNotIn(squeeze(ms['chart_key']), text)                                               # no charts, so no key to them
        self.assertNotIn('Bandsmark', text)

    def test_a_long_list_of_long_alerts_still_fits_and_nothing_is_lost(self):
        """Eight alerts with the longest wording the rules write, and then some: the table runs over to further pages, no row is cut, the header comes again on
        each page, and the badges are printed once, with their header on each page they run over."""
        ctx = self.edited('three')
        longest = max(ctx['measures']['alerts'], key=lambda a: len(a['try']))
        alerts = [{**longest, 'title': f'Alert number {i} with a rather long title that wraps over several lines', 'level': ('high', 'medium', 'good')[i % 3],
                   'detail': longest['detail'] * 2, 'try': longest['try'] * 2, 'when': 'about two thirds of the way in (May 5 to May 6)'} for i in range(8)]
        ctx['measures']['alerts'] = alerts
        ctx['measures']['notes'] = [{'key': 'a', 'text': 'A quiet line about the basis of the figures. ' * 6, 'about': 'alerts'}, {'key': 'b', 'text': 'Another quiet line.', 'about': 'measures'}]
        data, _story, _seen = self.laid_out(ctx)
        mine = our_pages(page_texts(data))
        self.assertGreaterEqual(len(mine), 4)                                                          # the alerts alone take more than a page
        text = squeeze(' '.join(mine))
        for alert in alerts:
            self.assertIn(squeeze(alert['title']), text)
            self.assertIn(squeeze(alert['try']), text)
        for note in ctx['measures']['notes']:
            self.assertEqual(text.count(squeeze(note['text'])), 1)
        for t in mine:
            if 'Alertnumber' in squeeze(t):
                self.assertIn('PriorityAlertWhatwesaw', squeeze(t))
            if 'Notyet' in squeeze(t) or 'Earned' in squeeze(t):
                self.assertIn('Whyorwhatitneeds', squeeze(t))
        for badge in ctx['measures']['badges']:
            self.assertEqual(text.count(squeeze(badge['evidence'] if badge['earned'] else badge['how'])), 1, badge['key'])      # the figure of an earned badge, the rule of the others

    def test_a_health_score_of_zero_is_still_printed(self):
        """`text or ''` would drop a 0: a health score of 0 out of 100 has to show."""
        import report_export
        ctx = self.edited('three')
        ctx['measures']['health'].update(score=0.0, status='Needs attention', tone='bad', tone_word=measures.TONE_WORDS['bad'])
        text = squeeze(' '.join(our_pages(page_texts(report_export.pdf(ctx, ROOT)))))
        self.assertIn('0outof100', text)
        self.assertIn(squeeze('Health score: Needs attention (Look at this first)'), text)

    def test_the_model_line_sits_under_the_evidence_and_never_alone_on_the_last_page(self):
        """'Model: ...' came after the method text and, on a full page, was left alone on a last page of its own."""
        ctx = self.edited('three')
        ctx['a'] = dict(ctx['a'], model_used='qwen2.5:7b')
        data, _story, _seen = self.laid_out(ctx)
        texts = page_texts(data)
        flat = ' '.join(texts)
        self.assertIn('Evidence loaded', flat)
        self.assertLess(flat.index('Evidence loaded'), flat.index('Model: qwen2.5:7b.'))
        self.assertLess(flat.index('Model: qwen2.5:7b.'), flat.index('How this report works'))
        self.assertGreater(len(texts[-1].replace(FOOTER, '').split()), 40)                              # the last page holds more than a line or two
        import analysis
        for heading, text in analysis.HOW_IT_WORKS:                                                     # every entry of the method is whole on one page
            holders = [t for t in texts if squeeze(text) in squeeze(t)]
            self.assertEqual(len(holders), 1, heading)
        self.assertIn('Measures, alerts and badges', flat)

    def test_the_method_is_one_block_and_its_last_entry_never_lands_alone_on_a_page(self):
        """The Evidence page held the evidence, the Model line and the five entries of the method with 29 points to spare, so that with two or more sources the
        fifth entry ('Measures, alerts and badges', eight lines) landed alone on a last page a fifth full. The method is set a little tighter now, so that up to four
        sources fit with 40 points to spare, and it is one block: it stays under the evidence when all of it fits, else it starts the next page whole (and the
        evidence page, with its table, is then about half full)."""
        import analysis
        base = self.edited('three')
        base['a'] = dict(base['a'], model_used='qwen2.5:7b')
        held = {}
        for sources in (1, 2, 3, 4, 5, 8, 12):
            ctx = dict(base, evidence=[{'source_label': f'#channel-{i}', 'source_type': 'UPLOAD', 'n': 10 + i} for i in range(sources)])
            data, _story, seen = self.laid_out(ctx)
            texts = page_texts(data)
            where = f'{sources} sources'
            pages = {next(i for i, t in enumerate(texts) if squeeze(text) in squeeze(t)) for _heading, text in analysis.HOW_IT_WORKS}
            self.assertEqual(len(pages), 1, where)                                                      # all five entries are on one page ...
            method = pages.pop()
            self.assertEqual(method, len(texts) - 1, where)                                             # ... the last one ...
            evidence = next(i for i, t in enumerate(texts) if 'Evidence loaded' in t)
            held[sources] = method == evidence
            self.assertGreater(len(texts[method].replace(FOOTER, '').split()), 150, where)              # ... and it holds the whole method, not an entry or two
            if sources <= 4:
                self.assertEqual(method, evidence, where)                                               # one page for the evidence and the method
                self.assertGreaterEqual(seen[-1][1], 40, where)                                         # with 40 points or more to spare
            elif method != evidence:
                self.assertEqual(method, evidence + 1, where)                                           # else the whole method starts the next page, under nothing
        self.assertTrue(all(held[n] for n in (1, 2, 3, 4)))
        self.assertFalse(held[12])                                                                      # a long list of evidence sends the method to the next page, whole

    def test_the_cover_artwork_takes_the_room_that_is_left_and_never_lands_alone_on_page_two(self):
        """The artwork was a plain image under the cover text: when a long name left it less room than its 220 points, it moved to page 2 and stood there alone, a
        page with a picture and nothing else. It takes the room that is left now: its natural size when that is enough, a smaller one down to 150 points, and none
        at all below that. The contact line under it (FELIXA_REPORT_CONTACT) keeps its room too and stays on the cover."""
        import os
        import report_export
        base = self.edited('calm')
        sizes = {}
        for words in (0, 2, 6, 8, 10, 14, 20):
            for contact in ('', 'Questions: care@example.org'):
                ctx = dict(base)
                ctx['a'] = dict(base['a'], person_name=' '.join(['Night Owls Guild'] + ['Owls'] * words))
                with mock.patch.dict(os.environ, {'FELIXA_REPORT_CONTACT': contact}):
                    data, story, seen = self.laid_out(ctx)
                texts = page_texts(data)
                where = f'{words} more words in the name, contact {contact!r}'
                art = next(f for f in story if isinstance(f, report_export.CoverArt))
                self.assertEqual(next(page for page, _room, f in seen if f is art), 1, where)           # on the cover, or nowhere
                self.assertIn('Alerts and badges', texts[1], where)                                    # page 2 is the page of the alerts: never a picture alone
                self.assertEqual(texts[0].count('Alerts and badges'), 0, where)
                if art.size is not None:
                    self.assertLessEqual(art.size[0], report_export.ART_W, where)
                    self.assertGreaterEqual(art.size[1], report_export.ART_MIN, where)                  # never a thumbnail: it is left out below 150 points
                    self.assertAlmostEqual(art.size[1] / art.size[0], art.ratio, places=6)              # shrunk, not squashed
                self.assertEqual(('care@example.org' in texts[0]), bool(contact), where)               # the contact line is on the cover, whatever the picture takes
                room_left = [room for page, room, f in seen if page == 1 and room is not None]
                self.assertGreaterEqual(min(room_left), 0, where)                                      # and nothing runs off the page
                sizes[(words, contact)] = art.size
        self.assertEqual(sizes[(0, '')][0], report_export.ART_W)                                       # a short name: the artwork at its natural size
        shrunk = [s for s in sizes.values() if s is not None and s[0] < report_export.ART_W]
        dropped = [k for k, s in sizes.items() if s is None]
        self.assertTrue(shrunk, sizes)                                                                  # a longer one: a smaller picture
        self.assertTrue(dropped, sizes)                                                                 # a very long one: none (it used to move to page 2 alone)

    def test_a_table_that_ends_at_the_foot_of_a_page_does_not_make_an_empty_page(self):
        """A spacer after a table, which did not fit in the last few points of a page, went to a page of its own and the next section started a page later. The
        space after a table is part of the table now, so the next page break is the only thing left to do. The control sets the old spacer after the same table."""
        import io as _io
        import measures_export
        import report_export
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        import pypdf
        made = {}

        def spy(ms, story, p, page, table, drawing, small=None):
            made['table'] = table
            made['story'] = story
        with mock.patch.object(measures_export, 'pdf_pages', spy):
            report_export.pdf(self.context('three'), ROOT)
        style = getSampleStyleSheet()['BodyText']

        def grid():
            """Everything the table helper puts into the story for one table (the table, and whatever it sets after it)."""
            made['story'].clear()
            made['table']([['Badge', 'Status', 'Why or what it needs']] + [[f'Badge {i}', 'Not yet', 'A rule in a few words.'] for i in range(5)], measures_export.BADGE_WIDTHS)
            return list(made['story'])

        def pages(flowables):
            out = _io.BytesIO()
            SimpleDocTemplate(out, pagesize=(612, 792), rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=60).build(flowables)
            return len(pypdf.PdfReader(_io.BytesIO(out.getvalue())).pages)
        height = grid()[0].wrap(492, 10_000)[1]                                                          # the table alone
        for left in (0.5, 3, 6, 11):                                                                     # points left under the table: less than the 12 of the old spacer
            filler = 666 - height - left
            self.assertEqual(pages([Spacer(1, filler)] + grid() + [PageBreak(), Paragraph('The next section.', style)]), 2, left)
            self.assertEqual(pages([Spacer(1, filler)] + grid() + [Spacer(1, 12), PageBreak(), Paragraph('The next section.', style)]), 3, left)      # the control

    # -- Incognito -----------------------------------------------------------------------------------
    def test_with_incognito_on_no_clock_time_is_in_the_csv_or_the_pdf_pages(self):
        """Every part of the chat is named by its place ('Part 3 of 9') and its day: the hour and minute of a message stay out of everything this feature writes."""
        ms = self.ms('incognito')
        self.assertTrue(ms['m']['incognito'])
        self.assertTrue(ms['m']['has_series'])
        rows = measure_rows(self.csv('incognito'))
        self.assertGreater(len(rows), 30)
        self.assertEqual([r for r in rows if any(CLOCK.search(c) for c in r)], [])
        pages = our_pages(self.pdf('incognito')[1])
        self.assertEqual([t for t in pages if CLOCK.search(t)], [])
        parts = table_after(rows, PARTS_HEAD)
        self.assertTrue(parts and all(r[0].startswith('Part ') for r in parts))
        for stretch in ms['m']['stretches']:
            self.assertNotRegex(stretch['label'], CLOCK)
            self.assertNotRegex(stretch['axis'], CLOCK)

    def test_with_incognito_on_the_check_by_check_chart_is_drawn_for_checks_made_on_one_day(self):
        """The three checks of the demo ran on one day, so a label with the time of day would be needed to tell them apart. The chart names its points 'Check 1',
        'Check 2', 'Check 3' and shows no time, so the PDF draws it with Incognito on, like the page, and the CSV keeps the day of each check."""
        ms = self.ms('incognito')
        self.assertEqual(len(ms['trail']), 3)
        self.assertEqual(len({r['created_at'][:10] for r in ms['trail']}), 1)
        text = squeeze(' '.join(our_pages(self.pdf('incognito')[1])))
        self.assertIn('Healthscore,checkbycheck', text)
        self.assertIn('Check1Check2Check3', text)
        trail = table_after(measure_rows(self.csv('incognito')), TRAIL_HEAD)
        self.assertEqual(len(trail), 3)
        self.assertEqual({r[1] for r in trail}, {ms['trail'][0]['created_at'][:10]})
        self.assertFalse(any(CLOCK.search(c) for r in trail for c in r))

    def test_without_incognito_the_parts_of_the_chat_carry_their_times(self):
        """The control for the test above: the same chat with Incognito off does name its parts by time, so that test is not an empty one."""
        self.assertFalse(self.ms('three')['m']['incognito'])
        rows = measure_rows(self.csv('three'))
        self.assertTrue(any(CLOCK.search(r[0]) for r in table_after(rows, PARTS_HEAD)))
        self.assertTrue(any(CLOCK.search(t) for t in our_pages(self.pdf('three')[1])))                  # the axis of the charts

    def test_a_clock_time_in_a_name_is_cut_back_under_incognito(self):
        """Should a rule in measures.py ever give a part of the chat a name with a time in it, the CSV and the charts would still leave the time out."""
        import measures_export
        import report_export
        ctx = self.edited('three')
        m = ctx['measures']['m']
        self.assertTrue(all(CLOCK.search(s['label']) and CLOCK.search(s['axis']) for s in m['stretches'][:2]))     # as if Incognito had not been applied
        m['incognito'] = True
        rows = measures_export.csv_rows(ctx['measures'])
        parts = table_after(rows, PARTS_HEAD)
        self.assertEqual([r[0] for r in parts], [f'Part {i} of {len(parts)}' for i in range(1, len(parts) + 1)])
        self.assertEqual([r for r in rows if any(CLOCK.search(str(c)) for c in r)], [])
        pages = our_pages(page_texts(report_export.pdf(ctx, ROOT)))
        self.assertEqual([t for t in pages if CLOCK.search(t)], [])
        self.assertTrue(all(not CLOCK.search(s['axis']) for s in measures_export._parts(m)))


class Footer(AppCase):
    def test_the_footer_note_is_on_every_page_after_the_cover_even_with_no_measures(self):
        """The words come with every page after the cover, whatever the report holds: here a Vibe Check on its own, with no signal ratings."""
        import report
        aid = self.new_analysis()
        self.upload(aid, ('chat.csv', chat(n=30), 'text/csv'))
        self.fake.garbage_next('rating', 99)
        job = self.run_job_fast(aid)
        self.assertEqual(job['status'], 'complete', job)
        self.assertIsNone(report.build(aid)['measures'])
        texts = page_texts(self.client.get(f'/analysis/{aid}/report.pdf').content)
        self.assertGreater(len(texts), 2)
        self.assertEqual([i for i, t in enumerate(texts) if re.search(r'Alerts and badges Alerts are prompts to look', t)], [])      # no such page (the method text has its own words)
        self.assertNotIn(FOOTER, texts[0])
        self.assertTrue(all(FOOTER in t for t in texts[1:]))
        rows = self.export_rows(aid)
        self.assertNotIn(['Alerts'], rows)
        self.assertNotIn(['Badges'], rows)


if __name__ == '__main__':
    unittest.main()
