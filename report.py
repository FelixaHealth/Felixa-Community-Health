"""Everything a report needs, gathered once so the workspace, the report page, the PDF and the CSV agree."""
import datetime
import json
import re

import analysis
import answers
import bei_charts
import measures
import measures_export
import measures_view
import store
from vibe_check import assets_html, data_table, figure, render_html
from vibe_check.common import fmt_int, long_date
from vibe_check.render import SAFETY_NOTE, SAFETY_TYPES


def build(aid):
    """The context for one analysis, or None when it does not exist."""
    a = store.get_analysis(aid)
    if not a:
        return None
    metrics = analysis.metrics_for(aid)
    guidance = analysis.stored_guidance(a)
    vibe = store.latest_vibe(aid)
    notice = vibe_notice(vibe, store.failed_vibe_attempt(aid))
    rated_n = metrics[0]['n'] if metrics else 0
    units_n = store.count_units(aid)
    period = store.period_coverage(a)
    scope_n = period['kept'] if period else units_n        # the units a run reads: those inside the review period, if one is set
    rated_total = (a['rated_units_total'] or scope_n) if metrics else scope_n       # the units there were when the signals were rated
    ctx = {
        'aid': aid, 'a': a, 'metrics': metrics, 'guidance': guidance, 'vibe': vibe, 'vibe_notice': notice,
        'units_n': units_n, 'scope_n': scope_n, 'rated_n': rated_n, 'rated_total': rated_total,
        'sampled': bool(metrics) and rated_n < rated_total, 'added_since': max(0, scope_n - rated_total) if metrics else 0,
        'period': period, 'period_text': period_text(period),
        'evidence': store.evidence_by_source(aid), 'families': bei_charts.family_summary(metrics),
        'goals': [g for g in json.loads(a['goals_json'] or '[]') if g],
        'pathway': analysis.pathway(a), 'facts': [x for x in (a['subject_type'], a['person_role'], a['channel']) if x],
        'generated': datetime.date.today().isoformat(),
        'has_results': has_results(metrics, vibe), 'gaps': gaps(metrics, guidance, vibe), 'how': analysis.HOW_IT_WORKS,
    }
    cov = {'sampled': ctx['sampled'], 'total': rated_total, 'scope': scope_n, 'added_since': ctx['added_since'], 'left_out': max(0, rated_total - scope_n) if metrics else 0,
           'vibe_stale': bool(notice)}
    ctx['measures'] = measures.build(aid, a, vibe, cov) if metrics else None
    ctx['measures_html'] = measures_view.html(ctx['measures'], a) if ctx['measures'] else {}
    ctx['chart_html'] = chart_html(metrics)
    ctx['vibe_html'] = render_html(vibe, assets=False, notice=notice) if vibe else ''
    ctx['assets'] = assets_html() if (metrics or vibe) else ''
    ctx['summary_line'] = summary_line(ctx)
    ctx['answers'] = answers.build(ctx)
    return ctx


def bound_text(value) -> str:
    """One end of the review period in words: 'Mar 1, 2026' for a whole day, 'Mar 1, 2026 20:00 UTC' for a minute."""
    if isinstance(value, datetime.datetime):
        return f'{long_date(value)} {value:%H:%M} UTC'
    return long_date(value)


def period_span(period) -> str:
    """The review period in words: 'Mar 1, 2026 to Mar 31, 2026', 'from Mar 1, 2026' or 'up to Mar 31, 2026'; with times when it has them."""
    start, end = bound_text(period['start']) if period['start'] else '', bound_text(period['end']) if period['end'] else ''
    return f'{start} to {end}' if start and end else f'from {start}' if start else f'up to {end}'


def period_text(period) -> str:
    """The review period and what it left out, for a report. '' when no period is set."""
    if not period:
        return ''

    def units(n):
        return f'{fmt_int(n)} communication unit{"" if n == 1 else "s"}'
    text = f'Review period: {period_span(period)}.'
    if period['outside']:
        text += f' Left out: {units(period["outside"])} dated outside it.'
    if period['undated']:
        text += f' Kept: {units(period["undated"])} with no readable date.'
    return text


def has_results(metrics, vibe) -> bool:
    """True when there is something to report: rated signals, or a Vibe Check that finished. A Vibe Check that did not run
    is a message about a run, not a result, so on its own it makes no report, PDF or CSV."""
    return bool(metrics or (vibe and vibe.get('status') == 'ok'))


NO_BEHAVIOUR = ('Measured behaviour', 'The 13 communication signals have no ratings for this analysis: the rating was not run, or it did not finish. Alerts, badges and the measures over time need these ratings too. '
                'Run a check to rate them and write the guidance.')
NO_GUIDANCE = ('Guidance', 'The written guidance has not been created: the step that writes it was not run, or it did not finish. '
               'Run the check again to write it.')
