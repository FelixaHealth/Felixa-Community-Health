"""Turns anything a user uploads into message-like units for Vibe Check.

A unit is a dict: {'text', 'source', 'timestamp', 'role'}. `source` plays the part the
Discord channel played in the original bot: Vibe Check scores each source separately and
combines them into one weighted Impact Score.
"""
from __future__ import annotations

import codecs
import csv
import io
import itertools
import json
import re
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from time import monotonic

MAX_UNIT_CHARS = 500          # the original cut every message at 500 characters; here a longer one is read as several passages
PACK_CHARS = 400              # target size when long prose is cut into sentence groups
# How much of ONE input is read at most. These sit far above what the app stores (5,000 units per analysis by default), so ordinary
# files are unaffected, but a small crafted file can no longer make the reader build millions of objects before that cap applies.
MAX_TEXT_CHARS = 4_000_000    # characters of free text read from one file or paste
MAX_READ_UNITS = 50_000       # units (CSV rows, JSON records, lines or passages) taken from one file or paste
MAX_PDF_PAGES = 100           # pages whose text is read from one PDF
MAX_PDF_SECONDS = 30          # time one PDF may take to read (an ordinary one of 100 pages takes about 1 s); what is unread then is left out
MAX_DOCX_XML_BYTES = 10_000_000         # all XML in one Word file once unzipped (well over 1,000 pages of text)
MAX_DOCX_UNPACKED_BYTES = 100_000_000   # everything in one Word file once unzipped, pictures included
MAX_DOCX_BLOCKS = 50_000      # paragraphs plus table cells read from one Word file
STORE_CHARS = 5000            # longest unit kept verbatim when text is stored un-sanitised (clean=False)
ID_KEYS = ('unit_id', 'id')

TEXT_KEYS = ('text', 'comment', 'content', 'message', 'transcript', 'body')
TIME_KEYS = ('timestamp', 'date', 'created_at', 'datetime', 'time')
ROLE_KEYS = ('actor_role', 'role')
SOURCE_KEYS = ('source', 'channel', 'channel_name', 'room', 'thread')
CSV_DELIMITERS = ',;\t|'      # what can separate the columns of a CSV; a comma comes first and wins a tie (see _csv_reader)
# The byte order marks that name the encoding of a text file (see _read_text). The UTF-32 marks come first, because the UTF-32 LE mark
# begins with the UTF-16 LE mark.
BOM_ENCODINGS =((codecs.BOM_UTF32_LE, 'utf-32'), (codecs.BOM_UTF32_BE, 'utf-32'), (codecs.BOM_UTF16_LE, 'utf-16'), (codecs.BOM_UTF16_BE, 'utf-16'),
                 (codecs.BOM_UTF8, 'utf-8-sig'))


class UnsupportedFileError(ValueError):
    pass


# ---- sanitising (port of the bot's sanitize()) -----------------------------------
_PLACEHOLDER = re.compile(r'\[(?:link|user|channel|role|mention|\.\.\.)\]')


def _mask(text) -> str:
    """Masks mentions, links and very long tokens. Keeps all of the text.

    The long-token rule is ASCII-only, as in the original. A Unicode-aware rule would
    erase any 50-character run of Chinese or Japanese text, which has no spaces.
    """
    if not text or not isinstance(text, str):
        return ''
    t = re.sub(r'<@!?\d+>', '[user]', text)
    t = re.sub(r'<#\d+>', '[channel]', t)
    t = re.sub(r'<@&\d+>', '[role]', t)
    t = re.sub(r'@(everyone|here)\b', '[mention]', t)
    t = re.sub(r'https?://\S+', '[link]', t)
    t = re.sub(r'[A-Za-z0-9_]{50,}', '[...]', t)
    return t.strip()


def sanitize(text) -> str:
    """Masks mentions, links and very long tokens, then trims to 500 characters (the bot's limit for one chat message).
    Use `passages` to analyse text, which keeps what comes after the first 500 characters."""
    return _mask(text)[:MAX_UNIT_CHARS]


