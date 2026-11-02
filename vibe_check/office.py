"""Readers for the files that are not plain text: spreadsheets, word processors, slides, web pages, email and subtitles.

    .xlsx .xlsm .xls .ods      a table: one message per row, the way a CSV is read (a header row is found, or the longest text column is used)
    .docx .doc .rtf .odt       a document
    .pptx .odp                 the text of the slides
    .html .htm .xhtml .xml     the readable lines of a page
    .eml                       the subject and the body of an email
    .srt .vtt                  the lines of a transcript or subtitle file
    anything else              read as text when it is text, with a plain refusal when it is not

Files that are zips inside (Excel, PowerPoint, OpenDocument) are checked by their zip directory before anything is unpacked, and every XML part
is read with defusedxml, so a crafted file cannot unpack to gigabytes or expand entities. A reader returns rows or text; `ingest.units_from_file`
turns those into messages, with the same ceilings as every other file.
"""
from __future__ import annotations

import email
import email.policy
import html
import itertools
import re
import shutil
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from . import ingest
from .ingest import UnsupportedFileError

TABLE_TYPES = ('.xlsx', '.xlsm', '.xls', '.ods')
TEXT_TYPES = ('.doc', '.rtf', '.odt', '.odp', '.pptx', '.html', '.htm', '.xhtml', '.xml', '.eml', '.srt', '.vtt')
MAX_SHEETS = 25
MAX_ZIP_XML_BYTES = 40_000_000          # all XML in one spreadsheet or slide file once unzipped
MAX_ZIP_BYTES = 200_000_000             # everything in it, pictures included
CONVERT_SECONDS = 60

# Kinds of file that hold no text to read, with what to do instead.
REFUSALS = {
    **dict.fromkeys(('.png', '.jpg', '.jpeg', '.gif', '.heic', '.bmp', '.tif', '.tiff', '.webp'), 'it is a picture, and pictures are not read yet. Copy the text out of it or add the chat as a file'),
    **dict.fromkeys(('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.webm', '.mp4', '.mov', '.avi', '.mkv'), 'it is a recording. Use the Voice tab, or add a transcript'),
    **dict.fromkeys(('.zip', '.gz', '.tar', '.7z', '.rar'), 'it is an archive. Unzip it and add the files inside'),
    **dict.fromkeys(('.exe', '.dmg', '.pkg', '.app', '.dll', '.bin', '.iso'), 'it is a program, not a message file'),
}


def explain(ext: str) -> str:
    """Why a file of this kind is not read, in words for the person."""
    return REFUSALS.get(ext, 'it is not a text file. Add it as CSV, Excel, Word, PDF or text, or paste the text')


