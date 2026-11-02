"""A false-alarm and detection simulation for Measures over time (MEASURES.md, "How it was checked"; DEV.md says when to run it).

It makes up rating tables (13 tones for each message) for six communities that do not change (calm, support, banter, banter_pure, neutral, hostile)
and for six events planted in a calm chat (outburst, spat5, blip, slow_burn, start_burst, end_burst), at 24, 76, 300 and 1000 messages. It runs the
real `measures.analyse`, `measures.alerts` and `measures.badges` on every table, as the report does, and prints one table with four shares of runs,
in percent: a Medium or High alert, a heated moment, the heated-moment alert, and a rising or falling call.

    python3 tools/simulate_measures.py                                       # 100 runs per case and size, medium sensitivity, about half a minute
    python3 tools/simulate_measures.py --reps 200 --sizes 76,300 --sensitivity low
    python3 tools/simulate_measures.py --cases calm,outburst --reps 20       # a quick look
    python3 tools/simulate_measures.py --markdown                            # the same numbers as Markdown tables for MEASURES.md

Every message is one of a few types with fixed odds for its 13 ratings, the hostile tones rated together as a model does. Then every 0 has a 1.5%
chance of becoming a weak 1, as a rater's stray reading. The types, the mixes and the events are our assumptions, not data: read the shares as what
a cut-off costs on chats like these, not as what real chats do. Vibe Check is taken to have flagged nothing serious, so its one-message alert never
fires here.

Pure Python: the standard library and the app's own modules, so there is nothing to install (no numpy). A run's seed comes from --seed, the case, the
size and the run number, so the same command prints the same table on every computer, and `--reps 20` gives the first 20 runs of `--reps 100`.
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import itertools
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))           # the app's modules sit one folder up

import analysis  # noqa: E402
import measures  # noqa: E402

SIZES = (24, 76, 300, 1000)
MAX_SIZE = 5000                                         # the app keeps at most this many messages in one analysis
NOISE = 0.015                                           # the chance that a 0 becomes a weak 1
T0 = datetime.datetime(2026, 3, 5, 18, 0)               # the first message; each next one comes a minute later
TODAY = datetime.date(2026, 6, 1)                       # "today" for measures.analyse, so that no result depends on the clock
TONES = analysis.ATTRIBUTES


# ---- the made-up chats ----------------------------------------------------------------------------
def odds(*weights):
    """Cumulative odds for drawing 0, 1, 2 ... with these weights (they need not add up to 1)."""
    total = float(sum(weights))
    return tuple(1.0 if c > 1 - 1e-9 else c for c in (w / total for w in itertools.accumulate(weights)))


def draw(rng, cum) -> int:
    return bisect.bisect_right(cum, rng.random())


# the odds of a rating 0, 1, 2 and 3 for a tone, where a tone is not simply present or absent
POLITE, SUPPORTIVE, ENCOURAGING, FUNNY = odds(.55, .35, .10), odds(0, .30, .50, .20), odds(0, .30, .50, .20), odds(0, .35, .45, .20)
MILD_POLITE, MILD_ENCOURAGING, BANTER_FUNNY, TRASH_FUNNY = odds(.2, .5, .3, 0), odds(.5, .3, .2, 0), odds(.35, .40, .25, 0), odds(.4, .35, .25, 0)
SARCASM, AGITATION, HOSTILITY = odds(0, .35, .50, .15), odds(0, .5, .4, .1), odds(.30, .45, .25)


def message(rng, kind) -> dict:
    """The 13 ratings of one message of this type: P polite, S supportive, E encouraging, F funny, B friendly banter with sarcasm, Rl light trash
    talk, Q passive-aggressive snark, A agitated, R openly hostile, G grumbling, N no tone at all, R3 one very hostile message."""
    r = dict.fromkeys(TONES, 0)
    u = rng.random
    if kind == 'P':
        r.update(Polite=1 + draw(rng, POLITE), Encouraging=int(u() < .12), Empathetic=int(u() < .08), Funny=int(u() < .05))
    elif kind == 'S':
        r.update(Empathetic=max(1, draw(rng, SUPPORTIVE)), Polite=draw(rng, MILD_POLITE), Encouraging=draw(rng, MILD_ENCOURAGING))
    elif kind == 'E':
        r.update(Encouraging=max(1, draw(rng, ENCOURAGING)), Polite=int(u() < .5), Funny=int(u() < .1))
    elif kind == 'F':
        r.update(Funny=max(1, draw(rng, FUNNY)), Polite=int(u() < .1))
    elif kind == 'B':
        r.update(Sarcastic=max(1, draw(rng, SARCASM)), Funny=draw(rng, BANTER_FUNNY), Rude=int(u() < .12), Disrespectful=int(u() < .08),
                 Polite=int(u() < .10))
    elif kind == 'Rl':
        r.update(Rude=1 if u() < .6 else 2, Funny=draw(rng, TRASH_FUNNY), Disrespectful=int(u() < .4),
                 Sarcastic=(1 if u() < .5 else 2) if u() < .5 else 0)
    elif kind == 'Q':
        r.update(Sarcastic=2 if u() < .5 else 3, Polite=int(u() < .5), Judgmental=int(u() < .3), Disrespectful=int(u() < .15), Funny=int(u() < .1))
    elif kind == 'A':
        r.update(Agitated=max(1, draw(rng, AGITATION)), Neurotic=int(u() < .25), Rude=int(u() < .10), Sarcastic=int(u() < .10))
    elif kind == 'R':
        level = 1 + draw(rng, HOSTILITY)                # the hostile tones move together: a message rated Rude 3 is usually Disrespectful 3 too
        lower = level - 1
        v = u()
        aggressive = 0 if v < .25 else lower if v < .75 else level
        v = u()
        r.update(Rude=level if u() < .9 else lower, Disrespectful=level if u() < .75 else lower, Aggressive=aggressive,
                 Judgmental=0 if v < .45 else 1 if v < .80 else 2, Agitated=lower if u() < .6 else 0,
                 Sarcastic=(1 if u() < .6 else 2) if u() < .2 else 0, Ignorant=int(u() < .08), Egocentric=int(u() < .08))
    elif kind == 'G':
        r.update(Ignorant=(1 if u() < .6 else 2) if u() < .5 else 0, Egocentric=(1 if u() < .6 else 2) if u() < .5 else 0,
                 Neurotic=int(u() < .3), Judgmental=int(u() < .2))
    elif kind == 'R3':
        r.update(Rude=3, Disrespectful=3, Aggressive=2, Agitated=2)
    return r


def mix(weights):
    """(types, cumulative odds) for a mix of message types."""
    return tuple(weights), odds(*weights.values())


def pick(rng, mixture) -> str:
    return mixture[0][draw(rng, mixture[1])]


# polite and warm: in 100 messages about 17 polite, 11 encouraging, 8 caring and 2 heated
CALM ={'N': .30, 'P': .30, 'S': .12, 'E': .12, 'F': .10, 'A': .02, 'B': .02, 'G': .02}
# care and understanding clearly shown in about 1 message in 5
SUPPORT ={'S': .28, 'P': .22, 'E': .10, 'N': .26, 'A': .06, 'G': .04, 'F': .02, 'B': .01, 'R': .01}
# gaming talk: in 100 messages about 5 openly hostile, 6 sarcastic and 13 heated
BANTER ={'N': .38, 'B': .10, 'F': .16, 'P': .10, 'E': .06, 'S': .02, 'A': .05, 'Rl': .08, 'R': .02, 'G': .03}
# friendly sarcasm and jokes, no trash talk: in 100 messages about 5 sarcastic and 12 funny
BANTER_PURE ={'N': .38, 'B': .10, 'P': .20, 'F': .15, 'E': .08, 'S': .05, 'A': .02, 'G': .02}
# no tone at all
NEUTRAL = {'N': 1.0}
# open hostility all along: in 100 messages about 27 openly hostile and 45 heated
HOSTILE ={'R': .35, 'A': .15, 'B': .10, 'Q': .05, 'G': .05, 'N': .22, 'P': .04, 'F': .03, 'S': .005, 'E': .005}
# what a planted outburst is made of, and where a slow burn ends
OUTBURST = {'R': .55, 'A': .20, 'B': .10, 'Q': .05, 'N': .10}
HOT = {'R': .35, 'A': .15, 'B': .10, 'Q': .05, 'G': .05, 'N': .20, 'P': .05, 'F': .03, 'S': .01, 'E': .01}

COMMUNITIES = {'calm': CALM, 'support': SUPPORT, 'banter': BANTER, 'banter_pure': BANTER_PURE, 'neutral': NEUTRAL, 'hostile': HOSTILE}
EVENTS = ('outburst', 'spat5', 'blip', 'slow_burn', 'start_burst', 'end_burst')
MIXES = {name: mix(weights) for name, weights in COMMUNITIES.items()}
OUTBURST_MIX = mix(OUTBURST)


def kinds_of(rng, case, n) -> list:
    """The type of each of the n messages of a case. An event is planted in a calm chat: an outburst is 20 messages in the middle, start_burst and
    end_burst are 20 messages at the start or at the end, spat5 is 5 openly hostile messages in the middle, blip is one very hostile message in the
    middle, and slow_burn drifts from the calm mix to a hot one over the whole chat."""
    if case in MIXES:
        return [pick(rng, MIXES[case]) for _ in range(n)]
    if case == 'slow_burn':
        blend = lambda t: mix({k: (1 - t) * CALM.get(k, 0) + t * HOT.get(k, 0) for k in {**CALM, **HOT}})      # noqa: E731
        return [pick(rng, blend(i / max(1, n - 1))) for i in range(n)]
    kinds, middle = [pick(rng, MIXES['calm']) for _ in range(n)], n // 2
    burst = {'outburst': range(max(0, middle - 10), min(n, middle + 10)), 'start_burst': range(min(n, 20)), 'end_burst': range(max(0, n - 20), n)}
    for i in burst.get(case, ()):
        kinds[i] = pick(rng, OUTBURST_MIX)
    if case == 'spat5':
        for i in range(max(0, middle - 2), min(n, middle + 3)):
            kinds[i] = 'R'
    elif case == 'blip':
        kinds[middle] = 'R3'
    return kinds


def table_of(rng, case, n) -> list:
    """n rating dicts. Every 0 may become a weak 1, except in the neutral community, which has no tone at all."""
    rows = [message(rng, kind) for kind in kinds_of(rng, case, n)]
    if case != 'neutral':
        for r in rows:
            for tone in TONES:
                if r[tone] == 0 and rng.random() < NOISE:
                    r[tone] = 1
    return rows


# ---- what the real code makes of them -------------------------------------------------------------
def observe(rows, sensitivity) -> dict:
    """Runs measures.analyse, alerts and badges on a table, as the report does, and says what came out."""
    n = len(rows)
    units = [{'id': i + 1, 'timestamp': (T0 + datetime.timedelta(minutes=i)).isoformat(), 'ratings': r} for i, r in enumerate(rows)]
    m = measures.analyse(units, sensitivity, today=TODAY)
    vibe = {'status': 'ok', 'analyzed_n': n, 'toxicity_types': {}, 'flagged': []}              # a Vibe Check that flagged nothing serious
    cov = {'sampled': False, 'total': n, 'scope': n, 'added_since': 0}
    found, _notes = measures.alerts(m, vibe, None, cov)
    badges, _note = measures.badges(m, vibe, cov)
    assert len(badges) == len(measures.BADGES), 'badges() must judge every badge'
    lines = [m['trend'][kind] for kind in ('hostile', 'care', 'dig', 'health')]                # what the cards and the health tile call rising or falling
    return {'alert': any(a['level'] in ('high', 'medium') for a in found), 'moment': bool(m['episodes']),
            'heated_alert': any(a['key'] == 'heated' for a in found), 'trend': any(t is not None and t['call'] != 'steady' for t in lines)}


def run_cell(case, n, reps, sensitivity, seed) -> dict:
    """How many of `reps` runs of this case and size came out each way."""
    counts = dict.fromkeys(('alert', 'moment', 'heated_alert', 'trend'), 0)
    for rep in range(reps):
        seen = observe(table_of(random.Random(f'{seed}/{case}/{n}/{rep}'), case, n), sensitivity)
        for key, yes in seen.items():
            counts[key] += yes
    return counts


# ---- the table ------------------------------------------------------------------------------------
# the four columns: (long name for Markdown, short name for the terminal, key of observe())
METRICS = (('Medium or High alert', 'Medium/High alert', 'alert'), ('Heated moment', 'Heated moment', 'moment'),
           ('Heated-moment alert', 'Heated-moment alert', 'heated_alert'), ('Rising or falling call', 'Rising/falling call', 'trend'))
BLOCKS = (('Communities that do not change', 'Community', tuple(COMMUNITIES)), ('Events planted in a calm chat', 'Event', EVENTS))
LEGEND = ('Medium/High alert    an alert of Medium or High priority, of any kind',
          'Heated moment        a heated moment passed the chance test (the Escalation card shows it)',
          'Heated-moment alert  the alert "The mood heated up fast"',
          'Rising/falling call  Conflict, Empathy, Passive-aggressive or the health score is called rising or falling')
CAUTION = ('The chats are made up: the types, the mixes and the events are our assumptions, not data. '
           'Read the shares as what a cut-off costs on chats like these.')


def percent(count, reps) -> int:
    return int(100 * count / reps + 0.5)


def text_block(title, label, cases, results, sizes, reps) -> list:
    w = max(3, max(len(str(s)) for s in sizes))
    cells = lambda values: ' '.join(str(v).rjust(w) for v in values)      # noqa: E731
    widths = [max(len(short), len(cells(sizes))) for _, short, _ in METRICS]
    side = max(len(c) for c in (label,) + tuple(cases)) + 2
    lines = [title, ' ' * side + '   '.join(short.ljust(wd) for (_, short, _), wd in zip(METRICS, widths)),
             label.ljust(side) + '   '.join(cells(sizes).ljust(wd) for wd in widths)]
    for case in cases:
        shares = (cells(percent(results[case, s][key], reps) for s in sizes).ljust(wd) for (_, _, key), wd in zip(METRICS, widths))
        lines.append(case.ljust(side) + '   '.join(shares))
    return [line.rstrip() for line in lines]


def markdown_block(label, cases, results, sizes, reps) -> list:
    at = ' / '.join(f'{s:,}' for s in sizes)
    lines = [f'| {label} (percent of runs at {at} messages) | ' + ' | '.join(long for long, _, _ in METRICS) + ' |', '|---' * (len(METRICS) + 1) + '|']
    for case in cases:
        shares = (' / '.join(str(percent(results[case, s][key], reps)) for s in sizes) for _, _, key in METRICS)
        lines.append(f'| {case} | ' + ' | '.join(shares) + ' |')
    return lines


def report(results, cases, sizes, reps, sensitivity, seed, as_markdown) -> str:
    out = []
    if not as_markdown:
        out += ['Felixa Community Health: simulation of Measures over time',
                f'Made-up rating tables, sensitivity {sensitivity}, {reps} run{"" if reps == 1 else "s"} per case and size, seed {seed}. '
                'Each cell is the share of runs, in percent.', '']
    for title, label, names in BLOCKS:
        shown = [c for c in names if c in cases]
        if shown:
            block = markdown_block(label, shown, results, sizes, reps) if as_markdown else text_block(title, label, shown, results, sizes, reps)
            out += block + ['']
    if not as_markdown:
        out += list(LEGEND) + ['', CAUTION]
    return '\n'.join(out).rstrip() + '\n'


# ---- the command line -----------------------------------------------------------------------------
def main(argv=None) -> int:
    known = list(COMMUNITIES) + list(EVENTS)
    ap = argparse.ArgumentParser(description='Simulates Measures over time on made-up chats and prints how often each alert and call comes out.')
    ap.add_argument('--reps', type=int, default=100, help='runs per case and size (default %(default)s)')
    ap.add_argument('--sizes', default=','.join(map(str, SIZES)), help='messages per chat, separated by commas (default %(default)s)')
    ap.add_argument('--sensitivity', choices=('low', 'medium', 'high'), default='medium', help='the setting of the check (default %(default)s)')
    ap.add_argument('--cases', help='only these cases, separated by commas (default all of: ' + ', '.join(known) + ')')
    ap.add_argument('--seed', type=int, default=1, help='a different seed gives other chats, for a second opinion (default %(default)s)')
    ap.add_argument('--markdown', action='store_true', help='print the tables as Markdown, to paste into MEASURES.md')
    args = ap.parse_args(argv)
    try:
        sizes = list(dict.fromkeys(int(s) for s in args.sizes.split(',')))
    except ValueError:
        ap.error('--sizes takes whole numbers separated by commas, such as 24,76,300')
    if args.reps < 1 or not all(1 <= s <= MAX_SIZE for s in sizes):
        ap.error(f'--reps must be at least 1 and every size from 1 to {MAX_SIZE}')
    wanted = [c.strip() for c in args.cases.split(',')] if args.cases else known
    if [c for c in wanted if c not in known]:
        ap.error('unknown case: ' + ', '.join(c for c in wanted if c not in known) + '; the cases are ' + ', '.join(known))
    cases = [c for c in known if c in wanted]

    started, results = time.time(), {}
    for case in cases:
        for n in sizes:
            results[case, n] = run_cell(case, n, args.reps, args.sensitivity, args.seed)
            if sys.stderr.isatty():
                print(f'{case} at {n}: {time.time() - started:.0f} s', file=sys.stderr)
    sys.stdout.write(report(results, cases, sizes, args.reps, args.sensitivity, args.seed, args.markdown))
    runs = len(cases) * len(sizes) * args.reps
    print(f'{runs:,} run{"" if runs == 1 else "s"} in {time.time() - started:.0f} s.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
