"""A deterministic stand-in for the language model, for tests and offline demos ONLY.

`KeywordStubModel` follows the same contract as a real model function (instructions, input_text,
schema) and returns JSON in the Vibe Check shape. It sorts messages by keyword. It does not read
language, so the numbers it produces say nothing about any real conversation. Never ship results
from it as analysis.
"""
from __future__ import annotations

import json
import re

FRIENDLY = ('thanks', 'thank you', 'great', 'awesome', 'nice', 'love', 'appreciate', 'well played', 'gg', 'welcome',
            'congrats', 'helpful', 'happy to', 'amazing', 'good job', 'proud', 'glad', 'good luck')
INSULT = ('idiot', 'trash', 'noob', 'stupid', 'loser', 'pathetic', 'useless', 'uninstall', 'clown', 'dumb')
HARASS = ('report you', 'everyone hates', 'nobody likes', 'leave this server', 'shut up')
THREAT = ("i'll find you", 'you are dead', "you're dead", 'watch your back')
PROFANE = ('damn', 'wtf', 'crap', 'hell')
SPAM = ('free coins', 'buy now', 'click here', '[link]')


class KeywordStubModel:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, instructions, input_text, schema=None):
        self.calls.append({'instructions': instructions, 'input': input_text, 'schema': schema})
        if 'Write the narrative' in instructions:
            stats = json.loads(input_text)
            return json.dumps({
                'communityStrengths': f'Members keep the tone warm: {stats["sentimentPercent"]["friendly"]}% of messages are friendly.',
                'recommendation': 'Celebrate the members who welcome newcomers and turn their habits into a shared ritual.',
                'summary': f'Impact score {stats["impactScore"]}/10 across {stats["messagesAnalyzed"]} messages.'}), 'keyword-stub'
        sens = 'low' if 'SENSITIVITY: LOW' in instructions else 'high' if 'SENSITIVITY: HIGH' in instructions else 'medium'
        msgs = [m.group(2) for m in (re.match(r'^(\d+)\. (.*)$', line) for line in input_text.split('\n')) if m]
        labels, flagged = [], []
        tox = dict.fromkeys(('insults', 'harassment', 'threats', 'hate_speech', 'bullying', 'profanity', 'spam'), 0)
        for i, text in enumerate(msgs, 1):
            t = text.lower()
            kind = sev = None
            if any(k in t for k in THREAT):
                kind, sev = 'threat', 9
            elif any(k in t for k in HARASS):
                kind, sev = 'harassment', 7
            elif any(k in t for k in INSULT):
                kind, sev = 'insult', 5
            elif any(k in t for k in SPAM):
                kind, sev = 'spam', 3
            elif any(k in t for k in PROFANE) and sens != 'low':
                kind, sev = 'profanity', 2 if sens == 'high' else 4
            if kind and sens == 'low' and kind in ('insult', 'spam'):
                kind = None                       # banter and spam are tolerated at LOW sensitivity
            if kind:
                labels.append('unfriendly')
                flagged.append({'n': i, 'type': kind, 'severity': sev})
                tox[{'insult': 'insults', 'threat': 'threats'}.get(kind, kind)] += 1
            elif any(k in t for k in FRIENDLY):
                labels.append('friendly')
            else:
                labels.append('neutral')
        n = max(len(msgs), 1)
        f, u = labels.count('friendly'), labels.count('unfriendly')
        score = round(max(0.0, min(10.0, 10 * (f + 0.5 * labels.count('neutral')) / n - 2.0 * u / n)), 1)
        return json.dumps({
            'friendlinessScore': score,
            'sentiment': {'friendly': f, 'neutral': labels.count('neutral'), 'unfriendly': u},
            'flaggedMessages': flagged, 'toxicityTypes': tox,
            'communityStrengths': 'Members thank each other and celebrate good plays, which keeps new people coming back.',
            'recommendation': 'Spotlight the members who welcome newcomers each week. Invite the group to set one shared goal for the next session.',
            'summary': f'A {"warm" if score >= 6 else "mixed"} conversation with {u} unfriendly message(s).'}), 'keyword-stub'