# ---- zip safety --------------------------------------------------------------------
def check_zip(path) -> None:
    """Refuses a zip-based file that unpacks to far more than a document should, from its zip directory alone (nothing is unpacked)."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as e:
        raise UnsupportedFileError('This file is damaged or is not really the kind of file its name says.') from e
    xml = sum(i.file_size for i in infos if i.filename.lower().endswith(('.xml', '.rels')))
    total = sum(i.file_size for i in infos)
    if xml > MAX_ZIP_XML_BYTES or total > MAX_ZIP_BYTES:
        raise UnsupportedFileError(f'This file unpacks to {total // 1_000_000:,} MB, more than can be read safely. Save it as CSV or PDF, or paste the text.')


def _xml():
    try:
        from defusedxml import ElementTree
    except ImportError as e:
        raise UnsupportedFileError('This kind of file needs the defusedxml package. Run: pip install -r requirements.txt') from e
    return ElementTree


# ---- tables ------------------------------------------------------------------------
KNOWN_HEADERS = (set(ingest.TEXT_KEYS) | set(ingest.SOURCE_KEYS) | set(ingest.TIME_KEYS) | set(ingest.ROLE_KEYS) | set(ingest.ID_KEYS)
                 | {'author', 'user', 'username', 'name', 'sender', 'from', 'speaker', 'member', 'text', 'reply', 'post', 'review', 'feedback', 'note', 'notes'})


def _cell(value) -> str:
    if value is None:
        return ''
    if hasattr(value, 'isoformat'):
        return value.isoformat(sep=' ') if hasattr(value, 'hour') else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def grid_to_rows(grid) -> list[dict]:
    """A table as a list of lists of cells into rows with column names. A first row that names known columns (text, message, date, author...) is the
    header; so is a first row of short plain labels above longer text. With no header the columns are called col1, col2 and so on, and
    `units_from_rows` then takes the column that reads like prose."""
    grid = [[_cell(c) for c in row] for row in grid]
    grid = [row for row in grid if any(row)]
    if not grid:
        return []
    width = max(len(row) for row in grid)
    grid = [row + [''] * (width - len(row)) for row in grid]
    first, rest = grid[0], grid[1:]
    names = [c.lower() for c in first]
    header = any(n in KNOWN_HEADERS for n in names)
    if not header and rest:
        labels = [c for c in first if c]
        longest_rest = max((sum(len(r[i]) for r in rest) / len(rest) for i in range(width)), default=0)
        header = bool(labels) and all(len(c.split()) <= 3 and len(c) <= 30 and not c.endswith(('.', '!', '?')) and not re.fullmatch(r'[\d.,:/\- ]+', c) for c in labels) \
            and max(len(c) for c in labels) < longest_rest
    if header:
        seen, keys = set(), []
        for i, name in enumerate(names):
            key = name if name and name not in seen else f'col{i + 1}'
            seen.add(key)
            keys.append(key)
        body = rest
    else:
        keys, body = [f'col{i + 1}' for i in range(width)], grid
    return [dict(zip(keys, row)) for row in body]


def _xlsx_grids(path, notes):
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise UnsupportedFileError('Excel support needs the openpyxl package. Run: pip install -r requirements.txt') from e
    check_zip(path)
    workbook = load_workbook(str(path), read_only=True, data_only=True)
    try:
        for sheet in itertools.islice(workbook.worksheets, MAX_SHEETS):
            rows = list(itertools.islice(sheet.iter_rows(values_only=True), ingest.MAX_READ_UNITS + 1))
            if len(rows) > ingest.MAX_READ_UNITS and notes is not None:
                notes.append(f'sheet "{sheet.title}": only the first {ingest.MAX_READ_UNITS:,} rows were read')
            yield sheet.title, rows[:ingest.MAX_READ_UNITS]
        if len(workbook.worksheets) > MAX_SHEETS and notes is not None:
            notes.append(f'only the first {MAX_SHEETS} sheets were read')
    finally:
        workbook.close()


def _xls_grids(path, notes):
    try:
        import xlrd
    except ImportError as e:
        raise UnsupportedFileError('Excel (.xls) support needs the xlrd package. Run: pip install -r requirements.txt') from e
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except xlrd.XLRDError as e:
        raise UnsupportedFileError('This Excel file could not be read; it may be damaged or password-protected.') from e
    try:
        for index in range(min(book.nsheets, MAX_SHEETS)):
            sheet = book.sheet_by_index(index)
            rows = []
            for r in range(min(sheet.nrows, ingest.MAX_READ_UNITS)):
                row = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                            continue
                        except (xlrd.XLDateError, ValueError, OverflowError):
                            pass
                    row.append(cell.value if cell.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR) else None)
                rows.append(row)
            if sheet.nrows > ingest.MAX_READ_UNITS and notes is not None:
                notes.append(f'sheet "{sheet.name}": only the first {ingest.MAX_READ_UNITS:,} rows were read')
            yield sheet.name, rows
            book.unload_sheet(index)
    finally:
        book.release_resources()


ODF_TABLE = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
ODF_TEXT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'


def _ods_grids(path, notes):
    check_zip(path)
    tree = _xml()
    try:
        with zipfile.ZipFile(path) as zf, zf.open('content.xml') as handle:
            name, grid, row, total = '', None, None, 0
            for event, el in tree.iterparse(handle, events=('start', 'end')):
                if event == 'start':
                    if el.tag == ODF_TABLE + 'table':
                        name, grid = el.get(ODF_TABLE + 'name') or '', []
                    elif el.tag == ODF_TABLE + 'table-row':
                        row = []
                    continue
                if el.tag == ODF_TABLE + 'table-cell' and row is not None:
                    text = '\n'.join(''.join(p.itertext()) for p in el.iter(ODF_TEXT + 'p'))
                    repeat = int(el.get(ODF_TABLE + 'number-columns-repeated') or 1)
                    row.extend([text] * min(repeat, 5 if text else 50))
                elif el.tag == ODF_TABLE + 'table-row' and grid is not None:
                    if any(row or []) and total < ingest.MAX_READ_UNITS:
                        grid.append(row)
                        total += 1
                    el.clear()
                elif el.tag == ODF_TABLE + 'table' and grid is not None:
                    yield name, grid
                    grid, total = None, 0
    except (KeyError, ValueError, zipfile.BadZipFile) as e:
        raise UnsupportedFileError('This OpenDocument spreadsheet could not be read; it may be damaged.') from e


def table_rows(path, ext, notes=None) -> list[tuple[str, list[dict]]]:
    """[(sheet name, rows)] for a spreadsheet; sheets with nothing in them are left out."""
    grids = {'.xlsx': _xlsx_grids, '.xlsm': _xlsx_grids, '.xls': _xls_grids, '.ods': _ods_grids}[ext](Path(path), notes)
    out, budget = [], ingest.MAX_READ_UNITS
    for name, grid in grids:
        rows = grid_to_rows(grid)[:budget]
        if rows:
            out.append((name, rows))
            budget -= len(rows)
        if budget <= 0:
            if notes is not None:
                notes.append(f'only the first {ingest.MAX_READ_UNITS:,} rows were read')
            break
    return out


# ---- markup ------------------------------------------------------------------------
class _Lines(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'nav', 'header', 'footer', 'form', 'template', 'iframe', 'head'}
    BLOCK = {'p', 'div', 'li', 'br', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'section', 'article', 'pre', 'td', 'dd', 'dt'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        if tag in self.BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        if tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def html_lines(markup: str, plain: bool = False) -> list[str]:
    """The readable lines of a web page: paragraphs and list items, one per line, with menus, scripts and footers left out. Shared with the link reader."""
    if plain:
        raw = markup
    else:
        parser = _Lines()
        parser.feed(markup)
        raw = ''.join(parser.parts)
    lines = [' '.join(html.unescape(line).split()) for line in raw.splitlines()]
    return [line for line in lines if len(line) >= 12]


def _srt_lines(raw: str) -> str:
    """The spoken lines of a subtitle file, one cue to a line, without numbers, times or tags."""
    cues = []
    for block in re.split(r'\n\s*\n', raw.replace('\r\n', '\n').replace('\r', '\n')):
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        stamp = next((i for i, l in enumerate(lines) if '-->' in l), None)
        lines = lines[stamp + 1:] if stamp is not None else lines                                  # a cue's number or name comes before its times
        lines = [l for l in lines if not re.fullmatch(r'\d+', l) and not l.upper().startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION'))]
        text = re.sub(r'<[^>]+>|\{\\[^}]*\}', '', ' '.join(lines)).strip()
        if text:
            cues.append(text)
    return '\n'.join(cues)


def _eml_text(path) -> str:
    with open(path, 'rb') as handle:
        message = email.message_from_binary_file(handle, policy=email.policy.default)
    body = message.get_body(preferencelist=('plain', 'html'))
    if body is None:
        return str(message.get('subject') or '')
    content = body.get_content()
    if body.get_content_type() == 'text/html':
        content = '\n'.join(html_lines(content))
    return '\n\n'.join(x for x in (str(message.get('subject') or '').strip(), content.strip()) if x)


# ---- zip-based documents -------------------------------------------------------------
def _pptx_text(path) -> str:
    check_zip(path)
    tree = _xml()
    A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    slides = []
    with zipfile.ZipFile(path) as zf:
        names = sorted((n for n in zf.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)), key=lambda n: int(re.search(r'(\d+)\.xml', n).group(1)))
        for name in names[:500]:
            paragraphs, current = [], []
            with zf.open(name) as handle:
                for _event, el in tree.iterparse(handle, events=('end',)):
                    if el.tag == A + 't' and el.text:
                        current.append(el.text)
                    elif el.tag == A + 'p':
                        if current:
                            paragraphs.append(''.join(current))
                        current = []
            if paragraphs:
                slides.append('\n'.join(paragraphs))
    return '\n\n'.join(slides)


def _odf_text(path) -> str:
    """The paragraphs and headings of an OpenDocument text or presentation."""
    check_zip(path)
    tree = _xml()
    out = []
    try:
        with zipfile.ZipFile(path) as zf, zf.open('content.xml') as handle:
            for _event, el in tree.iterparse(handle, events=('end',)):
                if el.tag in (ODF_TEXT + 'p', ODF_TEXT + 'h'):
                    text = ''.join(el.itertext()).strip()
                    if text:
                        out.append(text)
                    el.clear()
                if len(out) > ingest.MAX_READ_UNITS:
                    break
    except (KeyError, zipfile.BadZipFile) as e:
        raise UnsupportedFileError('This OpenDocument file could not be read; it may be damaged.') from e
    return '\n\n'.join(out)


# ---- RTF and the old Word format -------------------------------------------------------
RTF_SKIP = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'header', 'footer', 'headerl', 'headerr', 'footerl', 'footerr', 'object', 'themedata',
            'colorschememapping', 'latentstyles', 'listtable', 'listoverridetable', 'rsidtbl', 'generator', 'datastore', 'xmlnstbl', 'fldinst'}


def rtf_text(raw: str) -> str:
    """The text of an RTF file: groups that hold fonts, styles or pictures are skipped, \\par is a new line, \\'hh and \\uN are characters."""
    token = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}]+)")
    out, stack, skip, skip_next, first_in_group = [], [], False, 0, False
    for found in token.finditer(raw):
        word, num, hexa, symbol, brace, text = found.groups()
        if brace == '{':
            stack.append(skip)
            first_in_group = True
            continue
        if brace == '}':
            skip = stack.pop() if stack else False
            first_in_group = False
            continue
        if word is not None:
            if word in RTF_SKIP and first_in_group:
                skip = True
            if skip:
                first_in_group = False
                continue
            if word in ('par', 'line', 'sect', 'page'):
                out.append('\n')
            elif word == 'tab':
                out.append('\t')
            elif word == 'u' and num is not None:
                out.append(chr(int(num) % 65536))
                skip_next = 1
            first_in_group = False
            continue
        if symbol is not None:
            if symbol == '*' and first_in_group:
                skip = True
            elif not skip and symbol in '\\{}':
                out.append(symbol)
            first_in_group = False
            continue
        first_in_group = False
        if skip:
            continue
        if hexa is not None:
            out.append(bytes([int(hexa, 16)]).decode('cp1252', errors='replace'))
        elif text:
            if skip_next:
                text, skip_next = text[1:], 0
            out.append(text.replace('\r', '').replace('\n', ''))
    return re.sub(r'\n{3,}', '\n\n', ''.join(out)).strip()


