"""Word rules: an engine that needs no AI.

It reads each message with lists of words and patterns and answers in the same shapes a language model does, so the rest of the app (the 13 tone ratings,
Vibe Check, the guidance) works on it unchanged. It is instant, free, and nothing leaves this computer. It cannot read context: it does not know that a
word is quoted, joked about or meant kindly, and its lists are English. Use it to look around quickly, and a language model when the reading matters.

`rules_call(instructions, input_text, schema=None)` has the contract of every model function of the app: it returns (JSON text, model name).
Nothing here reads a name or sends a message anywhere."""
import json
import re

ENGINE = 'rules:words'
MODEL_NAME = 'word-rules'
ATTRIBUTES = ('Polite', 'Funny', 'Empathetic', 'Encouraging', 'Ignorant', 'Egocentric', 'Neurotic', 'Sarcastic', 'Agitated', 'Aggressive', 'Rude', 'Disrespectful', 'Judgmental')


def _words(*items):
    """One pattern for a list of words and phrases: whole words only ('hell' never matches 'hello'), any case, an apostrophe either way."""
    parts = [re.escape(i).replace("'", "['’]").replace(r'\ ', r'\s+') for i in items]
    return re.compile(r'(?<![\w])(?:' + '|'.join(parts) + r')(?![\w])', re.I)


# tone -> (lists of words by strength). A weak hit counts 1, a strong hit counts 2, and the sum picks the rating: 1 -> 1, 2 -> 2, 3 or more -> 3.
LEXICON = {
    'Polite': (_words('please', 'thanks', 'thank you', 'thx', 'appreciate it', 'much appreciated', 'kindly', 'excuse me', 'pardon', 'welcome', 'my pleasure', 'cheers', 'sorry'),
               _words('thank you so much', 'many thanks', 'i appreciate', 'with respect', 'if you do not mind', "if you don't mind", 'would you mind')),
    'Funny': (_words('lol', 'lmao', 'haha', 'hehe', 'rofl', 'funny', 'joke', 'jk', 'kidding', 'hilarious', 'meme'),
              _words('i am dying', "i'm dying", 'cannot breathe', "can't breathe", 'made my day')),
    'Empathetic': (_words('i understand', 'sounds hard', 'sounds tough', 'must be hard', 'must be tough', 'hope you are ok', "hope you're ok", 'take care', 'here for you',
                          'sorry to hear', 'feel for you', 'been there', 'no worries'),
                   _words('you are not alone', "you're not alone", 'sending hugs', 'thinking of you', 'that sounds really', 'i am here if you', "i'm here if you")),
    'Encouraging': (_words('great', 'nice', 'good job', 'well done', 'well played', 'gg', 'keep it up', 'congrats', 'congratulations', 'proud', 'good luck', 'awesome', 'amazing', 'love it',
                           'nailed it', 'welcome aboard'),
                    _words('you can do it', 'proud of you', 'keep going', 'great work', 'great job', 'well deserved', 'you got this', "you've got this")),
    'Ignorant': (_words('whatever', 'who cares', 'not my problem', 'so what', 'do not care', "don't care", 'dont care', 'no idea', 'boring'),
                 _words('stop asking', 'not listening', 'i do not care', "i don't care", 'nobody asked', 'who asked')),
    'Egocentric': (_words('my way', 'i told you', 'i said so', 'as i said', 'i am the best', "i'm the best", 'because i said'),
                   _words('i know better', 'listen to me', 'it is all about me', "it's all about me", 'i am always right', "i'm always right")),
    'Neurotic': (_words('worried', 'anxious', 'nervous', 'panic', 'scared', 'afraid', 'stressed', 'stress', 'overwhelmed', 'what if', 'terrified', 'cannot cope', "can't cope"),
                 _words('freaking out', 'going to lose it', 'going to panic', 'cannot stop worrying', "can't stop worrying")),
    'Sarcastic': (_words('yeah right', 'sure,', 'as if', 'oh sure', 'thanks a lot', 'nice going', 'how original', 'what a surprise', 'oh wow', '/s'),
                  _words('oh brilliant', 'oh great, another', 'oh great', 'good job genius', 'wow, so helpful', 'thanks for nothing', 'what a genius')),
    'Agitated': (_words('wtf', 'omg', 'ugh', 'seriously', 'come on', 'for god', 'sick of', 'fed up', 'annoying', 'annoyed', 'furious', 'angry', 'mad'),
                 _words('i am done', "i'm done", 'losing my mind', 'cannot believe', "can't believe", 'so sick of', 'driving me crazy')),
    'Aggressive': (_words('shut up', 'get lost', 'back off', 'piss off', 'fight me', 'hate you', 'destroy you', 'crush you'),
                   _words("i'll find you", 'i will find you', 'you are dead', "you're dead", 'watch your back', 'i will kill', "i'll kill", 'i will hurt', "i'll hurt", 'you will regret', "you'll regret")),
    'Rude': (_words('damn', 'crap', 'sucks', 'suck', 'screw you', 'hell no', 'jerk', 'whatever you say'),
             _words('bullshit', 'asshole', 'bitch', 'shit', 'fuck', 'fucking', 'wtf', 'piss')),
    'Disrespectful': (_words('idiot', 'stupid', 'dumb', 'moron', 'loser', 'trash', 'garbage', 'useless', 'clown', 'noob', 'pathetic', 'uninstall'),
                      _words('shut up', 'you are worthless', "you're worthless", 'nobody likes you', 'nobody likes', 'everyone hates you', 'everyone hates', 'waste of space')),
    'Judgmental': (_words('you always', 'you never', 'typical', 'ridiculous', 'shame on you', 'what is wrong with you', "what's wrong with you", 'you people', 'how dare you'),
                   _words('disgusting', 'you should be ashamed', 'pathetic excuse', 'you are the problem', "you're the problem")),
}
CAPS_WORD = re.compile(r"\b[A-Z]{3,}\b")
SHOUT = re.compile(r'!{2,}')
FIRST_PERSON = re.compile(r"\b(?:i|me|my|mine|myself)\b", re.I)


