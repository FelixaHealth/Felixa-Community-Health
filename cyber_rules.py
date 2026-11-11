"""Cyber Attacks & Security Threats: the word and link rules behind the five behaviours of that category.

* Phishing: a message that pushes someone to "verify", "unlock" or "restore" an account, usually with a link.
* Credential or code requests: a request for a password, a one-time code, a PIN or a recovery phrase.
* Malicious links or files: an executable or script file, or a link that dangles a free prize or an urgent download.
* Hacking threats: a stated intention to hack, take over, leak or flood someone's account, device or address.
* Raids and flooding: a call for many people to pile onto a target, DDoS talk, mass reports, or the same message sent again and again.

These are literal English rules, unvalidated. A missed or a false match is expected. A match is a candidate for a person to read, never a verdict, and none of them gives a black flag.
A warning such as "never send your password" is not a match. The app reads wording, and a link in a message is never opened."""
from __future__ import annotations

import re

BEHAVIOURS = ('Phishing', 'Credential or Code Requests', 'Malicious Links or Files', 'Hacking Threats', 'Raids and Flooding')

URL = re.compile(r'https?://\S+|\bwww\.\S+|\b[\w\-]+\.(?:com|net|org|io|ru|xyz|top|click|link|info|co|me|gg)/\S*', re.I)
SHORT = re.compile(r'\b(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|is\.gd|cutt\.ly|rb\.gy|shorturl\.at)/\S+', re.I)
IP_URL = re.compile(r'https?://\d{1,3}(?:\.\d{1,3}){3}', re.I)
WARNING = re.compile(r"\b(?:never|don'?t|do not|dont|should not|shouldn'?t|must not|won'?t|will not|not to|no one should|nobody should|stop)\b[^.!?\n]{0,30}$", re.I)

_SECRET = r"(?:password|passcode|passphrase|pass word|pin(?: code)?|otp|2fa(?: code)?|verification code|security code|one[- ]time (?:code|password)|login(?: details| info)?|credentials|seed phrase|recovery phrase|cvv|card number|private key|auth(?:entication)? code|sms code)"
RULES = (
    ('Credential or Code Requests', 3, re.compile(rf"\b(?:send|give|tell|share|text|dm|forward|read out|type|paste|enter|provide)\b(?: me| us)?(?: (?:your|ur|the|that|this))? ?{_SECRET}", re.I)),
    ('Credential or Code Requests', 3, re.compile(rf"\bwhat(?:'s| is)\s+(?:your|ur)\s+{_SECRET}", re.I)),
    ('Credential or Code Requests', 3, re.compile(r"\b(?:send|give|read|tell|forward|share)(?: me| us)? (?:the )?(?:code|number) (?:you|that you|we|that we) (?:just )?(?:received|got|get|were sent|have been sent)", re.I)),
    ('Phishing', 2, re.compile(r"\b(?:verify|confirm|validate|update|secure|unlock|restore|reactivate|re-?activate|renew)\b[^.!?\n]{0,25}\b(?:your |ur )?(?:account|password|identity|payment|billing|login|wallet|profile|details)\b", re.I)),
    ('Phishing', 2, re.compile(r"\byour (?:account|wallet|profile|card|payment|login)\b[^.!?\n]{0,25}\b(?:has been|is|was|will be|have been)\b[^.!?\n]{0,20}\b(?:locked|suspended|disabled|compromised|limited|restricted|hacked|closed|flagged|blocked)\b", re.I)),
    ('Phishing', 2, re.compile(r"\b(?:unusual|suspicious|unauthori[sz]ed) (?:sign[- ]?in|log[- ]?in|activity|login attempt)\b", re.I)),
    ('Phishing', 2, re.compile(r"\b(?:claim|collect|redeem|get) your (?:prize|reward|gift|refund|winnings|free)\b|\byou(?:'ve| have)? (?:won|been selected)\b", re.I)),
    ('Malicious Links or Files', 3, re.compile(r"\b[\w\-. ]{1,60}\.(?:exe|scr|bat|cmd|msi|vbs|ps1|apk|jar|com\.exe|js)\b(?!\w)", re.I)),
    ('Malicious Links or Files', 2, re.compile(r"\b(?:free (?:nitro|robux|v-?bucks|gift ?card|crypto|bitcoin|followers|vip)|nitro (?:giveaway|gift)|airdrop)\b", re.I)),
    ('Malicious Links or Files', 2, re.compile(r"\b(?:download|install|run|open)\b[^.!?\n]{0,25}\b(?:this|the|my|attached)\b[^.!?\n]{0,20}\b(?:file|update|patch|attachment|app|tool|crack|keygen|zip)\b", re.I)),
    ('Hacking Threats', 3, re.compile(r"\b(?:i(?:'ll| will|'m going to|m going to| am going to|'m gonna|m gonna)|we(?:'ll| will| are going to|'re going to)|gonna|going to|imma|ima)\s+(?:hack|ddos|dox+|swat|crack)\b", re.I)),
    ('Hacking Threats', 3, re.compile(r"\b(?:i(?:'ll| will|'m going to|m going to| am going to|'m gonna|m gonna)|we(?:'ll| will| are going to|'re going to)|gonna|going to|imma|ima)\s+(?:take over|steal|compromise|breach|wipe|brick|leak|crash|flood)\s+(?:your|ur|his|her|their|the|this|that)\s+(?:account|server|pc|computer|laptop|phone|ip|address|data|password|network|site|website|channel|page|stream|files|photos|identity|info|bot)\b", re.I)),
    ('Hacking Threats', 3, re.compile(r"\bi (?:have|got|know|found) (?:your|ur) (?:ip|ip address|password|logins?|data|address|location|files|photos|webcam|account)\b", re.I)),
    ('Hacking Threats', 3, re.compile(r"\b(?:watch|wait|just wait)\b[^.!?\n]{0,15}\b(?:your|ur) (?:account|pc|computer|phone|server|ip|data|network|channel)\b[^.!?\n]{0,15}\b(?:gets?|goes?|will be|is gonna be|is going to be)\b[^.!?\n]{0,10}\b(?:hacked|gone|taken|down|wiped|leaked)\b", re.I)),
    ('Raids and Flooding', 3, re.compile(r"\b(?:ddos|d-dos|dos attack|booter|stresser|botnet|ip stress)\b", re.I)),
    ('Raids and Flooding', 2, re.compile(r"\b(?:everyone|all of you|everybody|you guys|let'?s|we should|go|join|come)\b[^.!?\n]{0,25}\b(?:spam|flood|brigade|mass[- ]?report|dogpile|swarm|pile on)\b", re.I)),
    ('Raids and Flooding', 2, re.compile(r"\b(?:raid|spam|flood|mass[- ]?report|brigade|dogpile)\b[^.!?\n]{0,15}\b(?:him|her|them|his|her|their|the (?:server|channel|stream|page|chat|account))\b", re.I)),
)


