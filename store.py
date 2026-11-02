"""SQLite storage for Felixa Community Health.

The schema is the original FELIXA schema (analyses, sources, communication_units, unit_ratings,
behavior_metrics, findings) plus `jobs` and `vibe_runs`. An existing felixa.db opens as is: missing
tables and columns are added, nothing is dropped. History that names its sources is rewritten with fingerprints (see fingerprint_history).
"""
import contextlib
import datetime
import json
import math
import os
import re
import sqlite3
import threading

import config
from vibe_check.common import parse_ts
from vibe_check.forecast import source_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_type TEXT,
  goals_json TEXT DEFAULT '[]',
  goal_text TEXT,
  person_name TEXT,
  person_role TEXT,
  organization TEXT,
  profile_url TEXT,
  success_metric TEXT,
  success_unit TEXT,
  current_value REAL,
  target_value REAL,
  direction TEXT DEFAULT 'increase',
  period_start TEXT,
  period_end TEXT,
  public_discovery INTEGER DEFAULT 0,
  audience_analysis INTEGER DEFAULT 0,
  status TEXT DEFAULT 'draft',
  ai_summary TEXT,
  ai_recommendations TEXT,
  model_used TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER NOT NULL,
  source_type TEXT NOT NULL,
  label TEXT,
  value TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS communication_units(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER NOT NULL,
  external_id TEXT,
  actor_role TEXT,
  source_type TEXT DEFAULT 'UPLOAD',
  source_label TEXT,
  timestamp TEXT,
  text TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS unit_ratings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id INTEGER NOT NULL,
  attribute TEXT NOT NULL,
  rating INTEGER NOT NULL,
  rationale TEXT,
  UNIQUE(unit_id, attribute)
);
CREATE TABLE IF NOT EXISTS behavior_metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER NOT NULL,
  attribute TEXT NOT NULL,
  n INTEGER NOT NULL,
  sum_rating REAL NOT NULL,
  detected_n INTEGER NOT NULL,
  present_sum REAL NOT NULL,
  fas REAL,
  detection_rate REAL,
  expression_strength REAL,
  UNIQUE(analysis_id, attribute)
);
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER NOT NULL,
  finding_type TEXT,
  title TEXT,
  body TEXT,
  confidence_label TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY,
  analysis_id INTEGER,
  kind TEXT,
  status TEXT,
  message TEXT,
  result TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS vibe_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER NOT NULL,
  job_id INTEGER,
  status TEXT,
  result_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS vibe_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER NOT NULL,
  created_at TEXT,
  snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS measure_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER NOT NULL,
  created_at TEXT,
  snapshot_json TEXT NOT NULL,
  gen INTEGER
);
CREATE INDEX IF NOT EXISTS idx_measurehist_analysis ON measure_history(analysis_id);
CREATE INDEX IF NOT EXISTS idx_units_analysis ON communication_units(analysis_id);
CREATE INDEX IF NOT EXISTS idx_ratings_unit ON unit_ratings(unit_id);
CREATE INDEX IF NOT EXISTS idx_jobs_analysis ON jobs(analysis_id);
CREATE INDEX IF NOT EXISTS idx_vibe_analysis ON vibe_runs(analysis_id);
CREATE INDEX IF NOT EXISTS idx_vibehist_analysis ON vibe_history(analysis_id);
"""

# Columns added after the original schema. Older databases gain them on start.
ADDED_COLUMNS = {'analyses': [('vibe_sensitivity', "TEXT DEFAULT 'medium'"), ('rated_units_total', 'INTEGER'), ('channel', 'TEXT'), ('user_name', 'TEXT'),
                              ('user_role', 'TEXT'), ('check_volume', 'INTEGER'), ('incognito', 'INTEGER DEFAULT 0'), ('engine', 'TEXT'), ('period_tz', 'INTEGER'), ('cloud_ok', 'INTEGER DEFAULT 0')], 'jobs': [('cancel_requested', 'INTEGER DEFAULT 0')],
                 'sources': [('content_hash', 'TEXT')], 'measure_history': [('gen', 'INTEGER')]}


@contextlib.contextmanager
def db():
    """One connection per unit of work: commits on success, rolls back on error, always closes.
    secure_delete makes SQLite overwrite what is deleted with zeros. Without it, a build that is not compiled with it on (Ubuntu's is)
    only marks the space free, and the text of a deleted message stays in the file."""
    c = sqlite3.connect(config.db_path(), timeout=15)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA secure_delete=ON')
        c.execute('PRAGMA journal_mode=WAL')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with db() as c:
        c.executescript(SCHEMA)
        for table, cols in ADDED_COLUMNS.items():
            have = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            for name, decl in cols:
                if name not in have:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {decl}')
        fingerprint_history(c)
    with contextlib.suppress(OSError):          # the file holds the messages: owner only. The -wal and -shm files beside it take its mode
        os.chmod(config.db_path(), 0o600)


def fingerprint_history(c):
    """A snapshot saved by an earlier build names its sources, and the name would outlive a delete of the evidence: rewrites those rows with
    fingerprints (see vibe_check.forecast.source_key). A row that is not a snapshot is left as it is."""
    for row in c.execute('SELECT id, snapshot_json FROM vibe_history').fetchall():
        try:
            snap = json.loads(row['snapshot_json'])
            kept = [source_key(x) for x in snap['sources']]
        except (ValueError, KeyError, TypeError):
            continue
        if kept != snap['sources']:
            c.execute('UPDATE vibe_history SET snapshot_json=? WHERE id=?', (json.dumps({**snap, 'sources': kept}), row['id']))


def empty_log():
    """Moves the write-ahead log into the database file and truncates it. A deleted message can still sit in the log, in the page as it
    was before the delete, while another connection keeps the log from being reset (a page being served, a tool that has the file
    open). Waits two seconds at most for a reader; the log goes anyway when the last connection to the file closes."""
    with db() as c:
        c.execute('PRAGMA busy_timeout=2000')
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()


def now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


# ---- analyses --------------------------------------------------------------------
def analysis_columns() -> set:
    with db() as c:
        return {r['name'] for r in c.execute('PRAGMA table_info(analyses)')}


def create_analysis() -> int:
    with db() as c:
        return c.execute('INSERT INTO analyses DEFAULT VALUES').lastrowid


def get_analysis(aid):
    with db() as c:
        return c.execute('SELECT * FROM analyses WHERE id=?', (aid,)).fetchone()


def update_analysis(aid, **fields):
    """Updates named columns. Column names come from code, and are checked against the table anyway."""
    if not fields:
        return
    allowed = analysis_columns() - {'id'}
    bad = set(fields) - allowed
    if bad:
        raise ValueError('Unknown analysis column: ' + ', '.join(sorted(bad)))
    fields['updated_at'] = now()
    keys = list(fields)
    with db() as c:
        c.execute('UPDATE analyses SET ' + ','.join(f'{k}=?' for k in keys) + ' WHERE id=?', [fields[k] for k in keys] + [aid])


def workplaces(limit=50) -> list:
    """The workplaces that have been set up, most recently touched first, each with the score of its latest Vibe Check (None before one)."""
    with db() as c:
        rows = c.execute("SELECT id, person_name, subject_type, person_role, channel, updated_at FROM analyses "
                         "WHERE status != 'draft' AND person_name IS NOT NULL AND person_name != '' ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        vibe = latest_vibe(row['id'])
        out.append({**dict(row), 'score': vibe.get('score') if vibe and vibe.get('status') == 'ok' else None})
    return out


# ---- review period ---------------------------------------------------------------
def parse_day(value):
    """The date in a 'YYYY-MM-DD' form field, or None when it is blank. Raises ValueError for anything else."""
    text = str(value or '').strip()
    return datetime.date.fromisoformat(text) if text else None


def parse_bound(value):
    """One end of a review period as saved: 'YYYY-MM-DD' (a whole day) gives a date, 'YYYY-MM-DDTHH:MM' (a minute, in UTC) gives a datetime,
    and blank gives None. Raises ValueError for anything else."""
    text = str(value or '').strip()
    if not text:
        return None
    return datetime.date.fromisoformat(text) if len(text) == 10 else datetime.datetime.strptime(text, '%Y-%m-%dT%H:%M')


def _starts(bound):
    """The first moment a bound lets in."""
    return bound if isinstance(bound, datetime.datetime) else datetime.datetime.combine(bound, datetime.time.min)


def _ends(bound):
    """The first moment after a bound: the end of its day, or the end of its minute."""
    return bound + datetime.timedelta(minutes=1) if isinstance(bound, datetime.datetime) else datetime.datetime.combine(bound, datetime.time.min) + datetime.timedelta(days=1)


def period_of(a):
    """The review period of an analysis as (start, end), each None when not set and each a date (a whole day) or a datetime (a minute, UTC).
    Text that is not a date, and an end before its start, count as not set: older databases kept whatever the form sent, and no run
    ever used it."""
    def bound(value):
        try:
            return parse_bound(value)
        except ValueError:
            return None
    start, end = (bound(a['period_start']), bound(a['period_end'])) if a else (None, None)
    return (None, None) if start and end and _ends(end) <= _starts(start) else (start, end)


def outside_period(timestamp, period) -> bool:
    """True when a unit is dated (as UTC) before the period's start or after its end. A unit with no readable date is never outside."""
    start, end = period
    stamp = parse_ts(timestamp)
    return stamp is not None and bool((start and stamp < _starts(start)) or (end and stamp >= _ends(end)))


def period_coverage(a):
    """How the review period of analysis `a` splits its units: {'start', 'end', 'total', 'outside', 'undated', 'kept'}, or None when no
    period is set. `kept` is what an analysis reads: the units dated inside the period plus the undated ones."""
    period = period_of(a)
    if not any(period):
        return None
    with db() as c:
        stamps = [r['timestamp'] for r in c.execute('SELECT timestamp FROM communication_units WHERE analysis_id=?', (a['id'],))]
    outside = sum(outside_period(s, period) for s in stamps)
    return {'start': period[0], 'end': period[1], 'total': len(stamps), 'outside': outside,
            'undated': sum(parse_ts(s) is None for s in stamps), 'kept': len(stamps) - outside}


# ---- communication units ---------------------------------------------------------
UPLOAD_LOCK = threading.Lock()      # held while an uploaded file is checked against the earlier ones and stored


def units_for(aid, in_period=False) -> list:
    """The communication units of an analysis, oldest first. `in_period` leaves out those dated outside its review period."""
    with db() as c:
        units = [dict(r) for r in c.execute('SELECT * FROM communication_units WHERE analysis_id=? ORDER BY id', (aid,))]
    if in_period:
        period = period_of(get_analysis(aid))
        if any(period):
            units = [u for u in units if not outside_period(u['timestamp'], period)]
    return units


def timestamp_span(aid):
    """(oldest, newest) of the dated messages of an analysis as UTC datetimes, or None when none has a readable date."""
    with db() as c:
        stamps = [parse_ts(r['timestamp']) for r in c.execute('SELECT timestamp FROM communication_units WHERE analysis_id=?', (aid,))]
    stamps = [x for x in stamps if x]
    return (min(stamps), max(stamps)) if stamps else None


def rated_units(aid) -> list:
    """The units of an analysis that have all of their signal ratings, in upload order: [{'id', 'timestamp', 'ratings': {signal: 0..3}}].
    A unit with some ratings missing is left out, as `calculate_metrics` leaves it out of the metrics. No message text is read."""
    with db() as c:
        rows = c.execute('SELECT cu.id, cu.timestamp, ur.attribute, ur.rating FROM communication_units cu '
                         'JOIN unit_ratings ur ON ur.unit_id=cu.id WHERE cu.analysis_id=? ORDER BY cu.id', (aid,)).fetchall()
    units, by_id = [], {}
    for row in rows:
        unit = by_id.get(row['id'])
        if unit is None:
            unit = by_id[row['id']] = {'id': row['id'], 'timestamp': row['timestamp'] or '', 'ratings': {}}
            units.append(unit)
        unit['ratings'][row['attribute']] = row['rating']
    return units


def count_units(aid) -> int:
    with db() as c:
        return c.execute('SELECT COUNT(*) n FROM communication_units WHERE analysis_id=?', (aid,)).fetchone()['n']


def add_units(aid, units, source_type: str) -> int:
    """Inserts vibe_check-style units ({'text','source','timestamp','role','external_id'}). Returns how many were stored,
    never exceeding the per-analysis cap."""
    room = max(0, config.max_units() - count_units(aid))
    rows = []
    start = count_units(aid)
    for i, u in enumerate(units[:room], 1):
        role = str(u.get('role') or '').upper()   # no role stays NULL: a chat with many authors is not 'the person'
        rows.append((aid, str(u.get('external_id') or f'U{start + i:05d}')[:80], role if role in ('SUBJECT', 'AUDIENCE') else None,
                     source_type, str(u.get('source') or 'Upload')[:200], str(u.get('timestamp') or ''), u['text']))
    if rows:
        with db() as c:
            c.executemany('INSERT INTO communication_units(analysis_id,external_id,actor_role,source_type,source_label,timestamp,text) '
                          'VALUES(?,?,?,?,?,?,?)', rows)
    return len(rows)


def has_upload(aid, digest: str) -> bool:
    """True when a file with this content (the SHA-256 of its bytes) was already imported into the analysis. Only whole files are compared:
    the units hold no author, so a message that repeats (a chat is full of them) cannot be told from a copy."""
    with db() as c:
        return c.execute("SELECT 1 FROM sources WHERE analysis_id=? AND source_type='upload_units' AND content_hash=?", (aid, digest)).fetchone() is not None


def add_upload(aid, name: str, units, digest: str):
    """Stores the units of one uploaded file and notes the file among the sources of the analysis. Returns how many units were stored, or None,
    with nothing stored, when a file with this digest was imported before. The check and the store are one step: two requests that carry the
    same file at the same moment would both pass a separate check. The digest is kept only when some units were stored: a file that found no
    room was not imported, so it can be sent again once there is."""
    with UPLOAD_LOCK:
        if has_upload(aid, digest):
            return None
        stored = add_units(aid, units, 'UPLOAD')
        with db() as c:
            c.execute('INSERT INTO sources(analysis_id,source_type,label,value,content_hash) VALUES(?,?,?,?,?)',
                      (aid, 'upload_units', name, f'{stored} communication units', digest if stored else None))
    return stored


def evidence_by_source(aid) -> list:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT source_label, source_type, COUNT(*) n, MIN(NULLIF(timestamp, '')) first_ts, MAX(NULLIF(timestamp, '')) last_ts "
            "FROM communication_units WHERE analysis_id=? GROUP BY source_label, source_type ORDER BY MIN(id)", (aid,))]


