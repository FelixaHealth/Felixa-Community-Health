"""Progress over time and the behaviour forecast.

Port of the original bot's /vibe-progress analytics and calcBehaviorProbability().
The weights, thresholds and wording are the original's, with five deliberate fixes
(each marked FIX below) where the original produced a wrong answer:

  1. With exactly three reports the "older" baseline was an empty list averaged as 0,
     so momentum always added the maximum +25. The baseline is now every earlier report.
  2. A score gain between 0 and 0.5 points was reported as a "slight dip".
  3. Flagged counts from runs of different size were compared directly. Rates per 100
     messages are compared when the run size is known.
  4. Wording such as "trending down -0.5 pts" carried a double negative.
  5. The progress lines contradicted each other. The flagged-messages sentence printed two raw counts beside a verdict reached on
     their rates per 100 messages ("Down from 10 to 12 flagged messages"), and the trend line, the health line and the
     recommendation cut a gain off at different points (+0.5 read "Trending upward" and "A stable community"; +0.3 read "Holding
     steady" and "an upward journey"). The sentence prints the rates it compared, and one cut-off (MOVEMENT) serves all three.

One addition: the sensitivity level changes what counts as unfriendly, so runs made at different levels are not like for like. They are still
compared as they are, but the health lines and the forecast caveat say so (see `_levels`).

All text is plain (no emoji) so it renders in HTML and in PDF fonts. `**x**` marks emphasis.
"""
from __future__ import annotations

import hashlib
import json
import re

from .common import fmt_int, fmt_score, shares, round1, js_round, run_labels, score_label

SEVERE_TYPES = ('harassment', 'threats', 'hate_speech', 'bullying')

# The score change, in points, that counts as movement: a gain of this much is an upward trend, a dip of more than this is a decline.
# Anything between is steady (or a slight dip). The trend line, the health lines and the recommendations all read the change against
# this one number, so that they cannot call one change by different names (FIX 5).
MOVEMENT = 0.5

TYPE_RECOMMENDATIONS = {
    'harassment': 'Shift attention by spotlighting respectful conversations. What gets celebrated gets repeated.',
    'insults': 'Create a "kindness challenge": invite members to share something they appreciate about the community.',
    'hate_speech': 'Build a stronger shared identity. When members feel proud of who they are together, exclusionary behaviour naturally decreases.',
    'spam': 'Low engagement often drives spam. Try a community Q&A or AMA to spark genuine conversation.',
    'threats': 'Build psychological safety by celebrating vulnerability and openness in conversations.',
    'bullying': 'Empower bystanders: recognise members who stand up for others and create a culture of mutual support.',
    'profanity': 'If this does not fit your community, model the tone you want to see rather than focusing on what you want to stop.',
}

FORECAST_CAVEAT = ('These percentages come from a transparent rules-based score over your saved Vibe Checks. '
                   'They show direction, not a calibrated probability.')
# Sensitivity changes what the model counts as unfriendly, so the same chat scores differently at each level. Runs are still compared
# as they are (nothing is dropped or adjusted); the reader is told, in the progress health lines and in the forecast caveat.
MIXED_SENSITIVITY_CAVEAT = ('The saved runs used different sensitivity levels, so part of the movement may come from the setting '
                            'and not from the community.')


_FINGERPRINT = re.compile(r'src-[0-9a-f]{12}')


def source_key(name) -> str:
    """A one-way fingerprint of a source name, which is what a snapshot keeps in its place. The forecast only has to tell sources
    apart, and a snapshot outlives the evidence it came from (a host keeps it when the messages are deleted), while a file or channel
    name can be as private as the messages in it. A fingerprint is returned as it is, so history saved with names by an earlier
    version compares with history saved now."""
    name = str(name)
    return name if _FINGERPRINT.fullmatch(name) else 'src-' + hashlib.sha256(name.encode('utf-8', 'replace')).hexdigest()[:12]


def snapshot_from_result(result: dict) -> dict:
    """The compact record a host stores after each run and passes back as `history`: numbers and counts, and each source as a
    fingerprint (see `source_key`), never a message or a name."""
    s = result['sentiment']
    return {
        'created_at': result['generated_at'],
        'score': result['score'],
        'friendly': s['friendly'], 'neutral': s['neutral'], 'unfriendly': s['unfriendly'],
        'flagged_count': result['flagged_total'],
        'toxicity_types': dict(result['toxicity_types']),
        'sources': [source_key(x['name']) for x in result['sources']],
        'analyzed_n': result['analyzed_n'],
        'sensitivity': result['sensitivity'],
    }


