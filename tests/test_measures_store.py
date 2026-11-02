"""The saved side of Measures over time: what the store keeps after a check (store.MEASURE_FIELDS and friends) and how it keeps it.

These tests use a real database in a folder of their own (FELIXA_DATA_DIR, as tests/test_app.py does) and, for the canary test at the end, a real check run
through the app with the fake model of tests/fake_ollama.py. The ratings are synthetic. Run with:

    python3 -m unittest tests.test_measures_store -v
"""
import contextlib
import datetime
import json
import math
import os
import pathlib
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import measures  # noqa: E402
import store  # noqa: E402

TONES = ('Polite', 'Funny', 'Empathetic', 'Encouraging', 'Ignorant', 'Egocentric', 'Neurotic', 'Sarcastic', 'Agitated', 'Aggressive', 'Rude', 'Disrespectful', 'Judgmental')
ALLOWED = {'created_at', 'basis', 'sensitivity', 'model', 'period_start', 'period_end', 'sig', 'gen', 'n', 'total', 'sampled', 'stretches', 'hostile_n', 'care_n', 'dig_n',
           'heated_n', 'episodes', 'health', 'health_sd', 'peak'}
INT_FIELDS = ('gen', 'n', 'total', 'sampled', 'stretches', 'hostile_n', 'care_n', 'dig_n', 'heated_n', 'episodes')
FLOAT_FIELDS = ('health', 'health_sd', 'peak')
T0 = datetime.datetime(2026, 3, 5, 18, 0)


def snap(**changes):
    """A good record, as measures.record makes one."""
    out = {'created_at': '2026-06-01T12:00:00', 'gen': 3, 'n': 96, 'total': 96, 'sampled': 0, 'basis': 'time', 'stretches': 12, 'model': 'qwen2.5:7b', 'sensitivity': 'medium',
           'period_start': '2026-03-01', 'period_end': '2026-03-31T12:00', 'health': 51.852, 'health_sd': 13.2, 'hostile_n': 4, 'care_n': 5, 'dig_n': 6, 'heated_n': 9,
           'episodes': 1, 'peak': 22.5, 'sig': '96:40000:24000:0:0'}
    out.update(changes)
    return out


def clean(**changes):
    return store.clean_measure_snapshot(snap(**changes))


def database_bytes(folder) -> bytes:
    """Everything on disk that belongs to the database in `folder`: the file itself and the write-ahead log and shared-memory files beside it."""
    return b''.join(path.read_bytes() for path in sorted(pathlib.Path(folder).glob('felixa.db*')))


@contextlib.contextmanager
def stock_sqlite():
    """SQLite as the upstream default has it: secure_delete off. The app turns it on itself, whichever build runs the tests."""
    connect = sqlite3.connect

    def stock(*args, **kwargs):
        c = connect(*args, **kwargs)
        c.execute('PRAGMA secure_delete=OFF')
        return c

    with mock.patch.object(sqlite3, 'connect', stock):
        yield


