"""Background tasks: the full FELIXA run, and Vibe Check on its own.

`run_job` is what the worker thread calls. The behaviour analysis and Vibe Check run as independent
steps: if one fails the other's results are still saved and the user is told what happened.
"""
import logging

import analysis
import cloud
import rules
import config
import measures
import store
from model import local_model_call
from vibe_check import ingest, run_vibe_check

log = logging.getLogger('bei.pipeline')
SENSITIVITIES = ('low', 'medium', 'high')
WITH_PROBLEMS = 'Finished with problems'     # opens the message of a task that gave some results but not all; the workspace shows it as a warning


class Cancelled(BaseException):
    """Raised between model calls when the user cancels a task. A BaseException so that the `except Exception` around each step cannot
    swallow it and carry on to the next step."""


def volume_limit(a):
    """How many messages a check may read: the volume chosen for it. None means the app's default (a sample for the 13 signals, everything for
    Vibe Check), 0 means all of them, and a number is that many, spread evenly through what was added."""
    volume = a['check_volume'] if a else None
    return None if volume is None else (volume or config.max_units())


def vibe_units(aid, limit=None) -> list:
    """The communication units inside the analysis's review period, in the shape Vibe Check reads. With a `limit`, an even sample of them."""
    units = [{'text': u['text'], 'source': u['source_label'] or 'Upload', 'timestamp': u['timestamp'] or None,
              'role': u['actor_role'], 'external_id': u['external_id']} for u in store.units_for(aid, in_period=True)]
    return ingest.sample_units(units, limit)[0] if limit else units


def engine_call(a):
    """The model call for a check: the engine picked for it (Claude only when the person ticked the box that lets messages leave this computer),
    or the app's default model."""
    name = a['engine'] if a else None
    if rules.is_rules(name):
        return rules.rules_call                                       # word lists: no model, nothing leaves this computer
    if cloud.is_cloud(name):
        if not a['cloud_ok']:
            def refuse(*_args, **_kwargs):
                said = f'The box that lets your messages go to {cloud.provider(name)} was not ticked.'
                raise cloud.login_failure(said) if cloud.is_login(name) else cloud.ClaudeUnavailable(cloud.REFUSED_OPENAI if cloud.is_openai(name) else cloud.REFUSED)
            return refuse
        return cloud.claude_call(name)
    return (lambda instructions, text, **kw: local_model_call(instructions, text, model=name, **kw)) if name else local_model_call


def run_vibe(aid, jid, progress, call=None) -> dict:
    """Runs Vibe Check over everything uploaded, saves the full result and its numeric snapshot, returns the result."""
    a = store.get_analysis(aid)
    sensitivity = (a['vibe_sensitivity'] or 'medium') if a else 'medium'
    limit = volume_limit(a)
    size = {'max_total': limit, 'max_per_source': limit} if limit else {}                    # a volume the person chose sets Vibe Check's reading cap too
    result = run_vibe_check(vibe_units(aid, limit), call or engine_call(a), sensitivity=sensitivity, **size,
                            history=store.vibe_history(aid), progress=progress)
    if result['status'] != 'ok':                                # the exception behind it is for the log, not for the person reading the page
        log.warning('Vibe Check gave no result (analysis %s): %s %s', aid, result.get('reason', ''), result.get('detail', ''))
    store.save_vibe_run(aid, jid, result)
    return result


def _behaviour(aid, call, progress, limit=None) -> tuple:
    """The 13-signal ratings and their metrics, then the written guidance. Returns (rated, problems). `rated` is what `rate_units` reported, or
    None when the ratings did not finish. `problems` holds a sentence for each step that failed, in words for the person: the exception goes to
    the log. The guidance is a step of its own, because it fails on its own (the model can rate and still answer the last request badly) and
    the ratings are saved by then: it must not read as though they were lost."""
    try:
        rated = analysis.rate_units(aid, call, progress, limit=limit)
        analysis.calculate_metrics(aid)
        measures.save_snapshot(aid, rated)                      # the numbers of this check, kept before the guidance step so a cancel there cannot lose them
    except analysis.AnalysisError as e:
        return None, [str(e)]
    except Exception:
        log.exception('signal ratings failed (analysis %s)', aid)
        return None, ['The signal ratings could not finish.']
    try:
        progress('Writing evidence-linked guidance…')
        analysis.generate_guidance(aid, call, coverage={'units_total': rated['total'], 'units_rated': rated['rated'], 'sampled': bool(rated['note'])})
        store.update_analysis(aid, status='analyzed')
    except Exception:
        log.exception('guidance failed (analysis %s)', aid)
        return rated, ['The written guidance could not be created. Run the check again to retry.']
    return rated, []