# ---- helpers ---------------------------------------------------------------------
def _ordered(reports) -> list[dict]:
    rows = [r for r in (reports or []) if isinstance(r, dict) and r.get('score') is not None]
    return sorted(rows, key=lambda r: str(r.get('created_at') or ''))


def _tox(report: dict) -> dict:
    t = report.get('toxicity_types')
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except ValueError:
            t = {}
    return t if isinstance(t, dict) else {}


def _mix(report: dict):
    f, n, u = (report.get(k) or 0 for k in ('friendly', 'neutral', 'unfriendly'))
    total = (f + n + u) or 1
    return tuple(shares([f, n, u]))


def _flag_rate(report: dict) -> float:
    """Flagged messages per 100 analysed (FIX 3). Raw count when the run size is unknown."""
    flagged = report.get('flagged_count') or 0
    size = report.get('analyzed_n') or 0
    return flagged * 100 / size if size else float(flagged)


def _levels(reports: list[dict]) -> list[str]:
    """The distinct sensitivity levels the saved runs were made at, oldest first. A snapshot that does not say (history saved
    before the level was kept) is left out, so it never raises a caution on its own."""
    levels = (str(r.get('sensitivity') or '').strip().lower() for r in reports)
    return list(dict.fromkeys(level for level in levels if level))


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _momentum(reports: list[dict]):
    """Mean of the latest k scores minus the mean of everything before them (FIX 1)."""
    n = len(reports)
    if n < 3:
        return None
    k = min(3, n - 1)
    return _mean(r['score'] for r in reports[-k:]) - _mean(r['score'] for r in reports[:-k])


# ---- behaviour probability -------------------------------------------------------
def behavior_probability(reports) -> dict | None:
    reports = _ordered(reports)
    n = len(reports)
    if n < 2:
        return None
    first, last = reports[0], reports[-1]
    confidence = 'High' if n >= 8 else 'Medium' if n >= 3 else 'Low'
    improve = 50.0
    factors: list[str] = []

    # Factor 1: score momentum, up to 25 points
    m = _momentum(reports)
    if m is not None:
        impact = min(abs(m) * 8, 25)
        if m > 0:
            improve += impact
            factors.append(f'Score trending up +{m:.1f} pts recently')
        elif m < 0:
            improve -= impact
            factors.append(f'Score trending down {abs(m):.1f} pts recently')

    # Factor 2: latest against oldest score, up to 15 points
    diff = last['score'] - first['score']
    if diff > 0:
        improve += min(diff * 4, 15)
        factors.append(f'Overall score improved +{diff:.1f} pts')
    elif diff < 0:
        improve -= min(abs(diff) * 4, 15)
        factors.append(f'Overall score dropped {abs(diff):.1f} pts')

    # Factor 3: share of unfriendly messages, up to 15 points
    u_first, u_last = _mix(first)[2], _mix(last)[2]
    u_delta = u_last - u_first
    if u_delta < -3:
        improve += min(abs(u_delta) * 1.5, 15)
        factors.append(f'Unfriendly messages decreasing ({u_first}% to {u_last}%)')
    elif u_delta > 3:
        improve -= min(u_delta * 1.5, 15)
        factors.append(f'Unfriendly messages rising ({u_first}% to {u_last}%)')

    # Factor 4: flagged messages
    r_first, r_last = _flag_rate(first), _flag_rate(last)
    unit = ' per 100 messages' if (first.get('analyzed_n') and last.get('analyzed_n')) else ''
    shown = (lambda v: f'{v:.1f}') if unit else (lambda v: f'{int(v)}')
    if r_last < r_first and r_first > 0:
        improve += 8
        factors.append(f'Flagged messages down ({shown(r_first)} to {shown(r_last)}{unit})')
    elif r_last > r_first * 1.3:
        improve -= 10
        factors.append(f'Flagged messages spiking ({shown(r_first)} to {shown(r_last)}{unit})')

    # Factor 5: severe toxicity in the latest run
    tox = _tox(last)
    severe = sum(int(tox.get(k) or 0) for k in SEVERE_TYPES)
    if severe == 0:
        improve += 8
        factors.append('No severe toxicity in latest report')
    elif severe > 5:
        improve -= 12
        factors.append(f'High severe toxicity ({severe} instances) in latest report')
    else:
        improve -= 5
        factors.append(f'Some severe toxicity ({severe} instances) in latest report')

    improve = int(min(max(js_round(improve), 5), 95))
    worsen = 100 - improve
    return {
        'improve': improve, 'worsen': worsen, 'confidence': confidence, 'n_reports': n,
        'improve_tone': 'up' if improve >= 70 else 'steady' if improve >= 50 else 'down',
        'worsen_tone': 'warn' if worsen >= 50 else 'ok',
        'factors': factors,
        'caveat': FORECAST_CAVEAT + (' ' + MIXED_SENSITIVITY_CAVEAT if len(_levels(reports)) > 1 else ''),
    }