NO_VIBE = ('Vibe Check', 'Vibe Check has not produced a result for this analysis: it was not run, or it did not finish. Run Vibe Check to create it.')


def gaps(metrics, guidance, vibe) -> list:
    """The parts of a report that have no results, as (title, reason) pairs, which the page, the PDF and the CSV all print in plain words: a part
    that is simply left out reads as though it was never meant to be there. [] while there is no report at all. A Vibe Check run that gave no
    result says "Not run" for itself, so `vibe` counts here only when there is no run at all (the task was cancelled, or could not save one).
    The guidance needs the ratings, so when they are missing one note covers both. A task that was never started leaves the same state as one
    that failed ("Run Vibe Check only" does not rate the signals), so the reasons say "not run, or did not finish" and never blame a failure."""
    if not has_results(metrics, vibe):
        return []
    found = []
    if not metrics:
        found.append(NO_BEHAVIOUR)
    elif not guidance:
        found.append(NO_GUIDANCE)
    if not vibe:
        found.append(NO_VIBE)
    return found


def vibe_notice(vibe, failed) -> str:
    """Says so when the Vibe Check shown is an earlier run because the newest attempt (`failed`) did not finish. Else ''."""
    if not failed or not vibe or vibe.get('status') != 'ok':
        return ''
    when = f', from {long_date(vibe["generated_at"])}' if vibe.get('generated_at') else ''
    return f'The latest Vibe Check did not finish, so this section shows the previous successful run{when}. {failed.get("reason") or ""}'.strip()


def chart_html(metrics) -> str:
    """The signal-profile chart (wide and phone layouts) followed by its table twin."""
    if not metrics:
        return ''
    fig = figure(lambda w: bei_charts.attribute_meters(metrics, width=w))
    table = data_table(['Signal', 'Family', 'FAS', 'Detection', 'Expression strength', 'Units rated'], bei_charts.attribute_table_rows(metrics))
    return fig + table


# ---- CSV ---------------------------------------------------------------------------
def csv_safe(value):
    """Stops spreadsheets from running a cell as a formula: a leading = + - @ (or tab / carriage return) gets an apostrophe."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def _plain(text) -> str:
    """The text without its **bold** markers: a cell has no bold."""
    return re.sub(r'\*\*(.+?)\*\*', r'\1', str(text or ''))


def _guidance_rows(g) -> list:
    """The FELIXA guidance as the report prints it: summary, what is working, what needs investigation, what to do next, confidence."""
    rows = [['Guidance']]
    if g.get('summary'):
        rows.append(['Summary', g['summary']])
    for label, key in (('What appears to be working', 'working'), ('What needs investigation', 'limitations')):
        rows += [[label, x.get('title', ''), x.get('body', '')] for x in g.get(key) or [] if isinstance(x, dict)]
    rows += [['What to do next', x.get('action', ''), x.get('why', ''), f'Measure: {x.get("measure", "")}']
             for x in g.get('recommendations') or [] if isinstance(x, dict)]
    if g.get('confidence'):
        rows.append(['Evidence confidence', g['confidence'], g.get('confidence_reason', '')])
    return rows + [[]]


def _progress_rows(prog) -> list:
    """The report's Progress over time and Behaviour forecast blocks. Nothing until a second run gives something to compare."""
    if not prog or prog['n_reports'] < 2:
        return []
    mix, fc = prog['sentiment'], prog.get('forecast')
    rows = [[], ['Progress over time'], ['Reports', prog['n_reports']], ['Oldest score', prog['oldest']], ['Latest score', prog['latest']],
            ['Average score', prog['average']], ['Change since the first report', prog['diff']], ['Trend', _plain(prog['trend']['text'])]]
    rows += [['Health', _plain(h['text'])] for h in prog['health']]
    rows += [['Flagged messages trend', _plain(prog['flagged']['text'])], [],
             ['Run', 'Score', 'Messages analysed', 'Flagged messages', 'Friendly', 'Neutral', 'Unfriendly', 'Sensitivity']]
    rows += [[r.get('created_at'), r['score'], r.get('analyzed_n') or 0, r.get('flagged_count') or 0, r.get('friendly') or 0,
              r.get('neutral') or 0, r.get('unfriendly') or 0, r.get('sensitivity') or ''] for r in prog['reports']]
    rows += [[], ['Sentiment share', 'First report (%)', 'Latest report (%)', 'Change (points)'],
             ['Friendly', mix['friendly_first'], mix['friendly_last'], mix['friendly_delta']],
             ['Unfriendly', mix['unfriendly_first'], mix['unfriendly_last'], mix['unfriendly_delta']]]
    if prog['toxicity']:
        rows += [[], ['Toxicity type', 'Instances over all runs']] + [[k, v] for k, v in prog['toxicity']]
    if fc:
        rows += [[], ['Behaviour forecast'], ['Chance the community improves', f'{fc["improve"]}%'], ['Chance toxicity increases', f'{fc["worsen"]}%'],
                 ['Confidence', f'{fc["confidence"]} ({fc["n_reports"]} reports)'], ['Caveat', fc['caveat']]]
        rows += [['Factor', f] for f in fc['factors']]
    advice = [['Prediction', _plain(x['text'])] for x in prog['predictions']]
    advice += [['Positive psychology recommendation', _plain(x['text'])] for x in prog['recommendations']]
    return rows + ([[]] + advice if advice else [])