def clear_evidence(aid):
    """Removes the messages and every result that quotes them, including full Vibe Check runs, and overwrites them in the database file.
    The analysis itself (name, role, goals) and the numeric Vibe Check history stay, so trends survive a re-upload. The history holds
    scores and counts, and each source name only as a fingerprint (see vibe_check.forecast.source_key): no message text, no file name.
    The records of the imported files go with the messages (see has_upload), so a file can be uploaded again.
    Finished tasks go too: their message ("Your report is ready: ...") describes results that no longer exist.
    A queued or running task is never touched, so its worker still finds its row."""
    with UPLOAD_LOCK, db() as c:                                                    # not between the storing of a file and the note about it (see add_upload)
        c.execute('DELETE FROM unit_ratings WHERE unit_id IN (SELECT id FROM communication_units WHERE analysis_id=?)', (aid,))
        for table in ('communication_units', 'behavior_metrics', 'findings', 'vibe_runs'):
            c.execute(f'DELETE FROM {table} WHERE analysis_id=?', (aid,))
        c.execute("DELETE FROM sources WHERE analysis_id=? AND source_type IN ('raw_csv','upload_units')", (aid,))
        c.execute("DELETE FROM jobs WHERE analysis_id=? AND status NOT IN ('queued','running')", (aid,))
    update_analysis(aid, ai_summary=None, ai_recommendations=None, rated_units_total=None, status='ready')
    empty_log()