def has_signal(text: str) -> bool:
    """True when a unit carries words or emoji, not only masked placeholders and punctuation."""
    rest = _PLACEHOLDER.sub('', text or '')
    return bool(re.search(r'[^\W_]|[☀-➿\U0001F000-\U0001FAFF]', rest))


# ---- free text -------------------------------------------------------------------
_CHAT_PREFIX = re.compile(
    r'^\s*(?:\[[^\]]{1,40}\]|<[^>\s]{1,32}>|\d{1,2}:\d{2}(?::\d{2})?\s?(?:[AaPp][Mm])?|'
    r'\d{4}-\d{2}-\d{2}[T ][\d:.]+|[\w.@#\- ]{1,32}:)\s?')


def _chat_like(lines: list[str]) -> bool:
    if len(lines) < 3:
        return False
    hits = sum(1 for line in lines if _CHAT_PREFIX.match(line))
    return hits / len(lines) >= 0.6


def _wrapped_prose(lines: list[str]) -> bool:
    """PDF text arrives hard-wrapped: long lines that rarely end in punctuation."""
    if len(lines) < 3:
        return False
    lens = sorted(len(line) for line in lines)
    median = lens[len(lens) // 2]
    ended = sum(1 for line in lines if re.search(r'[.!?:;"”)\]]$', line))
    return median >= 55 and ended / len(lines) < 0.5


def _wrap(sentence: str, limit: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    # Offsets into the string, so a long unbroken text is sliced once per piece, not re-copied each time.
    while len(sentence) - start > limit:
        cut = sentence.rfind(' ', start, start + limit)
        if cut - start < limit // 2:
            cut = start + limit
        piece = sentence[start:cut].strip()
        if piece:
            pieces.append(piece)
        start = cut
        while start < len(sentence) and sentence[start].isspace():
            start += 1
    rest = sentence[start:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def _pack_sentences(text: str, limit: int = PACK_CHARS) -> list[str]:
    out: list[str] = []
    cur = ''
    for sentence in re.split(r'(?<=[.!?…。！？])\s+', text):
        for piece in _wrap(sentence.strip(), limit):
            if cur and len(cur) + 1 + len(piece) > limit:
                out.append(cur)
                cur = piece
            else:
                cur = f'{cur} {piece}'.strip()
    if cur:
        out.append(cur)
    return out


def split_text(text, mode: str = 'auto', limit: int = MAX_UNIT_CHARS) -> list[str]:
    """Cuts free text into message-sized pieces.

    mode 'lines'      one unit per non-empty line (chat logs, pasted comments)
         'document'   paragraphs; wrapped lines are re-joined, long paragraphs cut by sentence
         'auto'       chat-looking text -> lines; blank-line paragraphs or wrapped prose -> document
    Pieces longer than `limit` characters are cut by sentence into groups of about 400.
    """
    raw = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.strip() for line in raw.split('\n') if line.strip()]
    if not lines:
        return []
    blocks = [b for b in re.split(r'\n[ \t]*\n', raw) if b.strip()]
    if mode == 'auto':
        if _chat_like(lines):
            mode = 'lines'
        elif len(blocks) >= 2 or _wrapped_prose(lines):
            mode = 'document'
        else:
            mode = 'lines'
    if mode == 'lines':
        pieces = lines
    else:
        pieces = []
        for block in blocks:
            block_lines = [line.strip() for line in block.split('\n') if line.strip()]
            if len(block_lines) > 1 and _chat_like(block_lines):
                pieces.extend(block_lines)
            else:
                pieces.append(' '.join(block_lines))
    out: list[str] = []
    for piece in pieces:
        piece = ' '.join(piece.split())
        out.extend(_pack_sentences(piece) if len(piece) > limit else [piece])
    return out


def passages(text) -> list[str]:
    """What Vibe Check reads of one message: its masked text on one line, all of it.

    Up to 500 characters is one passage. Longer text is cut by sentence into groups of about 400 characters, so nothing after
    the first 500 characters goes unread. Returns [] when only masked placeholders and punctuation are left."""
    t = ' '.join(_mask(text).split())            # one line per passage: the numbering in the prompt stays unambiguous
    return [p for p in (_pack_sentences(t) if len(t) > MAX_UNIT_CHARS else [t]) if has_signal(p)]


def _unit(text, source, timestamp=None, role=None, clean=True, external_id=None):
    """clean=True: masked mentions and links (up to STORE_CHARS), the view Vibe Check analyses; the engine cuts long text into passages.
    clean=False: the text as written (up to STORE_CHARS), for storage."""
    body = _mask(text)[:STORE_CHARS] if clean else str(text or '').strip()[:STORE_CHARS]
    if not body or not has_signal(body):
        return None
    return {'text': body, 'source': source, 'timestamp': timestamp or None, 'role': role or None, 'external_id': external_id or None}


def units_from_text(text, source: str = 'Pasted text', *, timestamp=None, role=None, mode: str = 'auto', clean: bool = True) -> list[dict]:
    limit = MAX_UNIT_CHARS if clean else 1500
    pieces = split_text(str(text or '')[:MAX_TEXT_CHARS], mode, limit)
    units = (u for u in (_unit(p, source, timestamp, role, clean) for p in pieces) if u)
    return list(itertools.islice(units, MAX_READ_UNITS))


# ---- tabular and structured data -------------------------------------------------
def _lower_keys(row: dict) -> dict:
    return {str(k).strip().lower(): v for k, v in row.items() if k is not None}


def _first(lowered: dict, keys) -> str:
    for key in keys:
        value = lowered.get(key)
        if value not in (None, ''):
            return str(value)
    return ''


def units_from_rows(rows, source: str = 'Upload', *, clean: bool = True) -> list[dict]:
    """CSV/JSON rows -> units. A source/channel column splits one file into several sources."""
    rows = [_lower_keys(r) for r in rows if isinstance(r, dict)]
    if not rows:
        return []
    columns = {k for r in rows for k in r}
    text_keys = [k for k in TEXT_KEYS if k in columns]
    if not text_keys:
        # A results table (date, metric, value, unit) holds numbers, not communication.
        if {'metric', 'value'} <= columns:
            return []
        # No recognised header: use the column with the longest average text, if it reads like prose.
        def avg_len(col):
            vals = [str(r.get(col) or '') for r in rows]
            return sum(map(len, vals)) / max(len(vals), 1)
        best = max(columns, key=avg_len) if columns else None
        if best is None or avg_len(best) < 15:
            return []
        text_keys = [best]
    out = []
    for r in rows:
        body = _first(r, text_keys)
        u = _unit(body, _first(r, SOURCE_KEYS) or source, _first(r, TIME_KEYS), _first(r, ROLE_KEYS).upper() or None, clean, _first(r, ID_KEYS))
        if u:
            out.append(u)
    return out


def _join_parts(parts: list) -> str:
    """The text of a message that a chat export writes as a list of parts. Telegram does this for any message with a link, a mention or
    formatting: plain strings and {'type', 'text'} objects in reading order. They are joined as written, with nothing between them (a link
    or a bold word sits inside its sentence). One level only, which is all such an export nests, so a crafted file cannot make this
    recurse; anything else in the list is not text."""
    out = []
    for part in parts:
        if isinstance(part, dict):
            part = part.get('text')
        if isinstance(part, str):
            out.append(part)
    return ''.join(out)


def _plain_fields(item: dict) -> dict:
    """A JSON message without its nested values (attachments, reactions, text_entities), which are not text. A text field written as a
    list of parts is the exception: it is joined first (see _join_parts), so that those messages are read and not dropped."""
    row = {}
    for key, value in item.items():
        if isinstance(value, list) and str(key).strip().lower() in TEXT_KEYS:
            value = _join_parts(value)
        if not isinstance(value, (dict, list)):
            row[key] = value
    return row


def _units_from_json(data, source: str, clean: bool = True) -> list[dict]:
    """Chat exports: a list of messages, or an object holding one under 'messages'. A message's text is a string or, as Telegram writes
    a message with a link, a mention or formatting, a list of parts."""
    channel = None
    if isinstance(data, dict):
        ch = data.get('channel')
        channel = ch.get('name') if isinstance(ch, dict) else (ch if isinstance(ch, str) else None)
        for key in ('messages', 'items', 'data', 'rows', 'comments'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    rows = []
    for item in data[:MAX_READ_UNITS] if isinstance(data, list) else []:
        if isinstance(item, str):
            rows.append({'text': item})
        elif isinstance(item, dict):
            rows.append(_plain_fields(item))
    return units_from_rows(rows, channel or source, clean=clean)


# ---- files -----------------------------------------------------------------------
def _xml_typed_parts(zf: zipfile.ZipFile) -> tuple[set, set]:
    """Part names and file extensions that the package's own [Content_Types].xml declares as XML. python-docx parses a part as XML
    because of this file, not because of the part's name."""
    from lxml import etree                      # python-docx depends on it
    declared = zf.getinfo('[Content_Types].xml')
    if declared.file_size > 1_000_000:         # a real one is a few KB
        raise UnsupportedFileError('This Word file is not laid out like a document, so it is not read.')
    names, extensions = set(), set()
    for entry in etree.fromstring(zf.read(declared), etree.XMLParser(resolve_entities=False, no_network=True)):
        if isinstance(entry.tag, str) and (entry.get('ContentType') or '').lower().endswith('xml'):
            names.add((entry.get('PartName') or '').lower())
            extensions.add((entry.get('Extension') or '').lower().lstrip('.'))
    return names - {''}, extensions - {''}


def _check_docx_size(path) -> None:
    """Refuses a Word file that unzips to far more than a document should, before python-docx loads any of it.

    A .docx is a zip, and a few hundred KB of it can unpack to gigabytes of XML. The sizes come from the zip directory alone, which
    costs nothing, and zipfile never hands back more bytes than a member declares, so a forged directory cannot get past this.
    XML is the costly part (python-docx builds an object for every element), so it has its own, lower ceiling. Word names such parts
    *.xml, but a crafted file can name them anything, so the types declared in [Content_Types].xml count as well."""
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        xml_names, xml_extensions = _xml_typed_parts(zf)
    total = xml = 0
    for info in infos:
        name = info.filename.lower()
        total += info.file_size
        if name.endswith(('.xml', '.rels')) or '/' + name in xml_names or PurePosixPath(name).suffix.lstrip('.') in xml_extensions:
            xml += info.file_size
    if xml > MAX_DOCX_XML_BYTES or total > MAX_DOCX_UNPACKED_BYTES:
        raise UnsupportedFileError(f'This Word file unpacks to {total // 1_000_000:,} MB, more than can be read safely. '
                                   'Save it as PDF or TXT, or paste the text.')


def _csv_reader(text: str) -> csv.DictReader:
    """A DictReader for CSV text, whichever delimiter separates its columns.

    Excel writes ';' wherever the decimal separator is a comma (Germany, France, Spain, Brazil and many more), and some tools write a tab or
    '|'. csv.DictReader assumes a comma, so such a file came out as one column holding 'text;channel;time' or, when a message held a comma,
    as messages cut at that comma with their channel and time lost. The delimiter is the one that splits the header row into the most
    columns, a comma when none does (a single-column file is read as before). A first line `sep=;`, Excel's own way to name it, is obeyed."""
    hint = re.match(r'sep=([,;\t|])[ \t]*\n', text)
    if hint:
        return csv.DictReader(io.StringIO(text[hint.end():]), delimiter=hint.group(1))
    stream, delimiter, columns = io.StringIO(text), ',', 0
    for candidate in CSV_DELIMITERS:
        stream.seek(0)
        try:
            width = len(next(csv.reader(stream, delimiter=candidate), ()))
        except csv.Error:                                       # a header cell over the csv module's field limit: it cannot be this delimiter
            continue
        if width > columns:
            delimiter, columns = candidate, width
    stream.seek(0)
    return csv.DictReader(stream, delimiter=delimiter)


def _shared_header_note(fieldnames) -> str | None:
    """csv.DictReader keeps only the last of two columns with the same header (and the app reads headers case-blind, so `Text` and `text`
    are one), so the messages in the earlier column were dropped without a word. Returns a phrase for the import message, or None.
    It names at most three headers, however many a file repeats: the phrase goes into a URL."""
    counts = Counter((name or '').strip().lower() for name in fieldnames or ())
    shared = [name for name, n in counts.items() if name and n > 1]          # a blank header repeats in any Excel export with spare columns
    if not shared:
        return None
    shown = ', '.join(f"'{name[:30]}'" for name in shared[:3]) + (', ...' if len(shared) > 3 else '')
    return f'columns share a header ({shown}), so only the last of each was read'


def _read_text(path, notes: list | None = None) -> str:
    """The text of a CSV, JSON, TXT, MD or LOG file, in whichever encoding it was saved.

    A byte order mark names the encoding: UTF-8, or the UTF-16 or UTF-32 that Excel's "Unicode Text", Notepad's "Unicode" and PowerShell's `>`
    write. Without one, a file is UTF-8 when it reads as UTF-8 and Windows-1252 when it does not, which is what Excel's "CSV (Comma delimited)"
    and Notepad's "ANSI" write on Western Windows. Read as UTF-8 with the bad bytes replaced, a Windows-1252 file lost every accented letter
    and curly quote for good, and a UTF-16 file came out with a NUL between its characters, each import reported as a success.
    A byte that still cannot be read (Windows-1252 leaves five undefined, and a UTF-16 file may end in half a character) becomes U+FFFD, and a
    phrase goes to `notes`, if given, so the import is not reported as clean. Another code page (Cyrillic or Japanese, for example) is not
    recognised: it is read as Windows-1252, which gives the wrong letters."""
    data = Path(path).read_bytes()
    encoding = next((name for mark, name in BOM_ENCODINGS if data.startswith(mark)), None)
    if encoding is None:
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            encoding = 'cp1252'
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        if notes is not None:
            notes.append('some characters could not be read and were replaced')
        return data.decode(encoding, errors='replace')


class _OutOfTime(BaseException):
    """Ends the reading of a PDF from inside pypdf (see _pdf_text). A BaseException on purpose: pypdf catches Exception around a Form XObject,
    logs it and goes on, which would let a page that calls one form over and over carry on."""


def _pdf_text(pages, notes: list | None) -> str:
    """The text of the first MAX_PDF_PAGES pages of a PDF, read for at most MAX_PDF_SECONDS.

    pypdf caps what one stream may unpack to (75 MB) but not how often a PDF asks for it. A 55 KB file whose pages share such a stream costs
    3.5 s a page, and so does each call of a Form XObject made of one, of which a page may make 5,000: hours of one request, however few
    pages are read. A stream that belongs to a page alone is also kept in memory (74 MB each). So the clock is checked before every operator
    pypdf runs, which reaches inside a page, and after every page. What is unfinished when the time is up is left unread and `notes` says
    so; the overshoot is the one stream that was being unpacked."""
    deadline = monotonic() + MAX_PDF_SECONDS

    def check(*_):
        if monotonic() >= deadline:
            raise _OutOfTime

    total, texts = len(pages), []
    for page in pages[:MAX_PDF_PAGES]:
        try:
            texts.append(page.extract_text(visitor_operand_before=check) or '')
        except _OutOfTime:
            break
        if monotonic() >= deadline:
            break
    if notes is not None:
        if len(texts) < min(total, MAX_PDF_PAGES):
            notes.append(f'reading stopped after {MAX_PDF_SECONDS:g} seconds, with {len(texts):,} of {total:,} pages read')
        elif total > MAX_PDF_PAGES:
            notes.append(f'only the first {MAX_PDF_PAGES:,} of {total:,} pages were read')
    return '\n\n'.join(texts)


def units_from_file(path, label: str | None = None, *, clean: bool = True, notes: list | None = None) -> list[dict]:
    """Reads .csv .tsv .json .txt .md .log .pdf .docx, spreadsheets (.xlsx .xlsm .xls .ods), .doc .rtf .odt, slides (.pptx .odp), web pages (.html .xml), email (.eml)
    and subtitles (.srt .vtt), and any other file that is text (see office.py). PDF and DOCX need pypdf / python-docx. The columns of a CSV may be separated by commas,
    semicolons, tabs or pipes, and a text file may be UTF-8, UTF-16 or UTF-32 (with a byte order mark) or Windows-1252 (see _read_text).
    Reads at most MAX_TEXT_CHARS characters of text, MAX_READ_UNITS units (a CSV counts its rows), and MAX_PDF_PAGES pages and MAX_PDF_SECONDS
    seconds of a PDF, and refuses a Word file that unzips to more than a document should. A list passed as `notes` gets a phrase for each CSV
    or PDF part left out at those ceilings ('only the first 100 of 150 pages were read', 'reading stopped after 30 seconds, with 12 of 80
    pages read'), by a repeated CSV header, or for characters that could not be read, so the caller can tell the user instead of importing a
    cut or damaged file in silence."""
    path = Path(path)
    source = label or path.name
    ext = path.suffix.lower()
    if ext in ('.csv', '.tsv'):
        reader = _csv_reader(_read_text(path, notes))
        rows = list(itertools.islice(reader, MAX_READ_UNITS))
        if notes is not None:
            if next(reader, None) is not None:                                  # a row beyond the ceiling exists
                notes.append(f'only the first {MAX_READ_UNITS:,} rows were read')
            shared = _shared_header_note(reader.fieldnames)
            if shared:
                notes.append(shared)
        return units_from_rows(rows, source, clean=clean)
    if ext == '.json':
        return _units_from_json(json.loads(_read_text(path, notes)), source, clean)
    if ext in ('.txt', '.md', '.log'):
        return units_from_text(_read_text(path, notes), source, clean=clean)
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise UnsupportedFileError('PDF support needs the pypdf package.') from e
        return units_from_text(_pdf_text(PdfReader(str(path)).pages, notes), source, mode='document', clean=clean)
    if ext == '.docx':
        try:
            from docx import Document
        except ImportError as e:
            raise UnsupportedFileError('DOCX support needs the python-docx package.') from e
        _check_docx_size(path)
        doc = Document(str(path))
        blocks = itertools.chain((p.text for p in doc.paragraphs), (c.text for t in doc.tables for r in t.rows for c in r.cells))
        return units_from_text('\n\n'.join(itertools.islice(blocks, MAX_DOCX_BLOCKS)), source, mode='document', clean=clean)
    from . import office
    if ext in office.TABLE_TYPES:                                               # Excel and OpenDocument spreadsheets: a row is a message, a sheet may be a source
        sheets = office.table_rows(path, ext, notes)
        units = []
        for name, rows in sheets:
            units.extend(units_from_rows(rows, f'{source} · {name}' if len(sheets) > 1 else source, clean=clean))
        return units[:MAX_READ_UNITS]
    if ext in office.TEXT_TYPES:                                                # old Word, RTF, slides, web pages, email, subtitles
        text, mode = office.text_of(path, ext, notes)
        return units_from_text(text, source, mode=mode, clean=clean)
    text = None if ext in office.REFUSALS else office.plain_text_or_none(path, notes)     # a kind nobody listed: read it if it is text
    if text is None:
        raise UnsupportedFileError(f'This file cannot be read: {office.explain(ext)}.')
    return units_from_text(text, source, clean=clean)


# ---- selection -------------------------------------------------------------------
def sample_units(units: list, limit: int):
    """Keeps at most `limit` units, spread evenly through the upload so the sample
    represents the whole file (a head cut would only ever see the oldest messages).
    Returns (units, note_or_None)."""
    n = len(units)
    if limit <= 0 or n <= limit:
        return list(units), None
    picked = [units[(i * n) // limit] for i in range(limit)]
    return picked, f'sampled {limit} of {n} messages, evenly through the upload'


def group_by_source(units: list[dict]) -> dict[str, list[dict]]:
    """Groups units by source, keeping first-seen order."""
    groups: dict[str, list[dict]] = {}
    for u in units:
        groups.setdefault(u.get('source') or 'Upload', []).append(u)
    return groups