def _warned(text, start) -> bool:
    return bool(WARNING.search(text[max(0, start - 40):start]))


def scan(text) -> list[dict]:
    """The findings of one message: [{'behaviour', 'score', 'quote'}], one for each behaviour at most."""
    text = ' '.join(str(text or '').split())
    found: dict = {}
    for name, score, rx in RULES:
        for m in rx.finditer(text):
            if _warned(text, m.start()):
                continue
            if name not in found or score > found[name]['score']:
                found[name] = {'behaviour': name, 'score': score, 'quote': text[:240]}
            break
    has_link = bool(URL.search(text))
    if 'Phishing' in found and has_link:
        found['Phishing']['score'] = 3
    if 'Credential or Code Requests' not in found and has_link and found.get('Phishing'):
        pass
    if (SHORT.search(text) or IP_URL.search(text)) and re.search(r"\b(?:free|win|won|prize|urgent|now|click|claim|verify|gift|giveaway)\b", text, re.I) and 'Malicious Links or Files' not in found:
        found['Malicious Links or Files'] = {'behaviour': 'Malicious Links or Files', 'score': 2, 'quote': text[:240]}
    return list(found.values())


def flooding(units, *, window_minutes=10, window_messages=10, repeats=3) -> set:
    """The ids of the messages that repeat the same text `repeats` times or more within a short stretch: the same line sent again and again. Needs no sender."""
    from vibe_check.common import parse_ts
    seen: dict = {}
    hits = set()
    for pos, u in enumerate(units):
        key = ' '.join(re.sub(r'[^\w ]', '', str(u.get('text') or '').lower()).split())
        if len(key) < 6:
            continue
        at = parse_ts(u.get('timestamp'))
        run = seen.setdefault(key, [])
        run.append((pos, at, u['id']))
        recent = [r for r in run if ((at - r[1]).total_seconds() / 60.0 <= window_minutes) if at is not None and r[1] is not None] if at is not None else [r for r in run if pos - r[0] <= window_messages]
        if len(recent) >= repeats:
            hits.update(r[2] for r in recent)
    return hits