def anonymise_units(aid) -> int:
    """Incognito mode: replaces names in the stored messages (see privacy.scrub), for good. Returns how many messages changed."""
    import privacy
    names, changed = privacy.Pseudonyms(), 0
    with db() as c:
        rows = c.execute('SELECT id, text FROM communication_units WHERE analysis_id=? ORDER BY id', (aid,)).fetchall()
        for row in rows:
            clean = privacy.scrub(row['text'], names)
            if clean != row['text']:
                c.execute('UPDATE communication_units SET text=? WHERE id=?', (clean, row['id']))
                changed += 1
    if changed:
        empty_log()                                     # the old text must not linger in the write-ahead log
    return changed


def new_check(aid):
    """Starts a check from nothing: the messages and results of the last one go, its settings reset. The workplace and the numeric history stay."""
    clear_evidence(aid)
    update_analysis(aid, check_volume=None, incognito=0, engine=None, cloud_ok=0, period_start='', period_end='', period_tz=None)


# ---- jobs ------------------------------------------------------------------------
def interrupt_stale_jobs():
    with db() as c:
        c.execute("UPDATE jobs SET status='interrupted', message='The service restarted. Please run this task again.' "
                  "WHERE status IN ('queued','running')")


def create_job(aid, kind: str, message: str = 'Waiting for the analysis engine…') -> int:
    with db() as c:
        return c.execute("INSERT INTO jobs(analysis_id,kind,status,message) VALUES(?,?,'queued',?)", (aid, kind, message)).lastrowid


