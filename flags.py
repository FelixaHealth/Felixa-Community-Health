"""The flags of the Felixa backend (community_health.py), by their own names.

A message is flagged HIGH severity when it shows Aggressive, Judgmental, Disrespectful or Rude (a tone rated 2 or 3) or one of the content categories Hate Speech
and Violence. When it has none of those it is flagged MEDIUM severity for Profanity, Sarcasm or Agitated. The content categories come from Vibe Check's flagged
messages (hate speech -> Hate Speech, threat -> Violence, profanity -> Profanity, spam -> Spam); the tones come from the 13 ratings. Spam is counted but not flagged,
as in the backend."""
import store

FLAG_MIN = 2                                                    # a tone is present when rated 2 or 3, as everywhere in the measures
HIGH_TONES = ('Aggressive', 'Judgmental', 'Disrespectful', 'Rude')
HIGH_CATEGORIES = ('Hate Speech', 'Violence')
SHOWN_AS = {'Sarcastic': 'Sarcasm', 'Egocentric': 'Ego-centric'}
VIBE_CATEGORY = {'hate_speech': 'Hate Speech', 'threat': 'Violence', 'profanity': 'Profanity', 'spam': 'Spam'}
HIGH_NAMES = HIGH_TONES + HIGH_CATEGORIES                       # the order the backend lists them in
MEDIUM_NAMES = ('Profanity', 'Sarcasm', 'Agitated')


def of_message(ratings, categories=()):
    """('high' | 'medium' | '', [flag names]) for one message, the way the backend's flagged() decides it."""
    high = [t for t in HIGH_TONES if int(ratings.get(t) or 0) >= FLAG_MIN] + [c for c in HIGH_CATEGORIES if c in categories]
    if high:
        return 'high', high
    medium = (['Profanity'] if 'Profanity' in categories else []) + (['Sarcasm'] if int(ratings.get('Sarcastic') or 0) >= FLAG_MIN else []) + \
             (['Agitated'] if int(ratings.get('Agitated') or 0) >= FLAG_MIN else [])
    return ('medium', medium) if medium else ('', [])


def _categories_by_text(vibe):
    out = {}
    if vibe and vibe.get('status') == 'ok':
        for f in vibe.get('flagged') or []:
            name = VIBE_CATEGORY.get(f.get('type'))
            if name:
                out.setdefault(str(f.get('message') or '').strip(), set()).add(name)
    return out


def build(aid, vibe=None) -> dict:
    """{'high': [(name, messages)], 'medium': [...], 'total_high', 'total_medium', 'rated', 'rows': [{'severity', 'flags', 'text', 'source', 'when'}]}."""
    cats = _categories_by_text(vibe)
    units = store.rated_text_units(aid)
    high_n, med_n, items = {}, {}, []
    for u in units:
        severity, names = of_message(u['ratings'], cats.get(u['text'].strip(), ()))
        if not severity:
            continue
        for name in names:
            (high_n if severity == 'high' else med_n)[name] = (high_n if severity == 'high' else med_n).get(name, 0) + 1
        items.append({'severity': severity, 'flags': names, 'text': u['text'], 'source': u['source'], 'when': (u['timestamp'] or '')[:10]})
    items.sort(key=lambda x: 0 if x['severity'] == 'high' else 1)
    return {'high': [(n, high_n[n]) for n in HIGH_NAMES if n in high_n], 'medium': [(n, med_n[n]) for n in MEDIUM_NAMES if n in med_n],
            'total_high': sum(1 for i in items if i['severity'] == 'high'), 'total_medium': sum(1 for i in items if i['severity'] == 'medium'),
            'rated': len(units), 'rows': items}