def run_full(aid, jid, progress, call=None):
    """Behaviour analysis, then Vibe Check. Returns (status, message).

    A task that gave every result reads "Your report is ready: ...". One that gave some but not all is 'complete' (there is a report to open) and
    its message opens with WITH_PROBLEMS, then says what was done and what was not. One that gave none is 'failed'."""
    a = store.get_analysis(aid)
    call = call or engine_call(a)
    rated, problems = _behaviour(aid, call, progress, volume_limit(a))
    parts = []
    if rated is not None:
        parts.append(f'rated {rated["rated"]:,} of {rated["total"]:,} communication units' if rated['note'] else f'rated {rated["rated"]:,} communication units')
    vibe = None
    try:
        vibe = run_vibe(aid, jid, progress, call)
    except Exception:
        log.exception('Vibe Check failed (analysis %s)', aid)
        problems.append('Vibe Check could not finish.')
    if vibe is not None:
        if vibe['status'] == 'ok':
            parts.append(f'ran Vibe Check ({vibe["score"]:g}/10)')
        else:
            problems.append('Vibe Check was not run: ' + vibe.get('reason', 'unknown reason'))
    if not parts:
        return 'failed', ' '.join(problems) + ' Your saved inputs are safe. Please retry.'
    done = 'We ' + ' and '.join(parts) + '.'
    if problems:
        return 'complete', f'{WITH_PROBLEMS}. {done} {" ".join(problems)}'
    return 'complete', f'Your report is ready: {done}'


def run_vibe_only(aid, jid, progress, call=None):
    result = run_vibe(aid, jid, progress, call)
    if result['status'] == 'ok':
        return 'complete', f'Vibe Check is ready: {result["score"]:g}/10 across {result["analyzed_n"]:,} messages.'
    return 'failed', 'Vibe Check was not run: ' + result.get('reason', 'unknown reason')


def run_tones_only(aid, jid, progress, call=None):
    """Only the tone ratings: Health Score, Escalation, Conflict, Empathy, Passive-aggressive, Badges and Alerts."""
    a = store.get_analysis(aid)
    call = call or engine_call(a)
    rated, problems = _behaviour(aid, call, progress, volume_limit(a))
    if rated is None:
        return 'failed', ' '.join(problems) + ' Your saved inputs are safe. Please retry.'
    done = f'rated {rated["rated"]:,} of {rated["total"]:,} messages' if rated['note'] else f'rated {rated["rated"]:,} messages'
    if problems:
        return 'complete', f'{WITH_PROBLEMS}. We {done}. {" ".join(problems)}'
    return 'complete', f'Your results are ready: we {done}.'


def run_job(jid, aid, kind, call=None):
    """Worker entry point. Never raises: every outcome is written to the job row.

    A task is cancelled between model calls: every progress message first looks for the user's request, so the call in progress
    finishes. Ratings and Vibe Check results are saved only when their step is complete, so a cancelled step leaves nothing half done."""
    def progress(msg):
        if store.cancel_requested(jid):
            raise Cancelled
        store.job_update(jid, 'running', msg)

    try:
        progress('Preparing the analysis…')
        a = store.get_analysis(aid)
        if a and a['incognito']:
            progress('Removing names from the messages…')
            store.anonymise_units(aid)
        runner = run_vibe_only if kind == 'vibe' else run_tones_only if kind == 'tones' else run_full
        status, message = runner(aid, jid, progress, call)
        store.job_update(jid, status, message)
    except Cancelled:
        store.job_update(jid, 'cancelled', 'Cancelled. Your uploads are safe, and anything the task had already saved was kept.')
    except Exception:
        log.exception('job %s (%s) failed', jid, kind)
        store.job_update(jid, 'failed', 'The analysis could not finish. Your saved inputs are safe. Please retry; if it persists, check the service logs.')