def latest_job(aid):
    with db() as c:
        row = c.execute('SELECT * FROM jobs WHERE analysis_id=? ORDER BY id DESC LIMIT 1', (aid,)).fetchone()
    return dict(row) if row else None


def active_job(aid):
    job = latest_job(aid)
    return job if job and job['status'] in ('queued', 'running') else None


def count_active_jobs() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running')").fetchone()['n']


def request_cancel(aid) -> bool:
    """Asks the queued or running task of an analysis to stop; the worker reads the flag between model calls. False when none is active."""
    with db() as c:
        return c.execute("UPDATE jobs SET cancel_requested=1, message='Cancelling…' WHERE analysis_id=? AND status IN ('queued','running')",
                         (aid,)).rowcount > 0


def cancel_requested(jid) -> bool:
    with db() as c:
        row = c.execute('SELECT cancel_requested FROM jobs WHERE id=?', (jid,)).fetchone()
    return bool(row and row['cancel_requested'])


def job_update(jid, status: str, message: str, result=None):
    with db() as c:
        c.execute('UPDATE jobs SET status=?, message=?, result=COALESCE(?,result) WHERE id=?',
                  (status, message, json.dumps(result) if result is not None else None, jid))


# ---- Vibe Check runs -------------------------------------------------------------
def save_vibe_run(aid, jid, result: dict) -> int:
    """Stores the full result, and its numeric snapshot in the history when the run succeeded."""
    with db() as c:
        rid = c.execute('INSERT INTO vibe_runs(analysis_id,job_id,status,result_json) VALUES(?,?,?,?)',
                        (aid, jid, result.get('status'), json.dumps(result))).lastrowid
        snap = result.get('snapshot')
        if result.get('status') == 'ok' and snap:
            c.execute('INSERT INTO vibe_history(analysis_id,created_at,snapshot_json) VALUES(?,?,?)',
                      (aid, snap.get('created_at'), json.dumps(snap)))
    return rid


