"""Unit tests for Measures over time, alerts, badges and "since your last check" (measures.py).

They are written from MEASURES.md, not from the code: every expected number was worked out by hand from the rules in that file, and the arithmetic
is in a comment beside it. A test that fails is a question about the code or about the spec, never a number to nudge. Where a test builds a chat, it
builds the exact counts the arithmetic needs, so a boundary is tried on both sides (the last message that counts and the first that does not).

The ratings are synthetic. Nothing here says how well a model rates real chats. Two floors of the spec cannot be tried alone, because other rules always
bind first: the 3 hostile messages of a level alert (a rate of 12 per 100 over at least 24 messages already means 3) and the 5 care messages that a fall must
start from (a fall that is "strong" needs about 9). They are covered through the rules that imply them.

What the wording and the surfaces rely on is held here too, number by number: Escalation reads Too early (grey, and with no count) while nothing can be judged, and the three
rate cards and the health tile are grey below 48 rated messages; every mark is grey when almost no tone was found (19 in 20 messages rated all 0), and Escalation's None is grey
when the chat was already tense in its first part; the card note for a hot part that chance could give, and the one for a tense start (on the Escalation card only); the quiet
notes (Low sensitivity, too few dated messages, almost no tone, a sample, messages added later) and the section each note belongs to (`about`); the reason two checks are not
alike, which is said once with the comparison (`since` and `comparison`) and is never a note; `all_clear`, which is false for a silent chat of any size; the No serious flags badge,
held back while a High priority alert stands; the fingerprint of the model name (`model_key`), which decides whether two checks used one model; the sentence under the four small
charts (`chart_key`); the two fixed sentences (`texts`) and the five caveats; the mark of every card and of the health tile in words (`tone_word`); one message or several in the
serious alert and in the basis line; one record per check in the trail; a damaged count clamped by `compare` and read as "Not known" when the store dropped it; raw part dates
that are days only under Incognito. Run with:

    python3 -m unittest tests.test_measures -v
"""
import collections
import datetime
import functools
import hashlib
import itertools
import json
import math
import pathlib
import random
import re
import sys
import unittest
from fractions import Fraction
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import measures  # noqa: E402
import store  # noqa: E402
from vibe_check.engine import _PUNITIVE  # noqa: E402

# ---- what the spec fixes ---------------------------------------------------------------------------------------------------------------------
TONES = ('Polite', 'Funny', 'Empathetic', 'Encouraging', 'Ignorant', 'Egocentric', 'Neurotic', 'Sarcastic', 'Agitated', 'Aggressive', 'Rude', 'Disrespectful',
         'Judgmental')
OVERT = ('Aggressive', 'Rude', 'Disrespectful', 'Judgmental')                      # "hostile" when any is rated 2 or 3
POSITIVE = {'Polite': 3, 'Empathetic': 3, 'Encouraging': 2, 'Funny': 1}             # the weights of the health score
NEGATIVE = {'Ignorant': 1, 'Egocentric': 1, 'Neurotic': 1, 'Sarcastic': 2, 'Agitated': 2, 'Aggressive': 3, 'Judgmental': 3, 'Disrespectful': 3, 'Rude': 3}
KINDS = ('hostile', 'care', 'dig', 'heated', 'polite', 'encouraging', 'funny')
# one tone that sets exactly one flag of its own (rated 2), to build chats whose counts are known
FLAG_TONE = {'hostile': {'Rude': 2}, 'care': {'Empathetic': 2}, 'dig': {'Sarcastic': 2}, 'heated': {'Agitated': 2}, 'polite': {'Polite': 2},
             'encouraging': {'Encouraging': 2}, 'funny': {'Funny': 2}}

TODAY = datetime.date(2026, 6, 1)                       # the day "today" is for every test, so that no test depends on the clock
T0 = datetime.datetime(2026, 3, 5, 18, 0)

# the sentences that MEASURES.md and measures.py fix word for word
CHART_KEY = 'Lines show messages per 100. Shaded bands show where Rare, Some and Often begin. A darker band is a heated moment.'       # ms['chart_key'], before any extra
SAMPLED = 'A short flare-up can fall between the samples. Choose "all of them" for the volume to see every message.'                  # the note about a sample
HELD_BACK = 'Held back while an alert about serious flags or a High priority alert stands.'                                                  # why No serious flags is not given
TENSE_NOTE = r'(?i)\balready tense\b'                                                                                                 # the card note for a tense start (a pattern: the spec quotes no wording)
CHANCE_NOTE = 'Some parts were heated, but not by more than chance alone would give.'                                                  # the card note for a hot part that chance could give
REMOVED_NOTES = ('not_alike', 'opening')                                                                                              # notes that no longer exist


def x_of_n(x, n):
    """The pattern of a count in the table of Since your last check: 'x of n', with or without the word 'messages' after it."""
    return rf'^{x:,} of {n:,}( messages)?$'


def fingerprint(model):
    """The fingerprint a record keeps of a model name (MEASURES.md: so two names the store cannot keep are never taken for one model). Saved records are compared by
    it, so it must not change from one run, one process or one release to the next: here it is the first 12 hex digits of the SHA-256 of the name, as measures.py makes it."""
    return hashlib.sha256(model.encode('utf-8')).hexdigest()[:12] if model else ''


# ---- builders --------------------------------------------------------------------------------------------------------------------------------
def ratings(**tones):
    """All 13 tones at 0, then the tones given."""
    out = dict.fromkeys(TONES, 0)
    out.update(tones)
    return out


def stamp(i, step=1):
    """The i-th minute (or every `step` minutes) from T0, as an ISO string without an offset."""
    return (T0 + datetime.timedelta(minutes=i * step)).isoformat()


def unit(i, ts=None, **tones):
    """The i-th message (0-based): id i+1, one minute after the one before, the tones given and the rest 0."""
    return {'id': i + 1, 'timestamp': stamp(i) if ts is None else ts, 'ratings': ratings(**tones)}


def chat(n, pick=None, step=1, dated=True):
    """n messages. pick(i) gives the tones of message i (a dict) or nothing for a message with no tone at all."""
    return [unit(i, stamp(i, step) if dated else '', **((pick(i) if pick else None) or {})) for i in range(n)]


def spread(n, k):
    """k positions out of n, evenly spaced (no two next to each other while k <= n/2), so that no part of the chat holds more than its share."""
    return {(2 * j + 1) * n // (2 * k) for j in range(k)}


def part_chat(counts, size, tones):
    """len(counts) parts of `size` messages each: in part j the first counts[j] messages carry `tones`. Needs 24 parts or a size of 8 (measures.cut
    then returns exactly these parts); the builder checks it."""
    units = [unit(j * size + t, **(tones if t < c else {})) for j, c in enumerate(counts) for t in range(size)]
    parts = measures.cut(len(units))
    assert len(parts) == len(counts) and all(e - s == size for s, e in parts), (len(counts), size, parts)
    return units


def an(units, sensitivity='medium', incognito=False):
    return measures.analyse(units, sensitivity, today=TODAY, incognito=incognito)


def vibe_ok(n, **kinds):
    """A finished Vibe Check run. `kinds` is its tally of flagged messages by type (insults=, threats=, ...)."""
    return {'status': 'ok', 'analyzed_n': n, 'toxicity_types': dict(kinds), 'flagged': []}


def cov_full(n):
    return {'sampled': False, 'total': n, 'scope': n, 'added_since': 0}


def scan(units, sensitivity='medium', incognito=False, vibe='ok', cov=None, comparison=None):
    """analyse + alerts: (m, alerts, notes). vibe='ok' is a clean finished run; pass None or a dict for anything else."""
    m = an(units, sensitivity, incognito)
    vibe = vibe_ok(len(units)) if vibe == 'ok' else vibe
    found, notes = measures.alerts(m, vibe, comparison, cov or cov_full(len(units)))
    return m, found, notes


def find(found, pattern):
    """The alerts whose title matches the pattern (case-insensitive)."""
    return [a for a in found if re.search(pattern, a['title'], re.I)]


def tally_of(n, **counts):
    """The tally of n messages of which counts[kind] show that flag (the first ones), built from real ratings."""
    flags = []
    for i in range(n):
        tones = {}
        for kind, count in counts.items():
            if i < count:
                tones.update(FLAG_TONE[kind])
        flags.append(measures.message_flags(ratings(**tones)))
    return measures.tally(flags)


def build_with(units, a=None, vibe='ok', cov=None, history=(), gen=2):
    """measures.build, the real one, on units given here: the three reads it makes from the store are replaced, so no database is needed.
    `a` is the analysis row (a dict is enough); `history` the saved records of earlier checks; `gen` the evidence generation of this check."""
    n = len(units)
    vibe = vibe_ok(n) if vibe == 'ok' else vibe
    row = {'vibe_sensitivity': 'medium', 'incognito': 0, 'model_used': 'qwen2.5:7b', 'period_start': '', 'period_end': ''}
    row.update(a or {})
    real_analyse = measures.analyse                                                     # build does not take a "today": pin it, so that no test depends on the clock
    pinned = lambda units, sensitivity='medium', today=None, incognito=False: real_analyse(units, sensitivity, today or TODAY, incognito)       # noqa: E731
    with mock.patch.object(measures.store, 'rated_units', return_value=units), mock.patch.object(measures.store, 'evidence_generation', return_value=gen), \
            mock.patch.object(measures.store, 'measure_history', return_value=list(history)), mock.patch.object(measures, 'analyse', pinned):
        return measures.build(1, row, vibe, cov or cov_full(n))


def page_strings(ctx):
    """Every string of a built context that a page, the PDF or the CSV can show: [(where, text)]. Raw ISO times (stretch start and end) are data, not text."""
    out = []

    def add(where, text):
        if isinstance(text, str) and text.strip():
            out.append((where, text))

    for s in ctx['m']['stretches']:
        add('stretch.label', s['label'])
        add('stretch.axis', s['axis'])
    for c in ctx['cards']:
        for key in ('name', 'meaning', 'level', 'tone_word', 'headline', 'trend', 'note', 'latest'):
            add(f'card.{c["key"]}.{key}', c.get(key))
    for key in ('name', 'status', 'tone_word', 'trend', 'caption'):
        add(f'health.{key}', ctx['health'].get(key))
    for key, text in ctx['texts'].items():
        add(f'text.{key}', text)
    for a in ctx['alerts']:
        for key in ('title', 'detail', 'try', 'base', 'when'):
            add(f'alert.{a["key"]}.{key}', a.get(key))
    for n in ctx['notes']:
        add(f'note.{n["key"]}', n['text'])
    for b in ctx['badges']:
        for key in ('name', 'how', 'evidence', 'needs'):
            add(f'badge.{b["key"]}.{key}', b.get(key))
    add('badge_note', ctx['badge_note'])
    if ctx.get('members'):
        add('members.text', ctx['members']['text'])
        add('members.note', ctx['members']['note'])
    if ctx['since']:
        add('since.why', ctx['since'].get('why'))
        for row in ctx['since']['rows']:
            for key in ('label', 'before', 'now', 'verdict'):
                add(f'since.{key}', row.get(key))
    if ctx['comparison']:
        add('comparison.why', ctx['comparison'].get('why'))
    add('basis_text', ctx['basis_text'])
    add('chart_key', ctx['chart_key'])
    for key, text in ctx['caveats'].items():
        add(f'caveat.{key}', text)
    for name in measures.LEVEL_NAMES.values():
        add('level_name', name)
    return out


# ---- the spec's own formulas, written again here as the oracle ---------------------------------------------------------------------------------
def smoothed(count, n):
    """MEASURES.md: p = (count + 1) / (n + 2)."""
    return (count + 1) / (n + 2)


def se_of_change(c1, n1, c2, n2):
    """MEASURES.md, direction: 100 x sqrt(v1/n1 + v2/n2) with v = p(1-p) and p = (count + 1) / (n + 2)."""
    p1, p2 = smoothed(c1, n1), smoothed(c2, n2)
    return 100 * math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)


def clears(k, n, cut):
    """MEASURES.md, level alerts and badges: 100p - 100 sqrt(p(1-p)/n) >= cut, with p = k / n."""
    p = k / n
    return 100 * p - 100 * math.sqrt(p * (1 - p) / n) >= cut


def expected_health(pos, neg):
    """MEASURES.md: clamp(50 + 50 x (mean positive - mean negative) / 4.5, 0, 100)."""
    return min(100.0, max(0.0, 50 + 50 * (pos - neg) / 4.5))


def expected_flags(r):
    """The flag table of MEASURES.md, for a ratings dict, as the oracle of the exhaustive test."""
    hostile = any(r[t] >= 2 for t in OVERT)
    dig = r['Sarcastic'] >= 2 and not hostile and r['Funny'] < 2
    return {'hostile': hostile, 'care': r['Empathetic'] >= 2, 'dig': dig, 'heated': hostile or r['Agitated'] >= 2 or dig, 'polite': r['Polite'] >= 2,
            'encouraging': r['Encouraging'] >= 2, 'funny': r['Funny'] >= 2}


# =============================================================================================================================================
class FlagTests(unittest.TestCase):
    """How a message is counted: the table in MEASURES.md, one row at a time and every combination of the tones that decide it."""

    def flags(self, **tones):
        got = measures.message_flags(ratings(**tones))
        return {kind: bool(got[kind]) for kind in KINDS}

    @staticmethod
    def only(*kinds):
        return {kind: kind in kinds for kind in KINDS}

    def test_there_are_thirteen_tones_and_these_are_the_names(self):
        import analysis
        self.assertEqual(sorted(TONES), sorted(analysis.ATTRIBUTES))

    def test_each_tone_alone_sets_the_flags_of_its_row_at_2_and_3_and_none_at_0_or_1(self):
        expected = {'Polite': ('polite',), 'Funny': ('funny',), 'Empathetic': ('care',), 'Encouraging': ('encouraging',),
                    'Ignorant': (), 'Egocentric': (), 'Neurotic': (),                    # these three only move the health score
                    'Sarcastic': ('dig', 'heated'),                                      # a dig is heated
                    'Agitated': ('heated',),
                    'Aggressive': ('hostile', 'heated'), 'Rude': ('hostile', 'heated'), 'Disrespectful': ('hostile', 'heated'), 'Judgmental': ('hostile', 'heated')}
        self.assertEqual(sorted(expected), sorted(TONES))
        for tone in TONES:
            for rating in (0, 1):                                                       # a 1 is a weak reading and never counts
                with self.subTest(tone=tone, rating=rating):
                    self.assertEqual(self.flags(**{tone: rating}), self.only())
            for rating in (2, 3):
                with self.subTest(tone=tone, rating=rating):
                    self.assertEqual(self.flags(**{tone: rating}), self.only(*expected[tone]))

    def test_a_message_with_every_tone_at_1_shows_nothing(self):
        self.assertEqual(self.flags(**dict.fromkeys(TONES, 1)), self.only())

    def test_sarcastic_with_funny_2_is_banter_not_a_dig(self):
        self.assertEqual(self.flags(Sarcastic=2, Funny=2), self.only('funny'))
        self.assertEqual(self.flags(Sarcastic=3, Funny=3), self.only('funny'))
        self.assertEqual(self.flags(Sarcastic=2, Funny=3), self.only('funny'))

    def test_sarcastic_with_funny_1_is_still_a_dig_because_a_1_never_counts(self):
        self.assertEqual(self.flags(Sarcastic=2, Funny=1), self.only('dig', 'heated'))

    def test_sarcastic_with_a_hostile_tone_is_hostile_not_a_dig(self):
        for tone in OVERT:
            for rating in (2, 3):
                with self.subTest(tone=tone, rating=rating):
                    self.assertEqual(self.flags(Sarcastic=2, **{tone: rating}), self.only('hostile', 'heated'))
        self.assertEqual(self.flags(Sarcastic=3, Rude=2, Funny=2), self.only('hostile', 'heated', 'funny'))      # funny does not undo hostile

    def test_sarcastic_with_a_hostile_tone_rated_1_is_a_dig(self):
        for tone in OVERT:
            with self.subTest(tone=tone):
                self.assertEqual(self.flags(Sarcastic=2, **{tone: 1}), self.only('dig', 'heated'))

    def test_sarcastic_rated_1_is_not_a_dig_whatever_else_is_there(self):
        self.assertEqual(self.flags(Sarcastic=1), self.only())
        self.assertEqual(self.flags(Sarcastic=1, Funny=0, Polite=3), self.only('polite'))

    def test_agitated_2_is_heated_and_agitated_1_is_not_and_neither_is_hostile_or_a_dig(self):
        self.assertEqual(self.flags(Agitated=2), self.only('heated'))
        self.assertEqual(self.flags(Agitated=1), self.only())
        self.assertEqual(self.flags(Agitated=2, Sarcastic=2), self.only('dig', 'heated'))

    def test_flags_do_not_exclude_each_other_unless_the_table_says_so(self):
        self.assertEqual(self.flags(Rude=2, Empathetic=2), self.only('hostile', 'heated', 'care'))
        self.assertEqual(self.flags(Polite=3, Empathetic=3, Encouraging=3, Funny=3), self.only('polite', 'care', 'encouraging', 'funny'))
        everything = self.flags(**dict.fromkeys(TONES, 3))
        self.assertEqual(everything, self.only('hostile', 'heated', 'care', 'polite', 'encouraging', 'funny'))      # hostile, so not a dig

    def test_every_combination_of_the_tones_that_decide_a_flag_matches_the_table(self):
        deciding = ('Sarcastic', 'Funny', 'Agitated') + OVERT                          # 4^7 = 16,384 messages
        for values in itertools.product(range(4), repeat=len(deciding)):
            r = ratings(**dict(zip(deciding, values)))
            want = expected_flags(r)
            got = measures.message_flags(r)
            for kind in KINDS:
                if bool(got[kind]) != want[kind]:
                    self.fail(f'{kind} for {dict(zip(deciding, values))}: got {got[kind]}, the table says {want[kind]}')
            self.assertLessEqual(got['hostile'] + got['dig'], 1, 'a message is never both hostile and a dig')

    def test_bad_values_are_read_as_the_nearest_valid_rating(self):
        self.assertFalse(self.flags(Rude=None)['hostile'])                              # no rating counts as 0
        self.assertFalse(self.flags(Rude=-4)['hostile'])                                # below 0 is 0
        self.assertFalse(self.flags(Rude='nonsense')['hostile'])
        self.assertTrue(self.flags(Rude=7)['hostile'])                                  # above 3 is 3
        self.assertTrue(self.flags(Rude='2')['hostile'])                                # a number written as text is that number
        self.assertFalse(self.flags(Rude='1')['hostile'])
        self.assertTrue(self.flags(Empathetic=99)['care'])
        # a missing tone is a 0
        only_rude = measures.message_flags({'Rude': 3})
        self.assertTrue(only_rude['hostile'] and not only_rude['care'])

    def test_a_rating_that_is_not_a_finite_number_counts_as_0_and_never_raises(self):
        """measures.py says bad values count as 0. An infinity raised OverflowError (int(float('inf'))), a nan ValueError; neither may reach a page."""
        for bad in (float('inf'), float('-inf'), float('nan'), -10 ** 400, [], {}, object(), '\x00', 1j, None):
            with self.subTest(rating=repr(bad)[:30]):
                got = measures.message_flags(ratings(Rude=bad, Empathetic=bad, Polite=bad, Sarcastic=bad, Funny=bad, Agitated=bad))
                self.assertEqual([got[k] for k in KINDS], [0] * len(KINDS))
                self.assertEqual((got['pos'], got['neg']), (0, 0))
                self.assertEqual(got['silent'], 1, 'a rating that cannot be read is a 0, and a message of 0s is silent')
        self.assertTrue(self.flags(Rude=10 ** 400)['hostile'], 'a number above 3, however large, is a 3')

    def test_a_rating_of_7_acts_exactly_like_3_in_the_health_score_too(self):
        wild = an([unit(0, Rude=7, Polite=9)])['health']
        tame = an([unit(0, Rude=3, Polite=3)])['health']
        self.assertAlmostEqual(wild, tame, places=9)


# =============================================================================================================================================
class HealthTests(unittest.TestCase):
    """The 0 to 100 health score: weights, the divisor 4.5, the clamp, the bands."""

    def score(self, *messages):
        return an([unit(i, **tones) for i, tones in enumerate(messages)])['health']

    def test_a_chat_with_no_tone_at_all_scores_exactly_50(self):
        self.assertEqual(self.score({}), 50.0)
        self.assertEqual(self.score(*[{}] * 60), 50.0)
        self.assertEqual(measures.health_of(0, 0), 50.0)
        self.assertEqual(an(chat(96))['health'], 50.0)

    def test_each_tone_moves_the_score_by_its_weight_times_rating_over_3(self):
        for tone, weight in {**POSITIVE, **NEGATIVE}.items():
            for rating in (1, 2, 3):
                x = rating / 3
                pos, neg = (weight * x, 0) if tone in POSITIVE else (0, weight * x)
                with self.subTest(tone=tone, rating=rating):
                    # e.g. Polite 3: 50 + 50 x 3 / 4.5 = 83.33; Rude 2: 50 - 50 x (3 x 2/3) / 4.5 = 27.78
                    self.assertAlmostEqual(self.score({tone: rating}), expected_health(pos, neg), places=2)      # analyse() rounds the score to 3 places, once

    def test_the_divisor_is_4_5_so_polite_3_alone_is_83_33_not_66_67(self):
        self.assertAlmostEqual(self.score({'Polite': 3}), 83.3333333, places=2)         # 50 + 50 x 3 / 4.5
        self.assertAlmostEqual(self.score({'Rude': 2}), 27.7777778, places=2)           # 50 - 50 x 2 / 4.5
        self.assertEqual(measures.health_of(2.25, 0), 75.0)                             # 50 + 50 x 2.25 / 4.5
        self.assertEqual(measures.health_of(0, 2.25), 25.0)
        self.assertEqual(measures.health_of(4.5, 0), 100.0)                             # the divisor itself reaches the top exactly
        self.assertEqual(measures.health_of(0, 4.5), 0.0)

    def test_the_score_is_clamped_at_0_and_100(self):
        warm = {'Polite': 3, 'Empathetic': 3, 'Encouraging': 3, 'Funny': 3}             # positive = 3 + 3 + 2 + 1 = 9, so 50 + 50 x 9 / 4.5 = 150
        cold = dict.fromkeys(NEGATIVE, 3)                                               # negative = 1+1+1+2+2+3+3+3+3 = 19, so 50 - 50 x 19 / 4.5 < 0
        self.assertEqual(self.score(warm), 100.0)
        self.assertEqual(self.score(cold), 0.0)
        self.assertEqual(measures.health_of(9, 0), 100.0)
        self.assertEqual(measures.health_of(0, 19), 0.0)
        self.assertEqual(measures.health_of(1000, 0), 100.0)
        self.assertEqual(measures.health_of(0, 1000), 0.0)

    def test_the_group_score_uses_the_mean_of_the_messages(self):
        # mean positive = 3/2 = 1.5 and mean negative = 3/2 = 1.5, so exactly 50
        self.assertEqual(self.score({'Polite': 3}, {'Rude': 3}), 50.0)
        # [Polite 3, Polite 3, Rude 3]: mean positive 6/3 = 2.0, mean negative 3/3 = 1.0, so 50 + 50 x 1.0 / 4.5 = 61.11
        self.assertAlmostEqual(self.score({'Polite': 3}, {'Polite': 3}, {'Rude': 3}), 61.1111111, places=2)
        # one warm message among nine that have no tone: mean positive 3/10, so 50 + 50 x 0.3 / 4.5 = 53.33
        self.assertAlmostEqual(self.score({'Polite': 3}, *[{}] * 9), 53.3333333, places=2)

    def test_each_stretch_has_the_health_score_of_its_own_messages(self):
        # six stretches of 8: all Polite 3 -> 83.33; no tone -> 50; all Rude 3 -> 16.67; Polite 3 on every other message (mean positive 1.5) -> 66.67;
        # all Funny 3 -> 50 + 50 x 1 / 4.5 = 61.11; all Empathetic 2 -> 50 + 50 x 2 / 4.5 = 72.22
        plans = [{'Polite': 3}, {}, {'Rude': 3}, 'half', {'Funny': 3}, {'Empathetic': 2}]
        units = [unit(j * 8 + t, **(({'Polite': 3} if t % 2 == 0 else {}) if plan == 'half' else plan)) for j, plan in enumerate(plans) for t in range(8)]
        m = an(units)
        want = [83.3333333, 50.0, 16.6666667, 66.6666667, 61.1111111, 72.2222222]
        self.assertEqual(len(m['stretches']), 6)
        for got, expected in zip([s['health'] for s in m['stretches']], want):
            self.assertAlmostEqual(got, expected, places=5)
        self.assertAlmostEqual(m['health'], sum(want) / 6, places=2, msg='equal stretches: the whole is the mean of the parts')
        self.assertAlmostEqual(measures.health_tile(m)['series'][0], 83.33, delta=0.01)

    def test_the_status_bands_are_judged_on_the_whole_number_printed(self):
        want = [(0, 'Needs attention'), (12.3, 'Needs attention'), (39.49, 'Needs attention'), (39.4999, 'Needs attention'),
                (39.5, 'Fair'), (40, 'Fair'), (50, 'Fair'), (59.49, 'Fair'),
                (59.5, 'Good'), (60, 'Good'), (70, 'Good'), (79.49, 'Good'),
                (79.5, 'Excellent'), (80, 'Excellent'), (99, 'Excellent'), (100, 'Excellent')]
        for score, status in want:
            with self.subTest(score=score):
                self.assertEqual(measures.health_status(score), status)

    def test_whole_rounds_halves_up_not_to_even(self):
        for value, shown in [(0.5, 1), (1.5, 2), (2.5, 3), (3.5, 4), (4.5, 5), (11.5, 12), (2.49, 2), (7.0, 7), (0.0, 0), (99.5, 100)]:
            with self.subTest(value=value):
                self.assertEqual(measures.whole(value), shown)

    def test_every_band_can_be_reached_by_some_chat(self):
        self.assertEqual(measures.health_status(self.score({'Polite': 3})), 'Excellent')        # 50 + 50 x 3 / 4.5 = 83.3
        self.assertEqual(measures.health_status(self.score({'Polite': 2})), 'Good')             # 50 + 50 x 2 / 4.5 = 72.2
        self.assertEqual(measures.health_status(self.score({'Ignorant': 2})), 'Fair')           # 50 - 50 x (2/3) / 4.5 = 42.6
        self.assertEqual(measures.health_status(self.score({})), 'Fair')                        # 50
        self.assertEqual(measures.health_status(self.score({'Rude': 3})), 'Needs attention')    # 50 - 50 x 3 / 4.5 = 16.7

    def test_the_tile_shows_the_same_score_status_and_says_it_can_differ_from_vibe_check(self):
        m = an(chat(96, lambda i: {'Polite': 3} if i % 2 else {'Rude': 2}))
        tile = measures.health_tile(m)
        self.assertAlmostEqual(tile['score'], m['health'], places=9)
        self.assertEqual(tile['status'], measures.health_status(tile['score']))
        self.assertIn('Vibe Check', tile['caption'])
        self.assertRegex(tile['caption'], r'(?i)can differ|may differ|differ')
        self.assertEqual(measures.health_tile(an([unit(i, Polite=3) for i in range(48)]))['tone'], 'good')
        self.assertEqual(measures.health_tile(an([unit(i, Rude=3) for i in range(48)]))['tone'], 'bad')

    def test_the_tile_mark_is_neutral_below_48_messages_and_the_band_and_the_score_stay(self):
        """MEASURES.md: the health tile's mark is neutral below 48 messages (too few to call the score good or bad). The status word and the score are not touched."""
        for tones, status, mark in [({'Polite': 3}, 'Excellent', ('good', 'A good sign')), ({'Polite': 2}, 'Good', ('good', 'A good sign')),
                                    ({'Ignorant': 2}, 'Fair', ('info', 'Neutral')), ({'Rude': 3}, 'Needs attention', ('bad', 'Look at this first'))]:
            for n in (1, 10, 24, 40, 47):
                with self.subTest(status=status, n=n):
                    m = an(chat(n, lambda i: dict(tones)))
                    tile = measures.health_tile(m)
                    self.assertEqual((tile['status'], tile['tone'], tile['tone_word']), (status, 'info', 'Neutral'))
                    self.assertEqual(tile['score'], m['health'])
            for n in (48, 49, 96):
                with self.subTest(status=status, n=n):
                    tile = measures.health_tile(an(chat(n, lambda i: dict(tones))))
                    self.assertEqual((tile['status'], (tile['tone'], tile['tone_word'])), (status, mark))

    def test_the_tile_counts_rated_messages_and_not_the_ones_on_the_timeline(self):
        # 70 rated messages of which 45 carry a date: the timeline holds 45 (too few for a verdict), but 70 messages were rated, so the tile keeps its mark
        units = [unit(i, stamp(i) if i < 45 else '', Polite=3) for i in range(70)]
        m = an(units)
        self.assertEqual((m['n'], m['timeline_n']), (70, 45))
        self.assertEqual(measures.health_tile(m)['tone'], 'good')

    def test_the_tile_mark_is_neutral_when_almost_no_tone_was_found_and_the_band_stays(self):
        """MEASURES.md: the health tile's mark is neutral ... when no tone was found (19 in 20 messages rated all 0). One message in 20 can still pull the score into the
        lowest band, so the band is told apart from the mark here: 5 maximally negative messages in 100 score 39.4, and a 6th makes the chat not silent."""
        worst = dict.fromkeys(NEGATIVE, 3)                                              # negative = 19: 5 in 100 give 50 - 50 x 0.95 / 4.5 = 39.44
        for n, hot in [(60, 3), (100, 5), (200, 10)]:                                   # 57 of 60, 95 of 100 and 190 of 200 silent: the fewest that trip it
            for count, silent in [(hot, True), (hot + 1, False)]:
                with self.subTest(n=n, hot=count):
                    m = an(chat(n, lambda i: dict(worst) if i < count else None))
                    self.assertEqual(measures.is_silent(m), silent)
                    tile = measures.health_tile(m)
                    self.assertEqual(tile['status'], 'Needs attention', 'the band does not depend on the silence')
                    self.assertEqual((tile['tone'], tile['tone_word']), ('info', 'Neutral') if silent else ('bad', 'Look at this first'))
        for n in (48, 96, 300):                                                         # nothing at all: score 50, a Fair chat, grey either way
            tile = measures.health_tile(an(chat(n)))
            self.assertEqual((tile['score'], tile['status'], tile['tone'], tile['tone_word']), (50.0, 'Fair', 'info', 'Neutral'))


# =============================================================================================================================================
class OrderTests(unittest.TestCase):
    """Time order against upload order: 3 in 5 dated, the date range, three different moments, ties, offsets."""

    @staticmethod
    def order(stamps):
        """order_units on messages whose timestamps are `stamps` (ids 1..n in upload order): (ids in order, basis, left off)."""
        units = [{'id': i + 1, 'timestamp': s, 'ratings': ratings()} for i, s in enumerate(stamps)]
        ordered, times, basis, left = measures.order_units(units, TODAY)
        assert len(times) == len(ordered)
        return [u['id'] for u in ordered], basis, left

    @staticmethod
    def minutes(k, offset=0):
        return [stamp(offset + j) for j in range(k)]

    def test_no_messages_is_upload_order_with_nothing_left_off(self):
        self.assertEqual(self.order([]), ([], 'order', 0))

    def test_three_in_five_decides_for_5_messages(self):
        ids, basis, left = self.order(self.minutes(3) + ['', ''])                       # 3 of 5 = 3/5: dated
        self.assertEqual((ids, basis, left), ([1, 2, 3], 'time', 2))
        ids, basis, left = self.order(self.minutes(2) + ['', '', ''])                   # 2 of 5 is under 3/5
        self.assertEqual((ids, basis, left), ([1, 2, 3, 4, 5], 'order', 0))

    def test_three_in_five_decides_for_100_messages(self):
        ids, basis, left = self.order(self.minutes(60) + [''] * 40)                     # 60 of 100
        self.assertEqual((basis, left, len(ids)), ('time', 40, 60))
        ids, basis, left = self.order(self.minutes(59) + [''] * 41)                     # 59 of 100
        self.assertEqual((basis, left, ids), ('order', 0, list(range(1, 101))))

    def test_three_in_five_for_other_sizes(self):
        # dated needed = smallest k with k x 5 >= 3 x n
        for n, need in [(3, 2), (4, 3), (6, 4), (7, 5), (8, 5), (9, 6), (10, 6), (11, 7), (20, 12), (21, 13), (50, 30), (51, 31)]:
            for dated in (need - 1, need):
                if dated < 3:                                                           # three different moments are needed anyway
                    continue
                with self.subTest(n=n, dated=dated):
                    _, basis, left = self.order(self.minutes(dated) + [''] * (n - dated))
                    self.assertEqual((basis, left), ('time', n - dated) if dated == need else ('order', 0))

    def test_ties_keep_upload_order(self):
        t0, t1, t2 = stamp(0), stamp(1), stamp(2)
        ids, basis, _ = self.order([t1, t0, t1, t0, t2, t1])
        self.assertEqual((ids, basis), ([2, 4, 1, 3, 6, 5], 'time'))

    def test_messages_are_put_in_time_order_not_text_order(self):
        ids, _, _ = self.order([stamp(5), stamp(3), stamp(9), stamp(0), stamp(1)])
        self.assertEqual(ids, [4, 5, 2, 1, 3])

    def test_offsets_are_honoured_and_equal_moments_are_ties(self):
        # A 18:00+02:00 = 16:00Z, B 17:00Z, C 16:30Z, D 12:30-04:00 = 16:30Z (the same moment as C, uploaded after it)
        ids, basis, _ = self.order(['2026-03-05T18:00:00+02:00', '2026-03-05T17:00:00Z', '2026-03-05T16:30:00+00:00', '2026-03-05T12:30:00-04:00'])
        self.assertEqual((ids, basis), ([1, 3, 4, 2], 'time'))

    def test_the_same_moment_written_two_ways_is_one_moment_so_two_moments_are_not_enough(self):
        stamps = ['2026-03-05T18:00:00Z', '2026-03-05T20:00:00+02:00', '2026-03-05T19:00:00Z', '2026-03-05T21:00:00+02:00']        # 18:00Z twice, 19:00Z twice
        ids, basis, left = self.order(stamps)
        self.assertEqual((ids, basis, left), ([1, 2, 3, 4], 'order', 0))
        ids, basis, left = self.order(stamps + ['2026-03-05T21:00:00Z'])                # a third moment
        self.assertEqual((basis, left), ('time', 0))

    def test_fewer_than_three_different_moments_means_upload_order(self):
        one = ['2026-03-05T18:00:00'] * 10
        two = ['2026-03-05T18:00:00'] * 5 + ['2026-03-05T19:00:00'] * 5
        three = ['2026-03-05T18:00:00'] * 4 + ['2026-03-05T19:00:00'] * 3 + ['2026-03-05T20:00:00'] * 3
        self.assertEqual(self.order(one), (list(range(1, 11)), 'order', 0))
        self.assertEqual(self.order(two), (list(range(1, 11)), 'order', 0))
        self.assertEqual(self.order(three)[1:], ('time', 0))
        self.assertEqual(self.order(['2026-03-05T18:00:00', '2026-03-05T19:00:00'])[1], 'order')       # 2 messages, 2 moments
        self.assertEqual(self.order(['2026-03-05T18:00:00'])[1], 'order')

    def test_in_upload_order_nothing_is_left_off_and_dated_messages_are_not_sorted(self):
        stamps = [stamp(9), '', stamp(1), '', '']                                       # 2 of 5 are dated: upload order, newest first stays as it is
        self.assertEqual(self.order(stamps), ([1, 2, 3, 4, 5], 'order', 0))

    def test_the_date_range_starts_on_1990_01_01(self):
        valid = ['2026-03-05T17:00:00', '2026-03-05T18:00:00', '2026-03-05T19:00:00']
        for text, usable in [('1990-01-01T00:00:00', True), ('1990-01-01', True), ('1989-12-31T23:59:59', False), ('1989-12-31', False),
                             ('1970-01-01T00:00:00', False), ('1970-01-01T00:00:00Z', False), ('0001-01-01T00:00:00', False), ('0001-01-01', False),
                             ('9999-12-31T23:59:59', False), ('9999-12-31', False), ('2000-02-29T12:00:00', True)]:
            with self.subTest(stamp=text):
                ids, basis, left = self.order([text] + valid)
                self.assertEqual(basis, 'time')
                self.assertEqual((1 in ids, left), (usable, 0 if usable else 1))

    def test_the_date_range_ends_tomorrow(self):
        valid = ['2026-03-05T17:00:00', '2026-03-05T18:00:00', '2026-03-05T19:00:00']
        for text, usable in [('2026-06-01T12:00:00', True), ('2026-06-02T00:00:00', True), ('2026-06-02T12:00:00', True), ('2026-06-02T23:59:59', True),
                             ('2026-06-02', True), ('2026-06-03T00:00:00', False), ('2026-06-03T12:00:00', False), ('2026-06-03', False),
                             ('2030-01-01T00:00:00', False)]:
            with self.subTest(stamp=text):
                ids, _, left = self.order([text] + valid)
                self.assertEqual((1 in ids, left), (usable, 0 if usable else 1))

    def test_text_that_is_not_a_date_is_left_off_and_never_raises(self):
        valid = self.minutes(8)
        junk = ['yesterday', '', '   ', '12:30', '2026-13-45', '2026-13-45T10:00:00', 'not a date', '0000-00-00', '2026-02-30', '99999-01-01', '\x00', '١٢٣', None]
        ids, basis, left = self.order(valid + junk[:4])                                 # 8 of 12 are dated: 2/3 >= 3/5
        self.assertEqual((ids, basis, left), (list(range(1, 9)), 'time', 4))
        for text in junk:
            with self.subTest(stamp=text):
                ids, basis, left = self.order(valid + [text])
                self.assertEqual((ids, basis, left), (list(range(1, 9)), 'time', 1))

    def test_dates_without_a_time_are_dates(self):
        ids, basis, left = self.order(['2026-03-06', '2026-03-04', '2026-03-05', '2026-03-05'])
        self.assertEqual((ids, basis, left), ([2, 3, 4, 1], 'time', 0))

    def test_the_timeline_holds_only_the_dated_messages_but_the_overall_figures_hold_all(self):
        # 80 rated messages, 3 in every 5 dated: 48 dated (exactly 3/5), 32 not
        units = [unit(i, stamp(i) if i % 5 < 3 else '', Rude=2) for i in range(80)]
        m = an(units)
        self.assertEqual((m['basis'], m['n'], m['timeline_n'], m['left_off']), ('time', 80, 48, 32))
        self.assertEqual(m['overall']['n'], 80)
        self.assertEqual(m['overall']['hostile'], 80)
        self.assertEqual(sum(s['n'] for s in m['stretches']), 48)
        self.assertTrue(m['verdicts'])                                                  # 48 on the timeline
        dated_at = spread(78, 47)
        self.assertEqual(len(dated_at), 47)
        short = an([unit(i, stamp(i) if i in dated_at else '', Rude=2) for i in range(78)])
        self.assertEqual(short['timeline_n'], 47)                                       # 47 of 78 dated: 47 x 5 = 235 >= 3 x 78 = 234
        self.assertEqual(short['basis'], 'time')
        self.assertFalse(short['verdicts'])                                             # 47 timeline messages: no direction though 78 were rated

    def test_when_the_dates_do_not_qualify_the_messages_stay_in_upload_order_and_all_are_on_the_timeline(self):
        units = [unit(i, stamp(i) if i < 59 else '') for i in range(100)]
        m = an(units)
        self.assertEqual((m['basis'], m['timeline_n'], m['left_off']), ('order', 100, 0))
        self.assertEqual(sum(s['n'] for s in m['stretches']), 100)


# =============================================================================================================================================
class CutTests(unittest.TestCase):
    """Equal stretches: at least 8 messages, at most 24 stretches, sizes differ by at most one."""

    def check(self, n):
        parts = measures.cut(n)
        self.assertTrue(parts, n)
        self.assertEqual(parts[0][0], 0)
        self.assertEqual(parts[-1][1], n)
        for (a, b), (c, d) in zip(parts, parts[1:]):
            self.assertEqual(b, c, 'the parts touch')
        sizes = [e - s for s, e in parts]
        self.assertGreaterEqual(min(sizes), 8, n)
        self.assertLessEqual(max(sizes) - min(sizes), 1, n)
        self.assertLessEqual(len(parts), 24, n)
        self.assertEqual(sum(sizes), n)
        return sizes

    def test_the_floors_and_the_neighbours(self):
        # parts = min(24, n // 8); sizes: n // parts, and n % parts of them one larger
        want = {24: (3, {8: 3}),                        # 24 = 3 x 8
                31: (3, {10: 2, 11: 1}),                # 31 = 3 x 10 + 1
                32: (4, {8: 4}),                        # 32 = 4 x 8
                47: (5, {9: 3, 10: 2}),                 # 47 = 5 x 9 + 2
                48: (6, {8: 6}),                        # 48 = 6 x 8
                191: (23, {8: 16, 9: 7}),               # 191 = 23 x 8 + 7
                192: (24, {8: 24}),                     # 192 = 24 x 8
                300: (24, {12: 12, 13: 12}),            # 300 = 24 x 12 + 12
                5000: (24, {208: 16, 209: 8})}          # 5000 = 24 x 208 + 8
        for n, (parts, sizes) in want.items():
            with self.subTest(n=n):
                got = self.check(n)
                self.assertEqual(len(got), parts)
                self.assertEqual(collections.Counter(got), collections.Counter(sizes))

    def test_below_24_there_are_no_parts(self):
        for n in (0, 1, 7, 8, 15, 16, 23):
            with self.subTest(n=n):
                self.assertEqual(measures.cut(n), [])

    def test_every_size_from_24_to_700(self):
        for n in range(24, 701):
            sizes = self.check(n)
            self.assertEqual(len(sizes), min(24, n // 8), n)

    def test_big_sizes_keep_24_parts(self):
        for n in (1000, 4999, 5001, 20000):
            with self.subTest(n=n):
                self.assertEqual(len(self.check(n)), 24)

    def test_the_parts_of_100_are_twelve_with_four_larger_ones(self):
        sizes = self.check(100)
        self.assertEqual(collections.Counter(sizes), collections.Counter({8: 8, 9: 4}))      # 100 = 12 x 8 + 4


# =============================================================================================================================================
class FloorTests(unittest.TestCase):
    """The timeline floors: 24 (lines over time), 48 (a direction, a heated moment), 72 (level alerts read the latest third)."""

    @staticmethod
    def rise(n):
        """Calm for the first two thirds, then every message openly hostile (Rude 2)."""
        return chat(n, lambda i: {'Rude': 2} if i >= n * 2 // 3 else None)

    def test_below_24_only_the_overall_figures_exist(self):
        for n in (1, 10, 23):
            with self.subTest(n=n):
                m = an(self.rise(n))
                self.assertFalse(m['has_series'])
                self.assertEqual(m['stretches'], [])
                self.assertFalse(m['verdicts'])
                self.assertEqual(m['episodes'], [])
                self.assertEqual(m['overall']['n'], n)
                self.assertEqual(m['overall']['hostile'], n - n * 2 // 3)
                self.assertFalse(any(m['trend'].values()))
                for card in measures.cards(m):
                    self.assertFalse(card['series'], card['key'])
                _, found, notes = scan(self.rise(n))
                self.assertFalse(found, 'under 24 messages the level alert does not read')
                self.assertTrue(any(re.search(r'\b%d\b' % n, x['text']) for x in notes), 'a quiet note says how few were rated')

    def test_from_24_there_are_lines_over_time_but_no_direction_before_48(self):
        for n, parts in [(24, 3), (31, 3), (40, 5), (47, 5)]:
            with self.subTest(n=n):
                chat_ = self.rise(n)
                m, found, notes = scan(chat_)
                self.assertTrue(m['has_series'])
                self.assertEqual(len(m['stretches']), parts)
                self.assertFalse(m['verdicts'])
                self.assertEqual(m['episodes'], [])
                self.assertFalse(any(m['trend'].values()), 'no rising or falling before 48')
                self.assertFalse(find(found, r'heated up'))
                self.assertFalse(find(found, r'picking up|growing|easing|improved|fewer'))
                self.assertTrue(find(found, r'openly hostile'), 'the level alert reads from 24 messages')
                for card in measures.cards(m):
                    self.assertEqual(len(card['series']), parts, card['key'])
                    self.assertNotRegex(card['trend'], r'(?i)rising|falling|\bup\b|\bdown\b')
                    if card['key'] != 'escalation':
                        self.assertRegex(card['trend'], r'(?i)too (few|early)|direction', card['key'])
                self.assertTrue(any(re.search(r'(?i)direction|too early|48', x['text']) for x in notes), 'a quiet note says it is too early to call a direction')

    def test_at_48_a_direction_and_a_heated_moment_can_be_called(self):
        m, found, notes = scan(self.rise(48))
        self.assertTrue(m['verdicts'])
        self.assertEqual(m['trend']['hostile']['call'], 'rising')
        self.assertTrue(m['episodes'])
        self.assertTrue(find(found, r'heated up'))
        self.assertFalse(any(re.search(r'(?i)direction|too early', x['text']) for x in notes))

    def test_the_level_alert_needs_24_messages_in_all(self):
        # 9 hostile messages spread evenly: with n=24 that is 37.5 per 100, 37.5 - 9.88 = 27.6 >= 20 (High). With n=23 there is no alert at all.
        def hostile_chat(n):
            where = spread(n, 9)
            return chat(n, lambda i: {'Rude': 2} if i in where else None)

        self.assertTrue(find(scan(hostile_chat(24))[1], r'openly hostile'))
        self.assertFalse(find(scan(hostile_chat(23))[1], r'openly hostile'))

    def test_level_alerts_read_the_latest_third_from_72_and_the_whole_check_before(self):
        # 72 = 9 parts of 8: the latest third is 3 parts = 24 messages. 71 = 8 parts: a third is 2 parts, 17 or 18 messages, under 24.
        for n, based in [(71, 71), (72, 24), (96, 32)]:
            with self.subTest(n=n):
                where = spread(n, n // 4)                                               # 1 message in 4 is hostile, evenly: Medium in any window of 24 or more
                _, found, _ = scan(chat(n, lambda i: {'Rude': 2} if i in where else None))
                hostile = find(found, r'openly hostile')
                self.assertTrue(hostile, n)
                self.assertRegex(hostile[0]['base'], r'\b%d\b' % based)
                if n >= 72:
                    self.assertRegex(hostile[0]['base'], r'(?i)latest')
                else:
                    self.assertNotRegex(hostile[0]['base'], r'(?i)latest')

    def test_the_latest_third_decides_when_the_whole_check_would_say_something_else(self):
        # 96 messages. The first 64 are hostile 1 in 2; the last 32 not at all. At 96 the latest third (32 messages) has no hostile message: no level alert.
        n = 96
        early = chat(n, lambda i: {'Rude': 2} if i < 64 and i % 2 == 0 else None)
        self.assertFalse(find(scan(early)[1], r'openly hostile'))
        # the same hostile messages in a chat of 60 are the whole check, and there they are 1 in 2
        short = chat(60, lambda i: {'Rude': 2} if i % 2 == 0 else None)
        self.assertTrue(find(scan(short)[1], r'openly hostile'))

    def test_floors_count_timeline_messages_not_rated_messages(self):
        # 100 rated; 59 dated: upload order, so all 100 are on the timeline
        m = an(chat(100, lambda i: None, dated=False))
        self.assertEqual((m['timeline_n'], m['basis']), (100, 'order'))
        self.assertTrue(m['verdicts'])


@functools.lru_cache(maxsize=None)
def cached_tally(kind, n, count):
    return tally_of(n, **{kind: count})


def expected_call(c1, n1, c2, n2, need=2.0, floor=3.0, relative=0.2):
    """MEASURES.md, direction: rising or falling when the rate moved by at least max(3 points, 20% of where it started) and by at least 2.0 standard errors."""
    first, last = 100 * c1 / n1, 100 * c2 / n2
    delta = last - first
    if abs(delta) >= max(floor, relative * first) and abs(delta) >= need * se_of_change(c1, n1, c2, n2):
        return 'rising' if delta > 0 else 'falling'
    return 'steady'


# =============================================================================================================================================
class TallyTests(unittest.TestCase):
    """Counting: a tally of messages, tallies merged, a rate per 100 messages, and the counts a check reports for every flag."""

    def test_a_tally_counts_each_flag_and_adds_up_the_health_sums(self):
        groups = ({'Rude': 3}, {'Polite': 2, 'Funny': 3}, {'Empathetic': 2}, {}, {'Sarcastic': 2}, {'Encouraging': 3})
        t = measures.tally([measures.message_flags(ratings(**g)) for g in groups])
        self.assertEqual({k: t[k] for k in KINDS}, {'hostile': 1, 'care': 1, 'dig': 1, 'heated': 2, 'polite': 1, 'encouraging': 1, 'funny': 1})
        self.assertEqual((t['n'], t['silent']), (6, 1))
        # positive: Polite 2 -> 3 x 2/3 = 2.0, Funny 3 -> 1.0, Empathetic 2 -> 2.0, Encouraging 3 -> 2 x 3/3 = 2.0: 7.0.  negative: Rude 3 -> 3.0, Sarcastic 2 -> 2 x 2/3: 4.333
        # health = 50 + 50 x (7.0 - 4.3333) / 6 / 4.5 = 54.938
        self.assertAlmostEqual(measures.health_mean(t), 50 + 50 * (7.0 - 13 / 3) / 6 / 4.5, places=9)
        self.assertAlmostEqual(measures.health_mean(t), 54.9382716, places=6)

    def test_merging_tallies_is_the_tally_of_all_their_messages(self):
        first = [measures.message_flags(ratings(**({'Rude': 2} if i < 3 else {'Empathetic': 3} if i < 5 else {}))) for i in range(10)]
        second = [measures.message_flags(ratings(**({'Sarcastic': 2} if i < 4 else {'Polite': 2, 'Agitated': 3} if i < 9 else {}))) for i in range(20)]
        merged = measures.merge([measures.tally(first), measures.tally(second)])
        whole = measures.tally(first + second)
        for key in whole:
            self.assertAlmostEqual(merged[key], whole[key], places=9, msg=key)
        self.assertEqual((merged['n'], merged['hostile'], merged['care'], merged['dig']), (30, 3, 2, 4))
        self.assertEqual(measures.merge([])['n'], 0)

    def test_a_rate_is_the_count_per_100_messages_and_an_empty_group_has_none(self):
        t = tally_of(8, hostile=3)
        self.assertEqual(measures.rate(t, 'hostile'), 37.5)
        self.assertEqual(measures.rate(t, 'care'), 0.0)
        empty = measures.tally([])
        self.assertEqual(measures.rate(empty, 'hostile'), 0.0)
        self.assertEqual(measures.health_mean(empty), 50.0)

    def test_a_check_reports_how_many_messages_show_every_flag(self):
        # disjoint messages: 10 polite, 7 encouraging, 5 funny, 9 caring, 4 hostile, 6 digs (Funny below 2), 3 only agitated, 56 with no tone; 100 in all
        plan = ([{'Polite': 2}] * 10 + [{'Encouraging': 3}] * 7 + [{'Funny': 2}] * 5 + [{'Empathetic': 2}] * 9 + [{'Rude': 2}] * 4 + [{'Sarcastic': 2, 'Funny': 1}] * 6
                + [{'Agitated': 2}] * 3 + [{}] * 56)
        m = an(chat(100, lambda i: plan[i]))
        o = m['overall']
        self.assertEqual((o['n'], o['polite'], o['encouraging'], o['funny'], o['care'], o['hostile'], o['dig']), (100, 10, 7, 5, 9, 4, 6))
        self.assertEqual(o['heated'], 4 + 6 + 3)                                    # hostile, digs and agitated
        self.assertEqual(m['silent'], 56)
        self.assertEqual(sum(s['counts']['polite'] for s in m['stretches']), 10)
        for kind in KINDS:
            self.assertEqual(sum(s['counts'][kind] for s in m['stretches']), o[kind], kind)


class SeriousFlagsTests(unittest.TestCase):
    """What the alert reads from Vibe Check: the serious kinds with counts, and the messages scoring 7 or more."""

    def test_nothing_or_nothing_serious(self):
        for vibe in (None, {}, vibe_ok(10), vibe_ok(10, insults=4, profanity=2, spam=1)):
            self.assertEqual(measures.serious_flags(vibe), {'kinds': {}, 'total': 0, 'red': 0, 'quoted': 0})

    def test_kinds_total_red_and_quoted(self):
        vibe = {'toxicity_types': {'threats': 2, 'insults': 9}, 'flagged': [{'type': 'threat', 'severity': 8}, {'type': 'insult', 'severity': 9}, {'type': 'bullying', 'severity': 3},
                                                                               {'type': 'harassment', 'severity': 7}, {'type': 'harassment', 'severity': 6}]}
        got = measures.serious_flags(vibe)
        self.assertEqual(got['kinds'], {'threats': 2, 'harassment': 2, 'bullying': 1})        # the larger of the tally (2) and the flagged threats (1); the insults are not serious
        self.assertEqual(got['total'], 5)
        self.assertEqual(got['red'], 2, 'the threat scoring 8 and the harassment scoring 7; an insult scoring 9 is not a serious kind')
        self.assertEqual(got['quoted'], 4, 'the serious flagged messages Vibe Check quotes')


class BadgeWordsTests(unittest.TestCase):
    """MEASURES.md: "There are no locks, scores, streaks or 'x of 7'. Badges never name a person, never rank teams."; the words of a badge keep to that."""

    def test_the_badge_words_have_no_game_in_them(self):
        bad = re.compile(r'(?i)\bstreaks?\b|\block(ed|s)?\b|\bunlock|\bof 7\b|\b\d+ ?/ ?7\b|\bleaderboard|\brank(ed|ing|s)?\b|\bscores?\b|\bwinners?\b|\btrophy|\bprize|\bcongrat|\bbadges? earned\b')
        texts = [(w, t) for name, ctx in scenarios() for w, t in page_strings(ctx) if w.startswith('badge.') or w == 'badge_note']
        self.assertGreater(len(texts), 500)
        for where, text in texts:
            self.assertNotRegex(text, bad, where)

    def test_every_badge_rule_is_a_sentence_about_wording_in_this_check(self):
        ctx = build_with(lively(neutral(96)))
        for b in ctx['badges']:
            self.assertRegex(b['how'], r'^[A-Z].*\.$', b['key'])
            self.assertNotRegex(b['how'], r'(?i)\bperson|people|member|team\b', b['key'])


class ChangeTests(unittest.TestCase):
    """Direction: the first third against the last, a floor of max(3 points, 20% of the start) and a chance test of 2.0 standard errors (2.5 to be 'strong')."""

    def change(self, c1, n1, c2, n2, kind='hostile'):
        return measures.rate_change(cached_tally(kind, n1, c1), cached_tally(kind, n2, c2), kind)

    def test_the_change_and_its_standard_error_are_the_ones_in_the_spec(self):
        # by hand: (10/100 -> 30/100): p1 = 11/102, p2 = 31/102, se = 100 x sqrt(0.0962/100 + 0.2116/100) = 5.548
        #          (10 -> 13 of 100): se 4.6328;  (5 -> 15): 4.3315;  (0 -> 4): 2.3733;  (0 -> 300 of 10000): 0.1711
        for c1, n1, c2, n2, se in [(10, 100, 30, 100, 5.5477), (10, 100, 13, 100, 4.6328), (5, 100, 15, 100, 4.3315), (0, 100, 4, 100, 2.3733), (0, 10000, 300, 10000, 0.1711)]:
            with self.subTest(case=(c1, n1, c2, n2)):
                got = self.change(c1, n1, c2, n2)
                self.assertAlmostEqual(got['se'], se, places=3)
                self.assertAlmostEqual(got['se'], se_of_change(c1, n1, c2, n2), places=9)
                self.assertAlmostEqual(got['delta'], 100 * c2 / n2 - 100 * c1 / n1, places=9)
                self.assertAlmostEqual(got['first'], 100 * c1 / n1, places=9)
                self.assertAlmostEqual(got['last'], 100 * c2 / n2, places=9)
        for c1, n1, c2, n2 in [(3, 40, 17, 80), (1, 24, 5, 24), (0, 16, 0, 16), (16, 16, 16, 16), (7, 333, 30, 41)]:      # unequal sizes, empty and full groups
            with self.subTest(case=(c1, n1, c2, n2)):
                self.assertAlmostEqual(self.change(c1, n1, c2, n2)['se'], se_of_change(c1, n1, c2, n2), places=9)

    def test_calls_at_100_messages_each(self):
        # (first count, last count, call, strong), 100 messages each; z = delta / se worked by hand
        for c1, c2, call, strong in [(10, 30, 'rising', True),        # delta 20, se 5.548, z 3.61
                                     (30, 10, 'falling', True),
                                     (10, 13, 'steady', False),       # delta 3 is the floor, but se 4.633 and z 0.65: a change of 3 points with a big se is steady
                                     (13, 10, 'steady', False),
                                     (5, 13, 'steady', False),        # delta 8, se 4.169, z 1.92: under 2.0
                                     (5, 14, 'rising', False),        # delta 9, se 4.252, z 2.12: called, not strong
                                     (5, 15, 'rising', False),        # z 2.309
                                     (5, 16, 'rising', False),        # delta 11, se 4.407, z 2.496: just under 2.5
                                     (5, 17, 'rising', True),         # delta 12, se 4.480, z 2.679
                                     (20, 8, 'falling', False),       # delta -12, se 4.939, z 2.43
                                     (20, 7, 'falling', True),        # delta -13, se 4.856, z 2.68
                                     (0, 4, 'steady', False),         # delta 4 passes the floor, z 1.69 does not
                                     (0, 0, 'steady', False),
                                     (100, 100, 'steady', False)]:
            with self.subTest(first=c1, last=c2):
                got = self.change(c1, 100, c2, 100)
                self.assertEqual((got['call'], got['strong']), (call, strong), got)

    def test_every_rate_kind_is_judged_the_same_way(self):
        for kind in ('hostile', 'care', 'dig', 'heated', 'polite'):
            with self.subTest(kind=kind):
                self.assertEqual(self.change(10, 100, 30, 100, kind)['call'], 'rising')
                self.assertEqual(self.change(30, 100, 10, 100, kind)['call'], 'falling')
                self.assertEqual(self.change(10, 100, 13, 100, kind)['call'], 'steady')

    def test_exactly_at_the_floor_the_change_counts_and_one_step_below_it_does_not(self):
        # n = 10,000 each, so that the chance test is passed by a mile (z > 7) and only the floor is in question: max(3, 20% of the start)
        for start, floor in [(0, 3), (1000, 3), (1500, 3), (2500, 5), (5000, 10), (8000, 16)]:       # counts out of 10,000: 0%, 10%, 15%, 25%, 50%, 80%
            for sign, call in [(1, 'rising'), (-1, 'falling')]:
                end = start + sign * floor * 100
                if not 0 <= end <= 10000:
                    continue
                with self.subTest(start=start, floor=floor, call=call):
                    self.assertEqual(self.change(start, 10000, end, 10000)['call'], call, 'a move of exactly the floor counts')
                    self.assertEqual(self.change(start, 10000, end - sign, 10000)['call'], 'steady', 'a hundredth of a point short of the floor does not')

    def test_the_floor_is_20_percent_of_the_start_when_that_is_more_than_3_points(self):
        # start 50 per 100: 20% is 10 points. 3, 5 and 9.99 points are not enough however sure the chance test is; 10 is.
        for points, call in [(3, 'steady'), (5, 'steady'), (9.99, 'steady'), (10, 'rising'), (12, 'rising')]:
            with self.subTest(points=points):
                self.assertEqual(self.change(5000, 10000, 5000 + round(points * 100), 10000)['call'], call)

    def test_the_call_follows_the_spec_for_many_pairs_of_counts(self):
        checked = 0
        for n1, n2 in [(24, 24), (50, 50), (100, 100), (16, 33)]:
            for c1 in range(0, n1 + 1, max(1, n1 // 25)):
                for c2 in range(0, n2 + 1):
                    first, last = 100 * c1 / n1, 100 * c2 / n2
                    delta = last - first
                    se = se_of_change(c1, n1, c2, n2)
                    floor = max(3.0, 0.2 * first)
                    if min(abs(abs(delta) - floor), abs(abs(delta) / se - 2.0), abs(abs(delta) / se - 2.5)) < 1e-9:
                        continue                                                    # a tie in floating point is not what is tested here
                    got = self.change(c1, n1, c2, n2)
                    want = expected_call(c1, n1, c2, n2)
                    self.assertEqual(got['call'], want, (c1, n1, c2, n2, got))
                    self.assertEqual(got['strong'], want != 'steady' and abs(delta) >= 2.5 * se, (c1, n1, c2, n2, got))
                    checked += 1
        self.assertGreater(checked, 1500)

    def test_the_health_score_has_a_5_point_floor_and_the_same_chance_test(self):
        def group(n, extra_every_n, extra):
            """n messages that are all Polite 3 (+3), of which the first `extra_every_n` also carry `extra`."""
            return measures.tally([measures.message_flags(ratings(Polite=3, **(extra if i < extra_every_n else {}))) for i in range(n)])

        warm = group(1000, 0, {})                                                   # d = +3 for everyone: 83.33
        # 36% also rated Aggressive 3 (d = 0): mean d = 3 x 0.64 = 1.92, so 50 + 50 x 1.92 / 4.5 = 71.33: down 12.0, a chance test passed with ease
        down12 = measures.health_change(warm, group(1000, 360, {'Aggressive': 3}))
        self.assertEqual((down12['call'], down12['strong']), ('falling', True))
        self.assertAlmostEqual(down12['delta'], -12.0, places=6)
        # 36% also rated Neurotic 3 (d = -1 for them): mean d = 2.64, so 79.33: down 4.0 -- under the 5-point floor even though z is about 24
        down4 = measures.health_change(warm, group(1000, 360, {'Neurotic': 3}))
        self.assertAlmostEqual(down4['delta'], -4.0, places=6)
        self.assertGreater(abs(down4['delta']) / down4['se'], 5)
        self.assertEqual((down4['call'], down4['strong']), ('steady', False))
        # up 6.0 : from "all Polite 3 and Neurotic 3" (d = 2) to 54% without the Neurotic rating
        cool = group(1000, 1000, {'Neurotic': 3})
        up6 = measures.health_change(cool, group(1000, 460, {'Neurotic': 3}))
        self.assertAlmostEqual(up6['delta'], 6.0, places=6)
        self.assertEqual(up6['call'], 'rising')
        # either side of the 5-point floor: 49.5% of the messages without the Neurotic rating is up 5.5 (a call); 40.5% is up 4.5 (no call); z is large for both
        for share, delta, call in [(495, 5.5, 'rising'), (405, 4.5, 'steady')]:
            got = measures.health_change(cool, group(1000, 1000 - share, {'Neurotic': 3}))
            self.assertAlmostEqual(got['delta'], delta, places=6)
            self.assertGreater(got['delta'] / got['se'], 5)
            self.assertEqual(got['call'], call, delta)
        # a big move that chance explains: 10 messages each, d = +3 or -3
        def spiky(n, polite):
            return measures.tally([measures.message_flags(ratings(**({'Polite': 3} if i < polite else {'Rude': 3}))) for i in range(n)])

        noisy = measures.health_change(spiky(10, 5), spiky(10, 3))                  # mean d 0 -> -1.2, so 50 -> 38.9 ... but the spread is +-3
        self.assertLess(noisy['delta'], -5)
        self.assertEqual((noisy['call'], noisy['strong']), ('steady', False))

    def test_the_first_and_last_thirds_are_compared_and_the_middle_is_left_out(self):
        # 300 messages = 24 stretches of 12 or 13; a third is 8 stretches. The hostile messages are spread evenly inside each third.
        parts = measures.cut(300)
        first_end, last_start = parts[7][1], parts[16][0]
        where = set(spread(first_end, 10)) | {first_end + p for p in spread(last_start - first_end, 90)} | {last_start + p for p in spread(300 - last_start, 30)}
        m = an(chat(300, lambda i: {'Rude': 2} if i in where else None))
        self.assertEqual((m['first3']['n'], m['first3']['hostile']), (first_end, 10))
        self.assertEqual((m['last3']['n'], m['last3']['hostile']), (300 - last_start, 30))
        got = m['trend']['hostile']
        self.assertEqual((got['call'], got['strong']), ('rising', True))
        self.assertAlmostEqual(got['first'], 100 * 10 / first_end, places=9)
        self.assertAlmostEqual(got['last'], 100 * 30 / (300 - last_start), places=9)

    def test_a_third_is_a_third_of_the_stretches_and_never_less_than_one_stretch(self):
        for parts in range(3, 25):
            with self.subTest(stretches=parts):
                m = an(chat(8 * parts))                                             # `parts` stretches of 8
                self.assertEqual(len(m['stretches']), parts)
                third = max(1, parts // 3)
                self.assertEqual((m['first3']['n'], m['last3']['n']), (8 * third, 8 * third))
        self.assertEqual(an(chat(48))['first3']['n'], 16, 'each end third holds at least 16 at the verdict floor')


# =============================================================================================================================================
HEAT = {'Agitated': 3}                      # heated, and nothing else: not hostile, not a dig


class ClimbTests(unittest.TestCase):
    """Heated moments: the climb above the recent level, the three tests a stretch must pass, how moments are counted and worded."""

    def climbs(self, *sizes_and_counts):
        return measures.climbs([cached_tally('heated', n, c) for n, c in sizes_and_counts])

    def test_the_climb_is_the_gap_to_the_pooled_rate_of_up_to_three_stretches_before_and_never_below_zero(self):
        # eight messages per stretch, heated messages per stretch 1, 2, 4, 8, 0, 0, 3 -> rates 12.5, 25, 50, 100, 0, 0, 37.5
        got = self.climbs(*[(8, c) for c in (1, 2, 4, 8, 0, 0, 3)])
        want = [(None, 0.0),                      # no past to climb from
                (12.5, 12.5),                     # 25 - 12.5
                (18.75, 31.25),                   # pooled (1+2)/16 = 18.75 -> 50 - 18.75
                (7 / 24 * 100, 100 - 7 / 24 * 100),          # pooled (1+2+4)/24 = 29.17 -> 70.83
                (14 / 24 * 100, 0.0),             # window is stretches 1, 2, 3 only: 14/24 = 58.3 -> 0 - 58.3, never below 0
                (50.0, 0.0),                      # 12/24
                (8 / 24 * 100, 37.5 - 8 / 24 * 100)]         # window 3, 4, 5: 8/24 = 33.3 -> 4.17
        self.assertEqual(len(got), len(want))
        for i, (base, climb) in enumerate(want):
            with self.subTest(stretch=i):
                self.assertAlmostEqual(got[i]['climb'], climb, places=9)
                if base is not None:
                    self.assertAlmostEqual(got[i]['base'], base, places=9)
        self.assertTrue(all(step['climb'] >= 0 for step in got))

    def test_the_recent_level_is_pooled_over_the_messages_not_an_average_of_rates(self):
        # stretch sizes 8, 9, 8 with 0, 9 and 8 heated: pooled (0+9)/17 = 52.94, so the climb is 100 - 52.94 = 47.06. An average of the two rates (0, 100) would give 50.
        got = self.climbs((8, 0), (9, 9), (8, 8))
        self.assertAlmostEqual(got[2]['climb'], 100 - 100 * 9 / 17, places=9)

    def test_a_flat_chat_has_no_climb_and_no_heated_moment(self):
        for counts in ([1] * 12, [0] * 12, [3] * 12, [2, 3] * 6):
            with self.subTest(counts=counts):
                m = an(part_chat(counts, 8, HEAT))
                self.assertEqual(m['episodes'], [])
                self.assertFalse(any(s['counted'] for s in m['stretches']))
        self.assertTrue(all(step['climb'] == 0 for step in self.climbs(*[(8, 1)] * 12)))

    def test_a_stretch_needs_at_least_three_heated_messages_even_when_the_climb_and_the_chance_test_pass(self):
        # 12 stretches of 8: 2 heated in one stretch after a calm run is a climb of 25. By hand, p = 3/98 (or 2.5/97 with a lighter smoothing),
        # se = 100 x sqrt(p(1-p) x (1/8 + 1/24)) = 7.0 (6.5), z = 25 / 7.0 = 3.56 (3.87): above 3.5. Only the count of heated messages is short.
        two = part_chat([0, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0], 8, HEAT)
        step = measures.climbs([cached_tally('heated', 8, c) for c in (0, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0)])[6]
        self.assertEqual(step['climb'], 25.0)
        self.assertGreaterEqual(step['climb'] / step['se'], 3.5, 'the chance test is passed')
        self.assertEqual(an(two)['episodes'], [])
        # 3 of 8: a climb of 37.5; z about 4.6 to 4.9
        three = an(part_chat([0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0], 8, HEAT))
        self.assertEqual(len(three['episodes']), 1)
        ep = three['episodes'][0]
        self.assertEqual((ep['first'], ep['last'], ep['heated'], ep['n']), (6, 6, 3, 8))
        self.assertAlmostEqual(ep['peak'], 37.5, places=9)                          # the biggest climb
        self.assertAlmostEqual(ep['reached'], 37.5, places=9)                       # the level it reached
        self.assertAlmostEqual(ep['baseline'], 0.0, places=9)                       # where it started from
        self.assertTrue(three['stretches'][6]['counted'])
        self.assertEqual(sum(s['counted'] for s in three['stretches']), 1)

    def test_a_climb_under_15_points_does_not_count(self):
        # 20 per stretch. 3 heated is 15 points over a calm run: counts. 2 heated is 10 points (and only 2 messages): does not.
        def moment(heated):
            counts = [0] * 24
            counts[10] = heated
            return an(part_chat(counts, 20, HEAT))

        self.assertEqual(len(moment(3)['episodes']), 1)
        self.assertEqual(moment(2)['episodes'], [])
        self.assertEqual(moment(1)['episodes'], [])

    def test_the_chance_test_keeps_out_a_stretch_that_is_only_as_heated_as_the_community_usually_is(self):
        # twelve stretches of 8 with a steady 3 heated in each (37.5 per 100), then 6 in the seventh (75): a climb of 37.5 points, but the community is heated
        # 39 messages in 96 (p = 0.41), so se = 100 x sqrt(p(1-p) x (1/8 + 1/24)) = 20 and z = 37.5 / 20 = 1.9: under 3.5.
        busy = [3] * 12
        busy[6] = 6
        m = an(part_chat(busy, 8, HEAT))
        step = measures.climbs([cached_tally('heated', 8, c) for c in busy])[6]
        self.assertAlmostEqual(step['climb'], 37.5, places=9)
        self.assertLess(step['climb'] / step['se'], 3.5)
        self.assertEqual(m['episodes'], [], 'a big climb in a community that is often heated is not a heated moment')

    def test_the_chance_test_is_3_and_a_half_standard_errors(self):
        # 24 stretches of s messages; r heated in every stretch but stretch 11, which has c. climb = 100 (c - r) / s, se = 100 x sqrt(p(1-p) x (1/s + 1/(3s))),
        # p = (heated + 1) / (24 s + 2). By hand: (24, 5, 13): 33.3 / 9.8 = z 3.40 and (40, 10, 21): z 3.43 are not counted; (24, 10, 20): 41.7 / 11.7 = z 3.57 and
        # (30, 11, 22): z 3.58 are. All have far more than 3 heated messages and a climb over 15, so only the chance test decides.
        for size, r, c, counted, z in [(24, 5, 13, False, 3.40), (40, 10, 21, False, 3.43), (24, 10, 20, True, 3.57), (30, 11, 22, True, 3.58)]:
            counts = [r] * 24
            counts[11] = c
            p = smoothed(sum(counts), 24 * size)
            climb = 100 * (c - r) / size
            self.assertAlmostEqual(climb / (100 * math.sqrt(p * (1 - p) * (1 / size + 1 / (3 * size)))), z, delta=0.02)
            with self.subTest(size=size, r=r, c=c, z=z):
                self.assertEqual(len(an(part_chat(counts, size, HEAT))['episodes']), 1 if counted else 0)

    def test_a_climb_of_14_points_is_not_enough_and_one_of_18_is(self):
        # 24 stretches of 28 messages, calm but for one stretch: 4 heated is 14.3 points (z over 7, 4 heated messages: only the 15-point floor is short), 5 heated is 17.9
        for heated, counted in [(3, False), (4, False), (5, True), (6, True)]:
            counts = [0] * 24
            counts[11] = heated
            with self.subTest(heated=heated):
                self.assertEqual(len(an(part_chat(counts, 28, HEAT))['episodes']), 1 if counted else 0)

    def test_a_plateau_is_one_moment_not_eight(self):
        # 24 stretches of 20: sixteen calm ones, then eight at 8 of 20 (40 per 100). Overall 64 heated of 480, so p = 0.134 and se = 100 x sqrt(p(1-p) x (1/20 + 1/60)) = 8.8.
        # The first high one is a climb of 40 (z = 4.6): counted. The next climbs on a baseline that already holds it: 40 - 13.3 = 26.7 (z = 3.0), then 40 - 26.7 = 13.3
        # (under 15), then nothing. One heated moment, not eight.
        m = an(part_chat([0] * 16 + [8] * 8, 20, HEAT))
        self.assertEqual([(e['first'], e['last']) for e in m['episodes']], [(16, 16)])
        self.assertEqual([s['i'] for s in m['stretches'] if s['counted']], [16])
        self.assertAlmostEqual(m['episodes'][0]['peak'], 40.0, places=6)

    def test_a_plateau_that_is_half_of_the_chat_is_not_a_moment_at_all(self):
        # six calm stretches then six at 4 of 8: heated is 1 message in 4 over the whole chat (p = 0.25), so a climb of 50 has z = 50 / 17.7 = 2.8: under 3.5
        m = an(part_chat([0] * 6 + [4] * 6, 8, HEAT))
        self.assertEqual(m['episodes'], [])

    def test_two_separate_bursts_are_two_moments(self):
        m = an(part_chat([0, 0, 0, 4, 0, 0, 0, 0, 0, 4, 0, 0], 8, HEAT))
        self.assertEqual([(e['first'], e['last']) for e in m['episodes']], [(3, 3), (9, 9)])
        self.assertTrue(all(abs(e['peak'] - 50.0) < 1e-9 for e in m['episodes']))
        self.assertEqual(measures.cards(m)[0]['count'], 2)

    def test_consecutive_counted_stretches_are_one_moment(self):
        # stretch 3: 4 heated (climb 50). stretch 4: all 8 heated; its baseline holds the 4: 4/24 = 16.7, so a climb of 83.3 (z about 6)
        m = an(part_chat([0, 0, 0, 4, 8, 0, 0, 0, 0, 0, 0, 0], 8, HEAT))
        self.assertEqual(len(m['episodes']), 1)
        ep = m['episodes'][0]
        self.assertEqual((ep['first'], ep['last'], ep['heated'], ep['n']), (3, 4, 12, 16))
        self.assertAlmostEqual(ep['peak'], 100 - 4 / 24 * 100, places=9)            # the biggest climb of the two stretches
        self.assertAlmostEqual(ep['reached'], 100.0, places=9)
        self.assertAlmostEqual(ep['baseline'], 0.0, places=9)                       # the level before the first of them
        self.assertEqual([s['counted'] for s in m['stretches']][2:6], [False, True, True, False])

    def test_a_moment_in_the_last_stretch_counts_and_is_placed_near_the_end(self):
        m = an(part_chat([0] * 11 + [4], 8, HEAT))
        self.assertEqual([(e['first'], e['last']) for e in m['episodes']], [(11, 11)])
        self.assertRegex(m['episodes'][0]['where'], r'(?i)end')
        self.assertTrue(m['stretches'][-1]['counted'])

    def test_a_moment_in_the_second_stretch_has_only_one_stretch_of_past(self):
        m = an(part_chat([0, 4] + [0] * 10, 8, HEAT))
        self.assertEqual([(e['first'], e['last']) for e in m['episodes']], [(1, 1)])
        self.assertRegex(m['episodes'][0]['where'], r'(?i)start')

    def test_the_first_stretch_can_never_be_a_heated_moment(self):
        m = an(part_chat([8] + [0] * 11, 8, HEAT))
        self.assertEqual(m['episodes'], [])
        self.assertFalse(m['stretches'][0]['counted'])

    def test_the_very_first_stretch_being_tense_is_flagged_at_30_in_100(self):
        # (stretch size, heated in the first stretch, opening tense?)   3 of 10 = 30.0 exactly; 5 of 17 = 29.4; 6 of 17 = 35.3; 3 of 11 = 27.3; 4 of 11 = 36.4
        for size, first, tense in [(10, 3, True), (10, 2, False), (10, 10, True), (10, 0, False), (17, 5, False), (17, 6, True), (11, 3, False), (11, 4, True)]:
            with self.subTest(size=size, first=first):
                m = an(part_chat([first] + [0] * 23, size, HEAT))
                self.assertEqual(m['opening_heated'], tense)
                note = measures.cards(m)[0]['note']
                self.assertEqual(bool(note), tense, note)
                if tense:
                    self.assertRegex(note, r'(?i)tense')
        self.assertFalse(an(part_chat([0] * 12, 8, HEAT))['opening_heated'])

    def test_escalation_words_by_the_biggest_climb(self):
        for peak, word in [(0, 'none'), (1, 'none'), (14, 'none'), (15, 'small'), (20, 'small'), (24, 'small'), (25, 'clear'), (30, 'clear'), (39, 'clear'),
                           (40, 'steep'), (41, 'steep'), (80, 'steep'), (100, 'steep')]:
            with self.subTest(peak=peak):
                self.assertEqual(measures.escalation_word(peak), word)
        self.assertEqual(measures.ESCALATION_LABEL, {'none': 'None', 'small': 'Small', 'clear': 'Clear', 'steep': 'Steep', 'early': 'Too early'})
        for peak in (0, 14, 15, 25, 40, 100):                                      # a judged check is never worded 'Too early': that word is for a check that could not be judged
            self.assertNotEqual(measures.escalation_word(peak), 'early')

    def test_the_card_names_the_word_of_the_biggest_counted_climb(self):
        # 24 stretches of 20; a calm run of one heated message per stretch (5%) for three stretches, then a spike: climb = 5 x heated - 5
        for heated, climb, word in [(3, None, 'None'), (4, 15, 'Small'), (5, 20, 'Small'), (6, 25, 'Clear'), (8, 35, 'Clear'), (9, 40, 'Steep'), (12, 55, 'Steep')]:
            with self.subTest(heated=heated):
                counts = [0] * 24
                counts[7:10] = [1, 1, 1]
                counts[10] = heated
                m = an(part_chat(counts, 20, HEAT))
                card = measures.cards(m)[0]
                self.assertEqual(card['level'], word)
                if climb is None:
                    self.assertEqual((card['count'], m['episodes']), (0, []))
                else:
                    self.assertEqual(card['count'], 1)
                    self.assertAlmostEqual(card['peak'], climb, places=6)
                    self.assertAlmostEqual(m['episodes'][0]['peak'], climb, places=6)

    def test_a_climb_exactly_on_a_cut_off_is_counted_and_worded_as_reaching_it(self):
        # Exact fractions decide: 100c/s - 100(b1+b2+b3)/(3s) is 15, 25 or 40 on the nose in these chats (24 stretches of s messages, calm but for the three before
        # and the one after), and the z of each is over 4. Whatever floating point does to the last digits, "at least 15" counts 15 and "25 to 39" starts at 25.
        tried = 0
        for s in (8, 9, 10, 11, 12, 13, 16, 20, 24):
            for past in [(0, 0, 0), (1, 1, 1), (0, 1, 2), (2, 2, 2), (1, 2, 3)]:
                for c in range(3, s + 1):
                    exact = Fraction(100 * c, s) - Fraction(100 * sum(past), 3 * s)
                    if exact not in (15, 25, 40):
                        continue
                    counts = [0] * 24
                    counts[7:10] = list(past)
                    counts[10] = c
                    p = smoothed(sum(counts), 24 * s)
                    if float(exact) / (100 * math.sqrt(p * (1 - p) * (1 / s + 1 / (3 * s)))) < 4.0:
                        continue
                    word = 'Steep' if exact >= 40 else 'Clear' if exact >= 25 else 'Small'
                    m = an(part_chat(counts, s, HEAT))
                    with self.subTest(size=s, past=past, heated=c, climb=int(exact)):
                        self.assertEqual(len(m['episodes']), 1)
                        self.assertEqual(measures.cards(m)[0]['level'], word)
                        self.assertAlmostEqual(m['episodes'][0]['peak'], float(exact), places=6)
                    tried += 1
        self.assertGreater(tried, 25)

    def test_no_climb_is_ever_negative_and_the_first_has_no_past(self):
        rng = random.Random(7)
        for _ in range(200):
            sizes = [rng.randint(8, 30) for _ in range(rng.randint(2, 24))]
            got = self.climbs(*[(n, rng.randint(0, n)) for n in sizes])
            self.assertEqual(len(got), len(sizes))
            self.assertEqual(got[0]['climb'], 0.0)
            self.assertTrue(all(step['climb'] >= 0 and step['se'] >= 0 for step in got))

    def test_moments_need_the_verdict_floor_of_48(self):
        counts = [0, 0, 0, 4, 0]                                                    # 40 messages in five stretches of 8: a burst of 4, but under 48
        self.assertEqual(an(part_chat(counts, 8, HEAT))['episodes'], [])
        self.assertEqual(len(an(part_chat([0, 0, 0, 4, 0, 0], 8, HEAT))['episodes']), 1)       # 48 messages in six stretches

    def test_episodes_have_where_and_label_but_no_clock_time_in_where(self):
        m = an(part_chat([0, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0, 0], 8, HEAT))
        ep = m['episodes'][0]
        self.assertRegex(ep['where'], r'(?i)middle')
        self.assertNotRegex(ep['where'], r'\d{1,2}:\d{2}')


# =============================================================================================================================================
class LevelWordTests(unittest.TestCase):
    """Rare, Some and Often per 100 messages, judged on the whole number printed; the Conflict and sarcasm cut-offs are 1.5 times higher at Low."""

    CUTS = {'hostile': (5, 12), 'dig': (5, 12), 'care': (3, 10)}

    @staticmethod
    def shown(value):
        return math.floor(value + 0.5)                                              # halves up

    def want(self, kind, value, scale):
        some, often = self.CUTS[kind]
        if kind != 'care':
            some, often = some * scale, often * scale
        return 'Rare' if self.shown(value) < some else 'Some' if self.shown(value) < often else 'Often'

    def test_every_whole_number_from_0_to_40_at_medium(self):
        table = {'hostile': ['Rare'] * 5 + ['Some'] * 7 + ['Often'] * 29,        # under 5, 5 to 11, 12 and over
                 'dig': ['Rare'] * 5 + ['Some'] * 7 + ['Often'] * 29,
                 'care': ['Rare'] * 3 + ['Some'] * 7 + ['Often'] * 31}           # under 3, 3 to 9, 10 and over
        for kind, words in table.items():
            for value, word in enumerate(words):
                with self.subTest(kind=kind, value=value):
                    self.assertEqual(measures.level_word(kind, value, 1.0), word)
                    self.assertEqual(measures.level_word(kind, value), word)

    def test_every_whole_number_from_0_to_40_at_low_sensitivity(self):
        # scale 1.5: Conflict and sarcasm are Some from 7.5 and Often from 18. On whole numbers: 7 is Rare, 8 is Some, 17 is Some, 18 is Often. Empathy does not move.
        for kind in ('hostile', 'dig'):
            for value in range(41):
                with self.subTest(kind=kind, value=value):
                    self.assertEqual(measures.level_word(kind, value, 1.5), 'Rare' if value <= 7 else 'Some' if value <= 17 else 'Often')
        for value in range(41):
            with self.subTest(kind='care', value=value):
                self.assertEqual(measures.level_word('care', value, 1.5), self.want('care', value, 1.0))

    def test_the_number_that_is_printed_decides_not_the_decimal_behind_it(self):
        # a rate of 4.5 prints as 5 and so is Some; 4.49 prints 4 and is Rare. Likewise 11.5 prints 12 and is Often.
        for kind, value, scale, word in [('hostile', 4.49, 1.0, 'Rare'), ('hostile', 4.5, 1.0, 'Some'), ('hostile', 11.49, 1.0, 'Some'), ('hostile', 11.5, 1.0, 'Often'),
                                         ('dig', 4.5, 1.0, 'Some'), ('dig', 11.5, 1.0, 'Often'),
                                         ('care', 2.49, 1.0, 'Rare'), ('care', 2.5, 1.0, 'Some'), ('care', 9.49, 1.0, 'Some'), ('care', 9.5, 1.0, 'Often'),
                                         ('hostile', 7.49, 1.5, 'Rare'), ('hostile', 7.5, 1.5, 'Some'), ('hostile', 17.49, 1.5, 'Some'), ('hostile', 17.5, 1.5, 'Often'),
                                         ('dig', 7.5, 1.5, 'Some'), ('dig', 17.5, 1.5, 'Often'), ('care', 2.5, 1.5, 'Some'), ('care', 9.5, 1.5, 'Often')]:
            with self.subTest(kind=kind, value=value, scale=scale):
                self.assertEqual(measures.level_word(kind, value, scale), word)

    def test_the_sweep_of_tenths_agrees_with_the_oracle(self):
        for kind in self.CUTS:
            for scale in (1.0, 1.5):
                for tenths in range(0, 401):
                    value = tenths / 10
                    self.assertEqual(measures.level_word(kind, value, scale), self.want(kind, value, scale), (kind, value, scale))

    def test_the_cut_offs_in_the_module_are_the_ones_in_the_spec(self):
        self.assertEqual({k: tuple(v) for k, v in measures.CUTS.items()}, self.CUTS)
        self.assertEqual(sorted(measures.SCALED), ['dig', 'hostile'])
        self.assertEqual(measures.LOW_SCALE, 1.5)
        self.assertEqual(measures.LOW_TENSION, 1.2)

    def test_analyse_sets_the_scale_by_sensitivity(self):
        units = chat(60)
        self.assertEqual(an(units, 'low')['scale'], 1.5)
        self.assertEqual(an(units, 'medium')['scale'], 1.0)
        self.assertEqual(an(units, 'high')['scale'], 1.0)
        self.assertEqual([an(units, s)['sensitivity'] for s in ('low', 'medium', 'high')], ['low', 'medium', 'high'])

    def test_a_card_uses_the_scale_of_the_check(self):
        # n = 100, k hostile: the rate is k per 100. At Low, 12 to 17 are Some; 18 is Often. At Medium 12 is already Often.
        for k, medium, low in [(4, 'Rare', 'Rare'), (5, 'Some', 'Rare'), (7, 'Some', 'Rare'), (8, 'Some', 'Some'), (11, 'Some', 'Some'), (12, 'Often', 'Some'),
                               (17, 'Often', 'Some'), (18, 'Often', 'Often')]:
            where = spread(100, k)
            units = chat(100, lambda i: {'Rude': 2} if i in where else None)
            with self.subTest(k=k):
                self.assertEqual(measures.cards(an(units, 'medium'))[1]['level'], medium)
                self.assertEqual(measures.cards(an(units, 'low'))[1]['level'], low)
        for k, word in [(2, 'Rare'), (3, 'Some'), (9, 'Some'), (10, 'Often')]:           # Empathy is the same at Low
            where = spread(100, k)
            units = chat(100, lambda i: {'Empathetic': 2} if i in where else None)
            with self.subTest(care=k):
                self.assertEqual(measures.cards(an(units, 'low'))[2]['level'], word)
                self.assertEqual(measures.cards(an(units, 'medium'))[2]['level'], word)


CLOCK = re.compile(r'(?<![\d:])\d{1,2}:\d{2}(?![\d:])')          # an hour and a minute, as in 18:00
MONTH_DAY = re.compile(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}\b|\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b|\b\d{4}-\d{2}-\d{2}\b')


# =============================================================================================================================================
class LabelTests(unittest.TestCase):
    """What the stretches are called, and where a clock time may appear."""

    def test_a_stretch_is_named_part_n_of_k(self):
        for dated in (False, True):
            m = an(chat(72, dated=dated))
            self.assertEqual(len(m['stretches']), 9)
            for i, s in enumerate(m['stretches']):
                with self.subTest(dated=dated, i=i):
                    self.assertTrue(s['label'].startswith(f'Part {i + 1} of 9'), s['label'])
                    self.assertEqual(s['i'], i)

    def test_messages_ordered_by_upload_are_labelled_by_their_numbers(self):
        # 288 messages are 24 stretches of 12: the third covers messages 25 to 36 -- the example in the spec
        m = an(chat(288, dated=False))
        self.assertEqual(m['basis'], 'order')
        self.assertIn('rated messages 25 to 36', m['stretches'][2]['label'])
        parts = measures.cut(288)
        for s, (a, b) in zip(m['stretches'], parts):
            self.assertIn(f'rated messages {a + 1} to {b}', s['label'])
            self.assertNotRegex(s['label'] + s['axis'], CLOCK)
            self.assertNotRegex(s['label'], MONTH_DAY)

    def test_with_times_inside_three_days_the_axis_shows_the_time_of_day(self):
        m = an(chat(48))                                                            # one minute apart: 47 minutes in all
        self.assertEqual(len(m['stretches']), 6)
        for s in m['stretches']:
            self.assertRegex(s['axis'], CLOCK)
            self.assertRegex(s['label'], CLOCK)
            self.assertRegex(s['label'], MONTH_DAY)
            self.assertRegex(s['label'], r'\b5\b')
        self.assertEqual(m['stretches'][0]['axis'], '18:00')
        self.assertEqual(m['stretches'][1]['axis'], '18:08')

    def test_the_time_of_day_needs_the_stretches_to_fall_within_three_days(self):
        # 48 messages 75 minutes apart span 2.45 days; 105 minutes apart span 3.43 days
        inside = an(chat(48, step=75))
        outside = an(chat(48, step=105))
        for s in inside['stretches']:
            self.assertRegex(s['axis'], CLOCK, s['axis'])
        for s in outside['stretches']:
            self.assertNotRegex(s['axis'], CLOCK, s['axis'])
            self.assertRegex(s['axis'] or 'Mar 5', MONTH_DAY)  # a repeated date is left blank
            self.assertRegex(s['label'], MONTH_DAY)

    def test_over_longer_spans_the_axis_is_a_date(self):
        m = an(chat(48, step=60 * 5))                                               # ten days
        for s in m['stretches']:
            self.assertNotRegex(s['axis'], CLOCK)
            self.assertRegex(s['axis'], MONTH_DAY)
            self.assertTrue(s['label'].startswith('Part '))
        self.assertRegex(m['stretches'][0]['axis'], MONTH_DAY)

    def test_dates_without_a_time_never_show_a_time(self):
        units = [unit(i, (T0.date() + datetime.timedelta(days=i // 16)).isoformat()) for i in range(48)]          # three days, 16 messages each
        m = an(units)
        self.assertEqual(m['basis'], 'time')
        for s in m['stretches']:
            self.assertNotRegex(s['axis'] + s['label'], CLOCK)

    def test_incognito_hides_every_clock_time_and_keeps_the_dates(self):
        for step in (1, 75, 105, 300):
            with self.subTest(step=step):
                loud = an(chat(48, step=step), incognito=False)
                quiet = an(chat(48, step=step), incognito=True)
                self.assertTrue(quiet['incognito'])
                for s in quiet['stretches']:
                    self.assertNotRegex(s['axis'], CLOCK)
                    self.assertNotRegex(s['label'], CLOCK)
                    self.assertTrue(s['label'].startswith('Part '))
                self.assertRegex(quiet['stretches'][0]['label'], MONTH_DAY, 'the day stays')
                if step in (1, 75):
                    self.assertTrue(any(CLOCK.search(s['axis']) for s in loud['stretches']), 'the same chat shows its times when Incognito is off')

    def test_incognito_hides_the_times_of_offset_stamps_too(self):
        units = [unit(i, stamp(i) + '+02:00') for i in range(48)]
        m = an(units, incognito=True)
        self.assertTrue(m['utc'])
        for s in m['stretches']:
            self.assertNotRegex(s['axis'] + s['label'], CLOCK)

    def test_under_incognito_the_raw_start_and_end_of_each_part_are_days_only(self):
        """MEASURES.md: while Incognito is on, even the raw start and end of each part in the analysis are days, never times of day."""
        day, moment = re.compile(r'^\d{4}-\d{2}-\d{2}$'), re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}')
        shapes = {f'{step} minutes': chat(48, step=step) for step in (1, 75, 105, 300)}
        shapes['offsets'] = [unit(i, stamp(i, 7) + '+02:00') for i in range(48)]
        shapes['zulu'] = [unit(i, stamp(i, 7) + 'Z') for i in range(48)]
        shapes['days only'] = [unit(i, (T0.date() + datetime.timedelta(days=i)).isoformat()) for i in range(48)]
        for name, units in shapes.items():
            with self.subTest(shape=name):
                quiet, loud = an(units, incognito=True), an(units)
                self.assertEqual((quiet['basis'], len(quiet['stretches'])), ('time', 6))
                for s, t in zip(quiet['stretches'], loud['stretches']):
                    self.assertRegex(s['start'], day)
                    self.assertRegex(s['end'], day)
                    self.assertEqual((s['start'], s['end']), (t['start'][:10], t['end'][:10]), 'the same days as with Incognito off')
                    self.assertRegex(t['start'], moment, 'the control: with Incognito off the raw start keeps its time')
                everything = json.dumps({k: quiet[k] for k in ('stretches', 'episodes', 'first3', 'last3')}, default=str)
                self.assertNotRegex(everything, r'T\d{2}:\d{2}|(?<![\d:])\d{1,2}:\d{2}(?![\d:])', 'no time of day anywhere in what the analysis holds')
        by_order = an(chat(48, dated=False), incognito=True)
        self.assertEqual(by_order['basis'], 'order')
        self.assertTrue(all(s['start'] is None and s['end'] is None for s in by_order['stretches']), 'in the order they were added there are no dates at all')

    def test_the_days_of_a_heated_moment_are_still_named_under_incognito(self):
        _, found, _ = scan(spike(9), incognito=True)
        (alert,) = find(found, r'heated up')
        self.assertRegex(alert['when'], MONTH_DAY)
        self.assertNotRegex(alert['when'], CLOCK)

    def test_times_are_called_utc_only_when_every_stamp_carries_an_offset(self):
        every = an([unit(i, stamp(i) + ('Z' if i % 2 else '+00:00')) for i in range(48)])
        self.assertTrue(every['utc'])
        for s in every['stretches']:
            self.assertIn('UTC', s['label'])
        mixed = an([unit(i, stamp(i) + ('Z' if i else '')) for i in range(48)])        # the first has none
        self.assertFalse(mixed['utc'])
        for s in mixed['stretches']:
            self.assertNotIn('UTC', s['label'] + s['axis'])
        none = an(chat(48))
        self.assertFalse(none['utc'])
        for s in none['stretches']:
            self.assertNotIn('UTC', s['label'] + s['axis'])

    def test_where_in_the_check_in_words(self):
        for k in (6, 12, 24):
            with self.subTest(stretches=k):
                self.assertRegex(measures.where_phrase(0, 0, k), r'start')
                self.assertRegex(measures.where_phrase(k - 1, k - 1, k), r'end')
                self.assertRegex(measures.where_phrase(k // 2 - 1, k // 2, k), r'middle')          # the two stretches either side of the centre
                self.assertNotRegex(measures.where_phrase(k // 2, k // 2, k), CLOCK)
        for k in (12, 24):
            self.assertRegex(measures.where_phrase(k // 2, k // 2, k), r'middle')


# =============================================================================================================================================
class CardTests(unittest.TestCase):
    """The four measure cards: names, counts, level words, trend sentences, and the health tile."""

    def card(self, units, key, sensitivity='medium'):
        return {c['key']: c for c in measures.cards(an(units, sensitivity))}[key]

    def test_four_cards_in_the_order_of_the_spec_with_its_words(self):
        cards = measures.cards(an(chat(96)))
        self.assertEqual([c['key'] for c in cards], ['escalation', 'conflict', 'empathy', 'passive'])
        self.assertEqual([c['name'] for c in cards], ['Escalation', 'Conflict', 'Empathy', 'Passive-aggressive'])
        self.assertEqual([c['kind'] for c in cards], ['heated', 'hostile', 'care', 'dig'])
        self.assertEqual([c['meaning'] for c in cards], ['How quickly the mood heats up.', 'How much of the talk is openly hostile.',
                                                         'How much of the talk shows care and understanding.',
                                                         'Sarcasm without open hostility. Friendly banter can count too.'])
        for c in cards:
            self.assertIn(c['tone'], ('good', 'info', 'warn', 'bad'))

    def test_the_headline_counts_messages_like_the_spec_example(self):
        where = spread(70, 8)
        card = self.card(chat(70, lambda i: {'Rude': 2} if i in where else None), 'conflict')
        self.assertRegex(card['headline'], r'^8 of 70 messages were openly hostile\.?$')
        self.assertEqual((card['count'], card['of']), (8, 70))
        self.assertEqual(card['level'], 'Some')                                      # 8/70 = 11.43 prints 11
        where = spread(69, 8)
        card = self.card(chat(69, lambda i: {'Rude': 2} if i in where else None), 'conflict')
        self.assertEqual(card['level'], 'Often')                                     # 8/69 = 11.59 prints 12: a printed 12 is never called Some

    def test_every_card_counts_its_own_flag(self):
        units = chat(60, lambda i: {'Rude': 2} if i % 6 == 0 else {'Empathetic': 3} if i % 6 == 1 else {'Sarcastic': 2} if i % 6 == 2 else {'Agitated': 2} if i % 6 == 3 else None)
        cards = {c['key']: c for c in measures.cards(an(units))}
        self.assertEqual((cards['conflict']['count'], cards['empathy']['count'], cards['passive']['count']), (10, 10, 10))
        for key in ('conflict', 'empathy', 'passive'):
            self.assertEqual(cards[key]['of'], 60)
            self.assertRegex(cards[key]['headline'], r'^10 of 60 messages')

    def test_the_level_word_follows_the_rate_printed(self):
        # 9 of 200 is 4.5 per 100: prints 5, so Some. 8 of 200 is 4.0: Rare. Empathy: 5 of 200 is 2.5, prints 3: Some; 4 of 200 is 2.0: Rare.
        for k, word in [(8, 'Rare'), (9, 'Some')]:
            where = spread(200, k)
            self.assertEqual(self.card(chat(200, lambda i: {'Rude': 2} if i in where else None), 'conflict')['level'], word)
            self.assertEqual(self.card(chat(200, lambda i: {'Sarcastic': 2} if i in where else None), 'passive')['level'], word)
        for k, word in [(4, 'Rare'), (5, 'Some')]:
            where = spread(200, k)
            self.assertEqual(self.card(chat(200, lambda i: {'Empathetic': 2} if i in where else None), 'empathy')['level'], word)

    def test_latest_is_the_word_for_the_last_third(self):
        # 96 messages: the first third (32 messages) is all hostile, the rest calm. Overall 32 of 96 = 33 per 100: Often. The last third has none: Rare.
        card = self.card(chat(96, lambda i: {'Rude': 2} if i < 32 else None), 'conflict')
        self.assertEqual((card['level'], card['latest']), ('Often', 'Rare'))
        card = self.card(chat(96, lambda i: {'Rude': 2} if i >= 64 else None), 'conflict')
        self.assertEqual((card['level'], card['latest']), ('Often', 'Often'))

    def test_the_series_is_the_rate_in_each_stretch(self):
        units = chat(96, lambda i: {'Empathetic': 2} if i % 4 == 0 else {'Rude': 2} if i < 8 else None)
        m = an(units)
        cards = {c['key']: c for c in measures.cards(m)}
        for key, kind in [('conflict', 'hostile'), ('empathy', 'care'), ('passive', 'dig'), ('escalation', 'heated')]:
            self.assertEqual(len(cards[key]['series']), 12)
            for got, s in zip(cards[key]['series'], m['stretches']):
                self.assertAlmostEqual(got, s['rates'][kind], delta=0.01)
        self.assertAlmostEqual(cards['conflict']['series'][0], 75.0, delta=0.01)    # 6 of the first 8 are hostile (the other 2 are the care ones)

    def test_a_steady_chat_says_no_clear_change_and_a_moving_one_says_which_way(self):
        flat = chat(96, lambda i: {'Rude': 2} if i % 4 == 0 else None)
        self.assertRegex(self.card(flat, 'conflict')['trend'], r'^No clear change\.?$')
        rising = chat(96, lambda i: {'Rude': 2} if i >= 64 and i % 2 == 0 else None)
        self.assertRegex(self.card(rising, 'conflict')['trend'], r'(?i)^rising')
        falling = chat(96, lambda i: {'Rude': 2} if i < 32 and i % 2 == 0 else None)
        self.assertRegex(self.card(falling, 'conflict')['trend'], r'(?i)^falling')
        care_up = chat(96, lambda i: {'Empathetic': 2} if i >= 64 and i % 2 == 0 else None)
        self.assertRegex(self.card(care_up, 'empathy')['trend'], r'(?i)^rising')

    def test_escalation_has_no_direction(self):
        card = self.card(chat(96, lambda i: {'Agitated': 2} if i >= 64 and i % 2 == 0 else None), 'escalation')
        self.assertNotRegex(card['trend'], r'(?i)rising|falling')

    def test_the_escalation_card_counts_moments(self):
        m = an(part_chat([0, 0, 0, 4, 0, 0, 0, 0, 0, 4, 0, 0], 8, HEAT))
        card = measures.cards(m)[0]
        self.assertEqual(card['count'], 2)
        self.assertAlmostEqual(card['peak'], 50.0, places=9)
        self.assertEqual(card['level'], 'Steep')
        self.assertRegex(card['headline'], r'\b2\b.*heated moments')
        one = measures.cards(an(part_chat([0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0], 8, HEAT)))[0]
        self.assertRegex(one['headline'], r'\b1\b.*heated moment\b')
        none = measures.cards(an(part_chat([0] * 12, 8, HEAT)))[0]
        self.assertEqual((none['count'], none['level']), (0, 'None'))
        self.assertRegex(none['headline'], r'(?i)\bno heated moments')

    def test_below_48_the_escalation_card_says_it_needs_more_messages(self):
        card = measures.cards(an(chat(40, lambda i: {'Agitated': 2} if 16 <= i < 24 else None)))[0]
        self.assertIsNone(card['count'], 'MEASURES.md: the count is None when Escalation reads Too early (a 0 would say that no heated moment was found)')
        self.assertRegex(card['headline'] + ' ' + card['note'], r'\b48\b')

    def test_tones_run_from_good_to_bad_in_the_right_direction(self):
        rank = {'good': 0, 'info': 1, 'warn': 2, 'bad': 3}
        calm = {c['key']: rank[c['tone']] for c in measures.cards(an(lively(chat(100))))}           # weak readings only: not silent, so the marks are not all grey
        hot = {c['key']: rank[c['tone']] for c in measures.cards(an(chat(100, lambda i: {'Rude': 2} if i % 3 == 0 else None)))}
        snide = {c['key']: rank[c['tone']] for c in measures.cards(an(chat(100, lambda i: {'Sarcastic': 2} if i % 3 == 0 else None)))}
        self.assertLess(calm['conflict'], hot['conflict'])
        self.assertLess(calm['passive'], snide['passive'])
        warm = {c['key']: rank[c['tone']] for c in measures.cards(an(chat(100, lambda i: {'Empathetic': 2} if i % 3 == 0 else None)))}
        self.assertLess(warm['empathy'], calm['empathy'], 'Often is better than Rare for Empathy')

    def test_the_cards_at_low_sensitivity_use_the_raised_cut_offs_and_the_page_says_so(self):
        units = chat(100, lambda i: {'Rude': 2} if i % 6 == 0 else None)           # 17 hostile in 100: Often at Medium, Some at Low
        self.assertEqual(self.card(units, 'conflict')['level'], 'Often')
        self.assertEqual(self.card(units, 'conflict', 'low')['level'], 'Some')
        low = build_with(units, {'vibe_sensitivity': 'low'})
        medium = build_with(units, {'vibe_sensitivity': 'medium'})
        mentions = re.compile(r'(?i)\blow\b.{0,40}\bsensitiv|\bsensitiv\w*.{0,40}\blow\b|cut-?offs?.{0,60}(raised|higher|relaxed|looser|moved up)|(raised|higher|relaxed|looser).{0,60}cut-?offs?')
        said = {t for w, t in page_strings(low) if mentions.search(t)} - {t for w, t in page_strings(medium) if mentions.search(t)}
        self.assertTrue(said, 'MEASURES.md: at Low sensitivity the cut-offs are raised "and the page says so"; no string of the built measures says it, '
                              'and none is there at Low that is not there at Medium')

    def test_the_health_tile(self):
        m = an(chat(96, lambda i: {'Polite': 3} if i % 3 == 0 else None))
        tile = measures.health_tile(m)
        self.assertEqual(tile['key'], 'health')
        self.assertEqual(tile['name'], 'Health score')
        self.assertEqual(len(tile['series']), 12)
        for got, s in zip(tile['series'], m['stretches']):
            self.assertAlmostEqual(got, s['health'], delta=0.01)
        self.assertRegex(tile['trend'], r'^No clear change\.?$')
        rising = measures.health_tile(an(chat(96, lambda i: {'Polite': 3} if i >= 64 else {'Rude': 2} if i < 32 else None)))
        self.assertRegex(rising['trend'], r'(?i)\brising\b')

    # -- Too early, the grey marks below 48 messages, and the note for a part that chance alone could make hot -------------------------------------------------
    def test_escalation_reads_too_early_in_grey_when_nothing_could_be_judged_and_never_a_green_none(self):
        """MEASURES.md: below 48 timeline messages nothing can be judged, so the card reads Too early with a neutral mark, never a green None."""
        burst = lambda i: {'Agitated': 2} if 16 <= i < 24 else None                 # what a longer chat could call a heated moment
        for n in (1, 10, 23, 24, 30, 40, 47):
            with self.subTest(n=n):
                card = self.card(chat(n, burst), 'escalation')
                self.assertEqual((card['level'], card['word'], card['tone'], card['tone_word']), ('Too early', 'early', 'info', 'Neutral'))
                self.assertIsNone(card['count'], 'no count when nothing could be judged: a 0 would read as "no heated moments"')
                self.assertRegex(card['headline'], r'\b24\b' if n < 24 else r'\b48\b')                 # it says how many messages it needs
                self.assertNotRegex(card['headline'], r'(?i)no heated moment')
        for n in (48, 49, 96):                                                       # judged and calm: a real None, with the green mark, and a count of 0
            with self.subTest(n=n):
                card = self.card(lively(chat(n)), 'escalation')
                self.assertEqual((card['level'], card['word'], card['tone'], card['tone_word'], card['count']), ('None', 'none', 'good', 'A good sign', 0))
                self.assertRegex(card['headline'], r'(?i)\bno heated moments')
                silent = self.card(chat(n), 'escalation')                              # judged, but almost no tone was found: the word is None, the mark is grey
                self.assertEqual((silent['level'], silent['tone'], silent['tone_word'], silent['count']), ('None', 'info', 'Neutral', 0))

    def test_too_early_counts_the_messages_on_the_timeline_and_not_the_ones_that_were_rated(self):
        units = chat(70, lambda i: {'Rude': 2} if i % 3 == 0 else None)
        for u in units[45:]:
            u['timestamp'] = ''                                                      # 45 of 70 carry a date: over 3 in 5, so the lines follow the clock and hold 45
        m = an(units)
        self.assertEqual((m['n'], m['timeline_n'], m['basis'], m['has_series'], m['verdicts']), (70, 45, 'time', True, False))
        cards = {c['key']: c for c in measures.cards(m)}
        self.assertEqual((cards['escalation']['level'], cards['escalation']['tone']), ('Too early', 'info'))
        self.assertEqual((cards['conflict']['level'], cards['conflict']['tone']), ('Often', 'bad'), '70 messages were rated, so the rate cards keep their marks')

    def test_the_rate_cards_are_grey_below_48_rated_messages_and_keep_their_level_words(self):
        """MEASURES.md: below 48 rated messages the mark of Conflict, Empathy and Passive-aggressive is neutral grey (too few messages to call it good or bad)."""
        mixed = lambda i: {'Rude': 2} if i % 3 == 0 else {'Empathetic': 2} if i % 3 == 1 else {'Sarcastic': 2}       # a third of the messages for each of the three
        for n, tones in ((1, 'info'), (24, 'info'), (47, 'info')):
            with self.subTest(n=n):
                cards = {c['key']: c for c in measures.cards(an(chat(n, mixed)))}
                self.assertEqual([cards[k]['tone'] for k in ('conflict', 'empathy', 'passive')], ['info'] * 3)
                self.assertEqual([cards[k]['tone_word'] for k in ('conflict', 'empathy', 'passive')], ['Neutral'] * 3)
        for n in (48, 49):
            with self.subTest(n=n):
                cards = {c['key']: c for c in measures.cards(an(chat(n, mixed)))}
                self.assertEqual([cards[k]['level'] for k in ('conflict', 'empathy', 'passive')], ['Often'] * 3)
                self.assertEqual([cards[k]['tone'] for k in ('conflict', 'empathy', 'passive')], ['bad', 'good', 'warn'])
        cards47, cards48 = ({c['key']: c for c in measures.cards(an(chat(n, mixed)))} for n in (47, 48))
        for key in ('conflict', 'empathy', 'passive'):
            self.assertEqual(cards47[key]['level'], cards48[key]['level'], 'the words are the same at 47 and 48: only the mark is neutral')
        calm47, calm48 = ({c['key']: c for c in measures.cards(an(lively(chat(n))))} for n in (47, 48))        # weak readings only: not silent, so only the count of messages decides
        self.assertEqual([calm47[k]['tone'] for k in ('conflict', 'empathy', 'passive')], ['info', 'info', 'info'], 'Rare is not called good below 48 messages')
        self.assertEqual([calm48[k]['tone'] for k in ('conflict', 'empathy', 'passive')], ['good', 'info', 'good'])
        some47, some48 = (self.card(hostile_chat(n, 4 if n == 47 else 5), 'conflict') for n in (47, 48))                  # 4 of 47 and 5 of 48 are Some (9 and 10 in 100)
        self.assertEqual((some47['level'], some47['tone'], some48['level'], some48['tone']), ('Some', 'info', 'Some', 'warn'))

    def test_the_marks_of_all_four_cards_are_neutral_when_almost_no_tone_was_found(self):
        """MEASURES.md: the marks are neutral when almost no tone was found (19 in 20 messages rated all 0). The level words and the counts are not touched."""
        for n in (1, 10, 24, 47, 48, 96, 300):
            with self.subTest(n=n):
                cards = {c['key']: c for c in measures.cards(an(chat(n)))}
                for key, card in cards.items():
                    self.assertEqual((card['tone'], card['tone_word']), ('info', 'Neutral'), key)
                self.assertEqual([cards[k]['level'] for k in ('conflict', 'empathy', 'passive')], ['Rare'] * 3, 'the words stay')
                self.assertEqual((cards['escalation']['level'], cards['escalation']['count']), ('Too early', None) if n < 48 else ('None', 0))
        # the edge, 19 in 20: the fewest silent messages that trip it are 95 of 100, 190 of 200 and 285 of 300; one fewer and the marks are the usual ones
        usual = {'escalation': 'good', 'conflict': 'good', 'empathy': 'info', 'passive': 'good'}              # Rare Conflict and Rare sarcasm are good; Rare care is only neutral
        for n, tripped in [(100, 95), (200, 190), (300, 285)]:
            for silent, grey in [(tripped, True), (tripped - 1, False)]:
                with self.subTest(n=n, silent=silent):
                    units = chat(n, lambda i: {'Ignorant': 1} if i < n - silent else None)             # the others carry a weak reading, which is a reading
                    cards = {c['key']: c for c in measures.cards(an(units))}
                    self.assertEqual({k: c['tone'] for k, c in cards.items()}, {k: 'info' for k in cards} if grey else usual)
        # a level that would be worth a look is grey too when almost no tone was found: 5 in 100 is Some, a warn in a chat with tone and grey in one with almost none
        for hostile, silent in [(5, True), (6, False)]:
            for key, tone in [('conflict', 'Rude'), ('passive', 'Sarcastic')]:
                with self.subTest(hostile=hostile, card=key):
                    card = self.card(hostile_chat(100, hostile, tone), key)
                    self.assertEqual((card['level'], card['tone'], card['tone_word']), ('Some', 'info', 'Neutral') if silent else ('Some', 'warn', 'Worth a look'))

    def test_a_tense_start_makes_the_escalation_none_neutral_and_nothing_else(self):
        """MEASURES.md: Escalation's None is neutral when the chat was already tense in its first part (30 or more heated messages in 100), because a climb before the
        start cannot be seen. The word stays None, the count stays 0 and the other cards are marked as usual."""
        for first, size, tense in [(6, 20, True), (7, 20, True), (20, 20, True), (3, 10, True), (5, 20, False), (2, 10, False), (0, 20, False)]:      # 30, 35, 100, 30 / 25, 20, 0 in 100
            with self.subTest(first=first, size=size):
                m = an(lively(part_chat([first] + [0] * 23, size, HEAT)))
                cards = {c['key']: c for c in measures.cards(m)}
                self.assertEqual(m['opening_heated'], tense)
                self.assertEqual((cards['escalation']['level'], cards['escalation']['word'], cards['escalation']['count']), ('None', 'none', 0))
                self.assertEqual((cards['escalation']['tone'], cards['escalation']['tone_word']), ('info', 'Neutral') if tense else ('good', 'A good sign'))
                if tense:
                    self.assertRegex(cards['escalation']['note'], TENSE_NOTE)
                else:
                    self.assertEqual(cards['escalation']['note'], '')
                self.assertEqual([cards[k]['tone'] for k in ('conflict', 'empathy', 'passive')], ['good', 'info', 'good'], 'the other cards are marked as usual')
        # a tense start followed by a heated moment: Escalation reads the moment and wears the mark of its word (a climb of 40 on three calm stretches at 5 in 100)
        m = an(lively(part_chat([6] + [0] * 7 + [1, 1, 1, 9] + [0] * 12, 20, HEAT)))
        card = measures.cards(m)[0]
        self.assertEqual((m['opening_heated'], card['level'], card['tone'], card['count']), (True, 'Steep', 'bad', 1))
        self.assertRegex(card['note'], TENSE_NOTE, 'and the note still says the start was tense')
        # under 48 messages on the timeline nothing can be judged: Too early, grey, with no count, and no note about the start either
        m = an(lively(chat(40, lambda i: {'Agitated': 3} if i < 8 else None)))
        card = measures.cards(m)[0]
        self.assertEqual((m['opening_heated'], card['level'], card['tone'], card['count'], card['note']), (True, 'Too early', 'info', None, ''))

    def test_the_escalation_count_is_none_while_it_reads_too_early_and_a_number_once_it_can_be_judged(self):
        """MEASURES.md: ms['cards'][0]['count'] is None when Escalation reads Too early. The three rate cards always have a count of messages."""
        for n in (1, 10, 23, 24, 40, 47):
            with self.subTest(n=n):
                cards = measures.cards(an(lively(chat(n))))
                self.assertIsNone(cards[0]['count'])
                self.assertEqual([c['count'] for c in cards[1:]], [0, 0, 0])
        for n in (48, 96):
            with self.subTest(n=n):
                self.assertEqual(measures.cards(an(lively(chat(n))))[0]['count'], 0)
        # 70 rated messages of which 45 carry a date: the timeline is too short, so Escalation is Too early whatever the rated count
        units = chat(70, lambda i: {'Rude': 2} if i % 3 == 0 else None)
        for u in units[45:]:
            u['timestamp'] = ''
        cards = measures.cards(an(units))
        self.assertEqual((cards[0]['count'], cards[0]['level'], cards[1]['count']), (None, 'Too early', 24))
        self.assertEqual(measures.cards(an(part_chat([0, 0, 0, 4, 0, 0, 0, 0, 0, 4, 0, 0], 8, HEAT)))[0]['count'], 2)

    def test_the_card_note_says_when_a_hot_part_could_be_chance_and_only_then(self):
        """MEASURES.md: when a part of the chat has 30 or more heated messages in 100 but no heated moment passed the chance test, the card says it."""
        said = 'Some parts were heated, but not by more than chance alone would give.'
        busy = [2, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3, 3]            # 12 parts of 8: the first is at 25 in 100, the seventh at 75, the rest at 38: a community that is often heated
        m = an(part_chat(busy, 8, HEAT))
        self.assertEqual((m['episodes'], m['opening_heated'], m['verdicts']), ([], False, True), 'the precondition: a hot part, no heated moment, no tense start')
        self.assertGreaterEqual(max(s['rates']['heated'] for s in m['stretches']), 30)
        card = measures.cards(m)[0]
        self.assertEqual((card['note'], card['level'], card['count']), (said, 'None', 0))
        self.assertNotIn('opening', [n['key'] for n in scan(part_chat(busy, 8, HEAT))[2]], 'it is a card note, and not the tense start')
        # the edge: 24 parts of 10 with the hottest at exactly 30 in 100 has the note, at 20 in 100 it has none
        for hottest, note in ((3, said), (2, '')):
            counts = [2] * 24
            counts[11] = hottest
            m = an(part_chat(counts, 10, HEAT))
            self.assertEqual(m['episodes'], [], hottest)
            self.assertEqual(measures.cards(m)[0]['note'], note, hottest)
        # not with a heated moment: the same community with one part that does pass the chance test
        moment = an(part_chat([0, 0, 0, 0, 0, 0, 6, 0, 0, 0, 0, 0], 8, HEAT))
        self.assertEqual((len(moment['episodes']), measures.cards(moment)[0]['note']), (1, ''))
        # not under 48 messages on the timeline: nothing can be judged yet, whatever the parts hold
        young = an(part_chat([0, 0, 6, 0, 0], 8, HEAT))
        self.assertEqual((young['verdicts'], measures.cards(young)[0]['note']), (False, ''))
        # not instead of the tense start: the note about a first part that is already tense stands alone
        tense = measures.cards(an(part_chat([3] + busy[1:], 8, HEAT)))[0]
        self.assertRegex(tense['note'], r'(?i)already tense')
        self.assertNotIn('chance', tense['note'])
        # the other three cards never carry it
        self.assertEqual([c['note'] for c in measures.cards(an(part_chat(busy, 8, HEAT)))[1:]], ['', '', ''])

    def test_every_card_and_the_health_tile_say_their_mark_in_words(self):
        """MEASURES.md: a surface that cannot show the icon (the PDF and the CSV) says the mark in words instead, from tone_word."""
        self.assertEqual(measures.TONE_WORDS, {'good': 'A good sign', 'info': 'Neutral', 'warn': 'Worth a look', 'bad': 'Look at this first'})
        seen = set()
        for units in (chat(96), chat(96, lambda i: {'Rude': 2} if i % 3 == 0 else None), chat(96, lambda i: {'Sarcastic': 2} if i % 3 == 0 else None),
                      chat(96, lambda i: {'Empathetic': 2} if i % 3 == 0 else None), chat(40, lambda i: {'Rude': 2} if i % 3 == 0 else None),
                      chat(96, lambda i: {'Agitated': 2} if 40 <= i < 56 else None)):
            m = an(units)
            for card in measures.cards(m):
                self.assertEqual(card['tone_word'], measures.TONE_WORDS[card['tone']], card['key'])
                seen.add(card['tone'])
            tile = measures.health_tile(m)
            self.assertEqual(tile['tone_word'], measures.TONE_WORDS[tile['tone']])
        self.assertEqual(seen, {'good', 'info', 'warn', 'bad'}, 'the chats above show every mark')

    def test_the_health_tile_says_a_good_sign_neutral_or_look_at_this_first_by_its_band(self):
        for tones, status, tone, word in [({'Polite': 3}, 'Excellent', 'good', 'A good sign'), ({'Polite': 2}, 'Good', 'good', 'A good sign'),
                                          ({'Ignorant': 2}, 'Fair', 'info', 'Neutral'), ({}, 'Fair', 'info', 'Neutral'),
                                          ({'Rude': 3}, 'Needs attention', 'bad', 'Look at this first')]:
            with self.subTest(status=status):
                tile = measures.health_tile(an(chat(48, lambda i: dict(tones))))
                self.assertEqual((tile['status'], tile['tone'], tile['tone_word']), (status, tone, word))


def hostile_chat(n, k, tone='Rude'):
    """n messages, k of them (spread evenly) openly hostile -- or whatever `tone` is rated 2."""
    where = spread(n, k)
    return chat(n, lambda i: {tone: 2} if i in where else None)


def thirds_chat(third, counts, tones):
    """3 x `third` messages in which `tones` is on counts[0], counts[1] and counts[2] messages of the first, middle and last third, spread evenly inside each.
    The builder checks that the third is what measures.cut makes of it."""
    n = 3 * third
    parts = measures.cut(n)
    k = len(parts) // 3
    assert parts[k - 1][1] == third and parts[len(parts) - k][0] == 2 * third, (n, parts)
    where = set()
    for t, count in enumerate(counts):
        where |= {t * third + p for p in spread(third, count)} if count else set()
    return chat(n, lambda i: dict(tones) if i in where else None)


def blend(*chats):
    """The same messages carrying the tones of every chat given (the highest rating of each tone)."""
    out = []
    for group in zip(*chats):
        r = ratings()
        for u in group:
            for tone, value in u['ratings'].items():
                r[tone] = max(r[tone], value)
        out.append({'id': group[0]['id'], 'timestamp': group[0]['timestamp'], 'ratings': r})
    return out


def spike(heated, base=(1, 1, 1), at=11, size=20):
    """24 stretches of `size`, calm but for the three before stretch `at` (base heated messages in each) and `at` itself (`heated`)."""
    counts = [0] * 24
    counts[at - 3:at] = list(base)
    counts[at] = heated
    return part_chat(counts, size, HEAT)


def saved(n=200, **kw):
    """A saved record of a check, as the store keeps it, for the comparison tests. It carries the fingerprint of its model name, as every record does
    (`model_key`; pass model_key= to set it by hand)."""
    out = {'created_at': '2026-05-01T12:00:00', 'gen': 1, 'n': n, 'total': n, 'sampled': 0, 'basis': 'time', 'stretches': 12, 'model': 'qwen2.5:7b', 'sensitivity': 'medium',
           'period_start': '', 'period_end': '', 'health': 50.0, 'health_sd': 10.0, 'hostile_n': 10, 'care_n': 10, 'dig_n': 10, 'heated_n': 20, 'episodes': 0, 'peak': 0.0,
           'sig': '1:2'}
    out.update(kw)
    if 'model_key' not in kw:
        out['model_key'] = fingerprint(out.get('model') or '')
    return out


def legacy(record):
    """The same record as an older build saved it: no fingerprint of the model name, only the name."""
    return {k: v for k, v in record.items() if k != 'model_key'}


def neutral(n=200):
    """A chat that raises no alert of its own: a little politeness, nothing else."""
    return chat(n, lambda i: {'Polite': 2} if i % 5 == 0 else None)


# =============================================================================================================================================
class AlertTests(unittest.TestCase):
    """Every alert at its cut-off and one step below it."""

    # -- openly hostile / sarcastic: the level alerts ------------------------------------------------------------------------------------------
    def test_the_hostile_level_alert_at_the_cut_offs_for_a_check_read_whole(self):
        # 60 messages (under 72, so the whole check is read). Lower bounds 100p - 100 sqrt(p(1-p)/n), by hand:
        #   11 hostile: 18.33 - 4.99 = 13.34 >= 12   10 hostile: 16.67 - 4.81 = 11.86 < 12   (Medium at 12)
        #   16 hostile: 26.67 - 5.71 = 20.96 >= 20   15 hostile: 25.00 - 5.59 = 19.41 < 20   (High at 20)
        self.assertTrue(clears(11, 60, 12) and not clears(10, 60, 12) and clears(16, 60, 20) and not clears(15, 60, 20))
        for k, level in [(0, None), (3, None), (5, None), (10, None), (11, 'medium'), (12, 'medium'), (15, 'medium'), (16, 'high'), (20, 'high'), (30, 'high')]:
            with self.subTest(hostile=k):
                _, found, _ = scan(hostile_chat(60, k))
                got = find(found, r'openly hostile')
                self.assertEqual([a['level'] for a in got], [level] if level else [])
                if k == 11:
                    self.assertRegex(got[0]['title'], r'^Some\b')
                    self.assertRegex(got[0]['detail'], r'\b11\b')
                    self.assertRegex(got[0]['detail'] + got[0]['base'], r'\b60\b')
                if level == 'high':
                    self.assertRegex(got[0]['title'], r'^Many\b')

    def test_the_sarcasm_alert_has_the_same_rules_and_is_never_above_medium(self):
        for k, level in [(10, None), (11, 'medium'), (15, 'medium'), (16, 'medium'), (30, 'medium')]:
            with self.subTest(dig=k):
                _, found, _ = scan(hostile_chat(60, k, 'Sarcastic'))
                got = find(found, r'Sarcastic wording is common')
                self.assertEqual([a['level'] for a in got], [level] if level else [])
                self.assertFalse(find(found, r'openly hostile'), 'a dig is not hostile')

    def test_the_cut_offs_are_higher_at_low_sensitivity(self):
        # Medium at Low is 18 per 100 (12 x 1.5). n = 60: 14 -> 23.33 - 5.46 = 17.87 < 18; 15 -> 19.41 >= 18.
        self.assertFalse(clears(14, 60, 18))
        self.assertTrue(clears(15, 60, 18))
        for kind, tone, title in [('hostile', 'Rude', r'openly hostile'), ('dig', 'Sarcastic', r'Sarcastic wording is common')]:
            for k, low, medium in [(11, False, True), (14, False, True), (15, True, True), (10, False, False)]:
                with self.subTest(kind=kind, k=k):
                    self.assertEqual(bool(find(scan(hostile_chat(60, k, tone), 'low')[1], title)), low)
                    self.assertEqual(bool(find(scan(hostile_chat(60, k, tone), 'medium')[1], title)), medium)
        self.assertEqual([a['level'] for a in find(scan(hostile_chat(60, 40), 'low')[1], r'openly hostile')], ['high'])
        # the Conflict cut-offs are raised both ways: High at Low is 20 x 1.5 = 30. n = 60: 22 hostile -> 36.67 - 6.22 = 30.45 >= 30; 21 -> 35 - 6.16 = 28.84 < 30
        self.assertTrue(clears(22, 60, 30) and not clears(21, 60, 30))
        for k, low, medium in [(21, 'medium', 'high'), (22, 'high', 'high'), (16, 'medium', 'high'), (15, 'medium', 'medium')]:
            with self.subTest(high_boundary=k):
                self.assertEqual([a['level'] for a in find(scan(hostile_chat(60, k), 'low')[1], r'openly hostile')], [low])
                self.assertEqual([a['level'] for a in find(scan(hostile_chat(60, k), 'medium')[1], r'openly hostile')], [medium])

    def test_a_level_alert_reads_the_latest_third_once_it_holds_24_messages(self):
        # 96 messages, a third is 32. The same number of hostile messages in each third (spread evenly), so no direction is called.
        # By hand for the latest 32: 7 -> 21.88 - 7.31 = 14.57 >= 12 (Medium); 6 -> 11.85 < 12; 9 -> 28.13 - 7.95 = 20.2 >= 20 (High); 8 -> 25 - 7.65 = 17.35 < 20.
        self.assertTrue(clears(7, 32, 12) and not clears(6, 32, 12) and clears(9, 32, 20) and not clears(8, 32, 20))
        for per_third, level in [(6, None), (7, 'medium'), (8, 'medium'), (9, 'high')]:
            with self.subTest(per_third=per_third):
                _, found, _ = scan(thirds_chat(32, (per_third,) * 3, {'Rude': 2}))
                got = find(found, r'openly hostile')
                self.assertEqual([a['level'] for a in got], [level] if level else [])
                if level:
                    self.assertRegex(got[0]['base'], r'^Based on 32 messages in the latest part of the chat\.?$')
                    self.assertFalse(find(found, r'picking up|easing'))

    # -- the mood heated up fast -----------------------------------------------------------------------------------------------------------------
    def test_the_heated_moment_alert_needs_climb_and_level_for_each_priority(self):
        # 24 stretches of 20. A moment's climb = its heated rate - the pooled rate of the three stretches before; its level = its heated rate.
        #   Medium: climb 25 and level 30      High: climb 40 and level 45
        table = [(6, (1, 1, 1), 'medium'),    # climb 30 - 5 = 25, level 30: both on the line
                 (5, (0, 0, 0), None),        # climb 25, level 25: the level is short
                 (5, (1, 1, 1), None),        # climb 20, level 25: both short
                 (6, (0, 0, 0), 'medium'),    # climb 30, level 30
                 (7, (1, 1, 1), 'medium'),    # climb 30, level 35
                 (8, (0, 0, 0), 'medium'),    # climb 40, level 40: the climb is High's, the level is not
                 (8, (1, 1, 1), 'medium'),    # climb 35, level 40
                 (9, (1, 1, 1), 'high'),      # climb 40, level 45: both on the line
                 (9, (1, 1, 2), 'medium'),    # climb 45 - 6.67 = 38.3: the climb is short of High
                 (9, (0, 0, 0), 'high'),      # climb 45, level 45
                 (10, (1, 1, 1), 'high'),     # climb 45, level 50
                 (12, (1, 1, 1), 'high')]
        for heated, base, level in table:
            with self.subTest(heated=heated, base=base):
                m, found, _ = scan(spike(heated, base))
                got = find(found, r'heated up')
                self.assertEqual([a['level'] for a in got], [level] if level else [])
                self.assertEqual(len(m['episodes']), 1, 'the moment itself is counted (climb 15 or more, 3 or more heated messages, z over 3.5)')
                if level:
                    self.assertRegex(got[0]['detail'], r'\d')
                    self.assertRegex(got[0]['base'], r'\d')

    def test_the_heated_moment_cut_offs_are_1_2_times_higher_for_medium_at_low_sensitivity(self):
        # Medium at Low: climb 30 and level 36. High stays at 40 and 45.
        table = [(6, (1, 1, 1), None, 'medium'),      # climb 25, level 30
                 (7, (1, 1, 1), None, 'medium'),      # climb 30, level 35: the level is short of 36
                 (8, (1, 1, 1), 'medium', 'medium'),  # climb 35, level 40
                 (8, (0, 0, 0), 'medium', 'medium'),  # climb 40, level 40
                 (8, (2, 2, 3), None, 'medium'),      # climb 40 - 11.67 = 28.3: the climb is short of 30
                 (9, (1, 1, 1), 'high', 'high')]
        for heated, base, low, medium in table:
            with self.subTest(heated=heated, base=base):
                got = [a['level'] for a in find(scan(spike(heated, base), 'low')[1], r'heated up')]
                self.assertEqual(got, [low] if low else [])
                got = [a['level'] for a in find(scan(spike(heated, base), 'medium')[1], r'heated up')]
                self.assertEqual(got, [medium] if medium else [])

    def test_the_heated_moment_alert_says_where_and_when_but_never_a_clock_time(self):
        m, found, _ = scan(spike(9))
        alert = find(found, r'heated up')[0]
        self.assertRegex(alert['when'], r'(?i)middle')
        self.assertRegex(alert['when'], MONTH_DAY, 'a dated chat names the day')
        self.assertRegex(alert['when'], r'\b5\b')
        every_text = ' '.join(str(alert[k]) for k in ('title', 'detail', 'try', 'base', 'when'))
        self.assertNotRegex(every_text, CLOCK)
        # over twenty days the day is named and still no clock time
        slow = [unit(i, stamp(i, 60)) for i in range(480)]
        for u, base in zip(slow, spike(9)):
            u['ratings'] = base['ratings']
        alert = find(scan(slow)[1], r'heated up')[0]
        self.assertRegex(alert['when'], MONTH_DAY)
        self.assertNotRegex(' '.join(str(alert[k]) for k in ('title', 'detail', 'try', 'base', 'when')), CLOCK)
        # no dates: where in the check, and no month
        undated = spike(9)
        for u in undated:
            u['timestamp'] = ''
        alert = find(scan(undated)[1], r'heated up')[0]
        self.assertRegex(alert['when'], r'(?i)middle')
        self.assertNotRegex(alert['when'], MONTH_DAY)

    def test_incognito_keeps_clock_times_out_of_every_alert_and_note(self):
        m, found, notes = scan(spike(9), incognito=True)
        self.assertTrue(find(found, r'heated up'))
        for a in found:
            self.assertNotRegex(' '.join(str(a[k]) for k in ('title', 'detail', 'try', 'base', 'when')), CLOCK)
        for note in notes:
            self.assertNotRegex(note['text'], CLOCK)

    def test_two_heated_moments_make_one_alert_at_the_priority_of_the_stronger(self):
        # a Medium moment (climb 25, level 30) and a High one (climb 40, level 45), in either order: one verdict for the measure
        for medium_at, high_at in [(6, 18), (18, 6)]:
            counts = [0] * 24
            counts[medium_at - 3:medium_at] = [1, 1, 1]
            counts[medium_at] = 6
            counts[high_at - 3:high_at] = [1, 1, 1]
            counts[high_at] = 9
            m, found, _ = scan(part_chat(counts, 20, HEAT))
            self.assertEqual(len(m['episodes']), 2)
            self.assertEqual([a['level'] for a in find(found, r'heated up')], ['high'], (medium_at, high_at))

    def test_the_moment_must_be_a_counted_moment(self):
        # 2 heated of 8 after a calm run is a climb of 25 with z over 3.5 but only 2 heated messages: no moment, so no alert, though the level would have been 25.
        _, found, _ = scan(part_chat([0, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0], 8, HEAT))
        self.assertFalse(find(found, r'heated up'))
        # 4 of 8 after a calm run: level 50, climb 50 -> High
        _, found, _ = scan(part_chat([0, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0, 0], 8, HEAT))
        self.assertEqual([a['level'] for a in find(found, r'heated up')], ['high'])

    # -- trends: picking up, growing, fading, easing ---------------------------------------------------------------------------------------------
    def test_hostile_wording_is_picking_up_when_conflict_rises_strongly(self):
        # thirds of 100: 0 -> 8 per 100 is z 2.66 (strong); 0 -> 7 is z 2.44 (a "rising", not strong). Both stay under the level alert's 12 per 100 + one se.
        self.assertGreaterEqual(abs(8) / se_of_change(0, 100, 8, 100), 2.5)
        self.assertLess(abs(7) / se_of_change(0, 100, 7, 100), 2.5)
        for last, alert in [(7, False), (8, True), (12, True)]:
            with self.subTest(last=last):
                m, found, _ = scan(thirds_chat(100, (0, 4, last), {'Rude': 2}))
                self.assertEqual(m['trend']['hostile']['call'], 'rising')
                self.assertEqual(m['trend']['hostile']['strong'], alert)
                self.assertEqual([a['level'] for a in find(found, r'Hostile wording is picking up')], ['medium'] if alert else [])
                self.assertFalse(find(found, r'openly hostile'))

    def test_picking_up_needs_five_per_100_lately(self):
        # thirds of 1000: 0 -> 44 per 1000 is strongly rising (z over 6) but 4.4 per 100 is under 5; 0 -> 50 is 5.0
        self.assertGreater(4.4 / se_of_change(0, 1000, 44, 1000), 2.5)
        for tone, title in [({'Rude': 2}, r'Hostile wording is picking up'), ({'Sarcastic': 2}, r'Sarcastic wording is growing')]:
            for last, alert in [(44, False), (50, True)]:
                with self.subTest(tone=tone, last=last):
                    m, found, _ = scan(thirds_chat(1000, (0, 20, last), tone))
                    self.assertTrue(m['trend']['hostile' if 'Rude' in tone else 'dig']['strong'])
                    self.assertEqual([a['level'] for a in find(found, title)], ['medium'] if alert else [])

    def test_sarcastic_wording_is_growing_when_it_rises_strongly_to_at_least_5(self):
        for last, alert in [(7, False), (8, True)]:
            with self.subTest(last=last):
                m, found, _ = scan(thirds_chat(100, (0, 4, last), {'Sarcastic': 2}))
                self.assertEqual([a['level'] for a in find(found, r'Sarcastic wording is growing')], ['medium'] if alert else [])
                self.assertFalse(find(found, r'Sarcastic wording is common'))

    def test_fewer_messages_show_care_when_care_falls_strongly(self):
        # thirds of 100: 20 -> 7 is z 2.68 (strong); 20 -> 8 is z 2.43 (a "falling", not strong)
        self.assertGreaterEqual(13 / se_of_change(20, 100, 7, 100), 2.5)
        self.assertLess(12 / se_of_change(20, 100, 8, 100), 2.5)
        for last, alert in [(8, False), (7, True), (3, True)]:
            with self.subTest(last=last):
                m, found, _ = scan(thirds_chat(100, (20, 14, last), {'Empathetic': 2}))
                self.assertEqual(m['trend']['care']['call'], 'falling')
                self.assertEqual([a['level'] for a in find(found, r'Fewer messages show care')], ['medium'] if alert else [])
                self.assertFalse(find(found, r'Care is growing'))

    def test_care_is_growing_when_care_rises_strongly(self):
        for last, alert in [(7, False), (8, False), (20, True)]:
            first = 7 if last == 20 else 20 - 0
            with self.subTest(first=first, last=last):
                m, found, _ = scan(thirds_chat(100, (first, 14, last), {'Empathetic': 2}))
                self.assertEqual([a['level'] for a in find(found, r'Care is growing')], ['good'] if alert else [])
        # 8 -> 20 is z 2.43: not strong, so not good news
        m, found, _ = scan(thirds_chat(100, (8, 14, 20), {'Empathetic': 2}))
        self.assertEqual(m['trend']['care']['call'], 'rising')
        self.assertFalse(find(found, r'Care is growing'))
        self.assertFalse(find(found, r'Fewer messages show care'))

    def test_hostility_is_easing_when_conflict_falls_strongly(self):
        m, found, _ = scan(thirds_chat(100, (12, 6, 0), {'Rude': 2}))
        self.assertTrue(m['trend']['hostile']['strong'])
        self.assertEqual([a['level'] for a in find(found, r'Hostility is easing')], ['good'])
        self.assertFalse(find(found, r'openly hostile|picking up'))
        # 7 -> 0 is z 2.44: called, not strong, so no good news
        m, found, _ = scan(thirds_chat(100, (7, 4, 0), {'Rude': 2}))
        self.assertEqual(m['trend']['hostile']['call'], 'falling')
        self.assertFalse(find(found, r'Hostility is easing'))

    def test_good_news_is_dropped_while_an_alert_about_the_same_measure_stands(self):
        # hostile 60, 40 and 20 per 100: falling strongly (z 6.3), so "easing" would be good news. But the latest third still has 20 hostile messages in 100
        # (20 - 4.0 = 16.0 >= 12), so the level alert stands, and one verdict per measure means the good news line goes.
        m, found, _ = scan(thirds_chat(100, (60, 40, 20), {'Rude': 2}))
        self.assertTrue(m['trend']['hostile']['strong'])
        self.assertEqual(m['trend']['hostile']['call'], 'falling')
        self.assertTrue(find(found, r'openly hostile'))
        self.assertFalse(find(found, r'Hostility is easing'))
        # the same fall that ends calm keeps its good news
        self.assertTrue(find(scan(thirds_chat(100, (60, 40, 5), {'Rude': 2}))[1], r'Hostility is easing'))

    def test_care_growing_is_dropped_while_care_is_down_on_the_last_check(self):
        m = an(thirds_chat(100, (7, 14, 20), {'Empathetic': 2}))
        cur = measures.record(m, {}, {'total': 300, 'note': None, 'model': 'qwen2.5:7b'}, 2, '2026-06-01T12:00:00')
        before = dict(cur, gen=1, created_at='2026-05-01T12:00:00', care_n=150)            # 50 per 100 before, 41 of 300 now: down 36 points
        found, _ = measures.alerts(m, vibe_ok(300), measures.compare(cur, before), cov_full(300))
        self.assertTrue(find(found, r'Fewer messages show care'))
        self.assertFalse(find(found, r'Care is growing'))
        found, _ = measures.alerts(m, vibe_ok(300), None, cov_full(300))                   # without the comparison the good news stands
        self.assertTrue(find(found, r'Care is growing'))

    # -- since your last check ---------------------------------------------------------------------------------------------------------------------
    def compared(self, cur, before, m=None, n=200):
        m = m or an(neutral(n))
        comparison = measures.compare(cur, before)
        found, notes = measures.alerts(m, vibe_ok(m['n']), comparison, cov_full(m['n']))
        return comparison, found, notes

    def test_care_down_5_points_on_the_last_check_is_an_alert_when_strong(self):
        # n = 2000 each (so the chance test is easy): 400 -> 300 is down exactly 5.0: yes. 400 -> 320 is down 4.0: under the floor.
        for now, alert in [(300, True), (290, True), (320, False), (310, False), (330, False)]:      # 4.5 points down (310) is under the floor of 5
            with self.subTest(now=now):
                _, found, _ = self.compared(saved(2000, care_n=now), saved(2000, care_n=400, gen=0))
                self.assertEqual(bool(find(found, r'Fewer messages show care')), alert)
        # n = 50 each: 15 -> 3 is 30 to 6 per 100: down 24, se 7.54, strong (2.5 se = 18.9). 15 -> 6 is down 18, se 8.12: clear (16.2) but not strong (20.3).
        _, found, _ = self.compared(saved(50, care_n=3), saved(50, care_n=15, gen=0), n=50)
        self.assertTrue(find(found, r'Fewer messages show care'))
        comparison, found, _ = self.compared(saved(50, care_n=6), saved(50, care_n=15, gen=0), n=50)
        self.assertFalse(find(found, r'Fewer messages show care'))
        self.assertEqual(comparison['changes']['empathy']['verdict'], 'worse')
        self.assertEqual(comparison['changes']['empathy']['strong_verdict'], 'same')

    def test_care_up_5_points_on_the_last_check_is_good_news(self):
        _, found, _ = self.compared(saved(2000, care_n=500), saved(2000, care_n=400, gen=0))
        self.assertEqual([a['level'] for a in find(found, r'Care is growing')], ['good'])
        _, found, _ = self.compared(saved(2000, care_n=490), saved(2000, care_n=400, gen=0))
        self.assertFalse(find(found, r'Care is growing'))

    def test_the_overall_mood_slipped_or_improved_by_8_points_or_two_and_a_half_standard_errors(self):
        # sd 10 per message, 200 messages each: se = sqrt(100/200 + 100/200) = 1.0, so the floor of 8 decides
        for now, slipped in [(42.0, True), (42.1, False), (30.0, True), (49.9, False)]:
            with self.subTest(now=now):
                _, found, _ = self.compared(saved(200, health=now), saved(200, health=50.0, gen=0))
                self.assertEqual(bool(find(found, r'overall mood slipped')), slipped)
                self.assertFalse(find(found, r'overall mood improved'))
        for now, improved in [(58.0, True), (57.9, False), (75.0, True)]:
            with self.subTest(now=now):
                _, found, _ = self.compared(saved(200, health=now), saved(200, health=50.0, gen=0))
                self.assertEqual(bool(find(found, r'overall mood improved')), improved)
                self.assertFalse(find(found, r'overall mood slipped'))
        # sd 40, 50 messages each: se = 8.0. Down 17 is clear (2 se = 16) but not strong (2.5 se = 20); down 21 is strong.
        for drop, slipped, verdict in [(15, False, 'same'), (17, False, 'worse'), (21, True, 'worse')]:
            with self.subTest(drop=drop):
                comparison, found, _ = self.compared(saved(50, health=71.0 - drop, health_sd=40.0), saved(50, health=71.0, health_sd=40.0, gen=0), n=50)
                self.assertEqual(bool(find(found, r'overall mood slipped')), slipped)
                self.assertEqual(comparison['changes']['health']['verdict'], verdict)

    def test_nothing_is_said_about_a_change_when_the_checks_are_not_alike(self):
        """MEASURES.md: the reason two checks are not alike is said once, with the comparison (ms['since']['why'] / ms['comparison']['why']), and is not a note."""
        for now, before in [(saved(200, model='other', health=10.0, care_n=0), saved(200, health=90.0, care_n=100, gen=0)),
                            (saved(200, sensitivity='low', health=10.0, care_n=0), saved(200, health=90.0, care_n=100, gen=0)),
                            (saved(47, health=10.0, care_n=0), saved(47, health=90.0, care_n=20, gen=0)),
                            (saved(200, health=10.0, care_n=0), saved(601, health=90.0, care_n=300, gen=0))]:
            with self.subTest(now=(now['model'], now['sensitivity'], now['n'])):
                comparison, found, notes = self.compared(now, before)
                self.assertFalse(comparison['comparable'])
                self.assertTrue(comparison['why'].strip(), 'the comparison says why')
                self.assertFalse(find(found, r'slipped|improved|Fewer messages show care|Care is growing'))
                self.assertEqual([n['key'] for n in notes if n['text'] == comparison['why'] or n['key'] == 'not_alike'], [], 'and no quiet note says it again')

    def test_a_comparison_alert_says_in_each_check_when_both_checks_rated_the_same_number_of_messages(self):
        """MEASURES.md: a base line reads "Based on 72 messages in each check" when two checks of the same size are compared."""
        cases = [('mood_down', 'health', 40.0, 50.0), ('mood_up', 'health', 60.0, 50.0),                        # the health score in points, a count as a share of the messages
                 ('empathy', 'care_n', .30, .50), ('empathy_up', 'care_n', .50, .30),
                 ('conflict_down', 'hostile_n', .10, .50), ('conflict', 'hostile_n', .50, .10)]
        for n in (200, 2000):
            for key, field, now, was in cases:
                with self.subTest(alert=key, n=n):
                    value = lambda x: x if field == 'health' else round(n * x)           # noqa: E731
                    _, found, _ = self.compared(saved(n, **{field: value(now)}), saved(n, gen=0, **{field: value(was)}), n=n)
                    (alert,) = [a for a in found if a['key'] == key]
                    self.assertEqual(alert['base'], f'Based on {n:,} messages in each check.')
        _, found, _ = self.compared(saved(200, health=40.0), saved(100, health=50.0, gen=0), n=200)          # not the same size: both numbers, and no 'in each check'
        (alert,) = [a for a in found if a['key'] == 'mood_down']
        self.assertRegex(alert['base'], r'^Based on 100 and 200 messages\.?$')
        self.assertNotIn('in each check', alert['base'])

    # -- Vibe Check's serious flags -------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def serious(vibe, n=60):
        return find(scan(neutral(n), vibe=vibe)[1], r'Vibe Check flagged serious messages')

    def test_the_serious_alert_says_one_message_in_the_singular_and_several_in_the_plural(self):
        def alert(flagged=(), **tally):
            vibe = dict(vibe_ok(60, **tally), flagged=list(flagged))
            return self.serious(vibe)[0]['detail']

        threat, hate, harass = ({'type': t, 'severity': 9, 'index': i} for i, t in enumerate(('threat', 'hate_speech', 'harassment'), 3))
        one = alert([threat], threats=1)
        self.assertRegex(one, r'\bflagged 1 message as a threat\b')
        self.assertRegex(one, r'\bOne scored 7 or more out of 10\b')
        self.assertRegex(one, r'\bIt is quoted under Flagged messages\b')
        self.assertNotRegex(one, r'1 messages|as threats|\bThey\b|\bThey are\b')
        self.assertIn('A model can be wrong', one)
        two = alert([threat, dict(threat, index=9)], threats=2)
        self.assertRegex(two, r'\bflagged 2 messages as threats\b')
        self.assertRegex(two, r'\b2 scored 7 or more out of 10\b')
        self.assertRegex(two, r'\bThey are quoted under Flagged messages\b')
        self.assertNotRegex(two, r'\bIt is quoted|as a threat\b')
        self.assertRegex(alert([hate], hate_speech=1), r'\bflagged 1 message as hate speech\b')
        self.assertRegex(alert([harass], harassment=1), r'\bflagged 1 message as harassment\b')
        # several kinds: one message can carry more than one flag, so the tallies are listed and never added into a total
        mixed = alert([threat, harass], threats=1, harassment=1)
        self.assertRegex(mixed, r'\bflagged messages for threats \(1\) and harassment \(1\)\.')
        self.assertRegex(mixed, r'\b2 scored 7 or more out of 10\b')
        self.assertRegex(mixed, r'\bThey are quoted under Flagged messages\b')
        self.assertNotRegex(mixed, r'flagged 2 messages')
        self.assertRegex(alert([threat, hate, harass, dict(threat, index=11)], threats=2, hate_speech=1, harassment=1),
                         r'\bflagged messages for threats \(2\), hate speech \(1\) and harassment \(1\)\.')
        # some quoted, some only counted: it says how many are quoted and that the rest were counted
        partial = alert([threat, dict(threat, index=9)], threats=13)
        self.assertRegex(partial, r'\b2 of them are quoted under Flagged messages\. The rest were counted, not quoted\.')
        self.assertRegex(alert([threat], threats=13), r'\b1 of them is quoted under Flagged messages\. The rest were counted, not quoted\.')
        # counted but not quoted (Vibe Check kept the count and no message)
        self.assertRegex(alert(threats=1), r'\bIt was counted, not quoted\b')
        self.assertRegex(alert(threats=2), r'\bThey were counted, not quoted\b')
        self.assertNotRegex(alert(threats=1), r'scored 7')                       # no message to score
        # nothing scored 7 or more: the sentence is not there
        mild = alert([dict(harass, severity=4)], harassment=1)
        self.assertNotIn('scored 7', mild)
        self.assertRegex(mild, r'\bIt is quoted\b')

    def test_the_serious_alert_is_high_for_threats_and_hate_speech_and_medium_for_harassment_and_bullying(self):
        for kind, level in [('threats', 'high'), ('hate_speech', 'high'), ('harassment', 'medium'), ('bullying', 'medium')]:
            for count in (1, 2, 9):
                with self.subTest(kind=kind, count=count):
                    got = self.serious(vibe_ok(60, **{kind: count}))
                    self.assertEqual([a['level'] for a in got], [level])
                    self.assertRegex(got[0]['detail'], r'\b%d\b' % count)
        got = self.serious(vibe_ok(60, harassment=1, bullying=1, threats=0))
        self.assertEqual([a['level'] for a in got], ['medium'])
        self.assertEqual([a['level'] for a in self.serious(vibe_ok(60, harassment=1, hate_speech=1))], ['high'])

    def test_the_other_kinds_of_flag_do_not_raise_it(self):
        self.assertEqual(self.serious(vibe_ok(60, insults=7, profanity=5, spam=3)), [])
        vibe = vibe_ok(60)
        vibe['flagged'] = [{'type': 'insult', 'severity': 9, 'index': 1}, {'type': 'profanity', 'severity': 8, 'index': 2}, {'type': 'spam', 'severity': 7, 'index': 3},
                           {'type': 'other', 'severity': 10, 'index': 4}]
        self.assertEqual(self.serious(vibe), [])
        self.assertEqual(self.serious(vibe_ok(60)), [])

    def test_a_message_scoring_7_or_more_makes_it_high(self):
        for severity, level in [(1, 'medium'), (6, 'medium'), (7, 'high'), (8, 'high'), (10, 'high')]:
            with self.subTest(severity=severity):
                vibe = vibe_ok(60, harassment=1)
                vibe['flagged'] = [{'type': 'harassment', 'severity': severity, 'index': 5}]
                self.assertEqual([a['level'] for a in self.serious(vibe)], [level])

    def test_a_flagged_message_counts_even_when_the_tally_says_zero(self):
        # the count is the larger of Vibe Check's tally and its flagged messages of that type
        vibe = vibe_ok(60, threats=0)
        vibe['flagged'] = [{'type': 'threat', 'severity': 3, 'index': 2}]
        got = self.serious(vibe)
        self.assertEqual([a['level'] for a in got], ['high'])
        self.assertRegex(got[0]['detail'], r'\b1\b')
        self.assertRegex(got[0]['detail'], r'(?i)threat')
        vibe = {'status': 'ok', 'analyzed_n': 60, 'flagged': [{'type': 'hate_speech', 'severity': 2, 'index': 2}]}           # no tally at all
        self.assertEqual([a['level'] for a in self.serious(vibe)], ['high'])
        vibe = {'status': 'ok', 'analyzed_n': 60, 'toxicity_types': {}, 'flagged': [{'type': 'bullying', 'severity': 2, 'index': 2}, {'type': 'harassment', 'severity': 2, 'index': 3}]}
        got = self.serious(vibe)
        self.assertEqual([a['level'] for a in got], ['medium'])
        self.assertRegex(got[0]['detail'], r'harassment \(1\) and bullying \(1\)')

    def test_the_count_is_the_larger_of_the_tally_and_the_flagged_messages(self):
        vibe = vibe_ok(60, threats=13)
        vibe['flagged'] = [{'type': 'threat', 'severity': 4, 'index': 2}, {'type': 'threat', 'severity': 4, 'index': 3}]
        self.assertRegex(self.serious(vibe)[0]['detail'], r'\b13\b')
        vibe = vibe_ok(60, threats=1)
        vibe['flagged'] = [{'type': 'threat', 'severity': 4, 'index': i} for i in range(2, 31)]
        self.assertRegex(self.serious(vibe)[0]['detail'], r'\b29\b')

    def test_the_threats_alert_says_a_model_can_be_wrong_points_to_the_quoted_messages_and_to_the_safety_process(self):
        for kind, flag in (('threats', 'threat'), ('hate_speech', 'hate_speech')):
            with self.subTest(kind=kind):
                vibe = dict(vibe_ok(60, **{kind: 2}), flagged=[{'type': flag, 'severity': 9, 'index': 3}, {'type': flag, 'severity': 9, 'index': 8}])
                alert = self.serious(vibe)[0]
                text = ' '.join(str(alert[k]) for k in ('title', 'detail', 'try', 'base', 'when'))
                self.assertRegex(text, r'(?i)\b(can|could|may) be wrong\b')
                self.assertRegex(text, r'(?i)quoted messages')
                self.assertRegex(text, r"(?i)community[\u2019']s safety process")
                self.assertRegex(text, r'(?i)local emergency services')
            with self.subTest(kind=kind, quoted=False):
                # counted and not quoted: the text never points to quoted messages that are not on the page, and the safety sentence stays
                alert = self.serious(vibe_ok(60, **{kind: 2}))[0]
                text = ' '.join(str(alert[k]) for k in ('title', 'detail', 'try', 'base', 'when'))
                self.assertNotRegex(text, r'(?i)quoted messages|Read the quoted|under Flagged')
                self.assertRegex(text, r'(?i)counted, not quoted')
                self.assertRegex(text, r'(?i)did not quote them')
                self.assertRegex(text, r"(?i)community[\u2019']s safety process")
                self.assertRegex(text, r'(?i)local emergency services')

    def test_the_serious_alert_does_not_wait_for_24_messages(self):
        self.assertEqual([a['level'] for a in self.serious(vibe_ok(10, threats=1), n=10)], ['high'])

    def test_a_single_hostile_message_never_raises_an_alert(self):
        # a calm chat of weak readings with one very hostile (or agitated, or sarcastic) message in it, wherever it is: no Medium or High alert
        # (3 heated messages are needed for a heated moment, 3 hostile ones and 12 per 100 for a level alert)
        for n in (24, 48, 76, 96, 300, 1000):
            for where in (0, n // 3, n // 2, n - 1):
                for tones in ({'Rude': 3, 'Disrespectful': 3, 'Aggressive': 2, 'Agitated': 2}, {'Agitated': 3}, {'Sarcastic': 3}, {'Judgmental': 3}):
                    for sensitivity in ('low', 'medium'):
                        units = lively(chat(n, lambda i: dict(tones) if i == where else None))
                        found = scan(units, sensitivity)[1]
                        self.assertEqual([a['title'] for a in found if a['level'] in ('high', 'medium')], [], (n, where, tones, sensitivity))

    def test_two_hostile_messages_are_not_a_heated_moment_either(self):
        for n in (48, 96, 300):
            units = lively(chat(n, lambda i: {'Rude': 3, 'Agitated': 3} if i in (n // 2, n // 2 + 1) else None))
            self.assertEqual([a['title'] for a in scan(units)[1] if a['level'] in ('high', 'medium')], [], n)

    # -- the list as a whole -----------------------------------------------------------------------------------------------------------------------
    def test_alerts_are_ordered_high_then_medium_then_good_and_have_every_field(self):
        units = blend(thirds_chat(100, (12, 6, 0), {'Rude': 2}), thirds_chat(100, (7, 14, 20), {'Empathetic': 2}))
        vibe = vibe_ok(300, harassment=1)
        m = an(units)
        found, _ = measures.alerts(m, vibe, None, cov_full(300))
        self.assertEqual({a['level'] for a in found}, {'medium', 'good'})
        found, _ = measures.alerts(m, vibe_ok(300, threats=1, harassment=1), None, cov_full(300))
        order = [measures.LEVELS.index(a['level']) for a in found]
        self.assertEqual(order, sorted(order))
        self.assertEqual(found[0]['level'], 'high')
        for a in found:
            self.assertEqual(sorted(a), sorted(['level', 'key', 'title', 'detail', 'try', 'base', 'when']))
            self.assertIn(a['level'], ('high', 'medium', 'good'))
            for field in ('title', 'detail', 'base'):
                self.assertTrue(a[field].strip(), (a['key'], field))
            self.assertTrue(a['try'].strip() or a['level'] == 'good', a['key'])
            self.assertRegex(a['detail'], r'\d', 'what was seen comes with counts')
        self.assertEqual(len({a['key'] for a in found}), len(found), 'one alert of each kind')

    def test_a_calm_chat_has_no_alert_and_no_note_but_the_vibe_one_when_vibe_check_did_not_run(self):
        _, found, notes = scan(neutral(200))
        self.assertEqual((found, notes), ([], []))
        _, found, notes = scan(neutral(200), vibe=None)
        self.assertEqual((found, [n['key'] for n in notes]), ([], ['no_vibe']))


def lively(units, filler='Ignorant'):
    """The same messages, with a weak reading (a 1, which counts for nothing) on every message that had no tone: a chat of zeros is a silent chat and earns no
    badge, and a test of a badge wants the badge to be in play."""
    out = []
    for u in units:
        r = dict(u['ratings'])
        if not any(r.values()):
            r[filler] = 1
        out.append({'id': u['id'], 'timestamp': u['timestamp'], 'ratings': r})
    return out


def undated_tail(n, dated):
    """n messages (each with a weak reading, so none is silent) of which only the first `dated` carry a date."""
    units = lively(chat(n))
    for u in units[dated:]:
        u['timestamp'] = ''
    return units


def with_holes(n=96, k=3):
    """n messages of which the first k lack one of their 13 ratings: incomplete, so left out of the figures."""
    units = lively(chat(n))
    for u in units[:k]:
        del u['ratings']['Rude']
    return units


def badge_map(units, vibe='ok', cov=None, sensitivity='medium'):
    """{badge name: badge} and the note, for a chat."""
    m = an(units, sensitivity)
    vibe = vibe_ok(len(units)) if vibe == 'ok' else vibe
    earned, note = measures.badges(m, vibe, cov or cov_full(len(units)))
    return {b['name']: b for b in earned}, note


CANT_TELL = "Can't tell yet: only part of the chat was read."


# =============================================================================================================================================
class NoteTests(unittest.TestCase):
    """The quiet lines about the basis of the figures. They are not alerts."""

    def notes(self, units, **kw):
        return scan(units, **kw)[2]

    def test_too_few_messages_to_follow_the_mood(self):
        for n in (1, 10, 23):
            with self.subTest(n=n):
                notes = self.notes(lively(chat(n)))
                self.assertTrue(any(re.search(r'\b%d\b' % n, x['text']) and re.search(r'(?i)only|few', x['text']) for x in notes), notes)
        self.assertFalse([x for x in self.notes(lively(chat(24))) if re.search(r'(?i)rated only', x['text'])])

    def test_between_24_and_47_it_is_too_early_to_call_a_direction(self):
        for n in (24, 47):
            notes = self.notes(lively(chat(n)))
            self.assertTrue([x for x in notes if re.search(r'(?i)direction|too early|48', x['text'])], (n, notes))
        self.assertEqual(self.notes(lively(chat(48))), [])

    def test_no_usable_dates(self):
        notes = self.notes(lively(chat(96, dated=False)))
        self.assertTrue([x for x in notes if re.search(r'(?i)no usable dates|dates', x['text'])], notes)
        self.assertEqual(self.notes(lively(chat(96))), [], 'a dated chat needs no such note')

    def test_the_order_note_says_why_the_lines_follow_the_order_added(self):
        def lead(units):
            (got,) = [x for x in self.notes(lively(units)) if x['key'] == 'order']
            self.assertRegex(got['text'], r'so the lines follow the order you added them\. Add dates to see when things changed\.')
            self.assertRegex(got['text'], r'read rising and falling the other way round\.$')
            return got['text'].split(', so the lines')[0]
        self.assertEqual(lead(chat(96, dated=False)), 'These messages have no usable dates')
        part = [u if i < 59 else dict(u, timestamp='') for i, u in enumerate(chat(100))]               # 59 in 100 carry a date: one short of the 3 in 5 the clock needs
        self.assertEqual(lead(part), 'Only 59 of 100 messages have a usable date')
        self.assertEqual(lead(chat(100, step=0)), 'These messages fall on fewer than three different dates and times')                    # all dated, all at one moment
        self.assertEqual([x for x in self.notes(lively(chat(100))) if x['key'] == 'order'], [], 'a chat that follows the clock needs no such note')

    def test_a_review_period_that_now_leaves_out_rated_messages_is_a_quiet_note(self):
        units = lively(chat(96))
        for left, pattern in [(1, r'^The review period now leaves out 1 message that was rated\. Run the check again to apply it\.$'),
                              (36, r'^The review period now leaves out 36 messages that were rated\. Run the check again to apply it\.$')]:
            with self.subTest(left=left):
                got = [x for x in self.notes(units, cov=dict(cov_full(96), left_out=left)) if x['key'] == 'period']
                self.assertEqual([x['about'] for x in got], ['measures'])
                self.assertRegex(got[0]['text'], pattern)
        self.assertEqual([x for x in self.notes(units, cov=cov_full(96)) if x['key'] == 'period'], [])
        self.assertEqual([x for x in self.notes(units, cov=dict(cov_full(96), left_out=0)) if x['key'] == 'period'], [])

    def test_a_sample(self):
        """The note about a sample says what a sample can miss and what to choose. The basis line (basis_text) already says how big the sample is, so the note does
        not say it again: the same figures are not printed twice."""
        cov = {'sampled': True, 'total': 500, 'scope': 500, 'added_since': 0}
        units = lively(chat(96))
        notes = self.notes(units, cov=cov)
        self.assertEqual([(x['key'], x['text'], x['about']) for x in notes if re.search(r'(?i)sample', x['text'])], [('sampled', SAMPLED, 'measures')])
        self.assertNotRegex(SAMPLED, r'\d', 'no count of messages in it: the basis line has them')
        self.assertRegex(build_with(units, cov=cov)['basis_text'], r'\b96\b.*\bsample of 500\b')
        for cov_ in (cov_full(96), {'sampled': False, 'total': 500, 'scope': 500, 'added_since': 0}):
            self.assertEqual(self.keyed(units, 'sampled', cov=cov_), [], 'no sample, no note')
        for n in (1, 10, 30, 300):                                                  # the same sentence at any size, and at Low sensitivity too
            for sensitivity in ('low', 'medium'):
                self.assertEqual([x['text'] for x in self.keyed(lively(chat(n)), 'sampled', cov={'sampled': True, 'total': 9 * n, 'scope': 9 * n, 'added_since': 0},
                                                                   sensitivity=sensitivity)], [SAMPLED])

    def test_incomplete_ratings(self):
        units = lively(chat(96))
        for u in units[:3]:
            del u['ratings']['Rude']
        m = an(units)
        self.assertEqual((m['n'], m['incomplete']), (93, 3))
        self.assertEqual(m['overall']['n'], 93)
        notes = self.notes(units)
        hit = [x for x in notes if re.search(r'(?i)incomplete', x['text'])]
        self.assertTrue(hit, notes)
        self.assertRegex(hit[0]['text'], r'\b3\b')

    def test_a_check_with_no_complete_unit_has_no_measures(self):
        self.assertIsNone(measures.analyse([], 'medium', today=TODAY))
        units = chat(10)
        for u in units:
            del u['ratings']['Polite']
        self.assertIsNone(an(units))

    def test_vibe_check_not_run_or_failed_is_a_note_and_never_an_all_clear(self):
        units = lively(neutral(96))
        for vibe in (None, {}, {'status': 'failed', 'reason': 'x', 'analyzed_n': 0}, {'status': 'no_input'}, {'status': 'cancelled', 'analyzed_n': 0}):
            with self.subTest(vibe=vibe):
                m, found, notes = scan(units, vibe=vibe)
                hit = [x for x in notes if x['key'] == 'no_vibe']
                self.assertEqual(len(hit), 1)
                self.assertRegex(hit[0]['text'], r'(?i)Vibe Check')
                self.assertRegex(hit[0]['text'], r'(?i)not an all clear|not an all-clear')
                self.assertFalse(find(found, r'Vibe Check flagged'))
                badge = badge_map(units, vibe=vibe)[0]['No serious flags']
                self.assertFalse(badge['earned'], 'no Vibe Check, no "no serious flags"')
                self.assertTrue(badge['needs'])
        self.assertFalse([x for x in scan(units)[2] if x['key'] == 'no_vibe'])

    def test_notes_have_only_a_key_and_a_text_and_are_not_in_the_alert_list(self):
        m, found, notes = scan(lively(chat(20, dated=False)), vibe=None)
        self.assertTrue(len(notes) >= 2)
        for x in notes:
            self.assertEqual(sorted(x), ['about', 'key', 'text'])
            self.assertEqual(x['about'], 'alerts' if x['key'] == 'no_vibe' else 'measures', x['key'])
        self.assertFalse([a for a in found if a['key'] in {x['key'] for x in notes}])

    # -- the notes that were added, and which section prints each note ---------------------------------------------------------------------------------------
    LOW = 'Your sensitivity is Low, so banter counts as normal. The bars for Conflict, Passive-aggressive and heated moments are higher.'

    def keyed(self, units, key, **kw):
        return [x for x in self.notes(units, **kw) if x['key'] == key]

    def test_low_sensitivity_says_so_in_a_quiet_note_and_the_other_settings_do_not(self):
        """MEASURES.md: at Low sensitivity a quiet note says so, with this very sentence."""
        for n in (10, 30, 96, 300):
            units = lively(chat(n))
            with self.subTest(n=n):
                self.assertEqual([(x['text'], x['about']) for x in self.keyed(units, 'low_sensitivity', sensitivity='low')], [(self.LOW, 'measures')])
                for sensitivity in ('medium', 'high'):
                    self.assertEqual(self.keyed(units, 'low_sensitivity', sensitivity=sensitivity), [], sensitivity)
        _, found, _ = scan(lively(chat(96)), 'low')
        self.assertFalse([a for a in found if 'sensitiv' in a['key'] or 'sensitiv' in a['title'].lower()], 'it is a note and not an alert')

    def test_too_few_dated_messages_for_lines_say_so_with_both_counts(self):
        """Lines need 24 messages on the timeline: 24 or more rated messages of which fewer than 24 carry a usable date have no lines, and the note says why."""
        keys = lambda n, k: [x['key'] for x in self.notes(undated_tail(n, k))]             # noqa: E731
        text = self.keyed(undated_tail(30, 20), 'few_dated')
        self.assertEqual(len(text), 1)
        self.assertEqual(text[0]['about'], 'measures')
        self.assertRegex(text[0]['text'], r'^Only 20 of 30 messages have a date\b')
        self.assertRegex(text[0]['text'], r'no lines over time')
        self.assertRegex(text[0]['text'], r'\b24 messages with a date')
        self.assertEqual(sorted(keys(30, 20)), ['few_dated'], 'it says it once, and no other note about the lines is there as well')
        self.assertEqual(sorted(keys(30, 23)), ['few_dated'])
        self.assertEqual(sorted(keys(30, 24)), ['no_verdict', 'undated'], 'with 24 on the timeline there are lines, and the note is the one for lines')
        self.assertEqual(sorted(keys(60, 40)), ['no_verdict', 'undated'])
        self.assertEqual(sorted(keys(30, 17)), ['no_verdict', 'order'], 'under 3 in 5 dated the lines follow the order, and all 30 are on the timeline')
        self.assertEqual(sorted(keys(23, 20)), ['few'], 'under 24 rated messages it is the note for too few messages')
        self.assertEqual(keys(48, 48), [])

    def test_a_silent_chat_says_so_in_a_quiet_note_from_30_messages(self):
        # 19 in 20 messages with all 13 ratings at 0: the fewest silent messages that trip it are 29 of 30, 38 of 40, 57 of 60, 95 of 100 and 190 of 200
        for n, tripped in [(30, 29), (40, 38), (60, 57), (100, 95), (200, 190)]:
            for silent, said in [(n, True), (tripped, True), (tripped - 1, False)]:
                with self.subTest(n=n, silent=silent):
                    units = chat(n, lambda i: {'Ignorant': 1} if i < n - silent else None)
                    got = self.keyed(units, 'silent')
                    self.assertEqual([x['about'] for x in got], ['measures'] if said else [])
                    if said:                                                                 # it says almost no tone was found, and that the model may not have read the text
                        self.assertRegex(got[0]['text'], r'^Almost no tone was found in these messages\.')
                        self.assertRegex(got[0]['text'], r'(?i)\bthe model may not have read\b')
        for n in (1, 10, 29):
            self.assertEqual(self.keyed(chat(n), 'silent'), [], f'{n} messages: the note needs at least 30')
        self.assertEqual(self.keyed(chat(100, lambda i: {'Funny': 1}), 'silent'), [], 'a weak reading is a reading')

    def test_messages_added_after_the_check_are_a_quiet_note_in_the_singular_and_the_plural(self):
        units = lively(chat(96))
        for added, pattern in [(1, r'^1 message falls inside the review period but was not rated, so it is not included\. Run the check again to include it\.$'),
                               (2, r'^2 messages fall inside the review period but were not rated, so they are not included\. Run the check again to include them\.$'),
                               (1200, r'^1,200 messages fall inside the review period but were not rated, so they are not included\.')]:
            with self.subTest(added=added):
                got = self.keyed(units, 'added', cov={'sampled': False, 'total': 96, 'scope': 96 + added, 'added_since': added})
                self.assertEqual(len(got), 1)
                self.assertEqual(got[0]['about'], 'measures')
                self.assertRegex(got[0]['text'], pattern)
        self.assertEqual(self.keyed(units, 'added', cov=cov_full(96)), [])
        self.assertEqual(self.keyed(units, 'added', cov={'sampled': False, 'total': 96, 'scope': 96, 'added_since': 0}), [])

    def test_each_note_belongs_to_the_alerts_or_to_the_measures_and_never_to_both(self):
        """`about` says which section prints the note: 'alerts' for the Vibe Check note, 'measures' for every other. (The reason two checks are not alike is no note,
        and neither is a tense start: see test_the_reason_two_checks_are_not_alike_and_a_tense_start_are_no_notes.)"""
        cases = {
            'no_vibe': scan(lively(chat(96)), vibe=None)[2],
            'few': scan(lively(chat(10)))[2], 'no_verdict': scan(lively(chat(30)))[2], 'order': scan(lively(chat(96, dated=False)))[2],
            'undated': self.notes(undated_tail(100, 70)), 'incomplete': scan(with_holes())[2],
            'sampled': scan(lively(chat(96)), cov={'sampled': True, 'total': 500, 'scope': 500, 'added_since': 0})[2], 'low_sensitivity': scan(lively(chat(96)), 'low')[2],
            'silent': scan(chat(96))[2], 'added': scan(lively(chat(96)), cov={'sampled': False, 'total': 96, 'scope': 100, 'added_since': 4})[2]}
        for key, notes in cases.items():
            with self.subTest(key=key):
                mine = [x for x in notes if x['key'] == key]
                self.assertEqual(len(mine), 1, [x['key'] for x in notes])
                self.assertEqual(mine[0]['about'], 'alerts' if key == 'no_vibe' else 'measures')
                self.assertEqual(len({x['key'] for x in notes}), len(notes), 'a note is there once')
                self.assertEqual(len({x['text'] for x in notes}), len(notes), 'and no sentence is said twice')
                self.assertFalse({x['key'] for x in notes} & set(REMOVED_NOTES), 'these two notes no longer exist')

    def test_the_reason_two_checks_are_not_alike_and_a_tense_start_are_no_notes(self):
        """MEASURES.md: the notes 'not_alike' and 'opening' no longer exist. The reason two checks are not alike is only ms['since']['why'] / ms['comparison']['why'],
        printed once with the comparison; a tense start is only on the Escalation card."""
        m = an(lively(neutral(96)))
        for now, before in [(saved(96, model='a'), saved(96, model='b', gen=0)), (saved(96, sensitivity='low'), saved(96, gen=0)), (saved(40), saved(96, gen=0)),
                            (saved(96), saved(600, gen=0))]:
            with self.subTest(now=(now['model'], now['sensitivity'], now['n']), before=(before['model'], before['n'])):
                comparison = measures.compare(now, before)
                self.assertFalse(comparison['comparable'])
                for vibe in (vibe_ok(96), None):
                    found, notes = measures.alerts(m, vibe, comparison, cov_full(96))
                    self.assertEqual([x for x in notes if x['key'] in REMOVED_NOTES or x['text'] == comparison['why']], [])
                    self.assertEqual([x['key'] for x in notes], [] if vibe else ['no_vibe'])
        tense = lively(part_chat([6] + [0] * 23, 20, HEAT))                         # the first part is 30 in 100 heated: a tense start
        self.assertTrue(an(tense)['opening_heated'])
        for sensitivity in ('low', 'medium', 'high'):
            _, found, notes = scan(tense, sensitivity)
            self.assertEqual([x for x in notes if x['key'] in REMOVED_NOTES or re.search(r'(?i)tense', x['text'])], [], sensitivity)
        ctx = build_with(tense)
        self.assertEqual([where for where, text in page_strings(ctx) if re.search(r'(?i)tense', text)], ['card.escalation.note'], 'said once, on the Escalation card')


# =============================================================================================================================================
class BadgeTests(unittest.TestCase):
    """Seven badges, their rules at the cut-off and one step below."""

    NAMES = ['Courteous', 'Supportive', 'Encouraging', 'Light-hearted', 'Calm waters', 'Cools fast', 'No serious flags']

    def test_the_seven_badges_in_order_each_with_its_fields(self):
        earned, _ = measures.badges(an(neutral(96)), vibe_ok(96), cov_full(96))
        self.assertEqual([b['name'] for b in earned], self.NAMES)
        self.assertEqual(len({b['key'] for b in earned}), 7)
        for b in earned:
            self.assertEqual(sorted(b), ['earned', 'evidence', 'how', 'key', 'name', 'needs'])
            self.assertTrue(b['how'].strip())
            self.assertIsInstance(b['earned'], bool)

    def test_none_under_30_rated_messages(self):
        badges, note = badge_map(chat(29, lambda i: {'Polite': 3}))
        self.assertFalse(any(b['earned'] for b in badges.values()))
        self.assertRegex(note, r'\b30\b')
        badges, note = badge_map(chat(30, lambda i: {'Polite': 3}))
        self.assertTrue(badges['Courteous']['earned'])
        self.assertEqual(note, '')

    def test_courteous_needs_20_in_100_polite_by_a_standard_error_and_under_3_in_100_hostile(self):
        # 100 messages. Polite 25: 25 - 4.33 = 20.67 >= 20; 24: 24 - 4.27 = 19.73 < 20.
        self.assertTrue(clears(25, 100, 20) and not clears(24, 100, 20))
        for polite, hostile, earned in [(25, 0, True), (24, 0, False), (26, 0, True), (60, 2, True), (60, 3, False), (60, 4, False), (100, 0, True), (25, 2, True), (24, 2, False)]:
            with self.subTest(polite=polite, hostile=hostile):
                units = blend(hostile_chat(100, polite, 'Polite'), hostile_chat(100, hostile), lively(chat(100)))
                self.assertEqual(badge_map(units)[0]['Courteous']['earned'], earned)

    def test_supportive_encouraging_and_light_hearted_need_12_in_100_by_a_standard_error(self):
        # 100 messages: 16 -> 16 - 3.67 = 12.33 >= 12; 15 -> 11.43 < 12. 30 messages: 6 -> 20 - 7.30 = 12.70; 5 -> 9.86.
        self.assertTrue(clears(16, 100, 12) and not clears(15, 100, 12) and clears(6, 30, 12) and not clears(5, 30, 12))
        for name, tone in [('Supportive', 'Empathetic'), ('Encouraging', 'Encouraging'), ('Light-hearted', 'Funny')]:
            for n, k, earned in [(100, 16, True), (100, 15, False), (100, 30, True), (30, 6, True), (30, 5, False), (30, 0, False)]:
                with self.subTest(badge=name, n=n, k=k):
                    self.assertEqual(badge_map(lively(hostile_chat(n, k, tone)))[0][name]['earned'], earned)

    def test_every_badge_is_withheld_below_30_whatever_the_rates(self):
        for n in (1, 10, 29):
            for tone in ('Polite', 'Empathetic', 'Encouraging', 'Funny'):
                badges, note = badge_map(lively(chat(n, lambda i: {tone: 3})))
                self.assertFalse(any(b['earned'] for b in badges.values()), (n, tone))
                self.assertTrue(note)

    def test_the_evidence_is_the_figure_behind_an_earned_badge(self):
        badges, _ = badge_map(lively(hostile_chat(100, 25, 'Polite')))
        self.assertRegex(badges['Courteous']['evidence'], r'\b25\b.*\b100\b')
        for b in badges.values():
            if not b['earned']:
                self.assertEqual(b['evidence'], '', b['name'])
        self.assertRegex(badges['No serious flags']['evidence'], r'\b100\b')

    def test_calm_waters_needs_48_no_heated_moment_and_under_3_in_100_hostile(self):
        # 48 messages, no tone but weak ones: yes. 47: the timeline is too short, and it says so.
        badges, _ = badge_map(lively(chat(48)))
        self.assertTrue(badges['Calm waters']['earned'])
        badges, _ = badge_map(lively(chat(47)))
        self.assertFalse(badges['Calm waters']['earned'])
        self.assertRegex(badges['Calm waters']['needs'], r'\b48\b')
        for hostile, earned in [(0, True), (2, True), (3, False), (4, False)]:             # 100 messages: 2 in 100 yes, 3 in 100 no
            with self.subTest(hostile=hostile):
                self.assertEqual(badge_map(lively(hostile_chat(100, hostile)))[0]['Calm waters']['earned'], earned)
        # a heated moment: 4 of 8 after a calm run
        moment = lively(part_chat([0, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0, 0], 8, HEAT))
        self.assertFalse(badge_map(moment)[0]['Calm waters']['earned'])
        self.assertTrue(badge_map(lively(part_chat([0] * 12, 8, HEAT)))[0]['Calm waters']['earned'])

    def test_cools_fast_needs_a_climb_of_25_that_settles_within_three_stretches(self):
        # 24 stretches of 20; the calm run before stretch 11 is 1, 1, 1 heated (5%); stretch 11 has 6 (30%): a climb of 25. "Settled" = back within 10 points of 5%.
        def cooling(after, heated=6, base=(1, 1, 1)):
            counts = [0] * 24
            counts[8:11] = list(base)
            counts[11] = heated
            counts[12:12 + len(after)] = list(after)
            return lively(part_chat(counts, 20, HEAT))

        cases = [((1,), 6, True),                # settled at once
                 ((6, 6, 1), 6, True),           # settled in the third stretch after
                 ((6, 6, 6, 1), 6, False),       # settled in the fourth: too late
                 ((4, 4, 4, 1), 6, False),       # 20% is 15 points above 5%: not within 10
                 ((3, 6, 6, 6), 6, True),        # 15% is exactly 10 points above 5%: within 10
                 ((2, 6, 6, 6), 6, True),
                 ((0,), 5, False)]               # a climb of 20 is a Small one: not enough
        for after, heated, earned in cases:
            with self.subTest(after=after, heated=heated):
                m = an(cooling(after, heated))
                self.assertEqual(len(m['episodes']), 1, 'one counted moment')
                self.assertEqual(badge_map(cooling(after, heated))[0]['Cools fast']['earned'], earned)
        self.assertFalse(badge_map(lively(part_chat([1] * 24, 20, HEAT)))[0]['Cools fast']['earned'], 'no heated moment, no cooling')

    def test_cools_fast_needs_a_climb_of_25_not_24(self):
        # 24 stretches of 25 messages: the three before hold 1 heated each (4%). 7 heated is 28%: a climb of 24. 8 heated is 32%: a climb of 28. Both settle at once.
        for heated, climb, earned in [(7, 24, False), (8, 28, True)]:
            counts = [0] * 24
            counts[8:12] = [1, 1, 1, heated]
            counts[12] = 1
            units = lively(part_chat(counts, 25, HEAT))
            with self.subTest(heated=heated):
                m = an(units)
                self.assertAlmostEqual(m['episodes'][0]['peak'], climb, places=6)
                self.assertEqual(badge_map(units)[0]['Cools fast']['earned'], earned)

    def test_cools_fast_is_back_within_10_points_not_11(self):
        # 24 stretches of 100: the three before hold 5 heated each (5%); the moment has 30 (a climb of 25). Then a stretch at 15 heated (10 points above 5%: within)
        # or at 16 (11 points: not), followed by stretches that stay at 30.
        for back, earned in [(15, True), (16, False), (14, True), (20, False)]:
            counts = [0] * 24
            counts[8:12] = [5, 5, 5, 30]
            counts[12:16] = [back, 30, 30, 30]
            units = lively(part_chat(counts, 100, HEAT))
            with self.subTest(back=back):
                self.assertEqual(len(an(units)['episodes']), 1)
                self.assertEqual(badge_map(units)[0]['Cools fast']['earned'], earned)

    def test_an_earned_cools_fast_shows_the_figures_behind_it(self):
        """Like every other earned badge it says what it found: how hot the heated moment got and how far it fell back, within how many parts."""
        counts = [0] * 24
        counts[8:12] = [1, 1, 1, 6]
        counts[12:15] = [4, 6, 1]                                                       # 20 messages a part: the moment reached 30 in 100, and the third part after fell to 5
        units = lively(part_chat(counts, 20, HEAT))
        tile = badge_map(units)[0]['Cools fast']
        self.assertTrue(tile['earned'])
        self.assertEqual(tile['evidence'], 'Heated messages reached 30 in 100, then fell back to 5 in 100 within 3 parts of the chat.')
        self.assertEqual(badge_map(lively(part_chat([1] * 24, 20, HEAT)))[0]['Cools fast']['evidence'], '', 'nothing is said when the badge is not earned')

    def test_cools_fast_needs_stretches_after_the_moment(self):
        counts = [0] * 24
        counts[20:23] = [1, 1, 1]
        counts[23] = 6
        self.assertFalse(badge_map(lively(part_chat(counts, 20, HEAT)))[0]['Cools fast']['earned'])

    def test_no_serious_flags(self):
        units = lively(neutral(200))
        # fewer than 1 serious flag per 200 messages read: 1 flag among 200 is not fewer; 1 among 201 is; 2 among 400 is not; 2 among 401 is
        for read, flags, earned in [(200, 0, True), (200, 1, False), (201, 1, True), (400, 2, False), (401, 2, True), (200, 3, False)]:
            with self.subTest(read=read, flags=flags):
                chat_ = lively(neutral(read))
                self.assertEqual(badge_map(chat_, vibe=vibe_ok(read, harassment=flags))[0]['No serious flags']['earned'], earned)
        self.assertTrue(badge_map(units, vibe=vibe_ok(200, insults=50, profanity=20, spam=9))[0]['No serious flags']['earned'], 'only serious kinds count')
        for kind in ('threats', 'hate_speech', 'bullying'):
            self.assertFalse(badge_map(units, vibe=vibe_ok(200, **{kind: 1}))[0]['No serious flags']['earned'], kind)

    def test_no_serious_flags_needs_90_percent_read_and_no_message_scoring_7(self):
        units = lively(neutral(200))
        for read, earned in [(200, True), (181, True), (180, True), (179, False), (100, False), (29, False), (0, False)]:
            with self.subTest(read=read):
                self.assertEqual(badge_map(units, vibe=vibe_ok(read))[0]['No serious flags']['earned'], earned)
        big = lively(neutral(400))
        for severity, earned in [(6, True), (7, False), (9, False)]:
            with self.subTest(severity=severity):
                vibe = vibe_ok(400, harassment=1)
                vibe['flagged'] = [{'type': 'harassment', 'severity': severity, 'index': 3}]
                self.assertEqual(badge_map(big, vibe=vibe)[0]['No serious flags']['earned'], earned)

    def test_the_no_flags_evidence_says_what_a_serious_flag_is_and_counts_in_the_singular_and_the_plural(self):
        """MEASURES.md: a serious flag is a threat, hate speech, harassment or bullying; the evidence of the badge says so."""
        flags = r'\(threats, hate speech, harassment or bullying\)'
        badges, _ = badge_map(lively(neutral(200)), vibe=vibe_ok(200))
        self.assertTrue(badges['No serious flags']['earned'])
        self.assertRegex(badges['No serious flags']['evidence'], r'^200 messages read, with 0 serious flags ' + flags)
        badges, _ = badge_map(lively(neutral(400)), vibe=vibe_ok(400, harassment=1))                      # 1 in 400 is fewer than 1 in 200
        self.assertTrue(badges['No serious flags']['earned'])
        self.assertRegex(badges['No serious flags']['evidence'], r'^400 messages read, with 1 serious flag ' + flags)
        self.assertNotIn('flags', badges['No serious flags']['evidence'])
        badges, _ = badge_map(lively(neutral(1200)), vibe=vibe_ok(1200, bullying=2, insults=40))          # insults are not serious flags
        self.assertRegex(badges['No serious flags']['evidence'], r'^1,200 messages read, with 2 serious flags ' + flags)
        for b in badges.values():
            if not b['earned']:
                self.assertEqual(b['evidence'], '', 'only an earned badge shows its figure')

    def test_the_absence_badges_need_every_message_read(self):
        calm = lively(hostile_chat(100, 30, 'Polite'))
        for cov in ({'sampled': True, 'total': 500, 'scope': 500, 'added_since': 0}, {'sampled': False, 'total': 100, 'scope': 130, 'added_since': 30}):
            with self.subTest(cov=cov):
                full, _ = badge_map(calm)
                part, _ = badge_map(calm, cov=cov)
                for name in ('Calm waters', 'No serious flags'):
                    self.assertTrue(full[name]['earned'], name)
                    self.assertFalse(part[name]['earned'], name)
                    self.assertEqual(part[name]['needs'], CANT_TELL)
                self.assertEqual(part['Cools fast']['needs'], CANT_TELL)
                for name in ('Courteous', 'Supportive', 'Encouraging', 'Light-hearted'):
                    self.assertEqual(part[name]['earned'], full[name]['earned'], f'{name} can still be judged from a sample')
                    self.assertNotEqual(part[name]['needs'], CANT_TELL)
        # and Cools fast, which would have been earned
        counts = [0] * 24
        counts[8:12] = [1, 1, 1, 6]
        counts[12] = 1
        cooled = lively(part_chat(counts, 20, HEAT))
        self.assertTrue(badge_map(cooled)[0]['Cools fast']['earned'])
        got = badge_map(cooled, cov={'sampled': True, 'total': 900, 'scope': 900, 'added_since': 0})[0]['Cools fast']
        self.assertEqual((got['earned'], got['needs']), (False, CANT_TELL))

    def test_a_silent_chat_earns_nothing_and_the_page_says_why(self):
        # 19 in 20 messages with all 13 ratings at 0: silent * 20 >= 19 * n. The fewest silent messages that trip it: 29 of 30, 38 of 40, 57 of 60, 95 of 100, 190 of 200.
        for n, tripped in [(30, 29), (40, 38), (60, 57), (100, 95), (200, 190)]:
            for silent, guarded in [(n, True), (tripped, True), (tripped - 1, False)]:
                with self.subTest(n=n, silent=silent):
                    units = chat(n, lambda i: {'Ignorant': 1} if i < n - silent else None)
                    m = an(units)
                    self.assertEqual(m['silent'], silent)
                    badges, note = badge_map(units)
                    if guarded:
                        self.assertFalse(any(b['earned'] for b in badges.values()))
                        self.assertRegex(note, r'(?i)almost no tone')
                    else:
                        self.assertNotRegex(note, r'(?i)almost no tone')
                        self.assertTrue(badges['No serious flags']['earned'], 'a chat that is only 93 or 94 in 100 silent can still earn a badge')
                        if n >= 48:
                            self.assertTrue(badges['Calm waters']['earned'])

    def test_a_weak_reading_is_not_silence(self):
        self.assertEqual(an(chat(50, lambda i: {'Funny': 1}))['silent'], 0)
        self.assertEqual(an(chat(50, lambda i: {'Funny': 1} if i < 10 else None))['silent'], 40)

    def test_the_rule_of_each_badge_is_written_with_its_numbers(self):
        badges, _ = badge_map(lively(neutral(96)))
        self.assertRegex(badges['Courteous']['how'], r'\b20\b.*\b3\b')
        self.assertRegex(badges['Supportive']['how'], r'\b12\b')
        self.assertRegex(badges['Encouraging']['how'], r'\b12\b')
        self.assertRegex(badges['Light-hearted']['how'], r'\b12\b')
        self.assertRegex(badges['Calm waters']['how'], r'\b3\b')
        self.assertRegex(badges['No serious flags']['how'], r'\b30\b.*\b200\b.*\b7\b')

    def test_a_badge_never_cancels_an_alert(self):
        units = blend(hostile_chat(100, 25, 'Empathetic'), hostile_chat(100, 25, 'Rude'))
        ctx = build_with(units)
        self.assertTrue(find(ctx['alerts'], r'openly hostile'))
        self.assertTrue([b for b in ctx['badges'] if b['name'] == 'Supportive' and b['earned']])
        self.assertEqual(ctx['badges_earned'], sum(1 for b in ctx['badges'] if b['earned']))

    def test_no_serious_flags_is_held_back_while_a_high_priority_alert_stands_and_not_otherwise(self):
        """MEASURES.md: the No serious flags tile is held back while a High priority alert stands, or any alert about serious flags (it would read as praise beside it); the fold says so."""
        def flags(ctx):
            return {b['key']: b for b in ctx['badges']}['noflags']

        calm = lively(neutral(400))
        # the rule of the badge is met in all three builds: 400 messages read, fewer than 1 serious flag in 200 (1 in 400), none scoring 7 or more
        held = build_with(calm, vibe=vibe_ok(400, threats=1))                           # a threat is a High priority alert
        medium = build_with(calm, vibe=vibe_ok(400, harassment=1))                      # harassment is a Medium priority alert
        clean = build_with(calm)
        self.assertEqual([(c['alert_counts']['high'], c['alert_counts']['medium']) for c in (held, medium, clean)], [(1, 0), (0, 1), (0, 0)])
        self.assertEqual((flags(held)['earned'], flags(held)['evidence'], flags(held)['needs']), (False, '', HELD_BACK))
        self.assertEqual((flags(medium)['earned'], flags(medium)['evidence'], flags(medium)['needs']), (False, '', HELD_BACK), 'an alert about serious flags holds it back at any level')
        self.assertEqual((flags(clean)['earned'], flags(clean)['needs']), (True, ''))
        self.assertRegex(flags(clean)['evidence'], r'^400 messages read, with 0 serious flags')
        # the other six badges are those of the chat whatever alert stands, and the number of badges earned leaves out the held back one
        self.assertEqual([b for b in held['badges'] if b['key'] != 'noflags'], [b for b in medium['badges'] if b['key'] != 'noflags'])
        self.assertEqual(held['badges_earned'], sum(b['earned'] for b in held['badges']))
        self.assertEqual(held['badges_earned'], clean['badges_earned'] - 1)
        # any High priority alert holds it back, not only one from Vibe Check: Conflict and a heated moment read from the ratings, with Vibe Check clean
        for name, units in [('openly hostile', lively(hostile_chat(60, 20))), ('heated up fast', lively(spike(9)))]:
            with self.subTest(name):
                ctx = build_with(units)
                self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (1, 0))
                self.assertEqual((flags(ctx)['earned'], flags(ctx)['evidence'], flags(ctx)['needs']), (False, '', HELD_BACK))
        ctx = build_with(lively(hostile_chat(60, 12)))                                  # the same chat with a Medium alert (12 of 60) only
        self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium'], flags(ctx)['earned'], flags(ctx)['needs']), (0, 1, True, ''))
        # a badge that is not earned for another reason keeps that reason: only a badge that would be earned is held back
        ctx = build_with(lively(hostile_chat(60, 20)), vibe=None)
        self.assertEqual(ctx['alert_counts']['high'], 1)
        self.assertTrue(flags(ctx)['needs'] and flags(ctx)['needs'] != HELD_BACK)
        ctx = build_with(lively(hostile_chat(60, 20)), cov={'sampled': True, 'total': 500, 'scope': 500, 'added_since': 0})
        self.assertEqual((ctx['alert_counts']['high'], flags(ctx)['needs']), (1, CANT_TELL))


# =============================================================================================================================================
class CompareTests(unittest.TestCase):
    """Since your last check: when two checks are alike, how big a change counts, and which way is better."""

    def test_two_checks_are_alike_when_model_and_sensitivity_match_both_rated_48_and_the_larger_is_at_most_3_times_the_smaller(self):
        cases = [(saved(100), saved(100, gen=0), True), (saved(100, model='x'), saved(100, gen=0), False), (saved(100, sensitivity='low'), saved(100, gen=0), False),
                 (saved(100), saved(100, gen=0, sensitivity='high'), False),
                 (saved(48), saved(48, gen=0), True), (saved(47), saved(100, gen=0), False), (saved(100), saved(47, gen=0), False),
                 (saved(144), saved(48, gen=0), True), (saved(145), saved(48, gen=0), False), (saved(48), saved(144, gen=0), True), (saved(48), saved(145, gen=0), False),
                 (saved(300), saved(100, gen=0), True), (saved(301), saved(100, gen=0), False), (saved(2000), saved(700, gen=0), True), (saved(2000), saved(600, gen=0), False)]
        for now, before, alike in cases:
            with self.subTest(now=(now['n'], now['model'], now['sensitivity']), before=(before['n'], before['model'], before['sensitivity'])):
                got = measures.compare(now, before)
                self.assertEqual(got['comparable'], alike)
                self.assertEqual(bool(got['why']), not alike, 'the page says why when it does not compare')
                if not alike:
                    self.assertFalse(got.get('changes'))
                else:
                    self.assertEqual(sorted(got['changes']), ['conflict', 'empathy', 'health', 'heated', 'passive'])

    def test_no_comparison_before_a_second_check(self):
        self.assertIsNone(measures.compare(saved(100), None))
        self.assertIsNone(measures.since_table(None, saved(100)))

    def test_a_clear_change_is_better_or_worse_by_the_direction_that_is_good_for_the_measure(self):
        # 200 messages each, 10 per 100 against 50 per 100: far past the floor and the chance test either way
        low, high = 20, 100
        for field, key, up_is_better in [('hostile_n', 'conflict', False), ('care_n', 'empathy', True), ('dig_n', 'passive', False), ('heated_n', 'heated', False)]:
            for first, last in [(low, high), (high, low)]:
                with self.subTest(measure=key, first=first, last=last):
                    got = measures.compare(saved(200, **{field: last}), saved(200, gen=0, **{field: first}))['changes'][key]
                    went_up = last > first
                    want = 'better' if went_up == up_is_better else 'worse'
                    self.assertEqual((got['verdict'], got['strong_verdict'], got['clear'], got['strong']), (want, want, True, True))
        for first, last, want in [(40.0, 60.0, 'better'), (60.0, 40.0, 'worse')]:
            got = measures.compare(saved(200, health=last), saved(200, gen=0, health=first))['changes']['health']
            self.assertEqual((got['verdict'], got['strong_verdict']), (want, want))

    def test_a_rate_changes_by_at_least_5_points_and_2_standard_errors(self):
        # 1000 messages each, so the se is about 1.5 and the floor of 5 decides. From 100 (10.0 per 100): 150 is up exactly 5.0, 149 is up 4.9;
        # 50 is down exactly 5.0, 51 is down 4.9.
        for last, verdict in [(150, 'worse'), (149, 'same'), (151, 'worse'), (50, 'better'), (51, 'same'), (49, 'better'), (100, 'same')]:
            with self.subTest(last=last):
                got = measures.compare(saved(1000, hostile_n=last), saved(1000, gen=0, hostile_n=100))['changes']['conflict']
                self.assertEqual(got['verdict'], verdict)
                self.assertLess(2 * got['se'], 5)

    def test_the_rule_for_every_pair_of_counts_matches_the_spec(self):
        # clear: |delta| >= max(5, 2 se).  strong: |delta| >= max(5, 2.5 se).  se as in MEASURES.md for the direction of a rate.
        checked = 0
        for n1, n2 in [(50, 50), (48, 144), (100, 100), (333, 150)]:
            for c1 in range(0, n1 + 1, max(1, n1 // 20)):
                for c2 in range(0, n2 + 1, max(1, n2 // 20)):
                    delta = 100 * c2 / n2 - 100 * c1 / n1
                    se = se_of_change(c1, n1, c2, n2)
                    ties = (abs(abs(delta) - 5), abs(abs(delta) - 2 * se), abs(abs(delta) - 2.5 * se))
                    if min(ties) < 1e-9:
                        continue
                    got = measures.compare(saved(n2, hostile_n=c2), saved(n1, gen=0, hostile_n=c1))['changes']['conflict']
                    clear = abs(delta) >= max(5, 2 * se)
                    strong = abs(delta) >= max(5, 2.5 * se)
                    with self.subTest(case=(c1, n1, c2, n2)):
                        self.assertEqual((got['clear'], got['strong']), (clear, strong))
                        self.assertEqual(got['verdict'], ('better' if delta < 0 else 'worse') if clear else 'same')
                        self.assertEqual(got['strong_verdict'], ('better' if delta < 0 else 'worse') if strong else 'same')
                        self.assertAlmostEqual(got['se'], se, places=9)
                        self.assertAlmostEqual(got['delta'], delta, places=9)
                    checked += 1
        self.assertGreater(checked, 400)

    def test_health_changes_by_at_least_8_points_and_2_standard_errors(self):
        # se = sqrt(sd1^2/n1 + sd2^2/n2); here sd 10 and 200 messages each: 1.0, so the floor of 8 decides
        for now, verdict in [(42.0, 'worse'), (42.1, 'same'), (58.0, 'better'), (57.9, 'same'), (50.0, 'same')]:
            with self.subTest(now=now):
                got = measures.compare(saved(200, health=now), saved(200, gen=0, health=50.0))['changes']['health']
                self.assertEqual(got['verdict'], verdict)
        # sd 40, 50 messages each: se 8.0, so the floor is 16 (2 se) and "strong" is 20
        for delta, verdict, strong in [(15, 'same', 'same'), (16.5, 'better', 'same'), (19.9, 'better', 'same'), (20.1, 'better', 'better')]:
            with self.subTest(delta=delta):
                got = measures.compare(saved(50, health=50.0 + delta, health_sd=40.0), saved(50, gen=0, health=50.0, health_sd=40.0))['changes']['health']
                self.assertEqual((got['verdict'], got['strong_verdict']), (verdict, strong))
                self.assertAlmostEqual(got['se'], 8.0, places=9)

    def test_the_table_says_better_worse_or_about_the_same(self):
        cur = saved(200, hostile_n=10, care_n=60, dig_n=10, heated_n=20, health=70.0)
        before = saved(200, gen=0, hostile_n=60, care_n=10, dig_n=10, heated_n=70, health=50.0)
        table = measures.since_table(measures.compare(cur, before), cur)
        self.assertTrue(table['comparable'])
        self.assertEqual(table['why'], '')
        rows = {r['label']: r for r in table['rows']}
        self.assertEqual(list(rows), ['Health score', 'Conflict', 'Empathy', 'Passive-aggressive', 'Heated messages'])
        self.assertEqual({k: r['verdict'] for k, r in rows.items()},
                         {'Health score': 'Better', 'Conflict': 'Better', 'Empathy': 'Better', 'Passive-aggressive': 'About the same', 'Heated messages': 'Better'})
        self.assertRegex(str(rows['Conflict']['before']), r'\b60\b')
        self.assertRegex(str(rows['Conflict']['now']), r'\b10\b')
        worse = measures.since_table(measures.compare(before, cur), before)
        self.assertEqual({r['label']: r['verdict'] for r in worse['rows']},
                         {'Health score': 'Worse', 'Conflict': 'Worse', 'Empathy': 'Worse', 'Passive-aggressive': 'About the same', 'Heated messages': 'Worse'})

    def test_when_not_alike_the_table_says_why_and_calls_no_change(self):
        cur, before = saved(200, hostile_n=10, model='b'), saved(200, gen=0, hostile_n=90, model='a')
        table = measures.since_table(measures.compare(cur, before), cur)
        self.assertFalse(table['comparable'])
        self.assertRegex(table['why'], r'(?i)model')
        self.assertFalse([r for r in table['rows'] if r['verdict'] in ('Better', 'Worse', 'About the same')])
        self.assertEqual(table['why'], measures.compare(cur, before)['why'], 'the table and the comparison say the same sentence')
        rows = {r['label']: r for r in table['rows']}
        self.assertRegex(rows['Conflict']['before'], x_of_n(90, 200), 'the last check and this check are still shown')
        self.assertRegex(rows['Conflict']['now'], x_of_n(10, 200))

    def test_the_model_is_judged_by_its_fingerprint_so_two_names_the_store_cannot_keep_are_never_taken_for_one_model(self):
        """MEASURES.md: a record carries a fingerprint of the model name, and compare() uses it, so two names the store cannot keep are never taken for one model."""
        def kept(name, gen):
            return store.clean_measure_snapshot(saved(96, gen=gen, model=name))                  # what the store keeps of a check made with this model

        unkept = ('a' * 130, 'b' * 130, 'a' * 121, 'a' * 120 + 'b' * 10, 'my model <v1>', 'my model <v2>', 'ñandú-7b', 'äpfel-7b', 'model\nname', 'model\tname', 'emoji \U0001f600')
        for first in unkept:
            self.assertNotIn('model', kept(first, 1), 'the store cannot keep this name')
            self.assertEqual(kept(first, 1)['model_key'], fingerprint(first), 'but it keeps the fingerprint of it')
            for second in unkept:
                with self.subTest(first=first[:14], second=second[:14]):
                    got = measures.compare(kept(first, 2), kept(second, 1))
                    self.assertEqual(got['comparable'], first == second)
                    self.assertEqual(bool(got['why']), first != second)
                    if first != second:
                        self.assertRegex(got['why'], r'(?i)different model')
        # a name the store can keep is judged the same way
        for first, second, alike in [('qwen2.5:7b', 'qwen2.5:7b', True), ('qwen2.5:7b', 'qwen2.5:14b', False), ('qwen2.5:7b', 'Qwen2.5:7b', False), ('a' * 120, 'a' * 119, False)]:
            with self.subTest(first=first[:14], second=second[:14]):
                self.assertEqual(measures.compare(kept(first, 2), kept(second, 1))['comparable'], alike)
        # an unnamed model has no fingerprint (an empty one), and a named model never reads as unnamed
        self.assertEqual(kept('', 1)['model_key'], '')
        self.assertTrue(measures.compare(kept('', 2), kept('', 1))['comparable'])
        self.assertFalse(measures.compare(kept('', 2), kept('qwen2.5:7b', 1))['comparable'])
        self.assertFalse(measures.compare(kept('a' * 130, 2), kept('', 1))['comparable'])
        # records of an older build have no fingerprint, only a name, and are compared by name
        self.assertTrue(measures.compare(legacy(saved(96, model='qwen2.5:7b')), legacy(saved(96, model='qwen2.5:7b', gen=0)))['comparable'])
        self.assertFalse(measures.compare(legacy(saved(96, model='qwen2.5:7b')), legacy(saved(96, model='llama3.1:8b', gen=0)))['comparable'])

    def test_each_reason_two_checks_are_not_alike_is_said_in_words(self):
        """MEASURES.md: otherwise the page says why. One reason at a time (the spec does not say which of several is said first)."""
        for now, before, words in [(saved(100, model='x'), saved(100, gen=0), r'different model'), (saved(100, sensitivity='low'), saved(100, gen=0), r'different sensitivity'),
                                   (saved(47), saved(100, gen=0), r'fewer than 48'), (saved(100), saved(47, gen=0), r'fewer than 48'),
                                   (saved(301), saved(100, gen=0), r'very different numbers'), (saved(100), saved(301, gen=0), r'very different numbers')]:
            with self.subTest(now=(now['n'], now['model'], now['sensitivity']), before=before['n']):
                got = measures.compare(now, before)
                self.assertFalse(got['comparable'])
                self.assertRegex(got['why'], '(?i)' + words)
                self.assertEqual(got['changes'], {})

    def test_since_rows_say_not_known_for_a_count_the_store_had_to_drop_and_call_no_change_for_it(self):
        """MEASURES.md: the rows are written as "x of n messages" (or "Not known" when the store had to drop that count) except the health score."""
        cur = saved(200, hostile_n=10, care_n=60, dig_n=2, heated_n=20, health=70.0)
        # the last check's Conflict and Heated counts were above its own number of messages (200): the store dropped them from its record; a 0 is a count and stays
        before = store.clean_measure_snapshot(saved(200, gen=0, hostile_n=500, care_n=10, dig_n=0, heated_n=250, health=50.0))
        self.assertEqual(sorted(key for key in ('hostile_n', 'care_n', 'dig_n', 'heated_n') if key not in before), ['heated_n', 'hostile_n'])
        comparison = measures.compare(cur, before)
        self.assertTrue(comparison['comparable'])
        self.assertEqual(sorted(comparison['changes']), ['empathy', 'health', 'passive'], 'nothing is compared for a count that is not known')
        table = measures.since_table(comparison, cur)
        rows = {r['label']: r for r in table['rows']}
        self.assertEqual(list(rows), ['Health score', 'Conflict', 'Empathy', 'Passive-aggressive', 'Heated messages'], 'still five rows')
        for label in ('Conflict', 'Heated messages'):
            self.assertEqual((rows[label]['before'], rows[label]['verdict']), ('Not known', ''), label)
        self.assertRegex(rows['Conflict']['now'], x_of_n(10, 200), 'this check is known')
        self.assertRegex(rows['Heated messages']['now'], x_of_n(20, 200))
        self.assertRegex(rows['Empathy']['before'], x_of_n(10, 200))
        self.assertRegex(rows['Empathy']['now'], x_of_n(60, 200))
        self.assertEqual(rows['Empathy']['verdict'], 'Better')
        self.assertRegex(rows['Passive-aggressive']['before'], x_of_n(0, 200), 'a count of 0 is known')
        self.assertEqual(rows['Passive-aggressive']['verdict'], 'About the same')
        self.assertEqual((str(rows['Health score']['before']), str(rows['Health score']['now']), rows['Health score']['verdict']), ('50', '70', 'Better'), 'a score, not "x of n"')
        # the same when the checks are not alike: the last check's column still says Not known, and nothing is called
        other = saved(200, model='other', hostile_n=10, care_n=60, dig_n=2, heated_n=20, health=70.0)
        table = measures.since_table(measures.compare(other, before), other)
        rows = {r['label']: r for r in table['rows']}
        self.assertEqual([rows[label]['before'] for label in ('Conflict', 'Heated messages')], ['Not known', 'Not known'])
        self.assertEqual([r['verdict'] for r in table['rows']], [''] * 5)
        # numbers over a thousand carry the separator
        big = saved(1200, gen=0, hostile_n=300)
        self.assertRegex({r['label']: r for r in measures.since_table(measures.compare(saved(1200), big), saved(1200))['rows']}['Conflict']['before'], x_of_n(300, 1200))

    def test_a_damaged_count_is_clamped_to_the_range_of_its_own_checks_messages_before_the_checks_are_compared(self):
        """MEASURES.md: a damaged count (below 0 or above the number of messages) is clamped to that range before the two checks are compared. Before this it
        raised 'math domain error' once a count passed its total by 2 or more."""
        fields = {'hostile_n': 'conflict', 'care_n': 'empathy', 'dig_n': 'passive', 'heated_n': 'heated'}
        for field, key in fields.items():
            for damaged, clamped in [(101, 100), (102, 100), (250, 100), (10 ** 9, 100), (-1, 0), (-5, 0), (-10 ** 9, 0)]:
                for side in ('now', 'before'):
                    def pair(value):
                        if side == 'now':
                            return saved(100, **{field: value}), saved(100, gen=0, **{field: 30})
                        return saved(100, **{field: 30}), saved(100, gen=0, **{field: value})
                    with self.subTest(field=field, damaged=damaged, side=side):
                        got, want = measures.compare(*pair(damaged)), measures.compare(*pair(clamped))
                        self.assertTrue(got['comparable'])
                        self.assertEqual(got['changes'], want['changes'])
                        self.assertTrue(all(math.isfinite(v) for c in got['changes'].values() for v in (c['first'], c['last'], c['delta'], c['se'])))
        # each count is clamped to the number of messages of its own record
        got = measures.compare(saved(200, hostile_n=250), saved(100, hostile_n=250, gen=0))['changes']['conflict']
        self.assertEqual((got['first'], got['last'], got['verdict']), (100.0, 100.0, 'same'))
        got = measures.compare(saved(100, hostile_n=-9), saved(200, hostile_n=-9, gen=0))['changes']['conflict']
        self.assertEqual((got['first'], got['last'], got['verdict']), (0.0, 0.0, 'same'))


# =============================================================================================================================================
class TrailAndRecordTests(unittest.TestCase):
    """The saved record of a check and the trail of them."""

    def test_the_trail_is_the_earlier_checks_oldest_first_and_then_this_one(self):
        r1, r2 = saved(100, gen=1, created_at='2026-05-01T10:00:00'), saved(100, gen=2, created_at='2026-05-02T10:00:00')
        live = saved(100, gen=3, created_at='2026-05-03T10:00:00')
        self.assertEqual(measures.trail_of([], live), [live])
        self.assertEqual(measures.trail_of([r1], live), [r1, live])
        self.assertEqual(measures.trail_of([r1, r2], live), [r1, r2, live])

    def test_this_check_replaces_its_own_saved_record(self):
        r1, r2 = saved(100, gen=1, created_at='2026-05-01T10:00:00', hostile_n=1), saved(100, gen=2, created_at='2026-05-02T10:00:00', hostile_n=2)
        live = saved(100, gen=2, created_at='2026-05-03T10:00:00', hostile_n=9)
        self.assertEqual(measures.trail_of([r1, r2], live), [r1, live])

    def test_each_earlier_check_appears_once_the_newest_record_of_it(self):
        old = saved(100, gen=1, created_at='2026-05-01T10:00:00', hostile_n=1)
        newer = saved(100, gen=1, created_at='2026-05-01T11:00:00', hostile_n=2)
        live = saved(100, gen=2, created_at='2026-05-03T10:00:00')
        self.assertEqual(measures.trail_of([old, newer], live), [newer, live])

    def test_trail_of_keeps_one_record_per_check_whatever_the_number_of_records_and_whatever_stands_between(self):
        """MEASURES.md: two records of one check count once (the newest). The check keeps its place in the trail."""
        a1 = saved(100, gen=1, created_at='2026-05-01T10:00:00', hostile_n=1)
        a2 = saved(100, gen=1, created_at='2026-05-01T11:00:00', hostile_n=2)
        a3 = saved(100, gen=1, created_at='2026-05-01T12:00:00', hostile_n=3)
        b = saved(100, gen=2, created_at='2026-05-02T10:00:00', hostile_n=4)
        c1 = saved(100, gen=3, created_at='2026-05-03T10:00:00', hostile_n=5)
        c2 = saved(100, gen=3, created_at='2026-05-03T11:00:00', hostile_n=6)
        live = saved(100, gen=4, created_at='2026-05-04T10:00:00', hostile_n=7)
        self.assertEqual(measures.trail_of([a1, a2, a3], live), [a3, live])
        self.assertEqual(measures.trail_of([a1, a2, b, c1, c2], live), [a2, b, c2, live])
        self.assertEqual(measures.trail_of([a1, b, a2, c1, a3, c2], live), [a3, b, c2, live], 'records of one check that are not next to each other are still one check')
        history = [a1, a2, b]
        measures.trail_of(history, live)
        self.assertEqual(history, [a1, a2, b], 'the history it was given is left as it was')
        self.assertEqual(len({r['gen'] for r in measures.trail_of([a1, a2, a3, b, c1, c2], live)}), 4)

    def test_the_live_check_stands_for_every_record_of_its_own_check(self):
        old = saved(100, gen=2, created_at='2026-05-01T10:00:00', hostile_n=1)
        again = saved(100, gen=2, created_at='2026-05-01T11:00:00', hostile_n=2)
        live = saved(100, gen=2, created_at='2026-05-03T10:00:00', hostile_n=9)
        earlier = saved(100, gen=1, created_at='2026-04-01T10:00:00')
        self.assertEqual(measures.trail_of([earlier, old, again], live), [earlier, live])
        self.assertEqual(measures.trail_of([old, again], live), [live])

    def test_the_record_holds_the_numbers_and_the_names_of_the_settings_and_nothing_else(self):
        units = blend(hostile_chat(96, 10, 'Rude'), hostile_chat(96, 20, 'Empathetic'), hostile_chat(96, 8, 'Sarcastic'), lively(chat(96)))
        m = an(units, 'low')
        a = {'model_used': 'qwen2.5:7b', 'period_start': '2026-03-01', 'period_end': '2026-03-31T12:00', 'person_name': 'ZQNAME', 'text': 'ZQTEXT', 'members': 240}
        rec = measures.record(m, a, {'total': 500, 'note': 'An even sample of 500', 'model': 'ignored'}, 7, '2026-06-01T09:30:00')
        o = m['overall']
        self.assertEqual(rec['created_at'], '2026-06-01T09:30:00')
        self.assertEqual((rec['gen'], rec['n'], rec['total'], rec['sampled']), (7, 96, 500, 1))
        self.assertEqual((rec['basis'], rec['sensitivity'], rec['model']), ('time', 'low', 'qwen2.5:7b'))
        self.assertEqual(rec['model_key'], fingerprint('qwen2.5:7b'), 'and a fingerprint of the model name')
        self.assertEqual((rec['period_start'], rec['period_end']), ('2026-03-01', '2026-03-31T12:00'))
        self.assertAlmostEqual(rec['health'], m['health'], places=3)
        self.assertGreater(rec['health_sd'], 0)
        self.assertEqual((rec['hostile_n'], rec['care_n'], rec['dig_n'], rec['heated_n']), (o['hostile'], o['care'], o['dig'], o['heated']))
        self.assertEqual((rec['episodes'], rec['peak']), (len(m['episodes']), max([e['peak'] for e in m['episodes']] or [0.0])))
        self.assertEqual(rec['stretches'], len(m['stretches']))
        self.assertNotIn('ZQNAME', repr(rec))
        self.assertNotIn('ZQTEXT', repr(rec))
        import store
        self.assertEqual(set(rec) - set(store.MEASURE_FIELDS), set(), 'only fields the store allows')
        self.assertEqual(set(store.MEASURE_FIELDS) - set(rec), set(), 'and every field the store allows is one the record fills (the fingerprint of the model name included)')
        self.assertEqual(store.clean_measure_snapshot(rec), rec, 'the store keeps it exactly as it is')

    def test_the_record_keeps_the_spread_of_the_health_score(self):
        # half the messages Polite 3 (+3), half Rude 3 (-3): the spread of the per-message score is 3 x 50 / 4.5 = 33.3 points (a little more with the n-1 estimate)
        rec = measures.record(an(chat(96, lambda i: {'Polite': 3} if i % 2 else {'Rude': 3})))
        self.assertAlmostEqual(rec['health'], 50.0, places=3)
        self.assertAlmostEqual(rec['health_sd'], 100 / 3, delta=0.5)
        self.assertEqual(measures.record(an(chat(96, lambda i: {'Polite': 3})))['health_sd'], 0.0)
        self.assertEqual(measures.record(an(chat(96)))['health_sd'], 0.0)

    def test_a_record_of_a_whole_check_is_not_a_sample_and_takes_the_model_from_the_ratings_when_the_row_has_none(self):
        m = an(neutral(96))
        rec = measures.record(m, None, {'total': None, 'note': None, 'model': 'tiny-model'}, None, '2026-06-01T09:30:00')
        self.assertEqual((rec['sampled'], rec['total'], rec['model'], rec.get('gen')), (0, 96, 'tiny-model', None))
        rec = measures.record(m)
        self.assertEqual((rec['model'], rec['period_start'], rec['sampled']), ('', '', 0))
        self.assertRegex(rec['created_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$')

    def test_the_record_holds_a_fingerprint_of_the_model_name_and_the_store_keeps_it(self):
        """MEASURES.md: the record holds a fingerprint of the model name (so two names the store cannot keep are never taken for one model)."""
        m = an(neutral(96))
        rec = measures.record(m, {'model_used': 'qwen2.5:7b'})
        self.assertEqual(rec['model_key'], fingerprint('qwen2.5:7b'))
        self.assertRegex(rec['model_key'], r'^[0-9a-f]{12}$')
        self.assertNotIn('qwen', rec['model_key'])
        self.assertEqual(measures.record(m, {'model_used': 'qwen2.5:7b'})['model_key'], rec['model_key'], 'the same name always has the same fingerprint')
        names = ['qwen2.5:7b', 'qwen2.5:7B', 'llama3.1:8b', 'a' * 130, 'b' * 130, 'a' * 131, 'my model <v1>', 'my model <v2>', 'ñ', 'ö']
        keys = [measures.record(m, {'model_used': name})['model_key'] for name in names]
        self.assertEqual(len(set(keys)), len(names), 'a different name, a different fingerprint, whether or not the store can keep the name')
        for name, key in zip(names, keys):
            kept = store.clean_measure_snapshot(measures.record(m, {'model_used': name}))
            self.assertEqual(kept['model_key'], key, 'the store keeps the fingerprint')
            self.assertEqual(kept.get('model'), name if store.MEASURE_FIELDS['model'].fullmatch(name) else None, 'and the name only when it can keep it')
        # no name, no fingerprint; the row's name comes first, then the name the ratings were made with
        self.assertEqual(measures.record(m)['model_key'], '')
        self.assertEqual(measures.record(m, {'model_used': ''}, {'model': ''})['model_key'], '')
        self.assertEqual(measures.record(m, None, {'model': 'tiny-model'})['model_key'], fingerprint('tiny-model'))
        self.assertEqual(measures.record(m, {'model_used': 'qwen2.5:7b'}, {'model': 'tiny-model'})['model_key'], fingerprint('qwen2.5:7b'))

    def test_the_record_follows_the_order_basis_and_the_stretch_count(self):
        rec = measures.record(an(chat(100, dated=False)))
        self.assertEqual((rec['basis'], rec['stretches']), ('order', 12))
        self.assertEqual(measures.record(an(chat(10)))['stretches'], 0)

    def test_records_of_the_same_chat_have_the_same_signature_and_of_other_chats_not(self):
        a = measures.record(an(neutral(96)))
        b = measures.record(an(neutral(96)))
        c = measures.record(an(chat(96, lambda i: {'Rude': 2} if i % 3 == 0 else None)))
        self.assertEqual(a['sig'], b['sig'])
        self.assertNotEqual(a['sig'], c['sig'])
        self.assertRegex(a['sig'], r'^\d+(?::\d+){0,6}$')


# =============================================================================================================================================
class BuildTests(unittest.TestCase):
    """measures.build: the pieces together, on the real function with the store's three reads replaced."""

    def test_nothing_to_build_without_a_complete_unit(self):
        self.assertIsNone(build_with([]))

    def test_the_context_has_every_block(self):
        ctx = build_with(lively(neutral(96)))
        self.assertEqual(sorted(ctx), sorted(['m', 'cards', 'health', 'alerts', 'alert_counts', 'notes', 'badges', 'badges_earned', 'badge_note', 'trail', 'since', 'comparison',
                                              'record', 'basis_text', 'chart_key', 'caveats', 'texts', 'all_clear', 'members']))
        self.assertIsInstance(ctx['chart_key'], str)
        self.assertEqual(len(ctx['cards']), 4)
        self.assertEqual(len(ctx['badges']), 7)
        self.assertEqual(sorted(ctx['caveats']), ['alerts', 'badges', 'empty', 'measures', 'since'])
        self.assertEqual(sorted(ctx['texts']), ['first_check', 'no_badges'])
        self.assertIsInstance(ctx['all_clear'], bool)
        self.assertEqual(ctx['trail'], [ctx['record']])
        self.assertIsNone(ctx['since'])
        self.assertIsNone(ctx['comparison'])

    def test_the_counts_add_up(self):
        ctx = build_with(thirds_chat(100, (12, 6, 0), {'Rude': 2}), vibe=vibe_ok(300, threats=1))
        self.assertEqual(ctx['alert_counts'], {level: sum(1 for a in ctx['alerts'] if a['level'] == level) for level in ('high', 'medium', 'good')})
        self.assertGreater(ctx['alert_counts']['high'] + ctx['alert_counts']['good'], 0)
        self.assertEqual(ctx['badges_earned'], sum(b['earned'] for b in ctx['badges']))

    def test_the_caveats_say_what_the_spec_says_they_say(self):
        # MEASURES.md: "Everything here describes wording in the messages. It says nothing about any person or team. A language model made the ratings, trained people have
        # not checked them yet, and every cut-off below is our own starting choice, not a validated norm. The page, the PDF and the CSV all say so."
        c = build_with(lively(neutral(96)))['caveats']
        self.assertEqual(sorted(c), ['alerts', 'badges', 'empty', 'measures', 'since'])
        for key, text in c.items():
            self.assertTrue(text.strip() and text.rstrip().endswith('.'), key)
        everything = ' '.join(c.values())
        self.assertRegex(everything, r'(?i)language model')
        self.assertRegex(everything, r'(?i)people have not checked')
        self.assertRegex(c['measures'], r'(?i)starting cut-?offs?.{0,25}not (a validated )?norms?')
        self.assertRegex(everything, r'(?i)\bwording\b')
        self.assertRegex(c['alerts'], r'(?i)\bnever a person or a team\b|\bnot .{0,20}(person|team)')
        self.assertRegex(c['empty'], r'(?i)does not mean|not an all.clear')
        self.assertRegex(c['badges'], r'(?i)not (awards|a ranking)')
        self.assertRegex(c['badges'], r'(?i)team')
        self.assertRegex(c['badges'], r'(?i)never cancels? an alert')

    def test_the_since_caveat_says_when_a_change_counts_and_that_it_describes_wording(self):
        # MEASURES.md: "A change counts when it is at least max(5 points, 2 standard errors)", "when the two checks are alike enough to compare": the caveat says it in plain words
        c = build_with(lively(neutral(96)))['caveats']
        self.assertRegex(c['since'], r'(?i)two checks were made alike')
        self.assertRegex(c['since'], r'(?i)bigger than chance')
        self.assertRegex(c['since'], r'(?i)wording, not people')
        self.assertTrue(c['since'].rstrip().endswith('.'))
        for other in (thirds_chat(100, (12, 6, 0), {'Rude': 2}), lively(chat(12)), hostile_chat(60, 20)):
            self.assertEqual(build_with(other)['caveats'], c, 'the five caveats are fixed lines: they do not depend on the chat')

    def test_the_two_fixed_sentences_are_the_ones_the_spec_quotes(self):
        want = {'first_check': 'This is your first check. Run another after your next batch of messages and this panel can show what moved, if both checks used the same settings.',
                'no_badges': 'No badges this time. Each one is earned only when the messages clearly show it.'}
        self.assertEqual(build_with(lively(neutral(96)))['texts'], want)
        for other in (thirds_chat(100, (12, 6, 0), {'Rude': 2}), lively(chat(12)), chat(96)):
            self.assertEqual(build_with(other)['texts'], want, 'the same two sentences for every check')

    def test_the_previous_check_is_the_newest_earlier_record_and_this_check_replaces_its_own_record(self):
        units = lively(neutral(96))
        older = saved(96, gen=1, created_at='2026-04-01T10:00:00', hostile_n=30)
        newer = saved(96, gen=2, created_at='2026-05-01T10:00:00', hostile_n=0)
        mine = saved(96, gen=3, created_at='2026-05-02T10:00:00', hostile_n=77)
        ctx = build_with(units, history=[older, newer, mine], gen=3)
        self.assertEqual([r['gen'] for r in ctx['trail']], [1, 2, 3])
        self.assertEqual(ctx['trail'][-1], ctx['record'])
        self.assertNotEqual(ctx['trail'][-1]['hostile_n'], 77, 'the saved record of this very check is not used')
        self.assertTrue(ctx['comparison']['comparable'])
        self.assertEqual(ctx['comparison']['previous']['gen'], 2)
        self.assertTrue(ctx['since']['comparable'])

    def test_records_without_a_generation_are_not_checks_of_their_own(self):
        ctx = build_with(lively(neutral(96)), history=[saved(96, gen=None)], gen=1)
        self.assertEqual(len(ctx['trail']), 1)

    def test_the_record_of_this_check_follows_the_same_rules_as_a_saved_one_so_two_checks_always_compare_like_with_like(self):
        """The record of the check being shown is cleaned like a record from the store. A model name too long to be kept is gone from both (the fingerprint of it is
        kept), so two checks of the same long-named model read as alike and not as 'a different model', and two long names that differ are not taken for one."""
        long_name = 'm' * 130
        kept = store.clean_measure_snapshot(saved(96, gen=1, model=long_name))                       # what the store keeps of a check made with that model
        self.assertNotIn('model', kept)
        self.assertEqual(kept['model_key'], fingerprint(long_name))
        ctx = build_with(lively(neutral(96)), {'model_used': long_name}, history=[kept], gen=2)
        self.assertNotIn('model', ctx['record'])
        self.assertEqual(ctx['record']['model_key'], fingerprint(long_name))
        self.assertTrue(ctx['comparison']['comparable'], ctx['comparison']['why'])
        self.assertTrue(ctx['since']['comparable'])
        again = build_with(lively(neutral(96)), {'model_used': 'm' * 120}, history=[dict(kept, model='m' * 120, model_key=fingerprint('m' * 120))], gen=2)       # 120 are kept
        self.assertEqual(again['record']['model'], 'm' * 120)
        self.assertTrue(again['comparison']['comparable'])
        other = build_with(lively(neutral(96)), {'model_used': 'qwen2.5:7b'}, history=[dict(kept, model='tiny:1b', model_key=fingerprint('tiny:1b'))], gen=2)
        self.assertFalse(other['comparison']['comparable'])
        self.assertRegex(other['comparison']['why'], r'(?i)different model')
        for second in ('n' * 130, 'm' * 131, 'my model <v2>'):                                        # another name that the store cannot keep either: not one model
            different = build_with(lively(neutral(96)), {'model_used': second}, history=[kept], gen=2)
            self.assertNotIn('model', different['record'], second)
            self.assertFalse(different['comparison']['comparable'], second)
            self.assertRegex(different['comparison']['why'], r'(?i)different model')
            self.assertFalse(different['since']['comparable'])

    def test_the_basis_says_how_many_were_rated_and_whether_they_were_a_sample(self):
        units = lively(neutral(96))
        ctx = build_with(units)
        self.assertRegex(ctx['basis_text'], r'\b96\b')
        self.assertNotRegex(ctx['basis_text'], r'(?i)sample')
        ctx = build_with(units, cov={'sampled': True, 'total': 500, 'scope': 500, 'added_since': 0})
        self.assertRegex(ctx['basis_text'], r'\b96\b')
        self.assertRegex(ctx['basis_text'], r'(?i)sample')
        self.assertRegex(ctx['basis_text'], r'\b500\b')
        self.assertEqual((ctx['record']['total'], ctx['record']['sampled']), (500, 1))

    def test_the_basis_line_for_one_message_and_for_several(self):
        """The basis line says how many messages were rated ('1 message that was rated', not '1 messages that were rated'), which part of them, and how many Vibe Check read."""
        one = an([unit(0, Polite=2)])
        singular = r'^Built from the 1 message that was rated\b'
        for text in (measures.basis_text(one, cov_full(1)), measures.basis_text(one, cov_full(1), vibe_ok(1)), build_with([unit(0, Polite=2)])['basis_text']):
            self.assertRegex(text, singular)
            self.assertNotRegex(text, r'(?i)\b1 messages\b|\bwere rated\b|\bsample\b')
        self.assertRegex(measures.basis_text(one, cov_full(1), vibe_ok(1)), r'Vibe Check read 1\b')
        self.assertRegex(measures.basis_text(an(lively(chat(2))), cov_full(2)), r'^Built from the 2 messages that were rated\b')
        many = an(lively(neutral(96)))
        self.assertRegex(measures.basis_text(many, cov_full(96)), r'^Built from the 96 messages that were rated\b')
        self.assertNotRegex(measures.basis_text(many, cov_full(96)), r'(?i)sample|Vibe Check', 'a whole check is not a sample, and Vibe Check is named only when it ran')
        self.assertRegex(measures.basis_text(many, cov_full(96), vibe_ok(90)), r'Vibe Check read 90\b')
        sample = {'sampled': True, 'total': 1500, 'scope': 1500, 'added_since': 0}
        text = measures.basis_text(many, sample, vibe_ok(96))
        self.assertRegex(text, r'^Built from the 96 messages that were rated \(an even sample of 1,500\)')
        self.assertRegex(text, r'Vibe Check read 96\b')
        self.assertRegex(measures.basis_text(one, dict(sample, total=1)), r'^Built from the 1 message that was rated \(an even sample of 1\)')
        for vibe in (None, {}, {'status': 'failed', 'analyzed_n': 0}, {'status': 'cancelled', 'analyzed_n': 0}):             # a Vibe Check that did not finish is not mentioned
            self.assertNotIn('Vibe Check', measures.basis_text(one, cov_full(1), vibe))

    def test_the_chart_key_is_the_one_sentence_under_the_four_small_charts_for_the_page_and_the_pdf(self):
        """MEASURES.md: ms['chart_key'] is THE sentence under the four small charts for the page and the PDF: both print that string and never their own wording."""
        ctx = build_with(lively(neutral(96)))
        self.assertEqual(ctx['chart_key'], CHART_KEY)
        self.assertEqual(ctx['chart_key'], measures.chart_key(ctx['m']))
        self.assertEqual(len(sentences(ctx['chart_key'])), 3)
        for units in (thirds_chat(100, (12, 6, 0), {'Rude': 2}), spike(9), lively(chat(30)), lively(hostile_chat(96, 20)), chat(96), lively(chat(10))):
            self.assertEqual(build_with(units)['chart_key'], CHART_KEY, 'it does not depend on the numbers of the chat')
        for row in ({'incognito': 1}, {'vibe_sensitivity': 'low'}, {'vibe_sensitivity': 'high'}, {'model_used': ''}):
            self.assertEqual(build_with(lively(neutral(96)), row)['chart_key'], CHART_KEY, row)
        for cov in ({'sampled': True, 'total': 500, 'scope': 500, 'added_since': 0}, {'sampled': False, 'total': 96, 'scope': 100, 'added_since': 4}):
            self.assertEqual(build_with(lively(neutral(96)), cov=cov)['chart_key'], CHART_KEY)

    def test_the_chart_key_adds_one_sentence_when_the_lines_follow_the_order_the_messages_were_added(self):
        """MEASURES.md: ... plus, when relevant, the order-basis sentence. The labels under each chart are 'rated messages 25 to 36' then, and the key says so."""
        two_moments = [unit(i, '2026-03-05T18:00:00' if i < 24 else '2026-03-05T19:00:00', Ignorant=1) for i in range(48)]            # fewer than three moments: not a clock
        for name, units in [('no dates', lively(chat(96, dated=False))), ('59 of 100 dated', undated_tail(100, 59)), ('two moments', two_moments)]:
            with self.subTest(name):
                ctx = build_with(units)
                self.assertEqual(ctx['m']['basis'], 'order')
                key = ctx['chart_key']
                self.assertTrue(key.startswith(CHART_KEY + ' '), key)
                added = key[len(CHART_KEY):].strip()
                self.assertEqual(len(sentences(added)), 1, 'one sentence is added')
                self.assertRegex(added, r'(?i)rated message')
                self.assertRegex(added, r'(?i)\border\b')
                self.assertNotIn('UTC', key)
                self.assertTrue(all('rated messages' in s['label'] for s in ctx['m']['stretches']), 'and the labels are numbers of rated messages')
        self.assertEqual(build_with(undated_tail(100, 60))['chart_key'], CHART_KEY, '60 of 100 dated is enough for the clock')

    def test_the_chart_key_says_times_are_utc_only_when_every_time_of_day_carried_an_offset(self):
        every = [unit(i, stamp(i) + ('Z' if i % 2 else '+00:00'), Ignorant=1) for i in range(48)]
        ctx = build_with(every)
        self.assertEqual((ctx['m']['basis'], ctx['m']['utc']), ('time', True))
        self.assertEqual(ctx['chart_key'], CHART_KEY + ' Times are UTC.')
        self.assertTrue(all('UTC' in s['label'] for s in ctx['m']['stretches']), 'the labels of the parts say it too')
        mixed = build_with([unit(i, stamp(i) + ('Z' if i else ''), Ignorant=1) for i in range(48)])             # the first has no offset
        self.assertEqual((mixed['m']['utc'], mixed['chart_key']), (False, CHART_KEY))
        self.assertEqual(build_with(lively(chat(48)))['chart_key'], CHART_KEY, 'times without an offset are not called UTC')
        days = [unit(i, (T0.date() + datetime.timedelta(days=i // 16)).isoformat(), Ignorant=1) for i in range(48)]
        self.assertEqual(build_with(days)['chart_key'], CHART_KEY, 'dates without a time of day have nothing to call UTC')
        undated = build_with([unit(i, '', Ignorant=1) for i in range(48)])
        self.assertNotIn('UTC', undated['chart_key'])
        # whatever the chat, when the label of a part says UTC the key says it as well, and no key holds a clock time
        for inc in (0, 1):
            for units in (every, lively(chat(48)), days):
                ctx = build_with(units, {'incognito': inc})
                self.assertNotRegex(ctx['chart_key'], CLOCK)
                if any('UTC' in s['label'] for s in ctx['m']['stretches']):
                    self.assertTrue(ctx['chart_key'].endswith(' Times are UTC.'))

    def test_the_reason_two_checks_are_not_alike_is_said_once_with_the_comparison_and_is_no_note(self):
        """MEASURES.md: the reason two checks are not alike is only ms['since']['why'] / ms['comparison']['why'], printed once with the comparison."""
        units = lively(neutral(96))
        for name, previous in [('model', saved(96, gen=1, model='tiny:1b')), ('sensitivity', saved(96, gen=1, sensitivity='low')), ('too few', saved(40, gen=1)),
                               ('very different sizes', saved(400, gen=1))]:
            with self.subTest(name):
                ctx = build_with(units, history=[previous], gen=2)
                why = ctx['comparison']['why']
                self.assertTrue(why)
                self.assertEqual((ctx['comparison']['comparable'], ctx['since']['comparable'], ctx['since']['why']), (False, False, why))
                self.assertEqual(sorted(where for where, text in page_strings(ctx) if text == why), ['comparison.why', 'since.why'], 'only with the comparison')
                self.assertEqual([x['key'] for x in ctx['notes']], [])
                self.assertEqual([a for a in ctx['alerts'] if a['key'] in ('mood_down', 'mood_up', 'empathy', 'empathy_up', 'conflict', 'conflict_down')], [])
                self.assertEqual([r['verdict'] for r in ctx['since']['rows']], [''] * 5, 'no change is called')
                self.assertTrue(all(r['before'] and r['now'] for r in ctx['since']['rows']), 'but the last check and this check are both shown')
        ctx = build_with(units, history=[saved(96, gen=1)], gen=2)                          # alike: no reason to give
        self.assertEqual((ctx['comparison']['comparable'], ctx['comparison']['why'], ctx['since']['why']), (True, '', ''))

    def test_the_sensitivity_and_incognito_of_the_analysis_row_are_used(self):
        units = hostile_chat(100, 14)
        self.assertEqual(build_with(units, {'vibe_sensitivity': 'low'})['m']['sensitivity'], 'low')
        self.assertEqual(build_with(units, {'vibe_sensitivity': None})['m']['sensitivity'], 'medium')
        self.assertTrue(build_with(chat(48), {'incognito': 1})['m']['incognito'])
        self.assertFalse(build_with(chat(48), {'incognito': 0})['m']['incognito'])


# =============================================================================================================================================
class AllClearTests(unittest.TestCase):
    """MEASURES.md: ms['all_clear'] is true only when Vibe Check ran and read at least 30 messages and nearly all of the check, its newest run finished, the check holds
    at least 24 rated messages, no High or Medium alert stands, no figure is marked Worth a look or Look at this first, and the model found some tone. Only then may a
    page say 'Nothing needs urgent attention in these N messages.'"""

    def test_a_clean_check_that_vibe_check_read_is_an_all_clear(self):
        for n in (30, 48, 96, 300):
            with self.subTest(n=n):
                ctx = build_with(lively(neutral(n)))
                self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (0, 0))
                self.assertTrue(ctx['all_clear'])

    def test_a_check_with_fewer_than_30_messages_read_is_never_an_all_clear(self):
        for n in (1, 10, 23, 24, 29):
            with self.subTest(n=n):
                ctx = build_with(lively(neutral(n)))
                self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (0, 0))
                self.assertFalse(ctx['all_clear'], 'too few messages to say nothing needs attention')

    def test_vibe_check_must_have_read_nearly_all_of_the_check_and_finished_its_newest_run(self):
        units = lively(neutral(96))
        self.assertTrue(build_with(units, vibe=vibe_ok(87), cov=cov_full(96))['all_clear'], '87 of 96 is 90 in 100 or more')
        self.assertFalse(build_with(units, vibe=vibe_ok(86), cov=cov_full(96))['all_clear'], '86 of 96 is under 90 in 100')
        self.assertFalse(build_with(units, vibe=vibe_ok(40), cov=cov_full(400))['all_clear'], 'a Vibe Check that read a tenth of the check cannot clear it')
        self.assertFalse(build_with(units, vibe=vibe_ok(96), cov=dict(cov_full(96), vibe_stale=True))['all_clear'], 'its newest run did not finish')
        self.assertTrue(build_with(units, vibe=vibe_ok(96), cov=dict(cov_full(96), vibe_stale=False))['all_clear'])

    def test_a_figure_marked_worth_a_look_or_look_at_this_first_stops_it_even_with_no_alert(self):
        # 6 hostile messages in 100 are Some, and the Conflict card is amber, with no alert standing: the page cannot say that nothing needs attention
        ctx = build_with(lively(hostile_chat(100, 6)))
        self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (0, 0))
        marked = [c['key'] for c in ctx['cards'] if c['tone'] in ('warn', 'bad')]
        self.assertEqual(marked, ['conflict'])
        self.assertFalse(ctx['all_clear'])
        # the same chat with 3 hostile in 100 is Rare, so the card is green and the all clear stands
        ctx = build_with(lively(hostile_chat(100, 3)))
        self.assertEqual([c['key'] for c in ctx['cards'] if c['tone'] in ('warn', 'bad')], [])
        self.assertTrue(ctx['all_clear'])
        # a health score that needs attention is marked as well
        ctx = build_with(lively(chat(100, lambda i: {'Ignorant': 3, 'Egocentric': 3, 'Neurotic': 3} if i % 2 else None)))
        self.assertEqual(ctx['health']['tone'], 'bad')
        self.assertFalse(ctx['all_clear'])

    def test_without_a_vibe_check_that_ran_there_is_no_all_clear(self):
        units = lively(neutral(96))
        for vibe in (None, {}, {'status': 'failed', 'reason': 'x', 'analyzed_n': 0}, {'status': 'no_input'}, {'status': 'cancelled', 'analyzed_n': 0}):
            with self.subTest(vibe=vibe):
                ctx = build_with(units, vibe=vibe)
                self.assertEqual(ctx['alerts'], [], 'nothing stands, and still it is no all clear')
                self.assertFalse(ctx['all_clear'])
                self.assertIn('no_vibe', [x['key'] for x in ctx['notes']])

    def test_a_high_or_a_medium_alert_that_stands_stops_it(self):
        cases = [('threat', lively(neutral(96)), vibe_ok(96, threats=1)), ('hate speech', lively(neutral(96)), vibe_ok(96, hate_speech=1)),
                 ('harassment', lively(neutral(96)), vibe_ok(96, harassment=1)), ('bullying', lively(neutral(96)), vibe_ok(96, bullying=1)),
                 ('heated high', lively(spike(9)), 'ok'), ('heated medium', lively(spike(6)), 'ok'),
                 ('hostile high', lively(hostile_chat(60, 20)), 'ok'), ('hostile some', lively(hostile_chat(60, 12)), 'ok'),
                 ('sarcasm', lively(hostile_chat(60, 14, 'Sarcastic')), 'ok'), ('picking up', lively(thirds_chat(100, (0, 4, 12), {'Rude': 2})), 'ok'),
                 ('care fading', lively(thirds_chat(100, (20, 14, 7), {'Empathetic': 2})), 'ok')]
        for name, units, vibe in cases:
            with self.subTest(alert=name):
                ctx = build_with(units, vibe=vibe)
                self.assertGreater(ctx['alert_counts']['high'] + ctx['alert_counts']['medium'], 0, 'the case has an alert to stop it')
                self.assertFalse(ctx['all_clear'])

    def test_good_news_alone_does_not_stop_it(self):
        ctx = build_with(lively(thirds_chat(40, (4, 10, 20), {'Empathetic': 2})))               # care is growing, and every figure is green
        self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (0, 0))
        self.assertGreater(ctx['alert_counts']['good'], 0)
        self.assertEqual([c['key'] for c in ctx['cards'] if c['tone'] in ('warn', 'bad')], [])
        self.assertTrue(ctx['all_clear'])

    def test_hostility_that_is_easing_but_still_Some_is_good_news_and_no_all_clear(self):
        ctx = build_with(lively(thirds_chat(100, (12, 6, 0), {'Rude': 2})))                      # 6 in 100 over the whole check: the Conflict card is amber
        self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (0, 0))
        self.assertGreater(ctx['alert_counts']['good'], 0)
        self.assertFalse(ctx['all_clear'])

    def test_a_chat_with_almost_no_tone_is_not_an_all_clear_but_a_chat_with_some_is(self):
        ctx = build_with(chat(96))                                                            # 96 of 96 silent
        self.assertIn('silent', [x['key'] for x in ctx['notes']])
        self.assertEqual(ctx['alert_counts'], {'high': 0, 'medium': 0, 'good': 0})
        self.assertFalse(ctx['all_clear'])
        edge = lambda silent: chat(100, lambda i: {'Ignorant': 1} if i < 100 - silent else None)      # noqa: E731
        self.assertFalse(build_with(edge(95))['all_clear'], '95 in 100 silent is almost no tone')
        some = build_with(edge(94))
        self.assertNotIn('silent', [x['key'] for x in some['notes']])
        self.assertTrue(some['all_clear'], '94 in 100 silent is a chat with some tone')
        self.assertNotIn('silent', [x['key'] for x in build_with(chat(29))['notes']], 'the note about almost no tone needs at least 30 messages')

    def test_a_silent_chat_is_never_an_all_clear_whatever_its_size(self):
        """MEASURES.md: ms['all_clear'] counts silence at any number of messages (19 in 20 rated all 0 is silence; only the note about it needs at least 30 messages)."""
        for n in (1, 2, 10, 24, 29, 30, 48, 96, 300):
            with self.subTest(n=n):
                ctx = build_with(chat(n))                                                          # Vibe Check ran and flagged nothing, and no alert stands
                self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (0, 0))
                self.assertFalse(ctx['all_clear'])
                self.assertEqual('silent' in [x['key'] for x in ctx['notes']], n >= 30, 'the note needs 30 messages; the rule does not')
        # the edge at the sizes that can be an all clear: the fewest silent messages that make 19 in 20 are 38 of 40 and 57 of 60; one fewer is a chat with tone
        for n, tripped in [(40, 38), (60, 57)]:
            for silent, clear in [(tripped, False), (tripped - 1, True)]:
                with self.subTest(n=n, silent=silent):
                    units = chat(n, lambda i: {'Ignorant': 1} if i < n - silent else None)
                    self.assertEqual(build_with(units)['all_clear'], clear)
        # under 30 messages a chat with tone is no all clear either
        for n, silent in [(20, 18), (10, 9)]:
            with self.subTest(n=n, silent=silent):
                self.assertFalse(build_with(chat(n, lambda i: {'Ignorant': 1} if i < n - silent else None))['all_clear'])


# ---- wording ---------------------------------------------------------------------------------------------------------------------------------
# A second, stricter list than vibe_check.engine._PUNITIVE: words that suggest dealing with people, watching them, or taking action against them.
STRICT = [r'accountab', r'confront', r'offender', r'culprit', r'investigat', r'interven', r'enforc', r'restrict', r'moderat', r'crack(?:ing)? down', r'take action',
          r'action against', r'deal with', r'\bstep(?:s|ped|ping)? in\b', r'keep an eye', r'have a word', r'identify who', r'who (?:wrote|said|posted)',
          r'the (?:people|members|authors) involved', r'members who', r'people who', r'follow up with', r'\bremov', r'\bbans?\b|\bbann', r'\bmut(?:e|es|ed|ing)\b',
          r'\bwarn', r'punish', r'disciplin', r'sanction', r'\bpolic(?:e|ed|ing|y|ies)\b', r'escalat']
STRICT_RE = [re.compile(p, re.I) for p in STRICT]
# Words and ideas a page must never show (house rules): jargon and formulas
FORBIDDEN = [re.compile(p) for p in (r'(?i)\bstretch', r'(?i)\bepisode', r'(?i)\bbaseline', r'(?i)\bpeak', r'(?i)\btoxic', r'\bFAS\b', r'(?i)communication unit',
                                     r'(?i)standard error', r'(?i)z-?score', r'(?i)\bp-?value', r'(?i)confidence interval', r'(?i)\bsigma\b', r'(?i)\bsqrt\b',
                                     r'[±×÷≤≥−√]', r'\b[a-z]\s=\s\d', r'\d\s[*/^]\s\d')]
ALLOWED_NON_ASCII = {'·': 'middle dot (the PDF layer draws it)', '’': 'apostrophe (the PDF layer maps it)'}
MAX_WORDS = 22


def sentences(text):
    return [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s]


def wording_problems(where, text, incognito=False, long_ok=False):
    """What is wrong with one string a page can show: [(rule, detail)]. The measure name 'Escalation' is the one exempt word; the one exempt sentence is the
    serious alert's, which tells the reader to contact local emergency services."""
    problems = []
    scanned = text
    if where.startswith('alert.serious.'):
        scanned = ' '.join(s for s in sentences(text) if 'emergency services' not in s)
    if _PUNITIVE.search(scanned):
        problems.append(('vibe_check _PUNITIVE', _PUNITIVE.search(scanned).group(0)))
    without_name = re.sub(r'\bEscalation\b', '', scanned)
    for rx in STRICT_RE:
        hit = rx.search(without_name)
        if hit:
            problems.append(('stricter list', hit.group(0)))
    for rx in FORBIDDEN:
        hit = rx.search(text)
        if hit:
            problems.append(('forbidden word or formula', hit.group(0)))
    for char in sorted({c for c in text if ord(c) > 127 and c not in ALLOWED_NON_ASCII}):
        problems.append(('non-ASCII character', f'{char!r} U+{ord(char):04X}'))
    if not long_ok:
        for sentence in sentences(text):
            if len(sentence.split()) > MAX_WORDS:
                problems.append((f'sentence over {MAX_WORDS} words', f'{len(sentence.split())} words: {sentence[:70]}'))
    if incognito and CLOCK.search(text):
        problems.append(('clock time while Incognito is on', CLOCK.search(text).group(0)))
    if where.startswith('alert.') and CLOCK.search(text):
        problems.append(('clock time in an alert', CLOCK.search(text).group(0)))
    for leak in (r'\bnan\b', r'\binf\b', r'[{}]', r'\bTraceback\b', r'0x[0-9a-f]{6,}', r'\bNone\b(?! of)', r'  ', r'^\s|\s$'):
        if re.search(leak, text) and not (leak == r'\bNone\b(?! of)' and text == 'None'):
            problems.append(('formatting leak', leak))
    return problems


def pattern_units(name, n, rng):
    """A synthetic chat of n messages shaped like `name`: the tones of each third are drawn message by message from (probability, tones) lists."""
    s, e, c, h = {'Sarcastic': 2}, {'Empathetic': 2}, {'Polite': 2}, {'Rude': 2}
    thirds = {'calm': [[(.25, c), (.10, e)]] * 3, 'supportive': [[(.30, {'Empathetic': 3}), (.15, {'Encouraging': 2}), (.2, c)]] * 3,
              'banter': [[(.25, {'Funny': 2}), (.15, {'Sarcastic': 2, 'Funny': 2}), (.1, s)]] * 3,
              'hostile': [[(.25, h), (.05, {'Aggressive': 3})]] * 3, 'hostile_rising': [[(.02, h)], [(.1, h)], [(.4, h), (.1, {'Aggressive': 3})]],
              'hostile_falling': [[(.4, h), (.1, {'Aggressive': 3})], [(.1, h)], [(.02, h)]], 'sarcasm': [[(.25, s)]] * 3, 'sarcasm_rising': [[(.02, s)], [(.1, s)], [(.3, s)]],
              'care_fading': [[(.3, e)], [(.2, e)], [(.05, e)]], 'care_growing': [[(.05, e)], [(.2, e)], [(.3, e)]], 'agitated': [[(.2, {'Agitated': 3})]] * 3,
              'noise': [[(.05, {t: rng.randint(1, 3)}) for t in TONES]] * 3, 'silent': [[]] * 3, 'single': [[]] * 3,
              'outburst_mid': [[(.05, c)]] * 3, 'outburst_start': [[(.05, c)]] * 3, 'outburst_end': [[(.05, c)]] * 3, 'spat': [[(.05, c)]] * 3}[name]
    burst = {'outburst_mid': range(n // 2 - 10, n // 2 + 10), 'outburst_start': range(0, 20), 'outburst_end': range(n - 20, n), 'spat': range(n // 2 - 2, n // 2 + 3),
             'single': range(n // 2, n // 2 + 1)}.get(name, range(0))
    out = []
    for i in range(n):
        tones = {}
        if i in burst:
            tones = {'Rude': 3, 'Disrespectful': 2, 'Agitated': 2} if rng.random() < .8 else {}
        else:
            for chance, wanted in thirds[min(2, i * 3 // max(1, n))]:
                if rng.random() < chance:
                    tones = dict(wanted)
                    break
        out.append(unit(i, **tones))
    return out


PATTERNS = ('calm', 'supportive', 'banter', 'hostile', 'hostile_rising', 'hostile_falling', 'sarcasm', 'sarcasm_rising', 'care_fading', 'care_growing', 'agitated', 'noise',
            'silent', 'single', 'outburst_mid', 'outburst_start', 'outburst_end', 'spat')
SIZES = (10, 24, 40, 48, 72, 96, 300)
VIBES = {'ok': 'ok', 'none': None, 'failed': {'status': 'failed', 'reason': 'x', 'analyzed_n': 0}, 'harassment': lambda n: vibe_ok(n, harassment=2),
         'threats': lambda n: vibe_ok(n, threats=1, insults=3), 'hate': lambda n: vibe_ok(n, hate_speech=2, bullying=1),
         'red': lambda n: dict(vibe_ok(n, bullying=1), flagged=[{'type': 'bullying', 'severity': 8, 'index': 3}]),
         'flagged_only': lambda n: {'status': 'ok', 'analyzed_n': n, 'flagged': [{'type': 'threat', 'severity': 4, 'index': 2}]}}


def stamped(units, how, rng):
    """The same messages with their dates drawn another way."""
    out = []
    for i, u in enumerate(units):
        # every date stays before 2026-06-01 (300 messages at most), so that no scenario depends on the day the tests run
        ts = {'minutes': stamp(i), 'hours': stamp(i, 60), 'weeks': stamp(i, 360), 'undated': '', 'offset': stamp(i, 7) + 'Z', 'dateonly': stamp(i, 120)[:10]}[how]
        out.append(dict(u, timestamp=ts))
    return out


def earlier(rec, kind):
    """A saved record of an earlier check, made to differ from this check's record: 'improved' (this check is better than it), 'slipped' (this one is worse),
    'unlike' (made with another model), 'setting' (made with another sensitivity) or 'resized' (it rated four times as many messages). Counts stay within the
    number of messages rated."""
    n = rec['n']
    prev = dict(rec, gen=1, created_at='2026-05-01T12:00:00')
    shift = max(8, n // 3)
    if kind == 'improved':
        prev.update(health=max(0.0, rec['health'] - 25), care_n=max(0, rec['care_n'] - shift), hostile_n=min(n, rec['hostile_n'] + shift), heated_n=min(n, rec['heated_n'] + shift))
    elif kind == 'slipped':
        prev.update(health=min(100.0, rec['health'] + 25), care_n=min(n, rec['care_n'] + shift), hostile_n=max(0, rec['hostile_n'] - shift), heated_n=max(0, rec['heated_n'] - shift))
    elif kind == 'setting':                                                             # made with another sensitivity
        prev.update(sensitivity='low' if rec.get('sensitivity') != 'low' else 'high')
    elif kind == 'resized':                                                             # rated four times as many messages (every count is scaled with them)
        prev.update(n=4 * n, total=4 * n, **{key: 4 * rec[key] for key in ('hostile_n', 'care_n', 'dig_n', 'heated_n') if key in rec})
    else:                                                                               # 'unlike': made with another model (the name and its fingerprint)
        prev.update(model='some-other-model', model_key=fingerprint('some-other-model'))
    return prev


def variant(units, rng, previous='none'):
    """One built context for a chat under random settings, with an earlier check saved when `previous` says so."""
    n = len(units)
    sens = rng.choice(('low', 'medium', 'high'))
    inc = rng.random() < .4
    how = rng.choice(('minutes', 'hours', 'weeks', 'undated', 'offset', 'dateonly'))
    vibe = VIBES[rng.choice(sorted(VIBES))]
    vibe = vibe(n) if callable(vibe) else vibe
    cov = rng.choice([cov_full(n), {'sampled': True, 'total': n * 4, 'scope': n * 4, 'added_since': 0}, {'sampled': False, 'total': n, 'scope': n + 9, 'added_since': 9}])
    dated = stamped(units, how, rng)
    row = {'vibe_sensitivity': sens, 'incognito': int(inc)}
    ctx = build_with(dated, row, vibe, cov)
    if ctx is not None and previous != 'none':
        ctx = build_with(dated, row, vibe, cov, history=[earlier(ctx['record'], previous)], gen=2)
    return ctx


@functools.lru_cache(maxsize=None)
def scenarios():
    """A broad, fixed set of built contexts: every shape of chat at several sizes under random settings, and one hand-built context for each alert there is."""
    out = []
    for pi, name in enumerate(PATTERNS):
        for ni, n in enumerate(SIZES):
            for k in range(3):
                rng = random.Random(1000 * pi + 10 * ni + k)
                ctx = variant(pattern_units(name, n, rng), rng, previous=rng.choice(['none', 'none', 'improved', 'slipped', 'unlike']))
                if ctx:
                    out.append((f'{name}/{n}/{k}', ctx))
    rng = random.Random(5)
    hand = {'heated high': spike(9), 'heated medium': spike(6), 'heated end': part_chat([0] * 23 + [6], 20, HEAT), 'tense start': part_chat([6] + [0] * 23, 20, HEAT),
            'hostile many': hostile_chat(60, 20), 'hostile some': hostile_chat(60, 12), 'sarcastic': hostile_chat(60, 14, 'Sarcastic'),
            'rising': thirds_chat(100, (0, 4, 12), {'Rude': 2}), 'easing': thirds_chat(100, (12, 6, 0), {'Rude': 2}), 'fading': thirds_chat(100, (20, 14, 7), {'Empathetic': 2}),
            'growing': thirds_chat(100, (7, 14, 20), {'Empathetic': 2}), 'snide growing': thirds_chat(100, (0, 4, 12), {'Sarcastic': 2}), 'few': lively(chat(12)),
            'between': lively(chat(40)), 'silent': chat(100), 'courteous': lively(hostile_chat(100, 40, 'Polite')), 'encouraging': lively(hostile_chat(100, 30, 'Encouraging')),
            'supportive': lively(hostile_chat(100, 30, 'Empathetic')), 'cools': lively(part_chat([0] * 8 + [1, 1, 1, 6, 1] + [0] * 11, 20, HEAT)),
            'few dated': undated_tail(30, 20), 'some undated': undated_tail(100, 70), 'hot but not a moment': part_chat([2, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3, 3], 8, HEAT),
            'singular': lively(neutral(30))}
    holes = lively(hostile_chat(60, 12))
    for u in holes[:3]:
        del u['ratings']['Rude']                                                        # three messages with a rating missing
    hand['incomplete'] = holes
    for name, units in hand.items():
        for vibe in ('ok', 'none', 'threats', 'harassment'):
            for inc in (False, True):
                ctx = build_with(units, {'incognito': int(inc)}, VIBES[vibe](len(units)) if callable(VIBES[vibe]) else VIBES[vibe])
                out.append((f'hand/{name}/{vibe}/{inc}', ctx))
    for name, units in (('hostile some', hand['hostile some']), ('neutral', lively(neutral(96))), ('silent', chat(96))):                  # the settings of a check: Low sensitivity, a sample, messages added
        for sens in ('low', 'medium'):
            for cov in (cov_full(len(units)), {'sampled': True, 'total': 400, 'scope': 400, 'added_since': 0}, {'sampled': False, 'total': len(units), 'scope': len(units) + 1, 'added_since': 1},
                        {'sampled': False, 'total': len(units), 'scope': len(units) + 9, 'added_since': 9}):
                out.append((f'hand/{name}/{sens}/{cov["added_since"]}/{cov["sampled"]}', build_with(units, {'vibe_sensitivity': sens}, cov=cov)))
    for kind, vibe in (('one threat', dict(vibe_ok(60, threats=1), flagged=[{'type': 'threat', 'severity': 9, 'index': 2}])), ('one counted', vibe_ok(60, hate_speech=1)),
                       ('two kinds', dict(vibe_ok(60, threats=2, harassment=1), flagged=[{'type': 'harassment', 'severity': 4, 'index': 5}])), ('one mild', dict(vibe_ok(60, bullying=1), flagged=[{'type': 'bullying', 'severity': 3, 'index': 5}]))):
        out.append((f'hand/serious/{kind}', build_with(lively(neutral(60)), vibe=vibe)))
    for change in ('improved', 'slipped', 'unlike', 'setting', 'resized'):                              # an earlier check that is like this one and better or worse, or that is not alike
        for name in ('fading', 'growing', 'courteous', 'hostile some'):
            units = hand[name]
            out.append((f'hand/{name}/{change}', build_with(units, history=[earlier(build_with(units)['record'], change)], gen=2)))
    for name in ('tense start', 'silent', 'few', 'between'):                                           # and a few chats whose marks are neutral, next to an earlier check
        for change in ('improved', 'unlike'):
            out.append((f'hand/{name}/{change}', build_with(hand[name], history=[earlier(build_with(hand[name])['record'], change)], gen=2)))
    return tuple(out)


ALERT_KINDS = {'serious': r'Vibe Check flagged serious messages', 'heated': r'mood heated up fast', 'level hostile': r'(Many|Some) .*openly hostile',
               'picking up': r'Hostile wording is picking up', 'sarcasm common': r'Sarcastic wording is common', 'sarcasm growing': r'Sarcastic wording is growing',
               'care fading': r'Fewer messages show care', 'mood slipped': r'overall mood slipped', 'care growing': r'Care is growing', 'easing': r'Hostility is easing',
               'mood improved': r'overall mood improved'}


class WordingTests(unittest.TestCase):
    """Every string a page can show, from a broad set of chats: gentle, plain, short, free of jargon, with no clock time where there must be none."""

    @classmethod
    def setUpClass(cls):
        cls.found = scenarios()

    def test_the_scenarios_reach_every_kind_of_alert_note_and_badge(self):
        notes_wanted = {'vibe check not run': r'(?i)Vibe Check.*not run', 'too few': r'(?i)rated only', 'no dates': r'(?i)no usable dates', 'a sample': r'^A short flare-up can fall between the samples\.',
                        'too early': r'(?i)direction|too early', 'incomplete': r'(?i)incomplete', 'low sensitivity': r'^Your sensitivity is Low',
                        'few dated': r'have a date, so there are no lines', 'some undated': r'^Only \d+ of \d+ messages have a date\. The lines show those',
                        'silent': r'^Almost no tone was found in these messages\.', 'added one': r'^1 message falls inside the review period but was not rated',
                        'added many': r'^\d+ messages fall inside the review period but were not rated'}
        # the reasons two checks are not alike are no notes: they are said with the comparison (ms['comparison']['why']); and a tense start is a note on the Escalation card
        whys_wanted = {'another model': r'different model', 'another sensitivity': r'different sensitivity', 'too few messages': r'fewer than 48', 'another size': r'very different numbers'}
        seen_alerts, seen_levels, seen_notes, seen_badges, seen_whys, seen_card_notes = set(), set(), set(), set(), set(), set()
        for name, ctx in self.found:
            for a in ctx['alerts']:
                for kind, rx in ALERT_KINDS.items():
                    if re.search(rx, a['title'], re.I):
                        seen_alerts.add(kind)
                        seen_levels.add((kind, a['level']))
            for n in ctx['notes']:
                seen_notes |= {kind for kind, rx in notes_wanted.items() if re.search(rx, n['text'])}
            seen_badges |= {b['name'] for b in ctx['badges'] if b['earned']}
            if ctx['comparison'] and not ctx['comparison']['comparable']:
                seen_whys |= {kind for kind, rx in whys_wanted.items() if re.search(rx, ctx['comparison']['why'])}
            seen_card_notes |= {c['note'] for c in ctx['cards'] if c['note']}
        self.assertEqual(sorted(set(ALERT_KINDS) - seen_alerts), [], 'the scenarios must reach every alert for the wording tests to mean anything')
        self.assertTrue({('serious', 'high'), ('serious', 'medium'), ('heated', 'high'), ('heated', 'medium'), ('level hostile', 'high'), ('level hostile', 'medium')} <= seen_levels, seen_levels)
        self.assertGreaterEqual(len(self.found), 400)
        self.assertEqual(sorted(set(notes_wanted) - seen_notes), [], seen_notes)
        self.assertEqual(sorted(set(whys_wanted) - seen_whys), [], 'the scenarios reach every reason two checks are not alike')
        self.assertTrue(CHANCE_NOTE in seen_card_notes and any(re.search(TENSE_NOTE, note) for note in seen_card_notes), seen_card_notes)
        self.assertEqual(sorted(seen_badges), sorted(BadgeTests.NAMES))

    def collect(self):
        for name, ctx in self.found:
            for where, text in page_strings(ctx):
                yield name, where, text, ctx['m']['incognito']

    def test_no_string_trips_the_vibe_check_word_filter_or_the_stricter_list_or_the_jargon_list_or_the_clock_rule(self):
        bad = collections.defaultdict(set)
        total = 0
        for name, where, text, incognito in self.collect():
            total += 1
            for rule, detail in wording_problems(where, text, incognito, long_ok=True):
                bad[rule].add((re.sub(r'\.[a-z_]+\.', '.*.', where), detail))
        self.assertGreater(total, 5000)
        self.assertEqual({k: sorted(v)[:8] for k, v in bad.items()}, {})

    def test_no_sentence_is_longer_than_22_words(self):
        bad = set()
        for name, where, text, incognito in self.collect():
            for sentence in sentences(text):
                if len(sentence.split()) > MAX_WORDS:
                    bad.add((where.split('.')[0] + '.' + where.split('.')[-1] if where.startswith('caveat') else where.rsplit('.', 1)[0] + '.*', len(sentence.split()), sentence[:60]))
        self.assertEqual(sorted(bad)[:10], [])

    def test_the_exemptions_are_only_the_two_the_spec_names(self):
        # the serious alert's own sentence about emergency services is the only place that phrase may be, and 'Escalation' is only ever the measure's name
        for name, where, text, incognito in self.collect():
            if 'emergency services' in text:
                self.assertTrue(where.startswith('alert.serious.'), where)
            for hit in re.finditer(r'(?i)escalat\w*', text):
                self.assertEqual(hit.group(0), 'Escalation', (where, text))
                self.assertTrue(re.search(r'\bEscalation\b', text))

    def test_incognito_leaves_no_clock_time_anywhere_in_the_measures(self):
        quiet = [(n, c) for n, c in self.found if c['m']['incognito']]
        self.assertGreater(len(quiet), 100)
        for name, ctx in quiet:
            for where, text in page_strings(ctx):
                self.assertNotRegex(text, CLOCK, (name, where))

    def test_the_same_scenarios_show_clock_times_in_labels_only_when_incognito_is_off(self):
        times = [(n, c) for n, c in self.found if not c['m']['incognito'] and c['m']['basis'] == 'time' and any(CLOCK.search(s['label']) for s in c['m']['stretches'])]
        self.assertTrue(times, 'the control: some chats do show a time when it is allowed')

    def test_an_alert_never_holds_a_clock_time(self):
        for name, ctx in self.found:
            for a in ctx['alerts']:
                for field in ('title', 'detail', 'try', 'base', 'when'):
                    self.assertNotRegex(a[field], CLOCK, (name, a['key'], field))

    def test_alerts_never_quote_or_name_a_message_or_a_person(self):
        for name, ctx in self.found:
            for where, text in page_strings(ctx):
                if where.startswith('alert.'):
                    self.assertNotRegex(text, r'@\w', (name, where, text))
                    self.assertNotRegex(text, r'(?i)\b(you|your)\s+(wrote|said|posted)\b', (name, where, text))

    def test_every_alert_that_is_not_good_news_has_a_next_step(self):
        for name, ctx in self.found:
            for a in ctx['alerts']:
                if a['level'] != 'good':
                    self.assertTrue(a['try'].strip(), (name, a['key']))

    def test_every_note_says_which_section_prints_it_and_is_there_once(self):
        about = collections.defaultdict(set)
        for name, ctx in self.found:
            keys = [n['key'] for n in ctx['notes']]
            self.assertEqual(len(keys), len(set(keys)), name)
            for n in ctx['notes']:
                about[n['key']].add(n['about'])
        self.assertEqual({k for k, v in about.items() if v == {'alerts'}}, {'no_vibe'})
        self.assertEqual({k for k, v in about.items() if v == {'measures'}}, set(about) - {'no_vibe'})
        self.assertLessEqual({'no_vibe', 'few', 'few_dated', 'no_verdict', 'order', 'undated', 'incomplete', 'sampled', 'low_sensitivity', 'silent', 'added'},
                             set(about), 'the scenarios reach every note')
        self.assertEqual(set(about) & set(REMOVED_NOTES), set(), 'the notes about checks that are not alike and about a tense start no longer exist')

    def test_the_reason_two_checks_are_not_alike_is_in_the_comparison_once_and_in_no_note_in_every_scenario(self):
        compared = 0
        for name, ctx in self.found:
            comparison = ctx['comparison']
            if not comparison:
                self.assertIsNone(ctx['since'], name)
                continue
            compared += 1
            self.assertEqual(ctx['since']['comparable'], comparison['comparable'], name)
            self.assertEqual(ctx['since']['why'], comparison['why'], name)
            self.assertEqual(bool(comparison['why']), not comparison['comparable'], name)
            if not comparison['comparable']:
                self.assertEqual(sorted(where for where, text in page_strings(ctx) if text == comparison['why']), ['comparison.why', 'since.why'], name)
                self.assertEqual([r['verdict'] for r in ctx['since']['rows']], [''] * 5, name)
                self.assertFalse([a for a in ctx['alerts'] if a['key'] in ('mood_down', 'mood_up')], name)
        self.assertGreater(compared, 60, 'a good share of the scenarios have an earlier check')

    def test_a_tense_start_is_said_only_on_the_escalation_card_in_every_scenario(self):
        said = 0
        for name, ctx in self.found:
            where = [w for w, text in page_strings(ctx) if re.search(r'(?i)already tense', text)]
            m = ctx['m']
            tense = m['opening_heated'] and m['verdicts']
            self.assertEqual(where, ['card.escalation.note'] if tense else [], name)
            said += bool(where)
        self.assertGreater(said, 5, 'the scenarios have tense starts')

    def test_every_card_and_health_tile_says_its_mark_in_words_in_every_scenario(self):
        marks = collections.Counter()
        for name, ctx in self.found:
            for c in ctx['cards']:
                self.assertEqual(c['tone_word'], measures.TONE_WORDS[c['tone']], (name, c['key']))
                marks[c['tone']] += 1
                if c['key'] == 'escalation' and c['level'] == 'Too early':
                    self.assertEqual((c['tone'], c['tone_word']), ('info', 'Neutral'), name)
            self.assertEqual(ctx['health']['tone_word'], measures.TONE_WORDS[ctx['health']['tone']], name)
        self.assertEqual(set(marks), {'good', 'info', 'warn', 'bad'})

    def test_the_fixed_lines_are_the_same_in_every_scenario(self):
        first = self.found[0][1]
        for name, ctx in self.found:
            self.assertEqual((ctx['texts'], ctx['caveats']), (first['texts'], first['caveats']), name)

    def test_a_check_that_could_not_be_judged_never_reads_as_an_all_clear_or_a_green_none(self):
        silent_seen = 0
        for name, ctx in self.found:
            m = ctx['m']
            if not (m['has_series'] and m['verdicts']):
                card = ctx['cards'][0]
                self.assertEqual((card['level'], card['tone']), ('Too early', 'info'), name)
            if ctx['all_clear']:
                self.assertEqual((ctx['alert_counts']['high'], ctx['alert_counts']['medium']), (0, 0), name)
                self.assertNotIn('silent', [n['key'] for n in ctx['notes']], name)
            if 20 * m['silent'] >= 19 * m['n']:                                          # almost no tone found, at any number of messages
                self.assertFalse(ctx['all_clear'], name)
                silent_seen += 1
        self.assertGreater(silent_seen, 10, 'the scenarios hold silent chats, small and large')
        self.assertTrue(any(c['m']['n'] < 30 and 20 * c['m']['silent'] >= 19 * c['m']['n'] for _, c in self.found), 'and some of fewer than 30 messages (no note, still no all clear)')

    def test_the_marks_follow_the_neutral_rules_in_every_scenario(self):
        """MEASURES.md: the marks are neutral for all four cards when almost no tone was found, for Escalation while it reads Too early, for Escalation's None when the
        chat was hot from its first part, for Conflict, Empathy and Passive-aggressive below 48 rated messages, and for the health tile below 48 messages or when no
        tone was found. Otherwise the mark follows the level word and the band."""
        by_level = {'conflict': {'Rare': 'good', 'Some': 'warn', 'Often': 'bad'}, 'passive': {'Rare': 'good', 'Some': 'warn', 'Often': 'warn'},
                    'empathy': {'Rare': 'info', 'Some': 'info', 'Often': 'good'}}
        by_band = {'Excellent': 'good', 'Good': 'good', 'Fair': 'info', 'Needs attention': 'bad'}
        seen = collections.Counter()
        for name, ctx in self.found:
            m, cards = ctx['m'], {c['key']: c for c in ctx['cards']}
            silent = 20 * m['silent'] >= 19 * m['n']                                     # 19 in 20 messages rated all 0
            judged = m['has_series'] and m['verdicts']
            health = ctx['health']
            if silent:
                self.assertEqual({c['tone'] for c in cards.values()} | {health['tone']}, {'info'}, name)
                seen['silent'] += 1
            if m['n'] < 48:
                self.assertEqual([cards[k]['tone'] for k in ('conflict', 'empathy', 'passive')] + [health['tone']], ['info'] * 4, name)
                seen['young'] += 1
            if m['n'] >= 48 and not silent:
                self.assertEqual(health['tone'], by_band[health['status']], name)
                for key, table in by_level.items():
                    self.assertEqual(cards[key]['tone'], table[cards[key]['level']], (name, key))
                seen['by the word'] += 1
            esc = cards['escalation']
            if not judged:
                self.assertEqual((esc['level'], esc['tone'], esc['count']), ('Too early', 'info', None), name)
                seen['too early'] += 1
                continue
            self.assertEqual(esc['count'], len(m['episodes']), name)
            if not m['episodes'] and m['opening_heated']:
                self.assertEqual((esc['level'], esc['tone'], esc['tone_word']), ('None', 'info', 'Neutral'), name)
                seen['tense none'] += 1
            elif not m['episodes'] and not silent and esc['level'] == 'None' and not esc['note']:
                self.assertEqual((esc['tone'], esc['tone_word']), ('good', 'A good sign'), name)
                seen['green none'] += 1
        for kind in ('silent', 'young', 'by the word', 'too early', 'tense none', 'green none'):
            self.assertGreater(seen[kind], 0, f'the scenarios reach {kind}')

    def test_no_serious_flags_is_held_back_exactly_when_a_serious_alert_or_a_high_priority_alert_stands_in_every_scenario(self):
        held = 0
        for name, ctx in self.found:
            tile = {b['key']: b for b in ctx['badges']}['noflags']
            standing = bool(ctx['alert_counts']['high'] or any(a['key'] == 'serious' for a in ctx['alerts']))
            if standing:
                self.assertFalse(tile['earned'], name)
                self.assertEqual(tile['evidence'], '', name)
            if tile['needs'] == HELD_BACK:
                self.assertTrue(standing, name)
                held += 1
            self.assertEqual(ctx['badges_earned'], sum(b['earned'] for b in ctx['badges']), name)
        self.assertGreater(held, 5, 'the scenarios hold back the badge now and then')

    def test_the_chart_key_is_the_same_sentence_for_the_page_and_the_pdf_in_every_scenario(self):
        extras = collections.Counter()
        for name, ctx in self.found:
            m, key = ctx['m'], ctx['chart_key']
            self.assertEqual(key, measures.chart_key(m), name)
            self.assertTrue(key.startswith(CHART_KEY), name)
            extra = key[len(CHART_KEY):].strip()
            if m['basis'] == 'order':
                self.assertEqual(len(sentences(extra)), 1, name)
                self.assertRegex(extra, r'(?i)rated message.*\border\b|\border\b.*rated message')
                self.assertNotIn('UTC', key, name)
            elif extra:
                self.assertEqual((extra, m['utc']), ('Times are UTC.', True), name)
            if any(' UTC' in s['label'] for s in m['stretches']):
                self.assertEqual(extra, 'Times are UTC.', name)
            extras['order' if m['basis'] == 'order' else 'utc' if extra else 'plain'] += 1
        self.assertEqual(set(extras), {'order', 'utc', 'plain'}, 'the scenarios reach every variant of the key')


class RobustnessTests(unittest.TestCase):
    """Things a record or a chat may hold that the rules did not foresee."""

    def test_a_saved_record_that_the_store_accepts_never_breaks_the_comparison(self):
        # a record from a hand-edited database, or from a bug, still must not break the page, whatever the store lets through
        base = saved(100)
        for off_the_scale in [dict(health=1e9), dict(health=-1e9), dict(health=100.01), dict(health=-0.01), dict(health=101), dict(health=-1)]:
            self.assertIsNone(store.clean_measure_snapshot(dict(base, gen=1, **off_the_scale)), f'the store drops a record whose health is off 0 to 100: {off_the_scale}')
        for bad in [dict(hostile_n=250), dict(care_n=101), dict(dig_n=10 ** 9), dict(heated_n=10 ** 9), dict(health=100.0), dict(health=0.0), dict(health_sd=-5.0), dict(health_sd=0.0),
                    dict(n=0), dict(n=10 ** 12, total=10 ** 12), dict(peak=-3.0), dict(episodes=10 ** 9)]:
            before = store.clean_measure_snapshot(dict(base, gen=1, **bad))
            self.assertIsNotNone(before, bad)
            for pair in ((saved(100), before), (before, saved(100))):
                with self.subTest(record=bad):
                    got = measures.compare(*pair)
                    self.assertIn('comparable', got)
                    measures.since_table(got, pair[0])
                    measures.trail_of([pair[1]], pair[0])

    def test_a_record_that_did_not_come_through_the_store_cannot_break_the_comparison_either(self):
        """compare() is the second line of defence: a count above its total or below 0 is clamped, whatever way it got here."""
        for bad in [dict(hostile_n=250), dict(care_n=101), dict(dig_n=10 ** 9), dict(heated_n=10 ** 9), dict(hostile_n=-1), dict(care_n=-10 ** 9), dict(dig_n=-7),
                    dict(heated_n=102), dict(hostile_n=102, care_n=102, dig_n=102, heated_n=102), dict(hostile_n=-1, care_n=-1, dig_n=-1, heated_n=-1)]:
            raw = dict(saved(100), gen=1, **bad)
            for pair in ((saved(100), raw), (raw, saved(100))):
                with self.subTest(record=bad, now_is_raw=pair[0] is raw):
                    got = measures.compare(*pair)
                    self.assertTrue(got['comparable'])
                    self.assertEqual(sorted(got['changes']), ['conflict', 'empathy', 'health', 'heated', 'passive'])
                    for change in got['changes'].values():
                        self.assertTrue(all(math.isfinite(change[k]) for k in ('first', 'last', 'delta', 'se')), change)
                    measures.since_table(got, pair[0])

    def test_no_text_in_a_rating_can_reach_a_figure_or_a_sentence(self):
        units = chat(60, lambda i: {'Rude': 2})
        units[3]['ratings']['Rude'] = 'ZQCANARY <b>'
        units[4]['ratings']['Polite'] = None
        units[5]['ratings']['Funny'] = '3'
        ctx = build_with(units)
        self.assertEqual(ctx['m']['n'], 60)
        for where, text in page_strings(ctx):
            self.assertNotIn('ZQCANARY', text, where)

    def test_nothing_a_timestamp_holds_is_echoed_into_a_label_or_a_sentence(self):
        junk = ['<script>ZQ</script>', "ZQ'\"&", '\n\nZQ', '2026-03-05T18:00:00ZQCANARY', 'ZQ2026-03-05T18:00:00', '2026-03-05T18:00:00+ZQ:00', '2026-03-05ZQ', 'ZQ ' * 200]
        units = [unit(i, junk[i % len(junk)] if i % 3 == 0 else stamp(i), **({'Rude': 2} if i % 5 == 0 else {})) for i in range(60)]
        for incognito in (False, True):
            ctx = build_with(units, {'incognito': int(incognito)})
            self.assertGreater(len(ctx['m']['stretches']), 0)
            for where, text in page_strings(ctx):
                self.assertNotRegex(text, r'ZQ|<|>', where)

    def test_the_input_is_left_as_it_was_given(self):
        units = chat(60, lambda i: {'Rude': 2} if i % 5 == 0 else None, dated=True)
        units.reverse()
        before = repr(units)
        an(units)
        build_with(units)
        self.assertEqual(repr(units), before)


# =============================================================================================================================================
GARBAGE_STAMPS = ['', ' ', 'yesterday', 'N/A', '12:30', '2026-13-45', '2026-02-30T10:00:00', '0000-00-00', '0001-01-01T00:00:00', '9999-12-31T23:59:59', '1970-01-01T00:00:00Z',
                  '2100-01-01', '2026-03-05T18:00:00+99:99', '\x00', 'x' * 500, '١٢٣', '2026-03-05T18:00:00.123456789', '2026-03-05 18:00:00', '2026-03-05T18:00:00 UTC', '1e9',
                  '-1', 'null', None, 12345, 1.5]


def fuzz_stamps(rng, n):
    """Timestamps of every kind: good, offset, date only, none, garbage, a mix, a few moments only, backwards, far future, far past."""
    mode = rng.choice(('none', 'seq', 'seq', 'seq', 'hours', 'days', 'offset', 'dateonly', 'garbage', 'mixed', 'mixed', 'dupes', 'reverse', 'future', 'ancient', 'tight'))
    base = datetime.datetime(2020, 1, 1) + datetime.timedelta(minutes=rng.randint(0, 60 * 24 * 365 * 5))              # 2020 to 2025, so every good date is in the past
    at = lambda i, minutes: (base + datetime.timedelta(minutes=i * minutes))                                          # noqa: E731
    if mode == 'none':
        return [''] * n
    if mode == 'seq':
        return [at(i, 1).isoformat() for i in range(n)]
    if mode == 'hours':
        return [at(i, 60).isoformat() for i in range(n)]
    if mode == 'days':
        return [at(i, 60 * 24).isoformat() for i in range(n)]
    if mode == 'tight':
        return [(base + datetime.timedelta(seconds=i)).isoformat() for i in range(n)]
    if mode == 'offset':
        return [at(i, 7).isoformat() + rng.choice(['Z', '+00:00', '+05:30', '-08:00']) for i in range(n)]
    if mode == 'dateonly':
        return [at(i, 600).date().isoformat() for i in range(n)]
    if mode == 'garbage':
        return [rng.choice(GARBAGE_STAMPS) for _ in range(n)]
    if mode == 'mixed':
        return [at(i, 3).isoformat() if rng.random() < .7 else rng.choice(GARBAGE_STAMPS) for i in range(n)]
    if mode == 'dupes':
        moments = [at(j, 90).isoformat() for j in range(rng.randint(1, 4))]
        return [rng.choice(moments) for _ in range(n)]
    if mode == 'reverse':
        return [at(n - i, 1).isoformat() for i in range(n)]
    if mode == 'future':
        return [datetime.datetime(2100, 1, 1, 0, i % 60).isoformat() for i in range(n)]
    return [rng.choice(['0001-01-01T00:00:00', '1970-01-01T00:00:00', '1989-12-31']) for _ in range(n)]


def fuzz_units(rng):
    """A random table of ratings: sizes 0 to 400, tones of every density and shape (steady, rising, falling, bursts), a few incomplete or bad values, stamps of every kind."""
    n = rng.choice((0, 1, 2, 5, 10, 23, 24, 25, 30, 47, 48, 49, 71, 72, 73, 96, 100, 150, 200, 300, 400)) if rng.random() < .5 else rng.randint(0, 400)
    style = rng.choice(('zero', 'sparse', 'sparse', 'dense', 'weak', 'hot', 'blocks', 'extreme', 'banter', 'fall', 'rise', 'care_fall', 'care_rise', 'burst', 'burst', 'snide_rise'))
    incomplete = rng.choice((0, 0, 0, .03, .2))
    stamps = fuzz_stamps(rng, n)
    out = []
    for i in range(n):
        r = ratings()
        late = i / max(1, n)                                                            # 0 at the start, 1 at the end
        if style == 'sparse' and rng.random() < .3:
            for t in rng.sample(TONES, rng.randint(1, 3)):
                r[t] = rng.randint(1, 3)
        elif style == 'dense':
            r = {t: rng.randint(0, 3) for t in TONES}
        elif style == 'weak':
            r = {t: rng.randint(0, 1) for t in TONES}
        elif style == 'hot' and rng.random() < .5:
            r['Rude'] = rng.randint(2, 3)
            r['Agitated'] = rng.randint(0, 3)
        elif style == 'blocks' and (i // 20) % 2 and rng.random() < .8:
            r['Aggressive'] = rng.randint(1, 3)
        elif style == 'extreme':
            r = dict.fromkeys(TONES, 3)
        elif style == 'banter' and rng.random() < .6:
            r['Sarcastic'] = rng.randint(1, 3)
            r['Funny'] = rng.randint(0, 3)
        elif style == 'fall' and rng.random() < .03 + .5 * (1 - late):
            r['Rude'] = rng.randint(2, 3)
        elif style == 'rise' and rng.random() < .03 + .5 * late:
            r['Disrespectful'] = rng.randint(2, 3)
        elif style == 'care_fall' and rng.random() < .03 + .45 * (1 - late):
            r['Empathetic'] = rng.randint(2, 3)
        elif style == 'care_rise' and rng.random() < .03 + .45 * late:
            r['Empathetic'] = rng.randint(2, 3)
        elif style == 'snide_rise' and rng.random() < .03 + .4 * late:
            r['Sarcastic'] = rng.randint(2, 3)
        elif style == 'burst' and n // 2 <= i < n // 2 + 16 and rng.random() < .8:
            r['Agitated'] = rng.randint(2, 3)
        if rng.random() < incomplete:
            del r[rng.choice(TONES)]                                                    # an incomplete unit
        elif rng.random() < .02:
            r[rng.choice(TONES)] = rng.choice([None, '2', 7, -1, 'x', 2.0])             # a bad value
        out.append({'id': i + 1, 'timestamp': stamps[i], 'ratings': r})
    return out


class FuzzTests(unittest.TestCase):
    """1,500 random tables through analyse, the cards, the alerts, the badges and the whole build: no exception, and the invariants of the spec hold."""

    ROUNDS = 1500

    def test_random_tables(self):
        problems = []

        def check(ok, k, what):
            if not ok:
                problems.append(f'#{k}: {what}')

        for k in range(self.ROUNDS):
            rng = random.Random(100000 + k)
            units = fuzz_units(rng)
            sens = rng.choice(('low', 'medium', 'high', 'bogus', None, ''))
            inc = rng.random() < .5
            complete = [u for u in units if all(t in u['ratings'] for t in TONES)]
            m = measures.analyse(units, sens, today=TODAY, incognito=inc)
            if not complete:
                check(m is None, k, 'no complete unit must give None')
                check(build_with(units) is None, k, 'and no build')
                continue
            check(m is not None, k, 'a complete unit gives measures')
            if m is None:
                continue
            self.invariants(m, units, complete, inc, k, check)
            self.composition(units, m, rng, sens, inc, k, check)
            if len(problems) > 12:
                break
        self.assertEqual(problems[:12], [])

    def invariants(self, m, units, complete, inc, k, check):
        n, o = m['n'], m['overall']
        check(m['n'] == len(complete) and m['incomplete'] == len(units) - len(complete), k, 'n and incomplete add up')
        check(o['n'] == n, k, 'overall n')
        check(m['sensitivity'] in ('low', 'medium', 'high') and m['scale'] in (1.0, 1.5), k, 'sensitivity and scale')
        check(m['incognito'] == inc, k, 'incognito carried')
        for kind in KINDS:
            check(0 <= o[kind] <= n, k, f'overall {kind} within n')
        check(o['hostile'] + o['dig'] <= n and o['heated'] >= o['hostile'] + o['dig'] and o['heated'] <= n, k, 'hostile and dig never overlap, both are heated')
        check(0 <= m['silent'] <= n, k, 'silent within n')
        check(0.0 <= m['health'] <= 100.0, k, 'health in 0..100')
        check(m['basis'] in ('time', 'order'), k, 'basis')
        tl = m['timeline_n']
        check(m['left_off'] >= 0 and tl + m['left_off'] == n if m['basis'] == 'time' else (tl == n and m['left_off'] == 0), k, 'timeline and left off add up')
        check(m['has_series'] == (tl >= 24) and m['verdicts'] == (tl >= 48), k, 'the floors of 24 and 48')
        stretches = m['stretches']
        if m['has_series']:
            sizes = [s['n'] for s in stretches]
            check(len(stretches) == min(24, tl // 8) and sum(sizes) == tl and min(sizes) >= 8 and max(sizes) - min(sizes) <= 1, k, 'stretch sizes')
        else:
            check(stretches == [], k, 'no stretches below 24')
        for i, s in enumerate(stretches):
            check(s['i'] == i and s['label'].startswith(f'Part {i + 1} of {len(stretches)}'), k, 'stretch label')
            check(0.0 <= s['health'] <= 100.0, k, 'stretch health')
            check(s['climb'] >= 0 and (s['counted'] <= m['verdicts']), k, 'climb and counted')
            for kind in KINDS:
                check(0 <= s['counts'][kind] <= s['n'], k, 'stretch count within its size')
                check(0.0 <= s['rates'][kind] <= 100.0 and abs(s['rates'][kind] - 100 * s['counts'][kind] / s['n']) < 1e-6, k, 'stretch rate')
            if inc:
                check(not CLOCK.search(s['label']) and not CLOCK.search(s['axis']), k, f'no clock time under Incognito: {s["axis"]!r} {s["label"]!r}')
        check((m['first3'] is not None) == m['has_series'] and (m['last3'] is not None) == m['has_series'], k, 'thirds exist with the series')
        for key in ('hostile', 'care', 'dig', 'heated', 'health'):
            t = m['trend'][key]
            if not m['verdicts']:
                check(t is None, k, f'no trend before 48 ({key})')
                continue
            check(t is not None and t['call'] in ('rising', 'falling', 'steady') and t['se'] >= 0, k, f'trend {key}')
            if t:
                check((not t['strong']) or t['call'] != 'steady', k, f'strong implies a call ({key})')
                check(0 <= t['first'] <= 100 and 0 <= t['last'] <= 100 and abs(t['delta'] - (t['last'] - t['first'])) < 1e-6, k, f'trend {key} figures')
        eps = m['episodes']
        check(m['verdicts'] or eps == [], k, 'no heated moment before 48')
        counted = {s['i'] for s in stretches if s['counted']}
        covered = set()
        for e in eps:
            covered |= set(range(e['first'], e['last'] + 1))
            check(0 <= e['first'] <= e['last'] < max(1, len(stretches)) and e['heated'] >= 3 and 15 <= e['peak'] <= 100 and 0 <= e['baseline'] <= 100 and 0 <= e['reached'] <= 100,
                  k, f'moment figures {e}')
            check(not CLOCK.search(e['where']) and not CLOCK.search(e['dates']) and not (inc and CLOCK.search(e['label'])), k, 'moment words: no clock time in where or dates, none in the label under Incognito')
        check(covered == counted, k, 'moments cover exactly the counted stretches')
        check(all(b['first'] >= a['last'] + 2 for a, b in zip(eps, eps[1:])), k, 'moments are separate')

    def composition(self, units, m, rng, sens, inc, k, check):
        n = m['n']
        vibe = rng.choice([None, 'ok', 'ok', {'status': 'failed'}, vibe_ok(n, threats=rng.randint(0, 3), harassment=rng.randint(0, 3), insults=rng.randint(0, 9)),
                           {'status': 'ok', 'analyzed_n': n, 'flagged': [{'type': 'hate_speech', 'severity': rng.randint(1, 10), 'index': 1}]}])
        vibe = vibe_ok(n) if vibe == 'ok' else vibe
        cov = rng.choice([cov_full(n), cov_full(n), {'sampled': True, 'total': n * 3, 'scope': n * 3, 'added_since': 0}, {'sampled': False, 'total': n, 'scope': n + 5, 'added_since': 5}])
        row = {'vibe_sensitivity': sens, 'incognito': int(inc), 'model_used': 'qwen2.5:7b'}         # the model of build_with's own row, so an earlier check can be alike
        record = measures.record(m, row, {'total': cov['total'], 'note': cov['sampled']}, 2, '2026-06-01T12:00:00')
        check(store.clean_measure_snapshot(record) == record, k, f'the store keeps the record whole: {record}')
        check(record['model_key'] == fingerprint('qwen2.5:7b'), k, 'the record holds the fingerprint of its model name')
        history = []
        if rng.random() < .4:
            history = [earlier(dict(record, gen=1), rng.choice(('improved', 'slipped', 'unlike', 'setting')))]
            history[0]['n'] = rng.choice((n, max(1, n // 2), n * 2, 47, 48))
            history[0]['total'] = history[0]['n']
            for key in ('hostile_n', 'care_n', 'dig_n', 'heated_n'):                      # a record never counts more messages than it rated
                history[0][key] = min(history[0][key], history[0]['n'])
            if rng.random() < .3:                                                         # ... unless it was damaged on its way here: compare() clamps such a count
                history[0][rng.choice(('hostile_n', 'care_n', 'dig_n', 'heated_n'))] = rng.choice((history[0]['n'] + 2, 10 ** 9, -3))
        ctx = build_with(units, row, vibe, cov, history=history, gen=2)
        check(ctx is not None, k, 'build gives a context')
        if ctx is None:
            return
        cards, found, notes, badges = ctx['cards'], ctx['alerts'], ctx['notes'], ctx['badges']
        check([c['key'] for c in cards] == ['escalation', 'conflict', 'empathy', 'passive'], k, 'cards in order')
        for c in cards:
            check(len(c['series']) == len(m['stretches']) and all(0 <= x <= 100 for x in c['series']), k, f'card series {c["key"]}')
            check(c['tone'] in ('good', 'info', 'warn', 'bad'), k, 'card tone')
            check(c['tone_word'] == measures.TONE_WORDS[c['tone']], k, f'card {c["key"]} says its mark in words')
            if c['key'] != 'escalation':
                check(0 <= c['count'] <= c['of'] == n, k, 'card count within n')
                check(c['level'] in ('Rare', 'Some', 'Often'), k, 'card level word')
                check(n >= 48 or c['tone'] == 'info', k, f'card {c["key"]}: neutral below 48 rated messages')
            elif m['has_series'] and m['verdicts']:
                check(c['count'] == len(m['episodes']) and c['level'] in ('None', 'Small', 'Clear', 'Steep'), k, 'escalation card')
                check(m['episodes'] or not m['opening_heated'] or c['tone'] == 'info', k, 'escalation: a None is neutral when the chat was hot from its first part')
            else:
                check(c['count'] is None and (c['level'], c['word'], c['tone']) == ('Too early', 'early', 'info'), k, 'escalation card: Too early, no count, in the neutral tone')
        silent = 20 * m['silent'] >= 19 * n                                                  # almost no tone was found
        check(not silent or all(c['tone'] == 'info' for c in cards), k, 'almost no tone found: all four marks are neutral')
        check(not silent or ctx['health']['tone'] == 'info', k, 'almost no tone found: the health tile is neutral')
        check(n >= 48 or ctx['health']['tone'] == 'info', k, 'the health tile is neutral below 48 messages')
        check(not silent or not ctx['all_clear'], k, 'almost no tone found: never an all clear, at any number of messages')
        check(ctx['health']['tone_word'] == measures.TONE_WORDS[ctx['health']['tone']], k, 'the health tile says its mark in words')
        check(0.0 <= ctx['health']['score'] <= 100.0 and all(0 <= x <= 100 for x in ctx['health']['series']), k, 'health tile')
        check(ctx['chart_key'] == measures.chart_key(m) and ctx['chart_key'].startswith(CHART_KEY), k, 'the sentence under the charts')
        check(m['basis'] != 'order' or re.search(r'(?i)rated message', ctx['chart_key'][len(CHART_KEY):]), k, 'the key to the charts names the order basis')
        check(ctx['basis_text'].startswith(f'Built from the {n:,} message{"" if n == 1 else "s"} that {"was" if n == 1 else "were"} rated'), k, 'the basis line, in the singular for one message')
        levels = [measures.LEVELS.index(a['level']) for a in found]
        check(levels == sorted(levels), k, 'alerts ordered high, medium, good')
        check(len({a['key'] for a in found}) == len(found), k, 'one alert of a kind')
        for a in found:
            check(a['level'] in measures.LEVELS and a['title'] and a['detail'] and a['base'] and (a['try'] or a['level'] == 'good'), k, f'alert fields {a["key"]}')
        for good, standing in [(r'Hostility is easing', (r'openly hostile', r'Hostile wording is picking up')), (r'Care is growing', (r'Fewer messages show care',)),
                               (r'overall mood improved', (r'overall mood slipped',))]:
            if find(found, good):
                for pattern in standing:
                    check(not find(found, pattern), k, f'good news ({good}) next to an alert about the same measure ({pattern})')
        check(ctx['alert_counts'] == {lv: sum(a['level'] == lv for a in found) for lv in measures.LEVELS}, k, 'alert counts')
        check([b['name'] for b in badges] == BadgeTests.NAMES, k, 'seven badges')
        if n < 30 or m['silent'] * 20 >= 19 * n:
            check(not any(b['earned'] for b in badges), k, 'no badge for under 30 messages or a silent chat')
        if cov['sampled'] or cov['added_since']:
            check(not any(b['earned'] for b in badges if b['name'] in ('Calm waters', 'Cools fast', 'No serious flags')), k, 'absence badges need every message read')
        if not vibe or vibe.get('status') != 'ok':
            check(not [b for b in badges if b['name'] == 'No serious flags' and b['earned']] and 'no_vibe' in {x['key'] for x in notes}, k, 'no Vibe Check: no all clear')
        check(ctx['badges_earned'] == sum(b['earned'] for b in badges), k, 'badges earned')
        check(all(x['about'] == ('alerts' if x['key'] == 'no_vibe' else 'measures') for x in notes) and len({x['key'] for x in notes}) == len(notes), k,
              'every note belongs to one section, and each note is there once')
        check(not {x['key'] for x in notes} & set(REMOVED_NOTES), k, 'no note about checks that are not alike or about a tense start')
        comparison = ctx['comparison']
        check((comparison is None) == (not history), k, 'a comparison with the check before, and only then')
        if comparison:
            check(bool(comparison['why']) == (not comparison['comparable']) and ctx['since']['why'] == comparison['why'] and ctx['since']['comparable'] == comparison['comparable'], k,
                  'the reason two checks are not alike is in the comparison and in the table, and only when they are not alike')
            check(not [x for x in notes if comparison['why'] and x['text'] == comparison['why']], k, 'and in no note')
            check(comparison['comparable'] or all(r['verdict'] == '' for r in ctx['since']['rows']), k, 'no change is called between checks that are not alike')
            before_n = history[0]['n']
            alike = (history[0]['model_key'] == record['model_key'] and history[0]['sensitivity'] == record['sensitivity'] and min(before_n, n) >= 48
                     and max(before_n, n) <= 3 * min(before_n, n))
            check(comparison['comparable'] == alike, k, 'two checks are alike exactly when model and sensitivity match, both rated at least 48 and the sizes are within 3 times')
        if ctx['alert_counts']['high']:
            check(not [b for b in badges if b['name'] == 'No serious flags' and b['earned']], k, 'No serious flags is held back while a High priority alert stands')
        check(set(ctx['texts']) == {'first_check', 'no_badges'} and set(ctx['caveats']) == {'alerts', 'empty', 'measures', 'badges', 'since'}, k, 'the fixed lines')
        vibe_ran = bool(vibe) and vibe.get('status') == 'ok'
        check(isinstance(ctx['all_clear'], bool), k, 'all_clear is a yes or no')
        if ctx['all_clear']:
            check(vibe_ran and not ctx['alert_counts']['high'] and not ctx['alert_counts']['medium'] and 'silent' not in {x['key'] for x in notes}, k,
                  'all_clear needs a Vibe Check that ran, no High or Medium alert and some tone found')
        check(vibe_ran or not ctx['all_clear'], k, 'no Vibe Check, no all clear')
        check(ctx['trail'][-1] == ctx['record'] and len(ctx['trail']) == 1 + len(history), k, 'trail ends with this check')
        check((ctx['since'] is not None) == bool(history), k, 'since only after a second check')
        for where, text in page_strings(ctx):
            for rule, detail in wording_problems(where, text, ctx['m']['incognito'], long_ok=True):
                check(False, k, f'{rule} in {where}: {detail}')


# =============================================================================================================================================
# A small pure-Python version of the synthetic chats of the false-alarm simulation (numpy is not a dependency of the app). Each message is drawn from an
# archetype that fixes its typical 13 ratings, with the correlation a language model shows between the hostile tones; then every zero becomes a weak 1 with
# probability `noise` (a spurious weak rating). These are assumptions, not data.
def _cat(rng, probs):
    x, total = rng.random() * sum(probs), 0.0
    for value, p in enumerate(probs):
        total += p
        if x < total:
            return value
    return len(probs) - 1


def archetype(rng, name):
    r = ratings()
    u = rng.random
    if name == 'P':
        r.update(Polite=1 + _cat(rng, [.55, .35, .10]), Encouraging=int(u() < .12), Empathetic=int(u() < .08), Funny=int(u() < .05))
    elif name == 'S':
        r.update(Empathetic=max(1, _cat(rng, [0, .30, .50, .20])), Polite=_cat(rng, [.2, .5, .3, 0]), Encouraging=_cat(rng, [.5, .3, .2, 0]))
    elif name == 'E':
        r.update(Encouraging=max(1, _cat(rng, [0, .30, .50, .20])), Polite=int(u() < .5), Funny=int(u() < .1))
    elif name == 'F':
        r.update(Funny=max(1, _cat(rng, [0, .35, .45, .20])), Polite=int(u() < .1))
    elif name == 'B':
        r.update(Sarcastic=max(1, _cat(rng, [0, .35, .50, .15])), Funny=_cat(rng, [.35, .40, .25, 0]), Rude=int(u() < .12), Disrespectful=int(u() < .08), Polite=int(u() < .10))
    elif name == 'Q':
        r.update(Sarcastic=2 if u() < .5 else 3, Polite=int(u() < .5), Judgmental=int(u() < .3), Disrespectful=int(u() < .15), Funny=int(u() < .1))
    elif name == 'A':
        r.update(Agitated=max(1, _cat(rng, [0, .5, .4, .1])), Neurotic=int(u() < .25), Rude=int(u() < .10), Sarcastic=int(u() < .10))
    elif name == 'R':
        level = 1 + _cat(rng, [.30, .45, .25])
        dec = max(level - 1, 0)
        v = u()
        aggressive = 0 if v < .25 else dec if v < .75 else level
        v = u()
        r.update(Rude=level if u() < .9 else dec, Disrespectful=level if u() < .75 else dec, Aggressive=aggressive, Judgmental=0 if v < .45 else 1 if v < .80 else 2,
                 Agitated=dec if u() < .6 else 0, Sarcastic=(1 if u() < .6 else 2) if u() < .2 else 0, Ignorant=int(u() < .08), Egocentric=int(u() < .08))
    elif name == 'G':
        r.update(Ignorant=(1 if u() < .6 else 2) if u() < .5 else 0, Egocentric=(1 if u() < .6 else 2) if u() < .5 else 0, Neurotic=int(u() < .3), Judgmental=int(u() < .2))
    return r


CALM_MIX = {'N': .30, 'P': .30, 'S': .12, 'E': .12, 'F': .10, 'A': .02, 'B': .02, 'G': .02}
OUTBURST_MIX = {'R': .55, 'A': .20, 'B': .10, 'Q': .05, 'N': .10}


def synthetic_chat(seed, n=300, burst=None, noise=0.015):
    """n messages from the calm mixture (a community that does not change); the messages in range `burst` come from the outburst mixture instead."""
    rng = random.Random(seed)
    units = []
    for i in range(n):
        mix = OUTBURST_MIX if burst is not None and i in burst else CALM_MIX
        name = rng.choices(list(mix), weights=list(mix.values()))[0]
        r = archetype(rng, name)
        r = {t: (1 if v == 0 and rng.random() < noise else v) for t, v in r.items()}
        units.append(unit(i, **r))
    return units


class FalseAlarmTests(unittest.TestCase):
    """The simulation behind the cut-offs, small enough to run every time: calm chats that do not change must hardly ever raise a Medium or High alert, and a
    planted outburst must usually be seen. Fixed seeds, so the answer is the same on every run."""

    CHATS = 150

    def raised(self, units):
        m = an(units)
        found, _ = measures.alerts(m, vibe_ok(len(units)), None, cov_full(len(units)))
        return found

    def test_the_synthetic_chats_look_like_calm_chats(self):
        o = an(synthetic_chat(1))['overall']
        self.assertLess(o['hostile'] / o['n'], .06)
        self.assertGreater(o['care'] + o['polite'], 40)
        self.assertGreater(o['silent'], 20)

    def test_stationary_calm_chats_of_300_messages_rarely_raise_a_medium_or_high_alert(self):
        alarmed = []
        for seed in range(self.CHATS):
            found = self.raised(synthetic_chat(seed))
            if [a for a in found if a['level'] in ('high', 'medium')]:
                alarmed.append((seed, sorted({(a['level'], a['key']) for a in found})))
        self.assertLessEqual(len(alarmed), int(.06 * self.CHATS), f'{len(alarmed)} of {self.CHATS} calm chats raised an alert: {alarmed[:6]}')

    def test_calm_chats_are_rarely_called_rising_or_falling_in_any_one_measure(self):
        # MEASURES.md: "a trend was called in about 5% or fewer" of the communities that do not change. 150 chats: 10% is room for the luck of the draw.
        calls = collections.Counter()
        for seed in range(self.CHATS):
            m = an(synthetic_chat(1000 + seed))
            calls.update(key for key, t in m['trend'].items() if t and t['call'] != 'steady')
        self.assertTrue(all(count <= int(.10 * self.CHATS) for count in calls.values()), dict(calls))

    def test_a_planted_outburst_of_20_messages_in_the_middle_raises_the_heated_moment_alert_in_most_chats(self):
        seen = 0
        missed = []
        for seed in range(self.CHATS):
            found = self.raised(synthetic_chat(2000 + seed, burst=range(140, 160)))
            if find(found, r'heated up'):
                seen += 1
            else:
                missed.append(seed)
        self.assertGreaterEqual(seen, int(.85 * self.CHATS), f'seen in {seen} of {self.CHATS}; missed {missed[:8]}')


class FinalTidyTests(unittest.TestCase):
    """The last small rules: one message reads in the singular, an older record still compares by its model name, the chart key says UTC only when a
    label shows a time, and a check too short to judge saves no count of heated moments."""

    @staticmethod
    def rec(**kw):
        base = {'created_at': '2026-03-01T10:00:00', 'gen': 1, 'n': 100, 'total': 100, 'sampled': 0, 'basis': 'time', 'model': 'qwen2.5:7b', 'sensitivity': 'medium',
                'health': 55.0, 'health_sd': 10.0, 'hostile_n': 5, 'care_n': 10, 'dig_n': 3, 'heated_n': 8}
        base.update(kw)
        return base

    def test_one_message_reads_in_the_singular(self):
        self.assertEqual(measures._messages(1, 1, 'were openly hostile.'), '1 of 1 message was openly hostile.')
        self.assertEqual(measures._messages(0, 1, 'were openly hostile.'), '0 of 1 message was openly hostile.')
        self.assertEqual(measures._messages(0, 5, 'were openly hostile.'), 'None of the 5 messages were openly hostile.')
        self.assertEqual(measures._messages(2, 5, 'were openly hostile.'), '2 of 5 messages were openly hostile.')

    def test_a_record_saved_before_the_fingerprint_still_compares_with_its_model_name(self):
        old = self.rec()                                                          # no model_key, as an earlier build saved it
        same = dict(self.rec(gen=2), model_key=measures._fingerprint('qwen2.5:7b'))
        other = dict(self.rec(gen=2, model='another-model'), model_key=measures._fingerprint('another-model'))
        self.assertTrue(measures.compare(same, old)['comparable'])
        self.assertFalse(measures.compare(other, old)['comparable'])

    def test_two_model_names_the_store_cannot_keep_are_still_told_apart(self):
        a, b = 'x' * 130, 'y' * 130
        first = dict(self.rec(model=''), model_key=measures._fingerprint(a))
        again = dict(self.rec(gen=2, model=''), model_key=measures._fingerprint(a))
        other = dict(self.rec(gen=2, model=''), model_key=measures._fingerprint(b))
        self.assertTrue(measures.compare(again, first)['comparable'])
        self.assertFalse(measures.compare(other, first)['comparable'])

    def test_the_chart_key_says_utc_only_when_a_label_shows_a_time(self):
        base = {'basis': 'time', 'utc': True, 'clock': True}
        self.assertIn('Times are UTC.', measures.chart_key(base))
        self.assertNotIn('UTC', measures.chart_key(dict(base, clock=False)))
        self.assertNotIn('UTC', measures.chart_key(dict(base, utc=False)))
        self.assertIn('rated message numbers', measures.chart_key({'basis': 'order'}))
        self.assertTrue(measures.chart_key(base).startswith('Lines show messages per 100.'))

    def test_a_check_too_short_to_judge_saves_no_count_of_heated_moments(self):
        zero = {a: 0 for a in measures.analysis.ATTRIBUTES}
        m = measures.analyse([{'id': i, 'timestamp': '', 'ratings': zero} for i in range(1, 11)])
        rec = measures.record(m)
        self.assertNotIn('episodes', rec)
        self.assertNotIn('peak', rec)
        self.assertEqual(rec, measures.store.clean_measure_snapshot(rec))          # and what is saved is exactly what is here



class ReviewFixTests(unittest.TestCase):
    """The fixes of the final review, each with its own case: a long chat names its year, a heated moment that spans parts says how many, the strongest of several
    heated moments is the one the alert describes, a level alert counts the messages with no date, the health score is rounded once, a check keeps the date it ran,
    two checks compare health only when both kept their spread, Calm waters needs few heated messages, and the titles of the comparison alerts say what they compare."""

    LONG = datetime.datetime(2024, 1, 5, 18, 0)

    def spaced(self, units, days):
        """The same messages, `days` apart from 5 January 2024 (a date that is always in the past)."""
        return [dict(u, timestamp=(self.LONG + datetime.timedelta(days=days * i)).isoformat()) for i, u in enumerate(units)]

    def test_a_chat_of_more_than_300_days_names_the_year_in_every_label(self):
        long = an(self.spaced(chat(96), 4))                                             # 380 days
        self.assertEqual(long['basis'], 'time')
        self.assertTrue(long['stretches'])
        for part in long['stretches']:
            self.assertRegex(part['label'], r', 20\d\d', part['label'])
        short = an(self.spaced(chat(96), 2))                                            # 190 days
        for part in short['stretches']:
            self.assertNotRegex(part['label'], r'\b20\d\d\b', part['label'])
        # the edge: the year is named when the first message and the last are more than 300 days apart
        across = lambda days: [dict(u, timestamp=(self.LONG + datetime.timedelta(days=days * i / 95)).isoformat()) for i, u in enumerate(chat(96))]      # noqa: E731
        self.assertNotRegex(' '.join(s['label'] for s in an(across(300))['stretches']), r'\b20\d\d\b')
        self.assertRegex(an(across(301))['stretches'][0]['label'], r', 2024')

    def test_where_a_heated_moment_fell_names_the_year_in_a_long_chat(self):
        counts = [0] * 24
        counts[8:11], counts[11] = [1, 1, 1], 9
        found = scan(self.spaced(part_chat(counts, 20, HEAT), 1))[1]                    # 480 days, a day per message
        (alert,) = find(found, 'heated')
        self.assertRegex(alert['when'], r'\(?.*, 20\d\d')
        near = scan(self.spaced(part_chat(counts, 20, HEAT), 0.5))[1]                   # 240 days
        self.assertNotRegex(find(near, 'heated')[0]['when'], r'\b20\d\d\b')

    def test_a_heated_moment_in_one_part_or_in_several_says_so(self):
        one = [0] * 24
        one[8:11], one[11] = [1, 1, 1], 9
        (alert,) = find(scan(part_chat(one, 20, HEAT))[1], 'heated')
        self.assertRegex(alert['detail'], r'^Around the middle, 9 of 20 messages in one part of the chat were heated\. Before that, about 5 in 100 were\.$')
        self.assertEqual(alert['base'], 'Based on 20 messages in that part, 9 of them heated.')
        two = [0] * 24
        two[8:11], two[11], two[12] = [1, 1, 1], 10, 10
        (alert,) = find(scan(part_chat(two, 20, HEAT))[1], 'heated')
        self.assertRegex(alert['detail'], r'^Around the middle, 20 of 40 messages in 2 parts in a row of the chat were heated\.')
        self.assertEqual(alert['base'], 'Based on 40 messages in those parts, 20 of them heated.')

    def test_with_two_heated_moments_the_alert_describes_the_stronger_one_whichever_comes_first(self):
        def two(first, second):
            counts = [0] * 24
            counts[3:6], counts[6] = [1, 1, 1], first
            counts[14:17], counts[17] = [1, 1, 1], second
            return part_chat(counts, 20, HEAT)
        found = scan(two(6, 9))[1]
        self.assertEqual([(a['level'], a['title']) for a in found if a['level'] in ('high', 'medium')], [('high', 'The mood heated up fast')])
        self.assertTrue(found[0]['detail'].startswith('About two thirds of the way in, 9 of 20 messages in one part'), found[0]['detail'])
        found = scan(two(9, 6))[1]
        self.assertEqual([(a['level'], a['title']) for a in found if a['level'] in ('high', 'medium')], [('high', 'The mood heated up fast')])
        self.assertTrue(found[0]['detail'].startswith('About a third of the way in, 9 of 20 messages in one part'), found[0]['detail'])
        found = scan(two(6, 6))[1]
        self.assertEqual([a['level'] for a in find(found, 'heated')], ['medium'], 'two moderate moments are one Medium priority alert')

    def test_a_level_alert_counts_the_messages_that_have_no_date(self):
        units = lively(chat(100))
        for u in units[60:]:                                                          # the last 40 have no date, and half of them are hostile
            u['timestamp'] = ''
        for u in units[60:80]:
            u['ratings']['Rude'] = 2
        m, found, _ = scan(units)
        self.assertEqual((m['basis'], m['left_off'], m['n']), ('time', 40, 100))
        self.assertEqual([(a['level'], a['title'], a['base']) for a in find(found, 'openly hostile')],
                         [('medium', 'Some messages were openly hostile', 'Based on 100 messages across the check.')])
        dated = lively(chat(100))                                                      # the same messages with dates: the latest part is read, and says so
        for u in dated[60:80]:
            u['ratings']['Rude'] = 2
        again = find(scan(dated)[1], 'openly hostile')
        self.assertEqual(len(again), 1)
        self.assertIn('latest part of the chat', again[0]['base'])

    def test_the_health_score_is_rounded_to_three_places_once_and_every_surface_reads_that_number(self):
        m = an(chat(97, lambda i: {'Polite': 2} if i % 3 == 0 else {'Rude': 1} if i % 7 == 0 else None))
        self.assertEqual(m['health'], round(m['health'], 3))
        self.assertNotEqual(m['health'], round(m['health']), 'a real score, not a whole number')
        self.assertEqual(measures.health_tile(m)['score'], m['health'])
        self.assertEqual(measures.record(m)['health'], m['health'])
        self.assertEqual(build_with(chat(97, lambda i: {'Polite': 2} if i % 3 == 0 else {'Rude': 1} if i % 7 == 0 else None))['record']['health'], m['health'])

    def test_the_card_charts_draw_the_exact_rates_not_rounded_ones(self):
        m = an(hostile_chat(96, 5))                                                     # 12 parts of 8: a part with one hostile message is 12.5 in 100
        card = {c['key']: c for c in measures.cards(m)}['conflict']
        self.assertEqual(card['series'], [s['rates']['hostile'] for s in m['stretches']])
        self.assertIn(12.5, card['series'])
        tile = measures.health_tile(m)
        self.assertEqual(tile['series'], [s['health'] for s in m['stretches']])

    def test_a_check_keeps_the_date_it_ran_and_not_the_day_it_is_viewed(self):
        ran = '2026-04-01T09:30:00'
        units = lively(neutral(96))
        again = build_with(units, history=[saved(96, gen=2, created_at=ran)], gen=2)
        self.assertEqual(again['record']['created_at'], ran)
        self.assertEqual(again['trail'][-1]['created_at'], ran)
        fresh = build_with(units, history=[saved(96, gen=1, created_at=ran)], gen=2)    # other evidence: a new check, dated now
        self.assertNotEqual(fresh['record']['created_at'], ran)
        self.assertEqual([r['created_at'] for r in fresh['trail']][0], ran)

    def test_health_is_compared_only_when_both_checks_kept_their_spread(self):
        before, now = saved(100, health=40.0, gen=1), saved(100, health=60.0, gen=2)
        self.assertEqual(sorted(measures.compare(now, before)['changes']), ['conflict', 'empathy', 'health', 'heated', 'passive'])
        without = lambda rec: {k: v for k, v in rec.items() if k != 'health_sd'}      # noqa: E731
        for cur, prev in ((now, without(before)), (without(now), before), (without(now), without(before))):
            got = measures.compare(cur, prev)
            self.assertTrue(got['comparable'])
            self.assertEqual(sorted(got['changes']), ['conflict', 'empathy', 'heated', 'passive'])
            rows = measures.since_table(got, cur)['rows']
            self.assertEqual([r['verdict'] for r in rows][0], '', 'no call on the health score')

    def test_calm_waters_needs_fewer_than_10_heated_messages_in_100_as_well(self):
        def calm(sarcastic):
            ctx = build_with(lively(hostile_chat(100, sarcastic, 'Sarcastic')))        # sarcasm with nothing hostile: heated, not hostile
            self.assertEqual(ctx['m']['episodes'], [])
            return {b['key']: b for b in ctx['badges']}['calm']
        self.assertTrue(calm(9)['earned'])
        self.assertFalse(calm(10)['earned'])
        self.assertRegex(calm(9)['how'], r'fewer than 10 in 100 messages heated')

    def test_the_comparison_titles_say_what_they_compare(self):
        m = an(neutral(200))

        def titles(now, before):
            found, _ = measures.alerts(m, vibe_ok(m['n']), measures.compare(now, before), cov_full(m['n']))
            return [a['title'] for a in found]
        self.assertIn('Hostile wording is higher than at your last check', titles(saved(2000, hostile_n=400), saved(2000, hostile_n=100, gen=0)))
        self.assertIn('Hostility is lower than at your last check', titles(saved(2000, hostile_n=100), saved(2000, hostile_n=400, gen=0)))
        for title in ('Hostile wording is higher than at your last check', 'Hostility is lower than at your last check'):
            self.assertNotRegex(title, r'(?i)picking up|easing', 'the titles of the two kinds of alert are not the same words')


class MembersViewTests(unittest.TestCase):
    """The size of the group beside the checks: what each check was made with, how it changed, and a caution when the group changed a lot."""

    @staticmethod
    def trail(*counts):
        return [saved(100, gen=i, members=c) if c else saved(100, gen=i) for i, c in enumerate(counts)]

    def test_nothing_is_said_when_this_check_names_no_members(self):
        self.assertIsNone(measures.members_view([]))
        self.assertIsNone(measures.members_view(self.trail(200, None)))
        self.assertIsNone(measures.members_view(self.trail(None)))

    def test_a_first_count_says_so(self):
        got = measures.members_view(self.trail(None, 250))
        self.assertEqual(got, {'now': 250, 'before': None, 'text': 'Members at this check: 250. Later checks show how the group changes.', 'note': ''})

    def test_the_change_since_the_last_count_is_said_in_plain_words(self):
        for counts, text in [((200, 210), 'Members: 200 at your last check, 210 now (10 more).'), ((200, 190), 'Members: 200 at your last check, 190 now (10 fewer).'),
                             ((200, 200), 'Members: 200 at your last check, 200 now (the same number).'), ((1200, 1500), 'Members: 1,200 at your last check, 1,500 now (300 more).'),
                             ((200, None, 230), 'Members: 200 at an earlier check, 230 now (30 more).')]:
            with self.subTest(counts=counts):
                self.assertEqual(measures.members_view(self.trail(*counts))['text'], text)

    def test_a_quarter_or_more_change_adds_a_caution_and_less_does_not(self):
        caution = 'The group changed by a quarter or more, so part of any change may come from who is in it.'
        for before, now, said in [(100, 125, True), (100, 124, False), (100, 80, True), (100, 81, False), (100, 100, False), (40, 400, True), (400, 40, True)]:
            with self.subTest(before=before, now=now):
                self.assertEqual(measures.members_view(self.trail(before, now))['note'], caution if said else '')

    def test_the_sentences_follow_the_house_wording_rules(self):
        for counts in ((None, 250), (200, 260), (200, 100), (200, 200), (200, None, 400)):
            view = measures.members_view(self.trail(*counts))
            for text in (view['text'], view['note']):
                for sentence in sentences(text):
                    self.assertLessEqual(len(sentence.split()), MAX_WORDS, sentence)
                for rule in FORBIDDEN:
                    self.assertIsNone(rule.search(text), (rule.pattern, text))

    def test_build_carries_the_view_and_the_record_keeps_the_members_of_its_check(self):
        units = lively(neutral(96))
        ctx = build_with(units, a={'members': 250}, history=[saved(96, gen=1, members=200)], gen=2)
        self.assertEqual(ctx['record']['members'], 250)
        self.assertEqual(ctx['members']['text'], 'Members: 200 at your last check, 250 now (50 more).')
        self.assertEqual([r.get('members') for r in ctx['trail']], [200, 250])
        self.assertIsNone(build_with(units, history=[saved(96, gen=1, members=200)], gen=2)['members'], 'no count for this check: nothing is said')
        self.assertNotIn('members', build_with(units)['record'])


# @@APPEND@@