# ---- progress narrative ----------------------------------------------------------
def _line(tone: str, text: str) -> dict:
    return {'tone': tone, 'text': text}


def _trend_line(n: int, diff: float, latest_score: float) -> dict:
    if n < 2:
        return _line('info', 'Only one Vibe Check so far. Run it again after the next upload to start tracking trends.')
    d = fmt_score(abs(diff))
    if diff >= 2:
        return _line('good', f'**Major improvement.** Score up **+{d} pts**. The community is flourishing.')
    if diff >= MOVEMENT:
        return _line('good', f'**Trending upward** (+{d} pts). Positive momentum: the community is growing stronger.')
    if diff == 0:
        return _line('info', f'**Stable** at {fmt_score(latest_score)}/10. Community health is consistent.')
    if diff > 0:  # FIX 2: 0 < diff < 0.5 was reported as a dip
        return _line('info', f'**Holding steady** (+{d} pts). Community health is consistent.')
    if diff >= -MOVEMENT:
        return _line('warn', f'**Slight dip** (-{d} pts). A great time to amplify what is working well.')
    if diff >= -2:
        return _line('warn', f'**Declining** (-{d} pts). Focus on celebrating positive interactions to shift the energy.')
    return _line('bad', f'**Significant drop** (-{d} pts). Now is the time to spotlight positive members and reinvigorate the community.')