def _count(pattern, text) -> int:
    return len(pattern.findall(text))


def rate(text: str):
    """(ratings 0-3 for the 13 tones, why for each tone that is above 0)."""
    text = str(text or '')
    ratings, why = dict.fromkeys(ATTRIBUTES, 0), {}
    for tone, (weak, strong) in LEXICON.items():
        hits = _count(weak, text) + 2 * _count(strong, text)
        if tone == 'Sarcastic' and '/s' not in text and hits < 2 and not _count(strong, text):
            hits = 0 if hits < 1 else hits                  # 'sure,' alone is a weak hint
        if hits:
            ratings[tone] = min(3, hits)
    if _count(SHOUT, text) or len(CAPS_WORD.findall(text)) >= 2:
        ratings['Agitated'] = min(3, ratings['Agitated'] + (2 if _count(SHOUT, text) and len(CAPS_WORD.findall(text)) >= 2 else 1))
    words = re.findall(r"\w+", text)
    if len(words) >= 8 and len(FIRST_PERSON.findall(text)) / len(words) >= 0.3:
        ratings['Egocentric'] = max(ratings['Egocentric'], 1)
    if ratings['Rude'] and ratings['Disrespectful'] == 0 and re.search(r'\b(?:you|your|u)\b', text, re.I):
        ratings['Disrespectful'] = 1                          # rude wording aimed at someone
    if ratings['Polite'] and ratings['Rude']:
        ratings['Polite'] = max(0, ratings['Polite'] - 1)    # 'sorry, but this sucks': less polite than it sounds
    for tone, value in ratings.items():
        if value:
            why[tone] = 'Word rules matched wording for this tone.'
    return ratings, why


def rating_reply(prompt: str) -> dict:
    units = json.loads(prompt)['units']
    out = []
    for unit in units:
        ratings, why = rate(unit.get('text', ''))
        out.append({'external_id': str(unit.get('external_id')), 'ratings': ratings, 'rationale': why})
    return {'units': out}


# ---- Vibe Check ---------------------------------------------------------------------------------
FRIENDLY = _words('thanks', 'thank you', 'great', 'awesome', 'nice', 'love', 'appreciate', 'well played', 'gg', 'welcome', 'congrats', 'congratulations', 'helpful', 'happy to', 'amazing',
                  'good job', 'well done', 'proud', 'glad', 'good luck', 'please', 'sorry', 'lol', 'haha', 'welcome back', 'cheers')
INSULT = _words('idiot', 'stupid', 'dumb', 'moron', 'loser', 'trash', 'garbage', 'useless', 'clown', 'noob', 'pathetic', 'uninstall', 'worthless', 'jerk', 'asshole')
HARASS = _words('report you', 'everyone hates', 'nobody likes', 'leave this server', 'leave the server', 'shut up', 'go away', 'get lost')
THREAT = _words("i'll find you", 'i will find you', 'you are dead', "you're dead", 'watch your back', 'i will kill', "i'll kill", 'i will hurt', "i'll hurt", 'you will regret', "you'll regret")
HATE = _words('i hate all', 'go back to your country', 'subhuman', 'vermin')
BULLY = _words('nobody wants you', 'no one likes you', 'you have no friends', 'everyone laughs at you', 'you are a joke', "you're a joke", 'kill yourself', 'kys')
PROFANE = _words('damn', 'wtf', 'crap', 'hell', 'shit', 'fuck', 'fucking', 'bullshit', 'bitch', 'piss')
SPAM = _words('free coins', 'buy now', 'click here', 'free gift', 'limited offer', 'subscribe to my', 'check out my channel', '[link]')


