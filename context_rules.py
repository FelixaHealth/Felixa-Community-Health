"""Context rules: what a message does with a hostile word, read from its wording.

The 13 tone ratings and the High Risk readers look at words. A word can be mentioned, aimed at oneself, or used to start a fake apology, and none of those is an insult to someone.
Three labels come out of `classify(text)`:

* `mention`  - the message talks about an insult without using one: an apology ("sorry I called you an idiot"), a report ("he called me an idiot"), a question about a word.
* `self`     - the hostile word is aimed at the speaker ("I'm such an idiot"). Self-criticism is not an attack on another person.
* `fake_apology` - the message starts like an apology and turns into an attack or a brush-off ("sorry, but you are pathetic", "sorry you feel that way"). It is never a calming step.

`apply(ratings, labels)` takes the open-hostility tones to 0 for `mention` and `self` messages, so heat, conflict and escalation do not count them. A message the rules do not recognise is left as rated.
The labels are saved with each message (`communication_units.reading`) so that the measures, which never read message text, can use them. A label describes wording, never a person."""
import re

OVERT = ('Aggressive', 'Rude', 'Disrespectful', 'Judgmental')        # the open-hostility tones of measures.OVERT (a test keeps the two equal; measures imports the store, which imports this)

HOSTILE_WORDS = (r'idiot|moron|fool|stupid|dumb|loser|jerk|pathetic|useless|worthless|clown|trash|garbage|scum|bastard|bitch|asshole|a\*\*hole|dork|retard|ugly|disgusting|shut up|screw you|hate you')
_APOLOGY = r"(?:sorry|apolog(?:y|ies|ise|ize|ised|ized)|forgive me|my bad|i regret)"
_REPORTING = r"(?:call(?:ed|ing)|sa(?:id|ying)|wr(?:ote|iting)|sent|text(?:ed|ing)|told|tell(?:ing)?|insult(?:ed|ing)|yell(?:ed|ing) at|shout(?:ed|ing) at)"

# 1. mention: an apology for what was said, a report of what someone else said, a question about the word
_MENTION = [
    re.compile(rf"\b{_APOLOGY}\b[^.!?]{{0,60}}\b{_REPORTING}\b", re.I),                                                   # sorry I called you an idiot / I am sorry for saying that
    re.compile(rf"\b{_REPORTING}\b[^.!?]{{0,40}}\b{_APOLOGY}\b", re.I),                                                   # I called you an idiot, and I am sorry
    re.compile(rf"\b(?:he|she|they|someone|somebody|people|everyone|you|[A-Z][a-z]+)\s+(?:just\s+|also\s+|actually\s+)?(?:call(?:ed)|sa(?:id)|wr(?:ote)|text(?:ed)|insult(?:ed)|told)\s+(?:me|us|him|her|them)\b", re.I),   # he called me an idiot
    re.compile(r"\b(?:the|that)\s+(?:word|term|insult|slur)\b", re.I),                                                    # the word "idiot"
    re.compile(r"\bis\s+(?:it|that|\"?\w+\"?)\s+(?:ok(?:ay)?|rude|offensive|a (?:bad|rude) word|an insult)\b", re.I),     # is idiot a bad word / is it okay to say
    re.compile(r"\b(?:call(?:ed|ing)|sa(?:id|ying)|word|wrote)\s+(?:me|you|him|her|them)?\s*[“\"'‘]", re.I),            # called me "idiot"
]
# 2. self: the word is aimed at the speaker
_SELF = re.compile(rf"\b(?:i am|i'm|im|i feel(?: like)?|feeling(?: like)?|i was|i can be|i'm being|i look like)\s+(?:such\s+|so\s+|really\s+|totally\s+)?(?:an?\s+)?(?:complete\s+|total\s+|absolute\s+|real\s+)?(?:{HOSTILE_WORDS})\b", re.I)
_SELF_TALK = re.compile(r"\b(?:i hate myself|i suck|my stupid|i'm so dumb|stupid me)\b", re.I)
_SECOND_PERSON = re.compile(r"\byou(?:'re|’re| are| r|rself)?\b|\bu\s*r\b|\byour\b", re.I)
# 3. fake apology: starts like repair, then attacks or brushes off
_FAKE = [
    re.compile(rf"\b(?:{_APOLOGY}|no offen[cs]e|with all due respect|don'?t take this the wrong way|not to be rude|i hate to say)\b[^.!?]{{0,25}}\bbut\b", re.I),     # sorry, but ... / no offense, but ...
    re.compile(r"\bwith all due respect\b", re.I),                                                                          # a brush-off before an attack
    re.compile(r"\bsorry\s+(?:that\s+|if\s+|you\s+|u\s+)(?:you\s+|u\s+)?(?:feel|felt|are|'re|re|think|got|take|took|can't|cannot|don't|didn't|couldn't)\b", re.I),   # sorry you feel that way / sorry if you are offended
    re.compile(r"\b(?:sorry|apolog\w+)\b[^.!?]{0,30}\b(?:you(?:'re| are)|u r)\s+(?:so\s+|too\s+|just\s+)?(?:" + HOSTILE_WORDS + r"|sensitive|slow|dense|weak)\b", re.I),   # sorry you are so sensitive
]


def classify(text) -> set:
    """The labels that apply to one message: a subset of {'mention', 'self', 'fake_apology'}."""
    t = ' '.join(str(text or '').split())
    out = set()
    if not t:
        return out
    fake = any(p.search(t) for p in _FAKE)
    if fake:
        out.add('fake_apology')
    elif any(p.search(t) for p in _MENTION):
        out.add('mention')
    if not out and (_SELF.search(t) or _SELF_TALK.search(t)) and not _SECOND_PERSON.search(re.sub(_SELF, '', t)):
        out.add('self')
    return out


NEUTRAL = ('mention', 'self')


def apply(ratings, labels) -> dict:
    """A copy of the tone ratings with open hostility taken to 0 for a message that only mentions an insult or aims it at the speaker. Other ratings stay."""
    if not ratings or not (set(labels or ()) & set(NEUTRAL)):
        return ratings
    out = dict(ratings)
    for tone in OVERT:
        if tone in out:
            out[tone] = 0
    return out


def labels_from(value) -> set:
    return {x for x in str(value or '').split(',') if x in ('mention', 'self', 'fake_apology')}


def encode(labels) -> str:
    return ','.join(sorted(labels)) or '-'            # '-' = read, nothing found (an empty value means the message was never read)