def build_progress(reports) -> dict | None:
    reports = _ordered(reports)
    n = len(reports)
    if n == 0:
        return None
    first, last = reports[0], reports[-1]
    latest = last['score']
    diff = round1(latest - first['score'])
    avg = round1(_mean(r['score'] for r in reports))
    f_first, _, u_first = _mix(first)
    f_last, _, u_last = _mix(last)
    f_delta, u_delta = f_last - f_first, u_last - u_first

    tox_total: dict[str, int] = {}
    for r in reports:
        for k, v in _tox(r).items():
            tox_total[k] = tox_total.get(k, 0) + int(v or 0)
    tox_entries = sorted(((k, v) for k, v in tox_total.items() if v > 0), key=lambda kv: (-kv[1], kv[0]))

    # community health
    health = []
    levels = _levels(reports)
    if len(levels) > 1:
        names = ', '.join(level.capitalize() for level in levels)
        health.append(_line('warn', f'**Mixed sensitivity.** These runs used different sensitivity levels ({names}), so part of the change may '
                                    'come from the setting and not from the community. Compare runs made at the same level for a like-for-like trend.'))
    if latest >= 8:
        health.append(_line('good', f'**Thriving.** The community is in excellent health at {fmt_score(latest)}/10.'))
    elif latest >= 6:
        health.append(_line('good', f'**Healthy.** Generally positive at {fmt_score(latest)}/10.'))
    elif latest >= 4:
        health.append(_line('warn', f'**Mixed.** A score of {fmt_score(latest)}/10 shows room to grow.'))
    else:
        health.append(_line('bad', f'**Needs attention.** A score of {fmt_score(latest)}/10. Time to invest in community building.'))
    if f_last >= 70:
        health.append(_line('good', f'**{f_last}% friendly.** The community is warm and welcoming.'))
    elif f_last >= 50:
        health.append(_line('warn', f'**{f_last}% friendly.** Mostly positive with room to grow.'))
    else:
        health.append(_line('bad', f'**Only {f_last}% friendly.** There is a great opportunity to build more connection and belonging.'))
    if diff >= MOVEMENT:        # a smaller gain is "Holding steady" in the trend line, so it is not an upward journey here (FIX 5)
        health.append(_line('good', f'Score improved **+{fmt_score(diff)} pts** since the first report: an upward journey.'))
    elif diff < 0:
        health.append(_line('warn', f'Score down **{fmt_score(abs(diff))} pts**. This is an invitation to reinvest in what makes the community special.'))
    if tox_entries:
        health.append(_line('warn', f'Most common issue: **{tox_entries[0][0].replace("_", " ")}** ({tox_entries[0][1]} instances). See the toxicity breakdown.'))

    # flagged trend: the verdict and the figures it prints are the same numbers (FIX 5)
    f_flag, l_flag = first.get('flagged_count') or 0, last.get('flagged_count') or 0
    n_first, n_last = first.get('analyzed_n') or 0, last.get('analyzed_n') or 0
    r_first, r_last = _flag_rate(first), _flag_rate(last)
    if n_first and n_last and n_first != n_last:
        # runs of different size: rates per 100 analysed, compared as printed (never "Down from 5.0 to 5.0"), with the counts behind them
        before, after = round1(r_first), round1(r_last)
        start, end = f'{before:.1f}', f'{after:.1f}'
        what = f'flagged messages per 100 analysed ({fmt_int(f_flag)} of {fmt_int(n_first)}, then {fmt_int(l_flag)} of {fmt_int(n_last)})'
    else:
        before, after = f_flag, l_flag
        start, end, what = fmt_int(before), fmt_int(after), 'flagged messages'
    if after < before:
        flagged = _line('good', f'Down from {start} to {end} {what}. Great progress.')
    elif after > before:
        flagged = _line('warn', f'Up from {start} to {end} {what}. An opportunity to reinforce positive norms.')
    else:
        flagged = _line('info', f'Stable at {end} {what}.')

    # predictions
    predictions = []
    m = _momentum(reports)
    if m is not None:
        if m >= 1:
            predictions.append(_line('good', '**Accelerating upward.** Community energy is building. Expect continued improvement.'))
        elif m >= 0:
            predictions.append(_line('good', '**Positive momentum.** Health is stable and likely to improve.'))
        elif m >= -1:
            predictions.append(_line('warn', '**Slight downward drift.** A good moment to reinvigorate engagement with a community event or spotlight.'))
        else:
            predictions.append(_line('bad', '**Declining momentum.** Investing in member recognition and shared goals now can shift this trend.'))
    if u_delta > 5:
        predictions.append(_line('warn', f'**Unfriendly behaviour rising** ({u_first}% to {u_last}%). Consider highlighting your most positive members to reset the tone.'))
    elif u_delta < -5:
        predictions.append(_line('good', f'**Positive behaviour increasing** ({u_first}% to {u_last}%). Community norms are strengthening.'))
    if l_flag > 2 and r_last > r_first * 1.5:
        predictions.append(_line('bad', '**Flagged messages spiking.** A signal to double down on celebrating what makes the community great.'))
    elif l_flag == 0:
        predictions.append(_line('good', '**Zero harmful content** in the latest report. Community standards are working beautifully.'))

    # positive-psychology recommendations
    recs = []
    if diff < -MOVEMENT:
        recs.append(_line('info', 'The score dipped. Create a "community highlight" post celebrating the most positive recent conversations.'))
    elif diff >= MOVEMENT:
        recs.append(_line('info', 'The score is rising. Keep the momentum by publicly recognising the members who contribute most positively.'))
    else:
        recs.append(_line('info', 'A stable community: try a "spotlight of the week" to proactively reinforce the positive tone.'))
    if f_last >= 70:
        recs.append(_line('info', f'**{f_last}% friendly.** The community has a strong foundation. Channel this energy into a shared goal or community challenge.'))
    elif f_last >= 50:
        recs.append(_line('info', f'Build on the **{f_last}% friendly** baseline by creating more opportunities for members to connect over shared interests.'))
    else:
        recs.append(_line('info', 'Focus on building **belonging**. Small acts like welcoming new members publicly can shift community culture over time.'))
    if tox_entries:
        top = tox_entries[0][0]
        recs.append(_line('info', f'**For {top.replace("_", " ")}:** ' + TYPE_RECOMMENDATIONS.get(
            top, 'Focus on amplifying positive interactions to naturally crowd out negative ones.')))

    sources = {source_key(s) for r in reports for s in (r.get('sources') or [])}
    return {
        'n_reports': n, 'oldest': first['score'], 'latest': latest, 'average': avg, 'diff': diff,
        'direction': 'up' if diff > 0 else 'down' if diff < 0 else 'flat',
        'trend': _trend_line(n, diff, latest),
        'health': health,
        'sentiment': {'friendly_first': f_first, 'friendly_last': f_last, 'friendly_delta': f_delta,
                      'unfriendly_first': u_first, 'unfriendly_last': u_last, 'unfriendly_delta': u_delta},
        'flagged': {'first': f_flag, 'last': l_flag, **flagged},
        'toxicity': tox_entries,
        'predictions': predictions,
        'recommendations': recs,
        'forecast': behavior_probability(reports),
        'multi_source': len(sources) > 1,
        'reports': reports,
        'labels': [x['short'] for x in run_labels(reports)],
        'summary_label': score_label(latest),
    }