def vibe_reply(instructions: str, input_text: str) -> str:
    sens = 'low' if 'SENSITIVITY: LOW' in instructions else 'high' if 'SENSITIVITY: HIGH' in instructions else 'medium'
    msgs = [m.group(2) for m in (re.match(r'^(\d+)\. (.*)$', line) for line in input_text.split('\n')) if m]
    labels, flagged = [], []
    tox = dict.fromkeys(('insults', 'harassment', 'threats', 'hate_speech', 'bullying', 'profanity', 'spam'), 0)
    for i, text in enumerate(msgs, 1):
        kind = sev = None
        if THREAT.search(text):
            kind, sev = 'threat', 9
        elif HATE.search(text):
            kind, sev = 'hate_speech', 9
        elif BULLY.search(text):
            kind, sev = 'bullying', 8
        elif HARASS.search(text):
            kind, sev = 'harassment', 7
        elif INSULT.search(text):
            kind, sev = 'insult', 5
        elif SPAM.search(text):
            kind, sev = 'spam', 3
        elif PROFANE.search(text) and sens != 'low':
            kind, sev = 'profanity', 2 if sens == 'high' else 4
        if kind and sens == 'low' and kind in ('insult', 'spam'):
            kind = None                                       # banter and spam are tolerated at low sensitivity
        if kind:
            labels.append('unfriendly')
            flagged.append({'n': i, 'type': kind, 'severity': sev})
            tox[{'insult': 'insults', 'threat': 'threats'}.get(kind, kind)] += 1
        elif FRIENDLY.search(text):
            labels.append('friendly')
        else:
            labels.append('neutral')
    n = max(len(msgs), 1)
    f, u, z = labels.count('friendly'), labels.count('unfriendly'), labels.count('neutral')
    score = round(max(0.0, min(10.0, 10 * (f + 0.5 * z) / n - 2.0 * u / n)), 1)
    return json.dumps({'friendlinessScore': score, 'sentiment': {'friendly': f, 'neutral': z, 'unfriendly': u}, 'flaggedMessages': flagged, 'toxicityTypes': tox,
                       'communityStrengths': 'Members thank each other and cheer each other on, which gives newcomers a reason to stay.',
                       'recommendation': 'Spotlight the members who welcome newcomers each week. Invite the group to agree one shared goal for the next conversation.',
                       'summary': f'A {"warm" if score >= 6 else "mixed"} conversation, read with word lists, with {u} unfriendly message{"" if u == 1 else "s"}.'})


def synthesis_reply(input_text: str) -> str:
    stats = json.loads(input_text)
    friendly = (stats.get('sentimentPercent') or {}).get('friendly', 0)
    return json.dumps({'communityStrengths': f'Members keep the tone warm: {friendly}% of the messages read as friendly.',
                       'recommendation': 'Celebrate the members who welcome newcomers and turn their habits into a shared ritual.',
                       'summary': f'Impact score {stats.get("impactScore")}/10 across {stats.get("messagesAnalyzed")} messages.'})


# ---- guidance -----------------------------------------------------------------------------------
def guidance_reply(prompt: str) -> str:
    data = json.loads(prompt)
    metrics = sorted(data.get('behavior_metrics', []), key=lambda m: -m['FAS'])
    top = metrics[0] if metrics else {'attribute': 'none', 'FAS': 0}
    goals = [g for g in data.get('goals') or [] if g]
    return json.dumps({
        'summary': f'In the rated messages the strongest signal is {top["attribute"]} (FAS {top["FAS"]}). The ratings come from word lists, so read them as a first look.',
        'working': [{'title': 'Where the tone is warm', 'body': 'Polite, encouraging and caring wording appears in the rated messages. Look at which parts of the chat it comes from.'}],
        'limitations': [{'title': 'Word rules cannot read context', 'body': 'A word list does not know who a message is aimed at, whether it is a quote, or a joke between friends. Run a language model over the same messages to check.'}],
        'recommendations': [{'action': 'Spotlight the members who set a warm tone' + (f' while you look at "{goals[0]}"' if goals else ''), 'why': 'What is celebrated gets repeated.',
                             'measure': 'The positive tones on the next check'}],
        'confidence': 'LOW', 'confidence_reason': 'Word rules, not a language model.'})


def rules_call(instructions, input_text, schema=None, **_ignored):
    """The model function of the Word rules engine: (answer as JSON text, 'word-rules')."""
    head = str(instructions or '')
    if head.startswith('You are the FELIXA observable-communication rating engine'):
        return json.dumps(rating_reply(input_text)), MODEL_NAME
    if head.startswith('You are FELIXA, a behavioural intelligence system'):
        return guidance_reply(input_text), MODEL_NAME
    if 'Write the narrative' in head:
        return synthesis_reply(input_text), MODEL_NAME
    if head.startswith('You are the FELIXA Vibe Check engine'):
        return vibe_reply(head, str(input_text or '')), MODEL_NAME
    return '{}', MODEL_NAME


def is_rules(engine) -> bool:
    return str(engine or '') == ENGINE
