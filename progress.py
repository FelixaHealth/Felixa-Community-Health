"""What a running task is doing, in steps and a percentage, for the progress window.

The worker writes one sentence per step into the job row ('Rating communication signals (30 of 76)…'). This module reads those sentences and
turns them into a list of stages, which one is current, how far along the whole run is, and a plain description. It reads no message and no name:
only the sentence the worker wrote, which the workplace page already shows."""
import re

# (key, what the person reads, where the stage starts and ends on the bar, in percent)
FULL = [('prepare', 'Getting ready', 0, 4), ('names', 'Taking names out of the messages', 4, 8), ('rate', 'Rating the 13 tones in every message', 8, 52),
        ('vibe', 'Vibe Check: reading the mood', 52, 90), ('guide', 'Writing the guidance', 90, 97), ('report', 'Building the report', 97, 100)]
VIBE_ONLY = [('prepare', 'Getting ready', 0, 6), ('vibe', 'Vibe Check: reading the mood', 6, 96), ('report', 'Building the report', 96, 100)]

RATING = re.compile(r'Rating communication signals \((\d[\d,]*) of (\d[\d,]*)\)')
VIBE = re.compile(r'Vibe Check: reading source (\d+) of (\d+).*?messages (\d[\d,]*)\D+(\d[\d,]*) of (\d[\d,]*)')


def _num(text) -> int:
    return int(str(text).replace(',', ''))


def stages_for(kind, incognito):
    base = VIBE_ONLY if kind == 'vibe' else FULL
    return [s for s in base if s[0] != 'names' or incognito]


def describe(job, incognito=False) -> dict:
    """{'stages': [{'key', 'label', 'state': 'done' | 'now' | 'todo'}], 'percent': 0..100, 'detail': sentence, 'state': the job's status}. Empty for no job."""
    if not job:
        return {}
    status, message, kind = job.get('status'), str(job.get('message') or ''), job.get('kind')
    stages = stages_for(kind, incognito)
    index = {s[0]: i for i, s in enumerate(stages)}
    current, percent = 'prepare', 0.0
    if status == 'complete':
        current, percent = None, 100.0
    elif status == 'queued':
        percent = 0.0
    else:
        low = message.lower()
        if 'removing names' in low:
            current = 'names'
        elif (m := RATING.search(message)):
            current = 'rate'
        elif (m := VIBE.search(message)):
            current = 'vibe'
        elif 'guidance' in low:
            current = 'guide'
        elif 'vibe check' in low:
            current = 'vibe'
        elif 'report' in low or 'measures' in low:
            current = 'report'
        current = current if current in index else ('vibe' if kind == 'vibe' and current == 'rate' else 'prepare')
        _key, _label, start, end = stages[index[current]]
        share = 0.0
        m = RATING.search(message)
        if current == 'rate' and m:
            share = _num(m.group(1)) / max(1, _num(m.group(2)))
        m = VIBE.search(message)
        if current == 'vibe' and m:
            source, sources, upto, total = _num(m.group(1)), max(1, _num(m.group(2))), _num(m.group(4)), max(1, _num(m.group(5)))
            share = ((source - 1) + min(1.0, upto / total)) / sources
        percent = start + (end - start) * min(1.0, share)
    where = index.get(current, len(stages))
    out = []
    for i, (key, label, _s, _e) in enumerate(stages):
        out.append({'key': key, 'label': label, 'state': 'done' if status == 'complete' or i < where else 'now' if i == where and status in ('running', 'queued') else 'todo'})
    return {'stages': out, 'percent': round(percent), 'detail': message, 'state': status, 'kind': kind}