CONVERTERS = (('textutil', lambda p, t: ['textutil', '-convert', 'txt', '-stdout', p]),         # macOS has it built in
              ('antiword', lambda p, t: ['antiword', p]),
              ('catdoc', lambda p, t: ['catdoc', p]),
              ('soffice', lambda p, t: ['soffice', '--headless', '--convert-to', 'txt:Text', '--outdir', t, p]),
              ('libreoffice', lambda p, t: ['libreoffice', '--headless', '--convert-to', 'txt:Text', '--outdir', t, p]))


def convert_document(path) -> str | None:
    """The text of an old Word file (or any word-processor file) by the first converter this computer has, or None when it has none."""
    path = str(path)
    for program, build in CONVERTERS:
        if not shutil.which(program):
            continue
        with tempfile.TemporaryDirectory(prefix='felixa-convert-') as folder:
            try:
                done = subprocess.run(build(path, folder), capture_output=True, timeout=CONVERT_SECONDS, cwd=folder)
            except (subprocess.TimeoutExpired, OSError):
                continue
            if program in ('soffice', 'libreoffice'):
                made = sorted(Path(folder).glob('*.txt'))
                text = made[0].read_bytes().decode('utf-8', errors='replace') if made else ''
            else:
                text = done.stdout.decode('utf-8', errors='replace') if done.returncode == 0 else ''
        if text.strip():
            return text
    return None