class StoreCase(unittest.TestCase):
    """A real database in a folder of its own, one folder per test class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='bei-measures-store-')
        cls.env = mock.patch.dict(os.environ, {'FELIXA_DATA_DIR': cls.tmp})
        cls.env.start()
        store.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- builders -----------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def add_chat(aid, n, text=lambda i: f'message {i}', source=lambda i: 'general', tones=lambda i: {}, rate=True, stamp=lambda i: (T0 + datetime.timedelta(minutes=i)).isoformat()):
        """n messages in analysis `aid`, each with all 13 ratings (tones(i) gives the non-zero ones). Returns the ids of the new messages."""
        before = {r['id'] for r in store.rated_units(aid)}
        store.add_units(aid, [{'text': text(i), 'source': source(i), 'timestamp': stamp(i)} for i in range(n)], 'UPLOAD')
        with store.db() as c:
            ids = [r['id'] for r in c.execute('SELECT id FROM communication_units WHERE analysis_id=? ORDER BY id', (aid,))]
            new = [i for i in ids if i not in before][-n:]
            if rate:
                for k, uid in enumerate(new):
                    given = tones(k)
                    for tone in TONES:
                        c.execute('INSERT INTO unit_ratings(unit_id,attribute,rating,rationale) VALUES(?,?,?,?)', (uid, tone, given.get(tone, 0), ''))
        return new

    @staticmethod
    def raw_rows(aid):
        with store.db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM measure_history WHERE analysis_id=? ORDER BY id', (aid,))]


# =============================================================================================================================================
class CleanSnapshotTests(unittest.TestCase):
    """clean_measure_snapshot: the allow-list, run when a record is written and again when it is read."""

    def test_a_good_record_comes_back_as_it_was(self):
        self.assertEqual(store.clean_measure_snapshot(snap()), snap())
        self.assertEqual(set(snap()), ALLOWED, 'the test record uses every field there is')
        self.assertEqual(set(store.MEASURE_FIELDS), ALLOWED)

    def test_unknown_keys_are_dropped(self):
        extras = {'text': 'ZQTEXT', 'message': 'ZQMSG', 'author': 'ZQAUTHOR', 'source': 'ZQSOURCE', 'file_name': 'ZQFILE', 'label': 'ZQLABEL', 'rationale': 'ZQWHY',
                  'sources': ['a', 'b'], 'person_name': 'Sam Rivera', 'Polite': 3, '__proto__': {'x': 1}, 'ratings': {'Rude': 3}, '': 1, 'N': 5, 'created_at ': 'x', 'GEN': 4}
        got = store.clean_measure_snapshot(snap(**extras))
        self.assertEqual(got, snap())
        self.assertNotIn('ZQ', json.dumps(got))

    def test_a_record_needs_a_date_and_a_count(self):
        self.assertIsNone(store.clean_measure_snapshot({k: v for k, v in snap().items() if k != 'created_at'}))
        self.assertIsNone(store.clean_measure_snapshot({k: v for k, v in snap().items() if k != 'n'}))
        self.assertIsNone(store.clean_measure_snapshot({'health': 50.0, 'hostile_n': 1}))
        self.assertIsNone(store.clean_measure_snapshot({}))
        for bad in (None, [], [snap()], 'created_at', 5, 5.5, (1, 2), True, b'x'):
            with self.subTest(raw=bad):
                self.assertIsNone(store.clean_measure_snapshot(bad))

    def test_a_date_that_does_not_fit_makes_the_record_nothing(self):
        for bad in ('2026-06-01', '2026-06-01T12', '2026-06-01T12:00:00Z', '2026-06-01 12:00:00', 'yesterday', '', '2026-06-01T12:00:00\n', ' 2026-06-01T12:00:00',
                    '2026-6-1T12:00:00', '26-06-01T12:00:00', 12345, None, ['2026-06-01T12:00:00'], '2026-06-01T12:00:00.123', '2026-06-01T12:00:00+02:00'):
            with self.subTest(created_at=bad):
                self.assertIsNone(clean(created_at=bad))
        for good in ('2026-06-01T12:00:00', '2026-06-01T12:00', '1999-12-31T23:59:59'):
            self.assertEqual(clean(created_at=good)['created_at'], good)

    def test_a_count_that_does_not_fit_makes_the_record_nothing(self):
        for bad in ('96', '', True, False, None, -1, -10 ** 9, 2 ** 53, 2 ** 60, math.inf, -math.inf, math.nan, [96], {'n': 96}, (96,)):
            with self.subTest(n=bad):
                self.assertIsNone(clean(n=bad))
        for good, want in [(0, 0), (1, 1), (96, 96), (96.0, 96), (96.9, 96), (2 ** 53 - 1, 2 ** 53 - 1)]:
            with self.subTest(n=good):
                got = clean(n=good)
                self.assertEqual(got['n'], want)
                self.assertIsInstance(got['n'], int)

    def test_a_field_of_the_wrong_type_is_dropped_and_the_rest_is_kept(self):
        for key in INT_FIELDS:
            if key == 'n':
                continue
            for bad in ('5', '', True, False, [5], {'a': 1}, math.nan, math.inf, -1, 2 ** 53, 2 ** 70):
                with self.subTest(field=key, value=bad):
                    got = clean(**{key: bad})
                    self.assertNotIn(key, got)
                    self.assertEqual({k: v for k, v in got.items()}, {k: v for k, v in snap().items() if k != key})
        for key in FLOAT_FIELDS:
            for bad in ('5.0', '', True, False, [5.0], {'a': 1}, math.nan, math.inf, -math.inf, 1e9 + 1, -1e9 - 1, 1e300):
                with self.subTest(field=key, value=bad):
                    self.assertNotIn(key, clean(**{key: bad}))
        for key in INT_FIELDS + FLOAT_FIELDS:
            if key not in ('n',):
                self.assertNotIn(key, clean(**{key: None}))

    def test_the_edges_of_the_ranges(self):
        for key in INT_FIELDS:
            self.assertEqual(clean(**{key: 0})[key], 0)
            self.assertEqual(clean(**{key: 2 ** 53 - 1})[key], 2 ** 53 - 1)
        for key in FLOAT_FIELDS:
            self.assertEqual(clean(**{key: 1e9})[key], 1e9)
            self.assertEqual(clean(**{key: -1e9})[key], -1e9)
            self.assertEqual(clean(**{key: 0})[key], 0.0)

    def test_numbers_are_coerced_to_the_type_of_their_field(self):
        got = clean(hostile_n=4.0, health=52, peak=22, sampled=1.0)
        self.assertIsInstance(got['hostile_n'], int)
        self.assertIsInstance(got['sampled'], int)
        self.assertIsInstance(got['health'], float)
        self.assertEqual((got['hostile_n'], got['health'], got['peak']), (4, 52.0, 22.0))
        self.assertEqual(clean(health=51.85185185)['health'], 51.852)               # three decimals
        self.assertEqual(clean(health=0.0004)['health'], 0.0)

    def test_the_model_name_is_a_short_plain_name(self):
        for good in ('qwen2.5:7b', 'llama3.1:8b-instruct', 'claude-3.5 (sonnet)', 'gpt+4/x', 'a' * 64, '', 'Model_1.0'.replace('_', '-')):
            with self.subTest(model=good):
                self.assertEqual(clean(model=good)['model'], good)
        for bad in ('a' * 65, '<script>', 'a;b', 'line\nbreak', 'quote"', "it's", 'emoji \U0001f600', 'ñ', 'tab\there', 'a,b', 'a=b', 'a&b', 5, ['x'], None, 'a\\b', 'a#b'):
            with self.subTest(model=bad):
                self.assertNotIn('model', clean(model=bad))

    def test_basis_and_sensitivity_come_from_a_short_list(self):
        for good in ('time', 'order'):
            self.assertEqual(clean(basis=good)['basis'], good)
        for bad in ('clock', '', 'Time', 'TIME', 'time ', 5, None, ['time']):
            with self.subTest(basis=bad):
                self.assertNotIn('basis', clean(basis=bad))
        for good in ('low', 'medium', 'high', ''):
            self.assertEqual(clean(sensitivity=good)['sensitivity'], good)
        for bad in ('extreme', 'Low', 'LOW', 'low ', 'med', 5, None, ['low']):
            with self.subTest(sensitivity=bad):
                self.assertNotIn('sensitivity', clean(sensitivity=bad))

    def test_the_review_period_is_a_date_or_a_date_and_minutes_or_nothing(self):
        for key in ('period_start', 'period_end'):
            for good in ('', '2026-03-05', '2026-03-05T18:00', '1990-01-01'):
                with self.subTest(field=key, value=good):
                    self.assertEqual(clean(**{key: good})[key], good)
            for bad in ('2026-03-05T18:00:00', '2026-3-5', 'March 5', '2026-03-05 18:00', ' 2026-03-05', '2026-03-05\n', '2026-03-05Z', 20260305, ['2026-03-05']):
                with self.subTest(field=key, value=bad):
                    self.assertNotIn(key, clean(**{key: bad}))

    def test_the_signature_is_colon_separated_whole_numbers(self):
        for good in ('1', '96', '96:40000:24000:0:0', '1:2:3:4:5:6:7'):
            self.assertEqual(clean(sig=good)['sig'], good)
        for bad in ('', 'abc', 'a:b', '1:', ':1', '-1:2', '1.5:2', '1:2:3:4:5:6:7:8', '1 :2', '1\n', 12, None):
            with self.subTest(sig=bad):
                self.assertNotIn('sig', clean(sig=bad))

    def test_cleaning_twice_changes_nothing_and_the_result_is_plain_json(self):
        once = clean(health=51.85185185, hostile_n=4.7)
        self.assertEqual(store.clean_measure_snapshot(once), once)
        self.assertEqual(json.loads(json.dumps(once)), once)

    def test_random_garbage_never_raises_and_always_comes_out_valid_or_nothing(self):
        rng = random.Random(11)
        pool = [None, True, False, 0, 1, -1, 5, 96, 2 ** 53, 2 ** 60, 1e9, 1e10, -1e9, math.nan, math.inf, -math.inf, 3.7, '5', '', 'time', 'low', 'x' * 100, [], [1], {}, {'a': 1}, (1,),
                b'x', '2026-06-01T12:00:00', '2026-06-01', 'qwen2.5:7b', '1:2:3', 'order', 'medium', '2026-03-05', '\x00', 'a' * 64, 'é']
        keys = sorted(ALLOWED) + ['text', 'message', 'x', '', 'N']
        got_valid = 0
        for _ in range(4000):
            raw = {rng.choice(keys): rng.choice(pool) for _ in range(rng.randint(0, 24))}
            if rng.random() < .5:
                raw['created_at'] = rng.choice(['2026-06-01T12:00:00', '2026-06-01T12:00'])
                raw['n'] = rng.choice([1, 96, 96.0, 500])
            out = store.clean_measure_snapshot(raw)
            if out is None:
                continue
            got_valid += 1
            self.assertTrue(set(out) <= ALLOWED, out)
            self.assertIn('created_at', out)
            self.assertIn('n', out)
            for key, value in out.items():
                rule = store.MEASURE_FIELDS[key]
                if rule is int:
                    self.assertTrue(type(value) is int and 0 <= value < 2 ** 53, (key, value))
                elif rule is float:
                    self.assertTrue(type(value) is float and math.isfinite(value) and abs(value) <= 1e9, (key, value))
                elif isinstance(rule, tuple):
                    self.assertIn(value, rule)
                else:
                    self.assertTrue(isinstance(value, str) and rule.fullmatch(value), (key, value))
            self.assertEqual(store.clean_measure_snapshot(out), out)
            json.dumps(out)
        self.assertGreater(got_valid, 1000)


# =============================================================================================================================================
class SaveAndHistoryTests(StoreCase):
    """save_measure_snapshot and measure_history: one record per check, oldest first, only what the allow-list lets through."""

    def test_a_record_is_saved_and_read_back(self):
        aid = store.create_analysis()
        self.assertEqual(store.measure_history(aid), [])
        self.assertTrue(store.save_measure_snapshot(aid, snap()))
        self.assertEqual(store.measure_history(aid), [snap()])

    def test_the_same_check_saved_again_replaces_its_record_and_a_new_check_adds_one(self):
        aid = store.create_analysis()
        store.save_measure_snapshot(aid, snap(gen=5, hostile_n=1, created_at='2026-06-01T10:00:00'))
        store.save_measure_snapshot(aid, snap(gen=5, hostile_n=2, created_at='2026-06-01T11:00:00'))
        got = store.measure_history(aid)
        self.assertEqual([(r['gen'], r['hostile_n'], r['created_at']) for r in got], [(5, 2, '2026-06-01T11:00:00')])
        self.assertEqual(len(self.raw_rows(aid)), 1, 'replaced in place, not added')
        self.assertEqual(self.raw_rows(aid)[0]['created_at'], '2026-06-01T11:00:00')
        store.save_measure_snapshot(aid, snap(gen=6, hostile_n=3))
        self.assertEqual([(r['gen'], r['hostile_n']) for r in store.measure_history(aid)], [(5, 2), (6, 3)])
        store.save_measure_snapshot(aid, snap(gen=6, hostile_n=4))
        self.assertEqual([(r['gen'], r['hostile_n']) for r in store.measure_history(aid)], [(5, 2), (6, 4)])
        self.assertEqual(len(self.raw_rows(aid)), 2)

    def test_the_same_generation_in_another_analysis_is_another_record(self):
        a, b = store.create_analysis(), store.create_analysis()
        store.save_measure_snapshot(a, snap(gen=5, hostile_n=1))
        store.save_measure_snapshot(b, snap(gen=5, hostile_n=2))
        self.assertEqual([r['hostile_n'] for r in store.measure_history(a)], [1])
        self.assertEqual([r['hostile_n'] for r in store.measure_history(b)], [2])

    def test_the_history_is_ordered_by_generation_not_by_when_it_was_saved(self):
        aid = store.create_analysis()
        for gen in (9, 3, 7, 1):
            store.save_measure_snapshot(aid, snap(gen=gen))
        self.assertEqual([r['gen'] for r in store.measure_history(aid)], [1, 3, 7, 9])

    def test_a_record_without_a_generation_is_kept_every_time_and_comes_first(self):
        aid = store.create_analysis()
        store.save_measure_snapshot(aid, snap(gen=None, hostile_n=1))
        store.save_measure_snapshot(aid, snap(gen=2, hostile_n=2))
        store.save_measure_snapshot(aid, snap(gen=None, hostile_n=3))
        got = store.measure_history(aid)
        self.assertEqual([(r.get('gen'), r['hostile_n']) for r in got], [(None, 1), (None, 3), (2, 2)])

    def test_a_record_with_nothing_to_keep_is_refused_and_nothing_is_written(self):
        aid = store.create_analysis()
        for bad in ({}, {'n': 5}, {'created_at': '2026-06-01T12:00:00'}, {'created_at': 'x', 'n': 1}, None, 'x', [snap()], 5):
            with self.subTest(raw=bad):
                self.assertFalse(store.save_measure_snapshot(aid, bad))
        self.assertEqual(self.raw_rows(aid), [])

    def test_only_allowed_fields_reach_the_table(self):
        aid = store.create_analysis()
        store.save_measure_snapshot(aid, snap(text='ZQTEXT', source='ZQSOURCE', file_name='ZQFILE', model='ZQMODEL<script>', hostile_n='ZQ'))
        row = self.raw_rows(aid)[0]
        self.assertNotIn('ZQ', row['snapshot_json'])
        self.assertTrue(set(json.loads(row['snapshot_json'])) <= ALLOWED)
        self.assertNotIn('model', json.loads(row['snapshot_json']))

    def test_a_row_that_cannot_be_read_is_skipped_and_the_others_are_kept(self):
        aid = store.create_analysis()
        store.save_measure_snapshot(aid, snap(gen=1, hostile_n=1))
        with store.db() as c:
            for gen, text in [(2, 'not json'), (3, '{"n": 3}'), (4, '[1, 2]'), (5, '"a string"'), (6, 'null'), (7, '{"created_at": "x", "n": 1}'), (8, '{'), (9, '12'),
                              (10, json.dumps(snap(gen=10, created_at='yesterday')))]:
                c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, 'x', text, gen))
        store.save_measure_snapshot(aid, snap(gen=11, hostile_n=11))
        self.assertEqual([(r['gen'], r['hostile_n']) for r in store.measure_history(aid)], [(1, 1), (11, 11)])

    def test_a_row_is_checked_again_when_it_is_read(self):
        aid = store.create_analysis()
        evil = dict(snap(gen=1), text='ZQTEXT', author='ZQAUTHOR', hostile_n='lots', model='<b>x</b>', health=1e300, peak=-1e300)
        with store.db() as c:
            c.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json,gen) VALUES(?,?,?,?)', (aid, evil['created_at'], json.dumps(evil), 1))
        got = store.measure_history(aid)
        self.assertEqual(len(got), 1)
        self.assertNotIn('ZQ', json.dumps(got))
        for key in ('hostile_n', 'model', 'health', 'peak', 'text', 'author'):
            self.assertNotIn(key, got[0])
        self.assertEqual(got[0]['n'], 96)

    def test_a_saved_record_survives_a_second_start_of_the_database(self):
        aid = store.create_analysis()
        store.save_measure_snapshot(aid, snap(gen=4))
        store.init_db()
        store.init_db()
        self.assertEqual([r['gen'] for r in store.measure_history(aid)], [4])


# =============================================================================================================================================
class MigrationTests(unittest.TestCase):
    """A database made before records had a generation gains the column when it opens, and keeps what it held."""

    OLD_TABLE = ('CREATE TABLE measure_history(id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id INTEGER NOT NULL, created_at TEXT, snapshot_json TEXT NOT NULL)')

    def test_an_old_database_without_the_gen_column_gains_it_on_init_db(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {'FELIXA_DATA_DIR': folder}):
            raw = sqlite3.connect(pathlib.Path(folder) / 'felixa.db')
            raw.execute(self.OLD_TABLE)
            old = snap(gen=None)
            old.pop('gen')
            raw.execute('INSERT INTO measure_history(analysis_id,created_at,snapshot_json) VALUES(1,?,?)', (old['created_at'], json.dumps(old)))
            raw.commit()
            self.assertNotIn('gen', [r[1] for r in raw.execute('PRAGMA table_info(measure_history)')])
            raw.close()
            store.init_db()
            with store.db() as c:
                self.assertIn('gen', [r['name'] for r in c.execute('PRAGMA table_info(measure_history)')])
                self.assertEqual(c.execute('SELECT COUNT(*) n FROM measure_history').fetchone()['n'], 1)
                self.assertIsNone(c.execute('SELECT gen FROM measure_history').fetchone()['gen'])
            store.init_db()                                                             # a second start changes nothing
            self.assertEqual(store.measure_history(1), [old])
            # the old record comes first, and a new one with a generation is saved after it
            store.save_measure_snapshot(1, snap(gen=2))
            store.save_measure_snapshot(1, snap(gen=2, hostile_n=9))
            self.assertEqual([(r.get('gen'), r['hostile_n']) for r in store.measure_history(1)], [(None, 4), (2, 9)])
            self.assertEqual(store.evidence_generation(1), None)

    def test_a_new_database_has_the_gen_column_and_its_index(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {'FELIXA_DATA_DIR': folder}):
            store.init_db()
            with store.db() as c:
                self.assertIn('gen', [r['name'] for r in c.execute('PRAGMA table_info(measure_history)')])
                self.assertIn('idx_measurehist_analysis', [r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")])


# =============================================================================================================================================
class EvidenceGenerationTests(StoreCase):
    """evidence_generation names the set of messages behind a check: the lowest message id, which only grows."""

    def test_no_messages_no_generation(self):
        aid = store.create_analysis()
        self.assertIsNone(store.evidence_generation(aid))

    def test_it_is_the_lowest_message_id_and_stays_while_more_messages_are_added(self):
        aid = store.create_analysis()
        ids = self.add_chat(aid, 5)
        first = store.evidence_generation(aid)
        self.assertEqual(first, min(ids))
        more = self.add_chat(aid, 7)
        self.assertEqual(store.evidence_generation(aid), first, 'more files in the same check leave the generation as it is')
        self.assertGreater(min(more), max(ids))
        store.add_units(aid, [{'text': 'one more', 'source': 'x', 'timestamp': ''}], 'UPLOAD')
        self.assertEqual(store.evidence_generation(aid), first)

    def test_it_grows_after_clear_evidence(self):
        aid = store.create_analysis()
        self.add_chat(aid, 5)
        first = store.evidence_generation(aid)
        store.clear_evidence(aid)
        self.assertIsNone(store.evidence_generation(aid))
        self.add_chat(aid, 5)
        second = store.evidence_generation(aid)
        self.assertGreater(second, first)
        store.clear_evidence(aid)
        store.clear_evidence(aid)                                                       # clearing nothing is fine
        self.add_chat(aid, 1)
        self.assertGreater(store.evidence_generation(aid), second)

    def test_it_grows_after_a_new_check(self):
        aid = store.create_analysis()
        self.add_chat(aid, 5)
        first = store.evidence_generation(aid)
        store.new_check(aid)
        self.assertIsNone(store.evidence_generation(aid))
        self.add_chat(aid, 5)
        self.assertGreater(store.evidence_generation(aid), first)

    def test_it_never_goes_back_even_when_another_workplace_is_cleared(self):
        a, b = store.create_analysis(), store.create_analysis()
        self.add_chat(a, 3)
        self.add_chat(b, 3)
        seen = [store.evidence_generation(a)]
        for _ in range(3):
            store.clear_evidence(a)
            store.clear_evidence(b)
            self.add_chat(b, 2)
            self.add_chat(a, 2)
            seen.append(store.evidence_generation(a))
        self.assertEqual(seen, sorted(set(seen)), 'strictly growing')

    def test_each_workplace_has_its_own(self):
        a, b = store.create_analysis(), store.create_analysis()
        self.add_chat(a, 2)
        ids_b = self.add_chat(b, 2)
        self.assertEqual(store.evidence_generation(b), min(ids_b))
        self.assertNotEqual(store.evidence_generation(a), store.evidence_generation(b))


# =============================================================================================================================================
class HistorySurvivesTests(StoreCase):
    """The records stay when the messages are deleted or a new check starts."""

    def test_clear_evidence_keeps_the_measure_history_of_that_analysis_and_every_other(self):
        a, b = store.create_analysis(), store.create_analysis()
        for aid in (a, b):
            self.add_chat(aid, 6)
            store.save_measure_snapshot(aid, snap(gen=store.evidence_generation(aid)))
        before = store.measure_history(a)
        store.clear_evidence(a)
        self.assertEqual(store.count_units(a), 0)
        self.assertEqual(store.measure_history(a), before)
        self.assertEqual(len(store.measure_history(b)), 1)

    def test_new_check_keeps_the_measure_history(self):
        aid = store.create_analysis()
        self.add_chat(aid, 6)
        store.save_measure_snapshot(aid, snap(gen=store.evidence_generation(aid)))
        store.new_check(aid)
        self.assertEqual(store.count_units(aid), 0)
        self.assertEqual(len(store.measure_history(aid)), 1)
        self.add_chat(aid, 6)
        store.save_measure_snapshot(aid, snap(gen=store.evidence_generation(aid), hostile_n=7))
        self.assertEqual(len(store.measure_history(aid)), 2)

    def test_the_numbers_of_a_deleted_check_are_all_that_is_left_of_it(self):
        canary = 'zqleftover'
        aid = store.create_analysis()
        self.add_chat(aid, 20, text=lambda i: f'{canary} message {i}', source=lambda i: f'{canary}-room', tones=lambda i: {'Rude': 2} if i % 4 == 0 else {})
        store.add_upload(aid, f'{canary}-export.csv', [{'text': f'{canary} file text', 'source': f'{canary}-sheet', 'timestamp': ''}], 'digest-1')
        rec = measures.save_snapshot(aid, {'rated': 20, 'total': 20, 'note': None, 'model': 'qwen2.5:7b'})
        self.assertIsNotNone(rec)
        self.assertNotIn(canary, json.dumps(store.measure_history(aid)))
        store.clear_evidence(aid)
        history = store.measure_history(aid)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['n'], 20)
        self.assertEqual(history[0]['hostile_n'], 5)
        self.assertNotIn(canary, json.dumps(history))


# =============================================================================================================================================
class RatedUnitsTests(StoreCase):
    """store.rated_units: what measures.py reads, which is ratings and dates and never a word of any message."""

    def test_a_unit_is_an_id_a_date_and_its_ratings_and_nothing_else(self):
        aid = store.create_analysis()
        self.add_chat(aid, 4, text=lambda i: f'ZQSECRET message {i}', source=lambda i: 'ZQSOURCE', tones=lambda i: {'Rude': i})
        units = store.rated_units(aid)
        self.assertEqual(len(units), 4)
        for i, u in enumerate(units):
            self.assertEqual(sorted(u), ['id', 'ratings', 'timestamp'])
            self.assertEqual(sorted(u['ratings']), sorted(TONES))
            self.assertEqual(u['ratings']['Rude'], i)
            self.assertTrue(all(isinstance(v, int) for v in u['ratings'].values()))
        self.assertNotIn('ZQ', repr(units))

    def test_units_come_in_upload_order_with_their_dates(self):
        aid = store.create_analysis()
        ids = self.add_chat(aid, 5, stamp=lambda i: (T0 - datetime.timedelta(minutes=i)).isoformat())
        units = store.rated_units(aid)
        self.assertEqual([u['id'] for u in units], ids)
        self.assertEqual([u['timestamp'] for u in units], [(T0 - datetime.timedelta(minutes=i)).isoformat() for i in range(5)])

    def test_a_message_with_no_date_has_an_empty_one(self):
        aid = store.create_analysis()
        self.add_chat(aid, 2, stamp=lambda i: '')
        with store.db() as c:
            c.execute('UPDATE communication_units SET timestamp=NULL WHERE analysis_id=?', (aid,))
        self.assertEqual([u['timestamp'] for u in store.rated_units(aid)], ['', ''])

    def test_messages_that_were_not_rated_are_not_in_it(self):
        aid = store.create_analysis()
        self.add_chat(aid, 3)
        self.add_chat(aid, 4, rate=False)
        self.assertEqual(len(store.rated_units(aid)), 3)

    def test_a_message_with_only_some_of_its_ratings_comes_with_those_and_measures_calls_it_incomplete(self):
        aid = store.create_analysis()
        ids = self.add_chat(aid, 3, tones=lambda i: {'Polite': 2})
        with store.db() as c:
            c.execute('DELETE FROM unit_ratings WHERE unit_id=? AND attribute IN (?, ?)', (ids[1], 'Rude', 'Funny'))
        units = store.rated_units(aid)
        self.assertEqual([len(u['ratings']) for u in units], [13, 11, 13])
        m = measures.analyse(units, 'medium', today=datetime.date(2026, 6, 1))
        self.assertEqual((m['n'], m['incomplete']), (2, 1))

    def test_another_workplace_is_not_in_it(self):
        a, b = store.create_analysis(), store.create_analysis()
        self.add_chat(a, 3)
        self.add_chat(b, 5)
        self.assertEqual(len(store.rated_units(a)), 3)
        self.assertEqual(len(store.rated_units(b)), 5)
        self.assertEqual(store.rated_units(10 ** 9), [])


# =============================================================================================================================================
class SaveSnapshotTests(StoreCase):
    """measures.save_snapshot and measures.build on a real database: one record per check, the trail, the comparison."""

    RATED = {'rated': 60, 'total': 60, 'note': None, 'model': 'qwen2.5:7b'}

    def check(self, aid, n=60, tones=lambda i: {'Rude': 2} if i % 6 == 0 else {}):
        store.update_analysis(aid, model_used='qwen2.5:7b')                             # what a run of the ratings leaves on the row
        self.add_chat(aid, n, tones=tones)
        return measures.save_snapshot(aid, dict(self.RATED, rated=n, total=n))

    def test_a_check_saves_one_record_and_running_it_again_replaces_it(self):
        aid = store.create_analysis()
        rec = self.check(aid)
        self.assertIsNotNone(rec)
        history = store.measure_history(aid)
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0]['n'], history[0]['hostile_n'], history[0]['gen']), (60, 10, store.evidence_generation(aid)))
        measures.save_snapshot(aid, self.RATED)
        measures.save_snapshot(aid, self.RATED)
        self.assertEqual(len(store.measure_history(aid)), 1, 'running the same evidence again is not a new check')

    def test_a_new_check_adds_a_record_and_the_trail_and_the_comparison_follow(self):
        aid = store.create_analysis()
        self.check(aid)
        store.new_check(aid)
        self.check(aid, tones=lambda i: {'Rude': 2} if i % 30 == 0 else {})
        history = store.measure_history(aid)
        self.assertEqual(len(history), 2)
        self.assertLess(history[0]['gen'], history[1]['gen'])
        self.assertEqual([r['hostile_n'] for r in history], [10, 2])
        ctx = measures.build(aid, store.get_analysis(aid), {'status': 'ok', 'analyzed_n': 60, 'toxicity_types': {}, 'flagged': []},
                             {'sampled': False, 'total': 60, 'scope': 60, 'added_since': 0})
        self.assertEqual([r['hostile_n'] for r in ctx['trail']], [10, 2])
        self.assertEqual(ctx['trail'][-1], ctx['record'])
        self.assertTrue(ctx['since']['comparable'], ctx['since'])
        self.assertEqual({r['label']: r['verdict'] for r in ctx['since']['rows']}['Conflict'], 'Better')

    def test_build_leaves_the_history_alone(self):
        aid = store.create_analysis()
        self.check(aid)
        before = store.measure_history(aid)
        measures.build(aid, store.get_analysis(aid), None, {'sampled': False, 'total': 60, 'scope': 60, 'added_since': 0})
        self.assertEqual(store.measure_history(aid), before)

    def test_nothing_is_saved_without_ratings_and_it_does_not_raise(self):
        aid = store.create_analysis()
        self.assertIsNone(measures.save_snapshot(aid, self.RATED))
        self.add_chat(aid, 10, rate=False)
        self.assertIsNone(measures.save_snapshot(aid, self.RATED))
        self.assertEqual(store.measure_history(aid), [])
        self.assertIsNone(measures.build(aid, store.get_analysis(aid), None, {}))
        self.assertIsNone(measures.save_snapshot(10 ** 9, self.RATED))

    def test_the_record_holds_the_settings_of_the_analysis_row_and_no_name(self):
        aid = store.create_analysis()
        store.update_analysis(aid, vibe_sensitivity='low', person_name='ZQ Person', channel='ZQCHANNEL', incognito=1)
        self.check(aid)
        rec = store.measure_history(aid)[0]
        self.assertEqual(rec['sensitivity'], 'low')
        self.assertNotIn('ZQ', json.dumps(rec))
        self.assertEqual(self.raw_rows(aid)[0]['snapshot_json'].count('ZQ'), 0)


# =============================================================================================================================================
try:
    from test_app import AppCase
except unittest.SkipTest:                                       # no web stack: the store tests above still run
    AppCase = None

if AppCase is not None:
    class MeasuresCanaryTests(AppCase):
        """A word that is in a message, in the name of its source and in the name of its file never reaches the saved measures, and none of it is left in the
        database after the messages are deleted -- though the numbers of the check stay."""

        CANARY = 'zqmeasurecanary'

        def csv(self, n=60, word=None):
            word = word or self.CANARY
            rows = ['timestamp,source,text']
            for i in range(n):
                text = f'{word} message {i}: you are a stupid idiot' if i % 5 == 0 else f'{word} thanks for the update number {i}'
                rows.append(f'{(T0 + datetime.timedelta(minutes=i)).isoformat()},{word}-room-{i % 2},{text}')
            return '\n'.join(rows).encode()

        def database_bytes(self):
            return database_bytes(self.tmp)

        def test_the_canary_is_never_in_the_measure_history_and_never_on_disk_after_a_delete(self):
            aid = self.new_analysis()
            with stock_sqlite():
                self.upload(aid, (f'{self.CANARY}-export.csv', self.csv(), 'text/csv'))
                job = self.run_job(aid, path='check/6', engine='')
                self.assertEqual(job['status'], 'complete', job)
                self.assertTrue(self.CANARY.encode() in self.database_bytes(), 'the messages and the names are stored while they are needed')
                history = self.store.measure_history(aid)
                self.assertEqual(len(history), 1)
                self.assertGreater(history[0]['n'], 0)
                with self.store.db() as c:
                    rows = [r['snapshot_json'] for r in c.execute('SELECT snapshot_json FROM measure_history WHERE analysis_id=?', (aid,))]
                self.assertEqual(len(rows), 1)
                self.assertNotIn(self.CANARY, rows[0], 'not a message, not a source name, not a file name')
                self.assertTrue(set(json.loads(rows[0])) <= ALLOWED)
                self.client.post(f'/analysis/{aid}/clear-units')
                self.assertEqual(self.store.count_units(aid), 0)
                self.assertFalse(self.CANARY.encode() in self.database_bytes(), 'a delete left a message or a name on disk')
            after = self.store.measure_history(aid)
            self.assertEqual(after, history, 'the numbers of the check stay when its messages go')

        def test_the_canary_does_not_survive_a_new_check_either(self):
            aid = self.new_analysis()
            with stock_sqlite():
                self.upload(aid, (f'{self.CANARY}-export.csv', self.csv(), 'text/csv'))
                self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
                self.client.post(f'/analysis/{aid}/check/new')
                self.assertFalse(self.CANARY.encode() in self.database_bytes(), 'New check left a message or a name on disk')
            self.assertEqual(len(self.store.measure_history(aid)), 1)

        def test_a_second_check_makes_a_second_record_and_a_rerun_does_not(self):
            aid = self.new_analysis()
            self.addCleanup(self.store.clear_evidence, aid)
            self.upload(aid, ('first.csv', self.csv(word='harmless'), 'text/csv'))
            self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
            self.run_job(aid, path='check/6', engine='')
            self.assertEqual(len(self.store.measure_history(aid)), 1, 'the same evidence run again replaces its record')
            self.client.post(f'/analysis/{aid}/check/new')
            self.upload(aid, ('second.csv', self.csv(word='harmless'), 'text/csv'))
            self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
            history = self.store.measure_history(aid)
            self.assertEqual(len(history), 2)
            self.assertLess(history[0]['gen'], history[1]['gen'])
            self.assertEqual({k: v for k, v in history[0].items() if k in ('n', 'model', 'sensitivity', 'basis')},
                             {k: v for k, v in history[1].items() if k in ('n', 'model', 'sensitivity', 'basis')})


if __name__ == '__main__':
    unittest.main()