def latest_vibe(aid):
    """The Vibe Check result a report shows: the newest run that succeeded, so a failed re-run never replaces a good result.
    When no run has succeeded, the newest attempt, whose reason explains why the section is missing. None when nothing ran."""
    with db() as c:
        row = c.execute("SELECT result_json FROM vibe_runs WHERE analysis_id=? ORDER BY status='ok' DESC, id DESC LIMIT 1", (aid,)).fetchone()
    return json.loads(row['result_json']) if row else None


def failed_vibe_attempt(aid):
    """The newest run's result when that run did not succeed, so a report can say that the result it shows is an earlier one. Else None."""
    with db() as c:
        row = c.execute('SELECT status, result_json FROM vibe_runs WHERE analysis_id=? ORDER BY id DESC LIMIT 1', (aid,)).fetchone()
    return json.loads(row['result_json']) if row and row['status'] != 'ok' else None


def vibe_history(aid) -> list:
    """Snapshots of every earlier successful run, oldest first."""
    with db() as c:
        rows = c.execute('SELECT snapshot_json FROM vibe_history WHERE analysis_id=? ORDER BY id', (aid,)).fetchall()
    return [json.loads(r['snapshot_json']) for r in rows]


# ---- measure history -------------------------------------------------------------
# One record per check: the numbers of that check and the names of its settings. A record is checked against this list when it is saved and
# again when it is read, so a field that is not on it (a message, a name, a file name) can never be kept, whatever calls these functions.
_NUMBER = (int, float)
MEASURE_FIELDS = {
    'created_at': re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?'), 'basis': ('time', 'order'), 'sensitivity': ('low', 'medium', 'high', ''),
    'model': re.compile(r'[A-Za-z0-9._:/ ()+-]{0,64}'), 'period_start': re.compile(r'(?:\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?)?'),
    'period_end': re.compile(r'(?:\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?)?'), 'sig': re.compile(r'\d+(?::\d+){0,6}'),
    'gen': int, 'n': int, 'total': int, 'sampled': int, 'stretches': int, 'hostile_n': int, 'care_n': int, 'dig_n': int, 'heated_n': int, 'episodes': int,
    'health': float, 'health_sd': float, 'peak': float,
}


