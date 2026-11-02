"""The PDF pages and the CSV rows of Measures over time, alerts and badges (measures_export.py, and the footer and styles of report_export.py).

Each test builds a real analysis through the app (with the fake model of tests/fake_ollama.py), runs checks, and reads the PDF and the CSV that the app serves.
The fake model rates by keyword, so the numbers say nothing about real chats. They exercise the pages, the tables and the wording. Run with:

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
from vibe_check.common import long_date  # noqa: E402

CALM = ['Thanks for the update on the release', 'The schedule for tomorrow is posted in the pinned list', 'Please send the file when you can',
        'I understand, sorry you had a rough week', 'Great work on the new design', 'The schedule for tomorrow is posted in the pinned list']
HOT = ['you absolute idiot, stupid take', 'wtf is going on here', 'you absolute idiot, stupid take', 'The schedule for tomorrow is posted in the pinned list',
       'you always do this to the team']
CARING = ['I understand, sorry you had a rough week', 'The schedule for tomorrow is posted in the pinned list']
NEUTRAL = ['The schedule for tomorrow is posted in the pinned list']

CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')
BANNED = (r'\bFAS\b', r'(?i)\bstretch', r'(?i)\bepisode', r'(?i)\bbaseline', r'(?i)\bpeak', r'(?i)\btoxic', r'(?i)communication unit', r'(?i)standard error', r'(?i)z-score')
BLOCKS = ('Alerts', 'Measures over time', 'Since your last check', 'Badges')
MEASURE_HEAD = ['Measure', 'Level', 'Count', 'Of', 'Trend', 'What we saw', 'Note', 'What it means', 'Latest part']
PARTS_HEAD = ['Part of the chat', 'Messages', 'Conflict per 100 messages', 'Empathy per 100 messages', 'Passive-aggressive per 100 messages', 'Heated per 100 messages',
              'Counted as a heated moment', 'Health score']
TRAIL_HEAD = ['Check', 'Date', 'Messages rated', 'Health score', 'Conflict messages', 'Empathy messages', 'Passive-aggressive messages', 'Heated messages', 'Heated moments']
ALERT_HEAD = ['Priority', 'Alert', 'What we saw', 'Try this', 'Where', 'Based on']
BADGE_HEAD = ['Badge', 'Status', 'Why or what it needs', 'How it works']
FOOTER = 'Model-rated wording. Not a verdict on people.'
PRIORITY = {'high': 'High priority', 'medium': 'Medium priority', 'good': 'Good news'}


def days_ago(n):
    """18:00 on a day n days back: always a usable date, whatever day the tests run."""
    return (datetime.datetime.now() - datetime.timedelta(days=n)).replace(hour=18, minute=0, second=0, microsecond=0)


def chat(n=72, hot=(0, 0), start=None, step=2, calm=CALM, heated=HOT, first=None):
    """A CSV chat of `n` messages two minutes apart. The messages in `hot` come from `heated`, the first `first[0]` from `first[1]`, the rest from `calm`."""
    start = start or days_ago(90)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['timestamp', 'source', 'text'])
    for i in range(n):
        pool = heated if hot[0] <= i < hot[1] else first[1] if first and i < first[0] else calm
        writer.writerow([(start + datetime.timedelta(minutes=step * i)).isoformat(), 'general', f'{pool[i % len(pool)]} {i}'])
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
    return next(i for i, t in enumerate(texts) if 'Alerts and badges' in t)


def our_pages(texts):
    """The pages of this feature: from the one headed Alerts and badges to the page before the summary (or the measured behaviour, when there is no summary)."""
    start = our_start(texts)
    end = next((i for i, t in enumerate(texts) if i > start and ('Summary and guidance' in t or 'Measured behaviour' in t)), len(texts))
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
        """The id of a demo analysis, built on first use: 'three' (three checks, with alerts), 'incognito' (the same with Incognito on), 'calm' (no alerts,
        badges), 'fading' (an alert whose next step holds a question mark), 'few' (10 messages), 'unlike' (two checks made with different sensitivity) and
        'improved' (two checks that are alike, the second calmer: good news only)."""
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

    # -- the CSV -------------------------------------------------------------------------------------
    def test_the_csv_has_the_four_blocks_each_with_its_caveat(self):
        """After the guidance and before the Vibe Check come the blocks Alerts, Measures over time, Since your last check and Badges. Each opens with its
        title row and a 'How to read this' row that carries the caveat sentence of measures.py, word for word."""
        ms = self.ms('three')
        rows = self.csv('three')
        for title, key in (('Alerts', 'alerts'), ('Measures over time', 'measures'), ('Badges', 'badges')):
            self.assertEqual(rows[rows.index([title]) + 1], ['How to read this', ms['caveats'][key]], title)
        since = rows[rows.index(['Since your last check']) + 1]
        self.assertEqual(since[0], 'How to read this')
        self.assertTrue(since[1])
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
        self.assertEqual(rows[at + 1 + len(ms['alerts']):], [['Note', n['text']] for n in ms['notes']])

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
        for card, row in zip(ms['cards'], table_after(rows, MEASURE_HEAD)):
            self.assertEqual(row[:2], [card['name'], card['level']])
            self.assertEqual(row[2:4], [str(card.get('count', '')), str(card.get('of', ''))])
            self.assertEqual(row[4:6], [card['trend'], card['headline']])
            self.assertEqual(row[7:], [card['meaning'], card.get('latest', '')])
        parts = table_after(rows, PARTS_HEAD)
        self.assertEqual([r[0] for r in parts], [s['label'] for s in m['stretches']])
        self.assertEqual([r[1] for r in parts], [str(s['n']) for s in m['stretches']])
        self.assertEqual([r[6] for r in parts], ['Yes' if s['counted'] else 'No' for s in m['stretches']])
        self.assertIn('Yes', [r[6] for r in parts])                                                    # the demo has a heated moment
        self.assertIn(['Health score', str(measures.whole(ms['health']['score'])), ms['health']['status']], rows)
        self.assertIn(['Health score trend', ms['health']['trend']], rows)
        self.assertIn(['About the health score', ms['health']['caption']], rows)

    def test_the_since_block_gives_last_this_and_the_verdict_and_the_check_by_check_table(self):
        ms = self.ms('three')
        self.assertTrue(ms['since']['comparable'])
        rows = block(self.csv('three'), 'Since your last check')
        shown = table_after(rows, ['Measure', 'Last check', 'This check', 'Change'])
        self.assertEqual(shown, [[r['label'], str(r['before']), str(r['now']), r['verdict']] for r in ms['since']['rows']])
        self.assertTrue({'Better', 'Worse', 'About the same'} >= {r[3] for r in shown})
        trail = table_after(rows, TRAIL_HEAD)
        self.assertEqual([r[0] for r in trail], ['Check 1', 'Check 2', 'Check 3 (this check)'])
        self.assertEqual([r[2] for r in trail], [str(r['n']) for r in ms['trail']])
        self.assertEqual([r[3] for r in trail], [str(measures.whole(r['health'])) for r in ms['trail']])
        for row, record in zip(trail, ms['trail']):
            self.assertEqual(row[1], long_date(record['created_at']))                                   # the day of the check, no time of day
            self.assertNotRegex(row[1], CLOCK)

    def test_two_checks_that_are_not_alike_give_the_reason_and_no_verdict(self):
        ms = self.ms('unlike')
        self.assertFalse(ms['since']['comparable'])
        rows = block(self.csv('unlike'), 'Since your last check')
        self.assertIn(['No comparison', ms['since']['why']], rows)
        self.assertIn(['Measure', 'Last check', 'This check'], rows)
        self.assertFalse(any('Change' in row or 'Better' in row or 'Worse' in row for row in rows))
        text = ' '.join(our_pages(self.pdf('unlike')[1]))
        self.assertIn(squeeze('No comparison. ' + ms['since']['why']), squeeze(text))
        self.assertNotIn('Better', text)
        self.assertNotIn('Change', text)

    def test_the_badges_block_says_earned_or_not_yet_with_the_figure_or_what_is_missing(self):
        ms = self.ms('three')
        rows = block(self.csv('three'), 'Badges')
        shown = table_after(rows, BADGE_HEAD)
        self.assertEqual(len(shown), 7)
        for badge, row in zip(ms['badges'], shown):
            self.assertEqual(row, [badge['name'], 'Earned' if badge['earned'] else 'Not yet', badge['evidence'] if badge['earned'] else badge['needs'], badge['how']])
        self.assertIn('Earned', [r[1] for r in shown])
        self.assertIn('Not yet', [r[1] for r in shown])
        few = block(self.csv('few'), 'Badges')
        self.assertIn(['Note', self.ms('few')['badge_note']], few)                                     # 'Badges start at 30 messages'
        self.assertEqual({r[1] for r in table_after(few, BADGE_HEAD)}, {'Not yet'})

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
        score = next(r for r in rows if r[:1] == ['Health score'] and len(r) == 3)
        self.assertEqual(score, ['Health score', measures.whole(ms['health']['score']), ms['health']['status']])
        trail = table_after(rows, TRAIL_HEAD)
        self.assertEqual(len(trail), 3)
        for row in trail:
            self.assertTrue(all(isinstance(x, int) for x in row[2:]), row)

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
        out = report.csv_rows(ctx)
        self.assertTrue(any(row[:2] == ['High priority', "'=SUM(1+1)"] for row in out))
        self.assertTrue(any(row[-1:] == ["'-2+3"] for row in out))

    # -- the PDF -------------------------------------------------------------------------------------
    def test_the_pdf_has_the_alerts_and_badges_page_and_the_measures_page_after_the_cover(self):
        data, texts = self.pdf('three')
        ms = self.ms('three')
        whole_text = pdf_text(data)
        for needle in ('Alerts and badges', 'Measures over time', 'Since your last check', 'Earned', 'Not yet'):
            self.assertIn(needle, whole_text)
        self.assertNotIn('Alerts and badges', texts[0])
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
        for key in ('alerts', 'measures', 'badges'):
            self.assertIn(squeeze(ms['caveats'][key]), text, key)
        self.assertIn(squeeze(ms['basis_text']), text)
        for card in ms['cards']:
            self.assertIn(squeeze(f'{card["name"]}: {card["level"]}'), text)
            self.assertIn(squeeze(card['headline']), text)
            if card['trend']:
                self.assertIn(squeeze(card['trend']), text)
        self.assertIn(squeeze(ms['health']['caption']), text)
        self.assertIn(f'{measures.whole(ms["health"]["score"])}outof100', text)
        for row in ms['since']['rows']:
            self.assertIn(squeeze(row['label']), text)
            self.assertIn(squeeze(row['verdict']), text)
        self.assertIn('Healthscore,checkbycheck', text)

    def test_the_caveats_come_first_on_their_pages(self):
        ms = self.ms('three')
        mine = our_pages(self.pdf('three')[1])
        self.assertTrue(squeeze(mine[0].split('Alerts and badges', 1)[1]).startswith(squeeze(ms['caveats']['alerts'])))
        page = next(t for t in mine if 'Measures over time' in t)
        self.assertTrue(squeeze(page.split('Measures over time', 1)[1]).startswith(squeeze(ms['caveats']['measures'])))

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
        for symbol in ('\u2713', '\u2714', '\u2265', '\u2264', '\u2212', '\u2192', '\u00b7'):
            for name in ('three', 'fading'):
                self.assertNotIn(symbol, ' '.join(our_pages(self.pdf(name)[1])))
            self.assertFalse(any(symbol in c for row in measure_rows(self.csv('three')) for c in row))

    def test_the_question_mark_check_does_catch_a_symbol(self):
        """The control for the test above: put a greater-than-or-equal sign into an alert and the PDF does print a question mark, which that test would find."""
        import report_export
        ctx = self.edited('three')
        ctx['measures']['alerts'][0]['detail'] += ' (\u2265 5 in 100)'
        text = ' '.join(our_pages(page_texts(report_export.pdf(ctx, ROOT))))
        self.assertIn('(? 5 in 100)', text)

    def test_the_pages_use_plain_words_and_none_of_the_jargon(self):
        data, texts = self.pdf('three')
        self.assertRegex(pdf_text(data), r'\bFAS\b')                                                    # the older pages of the report do use it: only ours are checked
        for name in ('three', 'fading', 'few'):
            text = ' '.join(our_pages(self.pdf(name)[1]))
            for word in BANNED:
                self.assertNotRegex(text, word, name)
        rows = ' '.join(c for row in measure_rows(self.csv('three')) for c in row)
        for word in BANNED:
            self.assertNotRegex(rows, word)

    def test_tables_and_charts_fit_the_page_and_the_four_small_charts_sit_two_by_two(self):
        """Every table is 504 points wide at most (the page between its margins), and each of the four small charts is no wider than half of that."""
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
                seen.append(('drawing', d.width, scene.title))
                return d
            return real(ms, story, p, page, table_of, drawing_of, small)
        with mock.patch.object(measures_export, 'pdf_pages', spy):
            self.assertTrue(report_export.pdf(ctx, ROOT).startswith(b'%PDF'))
        tables = [s for s in seen if s[0] == 'table']
        self.assertEqual(len(tables), 3)                                                               # alerts, badges, since
        for _kind, total, cells, columns in tables:
            self.assertLessEqual(total, 504)
            self.assertEqual(cells, columns)
        charts = [s for s in seen if s[0] == 'drawing' and s[2] != 'scale']                              # (one empty scene is drawn to learn the scale)
        small = [c for c in charts if c[2].endswith('over time')]
        self.assertEqual(len(small), 4)
        for _kind, width, _title in small:
            self.assertLessEqual(width, 252)
        for _kind, width, _title in charts:
            self.assertLessEqual(width, 504)
        self.assertEqual(len([c for c in charts if c[2] == 'Health score']), 1)                          # the meter
        self.assertEqual(len([c for c in charts if c[2] == 'Health score, check by check']), 1)

    def test_no_heading_or_caveat_is_left_alone_on_a_page(self):
        """A caveat or a heading set after a table, or at the foot of a page, can land alone on the next page. Each one has to share its page with what it
        belongs to: the badges caveat with the badges table, 'Since your last check' with its table, the check-by-check heading with its chart."""
        for name in ('three', 'improved', 'calm', 'few', 'fading', 'unlike', 'incognito'):
            ms = self.ms(name)
            for number, text in enumerate(our_pages(self.pdf(name)[1])):
                flat = squeeze(text)
                where = f'{name}, page {number}'
                if squeeze(ms['caveats']['badges']) in flat:
                    self.assertIn('Whyorwhatitneeds', flat, where)
                if 'Whyorwhatitneeds' in flat:
                    self.assertIn(squeeze(ms['caveats']['badges']), flat, where)
                if 'Sinceyourlastcheck' in flat:
                    self.assertIn('MeasureLastcheckThischeck', flat, where)
                if 'Healthscore,checkbycheck' in flat:
                    self.assertIn('Check1Check2Check3', flat, where)
                if 'Alertsandbadges' not in flat:
                    self.assertNotIn(squeeze(ms['caveats']['alerts']), flat, where)             # the first caveat is on the page with its heading, never apart from it

    def test_nothing_small_trails_a_table(self):
        """The structure behind the test above: a note or a caveat that follows a table can land alone at the top of the next page, so none of the tables of
        these pages (nor a group that holds one) is followed by a paragraph of body or small type."""
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
                    after = next((g for g in story[i + 1:] if not isinstance(g, Spacer)), None)
                    if isinstance(after, Paragraph):
                        self.assertNotIn(after.style.name, ('BeiBody', 'BeiSmall'), f'{name}: a paragraph trails a table')
            self.assertGreaterEqual(tables, 3, name)

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
        self.assertNotIn('Since your last check', text)
        self.assertNotIn('check by check', text)
        self.assertNotIn(['Since your last check'], self.csv('calm'))
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
        self.assertIn(squeeze(ms['badge_note']), text)
        self.assertFalse(any(row[:1] == ['Part of the chat'] for row in self.csv('few')))
        self.assertNotIn('Bandsmark', text)                                                            # no charts, so no key to them

    def test_a_long_list_of_long_alerts_still_fits_and_nothing_is_lost(self):
        """Eight alerts with the longest wording the rules write: the table runs over to further pages, no row is cut and the badges stay in one piece."""
        import report_export
        ctx = self.edited('three')
        longest = max(ctx['measures']['alerts'], key=lambda a: len(a['try']))
        alerts = [{**longest, 'title': f'Alert number {i} with a rather long title that wraps over several lines', 'level': ('high', 'medium', 'good')[i % 3],
                   'detail': longest['detail'] * 2, 'try': longest['try'] * 2, 'when': 'about two thirds of the way in (May 5 to May 6)'} for i in range(8)]
        ctx['measures']['alerts'] = alerts
        ctx['measures']['notes'] = [{'key': 'a', 'text': 'A quiet line about the basis of the figures. ' * 6}, {'key': 'b', 'text': 'Another quiet line.'}]
        mine = our_pages(page_texts(report_export.pdf(ctx, ROOT)))
        self.assertGreaterEqual(len(mine), 4)                                                          # the alerts alone take more than a page
        text = squeeze(' '.join(mine))
        for alert in alerts:
            self.assertIn(squeeze(alert['title']), text)
            self.assertIn(squeeze(alert['try']), text)
        for note in ctx['measures']['notes']:
            self.assertIn(squeeze(note['text']), text)
        holders = {i for i, t in enumerate(mine) for name in ('Courteous', 'Calm waters', 'No serious flags') if name in t and 'Why or what it needs' in t}
        self.assertEqual(len(holders), 1)                                                              # the seven badges are on one page

    def test_a_health_score_of_zero_is_still_printed(self):
        """`text or ''` would drop a 0: a health score of 0 out of 100 has to show."""
        import report_export
        ctx = self.edited('three')
        ctx['measures']['health'].update(score=0.0, status='Needs attention', tone='bad')
        text = squeeze(' '.join(our_pages(page_texts(report_export.pdf(ctx, ROOT)))))
        self.assertIn('0outof100', text)
        self.assertIn('Healthscore:Needsattention', text)

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
        self.assertNotIn('Alerts and badges', ' '.join(texts))
        self.assertNotIn(FOOTER, texts[0])
        self.assertTrue(all(FOOTER in t for t in texts[1:]))
        rows = self.export_rows(aid)
        self.assertNotIn(['Alerts'], rows)
        self.assertNotIn(['Badges'], rows)


if __name__ == '__main__':
    unittest.main()