def _doc_text(path) -> str:
    text = convert_document(path)
    if text is None:
        raise UnsupportedFileError('This older Word file (.doc) needs Word, TextEdit, LibreOffice or antiword to be read. Open it and save it as .docx or PDF, then add it again.')
    return text


# ---- the front door ---------------------------------------------------------------------
def text_of(path, ext, notes=None) -> tuple[str, str]:
    """(text, how to split it into messages) for a document-like file."""
    if ext == '.pptx':
        return _pptx_text(path), 'document'
    if ext in ('.odt', '.odp'):
        return _odf_text(path), 'document'
    if ext == '.doc':
        return _doc_text(path), 'document'
    if ext == '.rtf':
        raw = ingest._read_text(Path(path), notes)
        if not raw.lstrip().startswith('{\\rtf'):
            raise UnsupportedFileError('This file is not really an RTF document.')
        return (convert_document(path) or rtf_text(raw)), 'document'
    if ext in ('.html', '.htm', '.xhtml', '.xml'):
        return '\n'.join(html_lines(ingest._read_text(Path(path), notes))), 'lines'
    if ext == '.eml':
        return _eml_text(path), 'document'
    if ext in ('.srt', '.vtt'):
        return _srt_lines(ingest._read_text(Path(path), notes)), 'lines'
    raise UnsupportedFileError(f'Unsupported file type {ext}.')


def plain_text_or_none(path, notes=None) -> str | None:
    """The text of a file of a kind nobody listed, when it is text: no NUL bytes, and nearly every character printable. None when it is not."""
    with open(path, 'rb') as handle:
        head = handle.read(200_000)
    if not head.strip():
        return None
    if b'\x00' in head and not head.startswith((b'\xff\xfe', b'\xfe\xff', b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return None
    text = ingest._read_text(Path(path), notes)
    sample = text[:5000]
    if not sample or sum(ch.isprintable() or ch in '\n\r\t' for ch in sample) / len(sample) < 0.9:
        return None
    return text
