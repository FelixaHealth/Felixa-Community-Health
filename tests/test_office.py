"""The readers for spreadsheets, word processors, slides, web pages, email and subtitles (vibe_check/office.py), through units_from_file."""
import datetime
import email.message
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from vibe_check import ingest, office  # noqa: E402
from vibe_check.common import parse_ts  # noqa: E402
from vibe_check.ingest import UnsupportedFileError, units_from_file  # noqa: E402

needs_office = unittest.skipUnless(shutil.which('soffice'), 'LibreOffice is needed to make an old .doc or .xls file')
MESSAGES = ['Welcome back everyone, glad to see the squad online tonight.', 'Thanks for the carry last game, really appreciate it.',
            'you are completely useless, uninstall the game', 'Great play on that last round, well done friends.']


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix='bei-office-'))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def path(self, name):
        return self.dir / name

    def texts(self, name, **kw):
        return [u['text'] for u in units_from_file(self.path(name), **kw)]

    def xlsx(self, name='chat.xlsx', sheets=None):
        from openpyxl import Workbook
        book = Workbook()
        book.remove(book.active)
        default = {'Chat': [['Date', 'Author', 'Channel', 'Message']]
                   + [[datetime.datetime(2026, 3, 1, 18, i), f'user{i}', '#general' if i < 2 else '#ranked', m] for i, m in enumerate(MESSAGES)]}
        for title, rows in (sheets or default).items():
            sheet = book.create_sheet(title)
            for row in rows:
                sheet.append(row)
        book.save(self.path(name))
        return self.path(name)

    def zipped(self, name, members):
        with zipfile.ZipFile(self.path(name), 'w', zipfile.ZIP_DEFLATED) as z:
            for member, content in members.items():
                z.writestr(member, content)
        return self.path(name)

    # ---- spreadsheets -----------------------------------------------------------
    def test_an_excel_file_is_read_a_row_to_a_message_with_its_date_and_channel(self):
        self.xlsx()
        units = units_from_file(self.path('chat.xlsx'))
        self.assertEqual([u['text'] for u in units], MESSAGES)
        self.assertEqual([u['source'] for u in units], ['#general', '#general', '#ranked', '#ranked'])          # a channel column splits the sources
        self.assertEqual(units[0]['timestamp'], '2026-03-01 18:00:00')
        self.assertEqual(parse_ts(units[1]['timestamp']), datetime.datetime(2026, 3, 1, 18, 1))

    def test_every_sheet_is_its_own_source_and_a_sheet_without_a_header_is_still_read(self):
        self.xlsx('two.xlsx', {'General': [['Message']] + [[m] for m in MESSAGES[:2]], 'Support': [[m] for m in MESSAGES[2:]]})
        units = units_from_file(self.path('two.xlsx'), label='two.xlsx')
        self.assertEqual([u['text'] for u in units], MESSAGES)
        self.assertEqual([u['source'] for u in units], ['two.xlsx · General'] * 2 + ['two.xlsx · Support'] * 2)

    def test_a_generic_header_is_dropped_and_a_first_message_is_kept(self):
        self.xlsx('generic.xlsx', {'S': [['Comments']] + [[m] for m in MESSAGES]})
        self.assertEqual(self.texts('generic.xlsx'), MESSAGES)                                                  # "Comments" is a label, not a message
        self.xlsx('nohead.xlsx', {'S': [[m] for m in MESSAGES]})
        self.assertEqual(self.texts('nohead.xlsx'), MESSAGES)                                                   # a long first line is a message

    def test_a_sheet_of_numbers_gives_no_messages_and_a_big_sheet_is_cut_with_a_note(self):
        self.xlsx('numbers.xlsx', {'Sales': [['Month', 'Revenue']] + [[f'M{i}', i * 100] for i in range(30)]})
        self.assertEqual(units_from_file(self.path('numbers.xlsx')), [])
        self.xlsx('big.xlsx', {'Chat': [['Message']] + [[f'message number {i} says hello there'] for i in range(60)]})
        notes = []
        with unittest.mock.patch.object(ingest, 'MAX_READ_UNITS', 25):
            units = units_from_file(self.path('big.xlsx'), notes=notes)
        self.assertEqual(len(units), 24)                                                                        # 25 rows were read, and the first is the header
        self.assertTrue(any('only the first 25' in n for n in notes), notes)

    def test_a_workbook_that_unpacks_to_far_too_much_is_refused_before_it_is_opened(self):
        bomb = self.zipped('bomb.xlsx', {'[Content_Types].xml': '<Types/>', 'xl/worksheets/sheet1.xml': '0' * (office.MAX_ZIP_XML_BYTES + 1)})
        with self.assertRaises(UnsupportedFileError) as raised:
            units_from_file(bomb)
        self.assertIn('more than can be read safely', str(raised.exception))

    def test_a_file_that_is_not_really_excel_says_so(self):
        self.path('fake.xlsx').write_bytes(b'this is not a spreadsheet at all')
        with self.assertRaises(Exception) as raised:
            units_from_file(self.path('fake.xlsx'))
        self.assertNotIn('Traceback', str(raised.exception))

    def test_an_opendocument_spreadsheet_is_read_and_a_huge_repeat_does_not_run_away(self):
        ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
              'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"')
        cell = lambda t: f'<table:table-cell><text:p>{t}</text:p></table:table-cell>'                          # noqa: E731
        rows = '<table:table-row>' + cell('Message') + cell('Author') + '<table:table-cell table:number-columns-repeated="1048000"/></table:table-row>'
        rows += ''.join(f'<table:table-row>{cell(m)}{cell("u")}</table:table-row>' for m in MESSAGES)
        content = f'<?xml version="1.0"?><office:document-content {ns}><office:body><office:spreadsheet><table:table table:name="Chat">{rows}</table:table></office:spreadsheet></office:body></office:document-content>'
        self.zipped('chat.ods', {'content.xml': content})
        self.assertEqual(self.texts('chat.ods'), MESSAGES)

    @needs_office
    def test_an_old_excel_file_is_read(self):
        self.xlsx('src.xlsx')
        subprocess.run(['soffice', '--headless', '--convert-to', 'xls', '--outdir', str(self.dir), str(self.path('src.xlsx'))], capture_output=True, timeout=120)
        self.assertTrue(self.path('src.xls').exists())
        units = units_from_file(self.path('src.xls'))
        self.assertEqual([u['text'] for u in units], MESSAGES)
        self.assertEqual(units[0]['source'], '#general')
        self.assertEqual(parse_ts(units[0]['timestamp']), datetime.datetime(2026, 3, 1, 18, 0))

    # ---- documents ----------------------------------------------------------------
    @needs_office
    def test_an_old_word_file_and_an_opendocument_text_are_read(self):
        from docx import Document
        doc = Document()
        for m in MESSAGES:
            doc.add_paragraph(m)
        doc.save(self.path('notes.docx'))
        subprocess.run(['soffice', '--headless', '--convert-to', 'doc', '--outdir', str(self.dir), str(self.path('notes.docx'))], capture_output=True, timeout=120)
        subprocess.run(['soffice', '--headless', '--convert-to', 'odt', '--outdir', str(self.dir), str(self.path('notes.docx'))], capture_output=True, timeout=120)
        for name in ('notes.doc', 'notes.odt'):
            with self.subTest(name):
                joined = ' '.join(self.texts(name))
                for m in MESSAGES:
                    self.assertIn(m.split(',')[0][:30], joined)

    def test_without_a_converter_an_old_word_file_says_what_to_do(self):
        self.path('old.doc').write_bytes(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 not really word')
        with unittest.mock.patch.object(office.shutil, 'which', return_value=None), self.assertRaises(UnsupportedFileError) as raised:
            units_from_file(self.path('old.doc'))
        self.assertIn('save it as .docx or PDF', str(raised.exception))

    def test_rtf_is_read_without_its_fonts_styles_and_codes(self):
        rtf = (r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}{\*\generator Word;}\f0\fs24 " + r"Welcome back everyone, glad to see the squad online tonight.\par "
               r"Thanks for the carry last game, caf\'e9 time \u8364? really appreciate it.\par {\b Great play} on that last round, well done friends.\par}")
        self.path('n.rtf').write_text(rtf)
        with unittest.mock.patch.object(office.shutil, 'which', return_value=None):                              # no converter: the built-in reader
            texts = self.texts('n.rtf')
        joined = ' '.join(texts)
        self.assertIn('Welcome back everyone, glad to see the squad online tonight.', joined)
        self.assertIn('café time €', joined)
        self.assertIn('Great play on that last round', joined)
        for noise in ('Arial', 'fonttbl', 'colortbl', 'generator', 'red0', 'fs24'):
            self.assertNotIn(noise, joined)

    def test_powerpoint_slides_are_read_in_order_and_an_xml_bomb_is_refused(self):
        A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        slide = lambda *lines: f'<p:sld {A} xmlns:p="x"><a:p><a:r><a:t>' + '</a:t></a:r></a:p><a:p><a:r><a:t>'.join(lines) + '</a:t></a:r></a:p></p:sld>'  # noqa: E731
        self.zipped('deck.pptx', {'ppt/slides/slide10.xml': slide(MESSAGES[2]), 'ppt/slides/slide2.xml': slide(MESSAGES[1]), 'ppt/slides/slide1.xml': slide(MESSAGES[0], 'A second line on slide one.'),
                                  'ppt/media/image1.png': 'x'})
        texts = ' | '.join(self.texts('deck.pptx'))
        self.assertLess(texts.index(MESSAGES[0]), texts.index(MESSAGES[1]))
        self.assertLess(texts.index(MESSAGES[1]), texts.index(MESSAGES[2]))                                       # slide 2 before slide 10
        self.assertIn('A second line on slide one.', texts)
        laughs = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "ha"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]>' + f'<p:sld {A} xmlns:p="x"><a:t>&c;</a:t></p:sld>'
        self.zipped('bomb.pptx', {'ppt/slides/slide1.xml': laughs})
        with self.assertRaises(Exception) as raised:
            units_from_file(self.path('bomb.pptx'))
        self.assertIn('Entit', type(raised.exception).__name__)                                                   # defusedxml refused the entities

    def test_web_pages_email_and_subtitles_are_read(self):
        self.path('page.html').write_text('<html><head><title>x</title><style>p{}</style></head><body><nav>Home About Contact us today</nav><h1>Community thread</h1>'
                                          f'<p>{MESSAGES[0]}</p><script>var secret="no";</script><ul><li>{MESSAGES[1]}</li></ul></body></html>')
        joined = ' '.join(self.texts('page.html'))
        self.assertIn(MESSAGES[0], joined)
        self.assertIn(MESSAGES[1], joined)
        self.assertNotIn('secret', joined)
        self.assertNotIn('Home About', joined)
        message = email.message.EmailMessage()
        message['Subject'] = 'Weekly guild thread'
        message['From'] = 'a@example.com'
        message.set_content(MESSAGES[2] + '\n\n' + MESSAGES[3])
        self.path('mail.eml').write_bytes(bytes(message))
        joined = ' '.join(self.texts('mail.eml'))
        self.assertIn('Weekly guild thread', joined)
        self.assertIn(MESSAGES[2], joined)
        self.assertNotIn('a@example.com', joined)                                                                 # headers other than the subject are not messages
        srt = '1\n00:00:01,000 --> 00:00:04,000\n<i>' + MESSAGES[0] + '</i>\n\n2\n00:00:05,000 --> 00:00:08,000\n{\\an8}' + MESSAGES[1] + '\n'
        self.path('talk.srt').write_text(srt)
        self.assertEqual(self.texts('talk.srt'), MESSAGES[:2])
        self.path('talk.vtt').write_text('WEBVTT\n\nNOTE a comment\n\ncue1\n00:01.000 --> 00:04.000 align:start\n' + MESSAGES[2] + '\n\n00:05.000 --> 00:08.000\n' + MESSAGES[3] + '\n')
        self.assertEqual(self.texts('talk.vtt'), MESSAGES[2:])

    def test_a_tab_separated_file_is_read(self):
        self.path('chat.tsv').write_text('Author\tMessage\n' + '\n'.join(f'u{i}\t{m}' for i, m in enumerate(MESSAGES)))
        self.assertEqual(self.texts('chat.tsv'), MESSAGES)

    # ---- anything else ---------------------------------------------------------------
    def test_a_file_of_a_kind_nobody_listed_is_read_when_it_is_text_and_refused_in_words_when_it_is_not(self):
        self.path('export.weird').write_text('\n'.join(MESSAGES))
        self.assertEqual(self.texts('export.weird'), MESSAGES)
        self.path('noext').write_text('\n'.join(MESSAGES))
        self.assertEqual(self.texts('noext'), MESSAGES)
        self.path('blob.dat').write_bytes(bytes(range(256)) * 20)
        with self.assertRaises(UnsupportedFileError) as raised:
            units_from_file(self.path('blob.dat'))
        self.assertIn('it is not a text file', str(raised.exception))
        for name, said in (('photo.png', 'a picture'), ('call.mp3', 'a recording'), ('all.zip', 'an archive'), ('setup.exe', 'a program')):
            with self.subTest(name):
                self.path(name).write_text('\n'.join(MESSAGES))                                                   # even text inside is not read under a name like this
                with self.assertRaises(UnsupportedFileError) as raised:
                    units_from_file(self.path(name))
                self.assertIn(said, str(raised.exception))

    def test_the_grid_reader_finds_headers_and_names_columns(self):
        rows = office.grid_to_rows([['Author', 'Author', '', 'Message'], ['a', 'b', 'c', 'hello there everyone']])
        self.assertEqual(list(rows[0]), ['author', 'col2', 'col3', 'message'])                                    # a repeated or empty name is renamed
        self.assertEqual(office.grid_to_rows([[None, ''], ['', None]]), [])
        self.assertEqual(office.grid_to_rows([[1.0, 2.5]]), [{'col1': '1', 'col2': '2.5'}])


import unittest.mock  # noqa: E402,F401

if __name__ == '__main__':
    unittest.main()