def csv_rows(ctx) -> list:
    """The report as rows: the same blocks as the page and the PDF, with each chart as its table and every text as a cell."""
    a, vibe = ctx['a'], ctx['vibe']
    rows = [['Felixa Community Health analysis', ctx['aid']], ['Workplace', a['person_name']], ['Kind', a['subject_type']], ['Area', a['person_role']], ['Chat', a['channel']],
            ['Model', a['model_used']],
            ['Generated', ctx['generated']]]
    if ctx['period']:
        rows += [['Review period', period_span(ctx['period'])], ['Units left out (dated outside the period)', ctx['period']['outside']],
                 ['Units kept with no readable date', ctx['period']['undated']]]
    rows.append([])
    if ctx['metrics']:
        rows.append(['Attribute', 'N', 'FAS', 'Detection rate', 'Expression strength', 'Family'])
        for m in sorted(ctx['metrics'], key=lambda m: m['attribute']):
            rows.append([m['attribute'], m['n'], round(m['fas'], 2), round(m['detection_rate'], 2), round(m['expression_strength'], 2), m['family']])
        rows.append([])
    if ctx['guidance']:
        rows += _guidance_rows(ctx['guidance'])
    if ctx.get('measures'):
        rows += measures_export.csv_rows(ctx['measures'])
    for title, reason in ctx['gaps']:
        rows += [[title], ['Not available', reason], []]
    if vibe and vibe.get('status') == 'ok':
        mix = vibe['sentiment']
        rows.append(['Vibe Check'])
        if ctx['vibe_notice']:
            rows.append(['Note', ctx['vibe_notice']])
        rows += [['Score (out of 10)', vibe['score']], ['Rating', vibe['label']], ['Sensitivity', vibe['sensitivity']],
                 ['Messages analysed', vibe['analyzed_n']], ['Sources', len(vibe['sources'])], ['Flagged messages', vibe['flagged_total']]]
        rows += [[label, vibe[key]] for label, key in (('Vibe verdict', 'summary'), ('Community strengths', 'strengths'), ('Recommendations', 'recommendation'))
                 if vibe.get(key)]
        rows += [['Sentiment', 'Messages', 'Share'],
                 ['Friendly', mix['friendly'], f'{mix["pct"]["friendly"]}%'], ['Neutral', mix['neutral'], f'{mix["pct"]["neutral"]}%'],
                 ['Unfriendly', mix['unfriendly'], f'{mix["pct"]["unfriendly"]}%'], []]
        rows.append(['Source', 'Score', 'Messages analysed'])
        rows += [[s['name'], s['score'], s['analyzed_n']] for s in vibe['sources']]
        rows += [[], ['Toxicity type', 'Instances']] + [[k, v] for k, v in sorted(vibe['toxicity_types'].items(), key=lambda kv: (-kv[1], kv[0])) if v]
        if vibe['flagged']:
            rows.append([])
            if any(f.get('type') in SAFETY_TYPES for f in vibe['flagged']):
                rows.append(['Note', SAFETY_NOTE])                    # the page and the PDF say this under Flagged messages: the CSV says it right before the messages
            rows.append(['Severity', 'Type', 'Source', 'Flagged message'])
            rows += [[f['severity'], f['type'], f['source'], f['message']] for f in vibe['flagged']]
        rows += _progress_rows(vibe.get('progress'))
    elif vibe:
        rows += [['Vibe Check'], ['Not run', vibe.get('reason', '')]]
    return [[csv_safe(c) for c in row] for row in rows]


def summary_line(ctx) -> str:
    bits = []
    if ctx['metrics']:
        bits.append(f'{fmt_int(ctx["rated_n"])} of {fmt_int(ctx["rated_total"])} communication units rated' if ctx['sampled']
                    else f'{fmt_int(ctx["rated_n"])} communication units rated')
        if ctx['added_since']:
            bits.append(f'{fmt_int(ctx["added_since"])} added since, not included')
    if ctx['vibe'] and ctx['vibe'].get('status') == 'ok':
        bits.append(f'Vibe Check {ctx["vibe"]["score"]:g}/10')
    return ' · '.join(bits)