def clean_measure_snapshot(raw):
    """The record with only the fields of MEASURE_FIELDS, each coerced to its type and dropped when it does not fit. None when it has no date or no count."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for key, rule in MEASURE_FIELDS.items():
        value = raw.get(key)
        if value is None:
            continue
        try:
            if rule is int:
                if isinstance(value, bool) or not isinstance(value, _NUMBER) or not math.isfinite(value) or not 0 <= value < 2 ** 53:
                    continue
                out[key] = int(value)
            elif rule is float:
                if isinstance(value, bool) or not isinstance(value, _NUMBER) or not math.isfinite(value) or abs(value) > 1e9:
                    continue
                out[key] = round(float(value), 3)
            elif isinstance(rule, tuple):
                if value in rule:
                    out[key] = value
            elif isinstance(value, str) and rule.fullmatch(value):
                out[key] = value
        except (TypeError, ValueError, OverflowError):
            continue
    return out if 'created_at' in out and 'n' in out else None


def evidence_generation(aid):
    """A number that names the set of messages behind a check: the lowest id among the analysis's messages. Ids are never reused, so every New check gets
    a larger one, while more files added to the same check leave it as it is. None when there are no messages."""
    with db() as c:
        return c.execute('SELECT MIN(id) m FROM communication_units WHERE analysis_id=?', (aid,)).fetchone()['m']


def save_measure_snapshot(aid, snapshot) -> bool:
    """Keeps the record of one check. A second record for the same set of messages replaces the first (a re-run is not a new check). False when the
    record has nothing a record needs."""
    clean = clean_measure_snapshot(snapshot)
    if not clean:
        return False
    with db() as c:
        gen = clean.get('gen')
        if gen is not None and c.execute('UPDATE measure_history SET created_at=?, snapshot_json=? WHERE analysis_id=? AND gen=?',
                                        (clean['created_at'], json.dumps(clean), aid, gen)).rowcount:
            return True
        c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, clean['created_at'], json.dumps(clean), gen))
    return True


def measure_history(aid) -> list:
    """The saved records of every check, oldest first. A row that cannot be read is skipped."""
    with db() as c:
        rows = c.execute('SELECT snapshot_json FROM measure_history WHERE analysis_id=? ORDER BY COALESCE(gen, 0), id', (aid,)).fetchall()
    out = []
    for row in rows:
        try:
            snap = clean_measure_snapshot(json.loads(row['snapshot_json']))
        except ValueError:
            continue
        if snap:
            out.append(snap)
    return out
