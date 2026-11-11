"""Deescalation: the messages that calm a chat down, and whether the heat fell after them.

A calming message is one that is not heated and shows empathy, politeness or encouragement (a tone rated 2 or 3). Two things are built from the ratings the app already keeps:

* `series(m, units)`: for each part of the chat, how many calming messages that came soon after heat there are in every 100 messages. It is the blue line of the Trends graph.
  "Soon after heat" = within the 4 messages after a heated one, or within 30 minutes of one when the chat has times.
* `traction(units)`: for each incident of Escalation (`incidents.find`), the first calming message and what followed. The heat held back when no heated message came after it.

A calming message describes wording, never a person, and it says nothing about what anyone meant."""
import incidents
import measures

WINDOW = 4            # messages after a heated one in which a calming message counts as a reaction to it
MINUTES = 30


def _reacting(msgs, timed):
    """The positions of the calming messages that follow heat closely."""
    out = set()
    last_heat = None
    for j, m in enumerate(msgs):
        if m['heated']:
            last_heat = j
        elif m['calming'] and last_heat is not None:
            close = ((m['when'] - msgs[last_heat]['when']).total_seconds() / 60.0 <= MINUTES) if timed else (j - last_heat <= WINDOW)
            if close and j - last_heat <= max(WINDOW, 12):
                out.add(j)
    return out


def series(m, units):
    """The calming messages after heat per 100 messages in each part of the chat, one number for each part Trends draws; [] when the parts do not line up."""
    msgs, timed = incidents.messages(units)
    spans = measures.cut(len(msgs))
    if not spans or len(spans) != len(m['stretches']):
        return []
    hit = _reacting(msgs, timed)
    return [100.0 * sum(1 for j in range(a, b) if j in hit) / max(1, b - a) for a, b in spans]


GENERIC = {'heat': 'You always do this. I am done with this thread.', 'calm': 'That sounds really hard. Let us take five minutes and then look at it together.',
           'traction': ['Thanks. I am sorry I snapped.', 'Okay. Let us start with the first item.']}


def stages(found):
    """The diagram of how deescalation works, with messages from the best incident: [{'key', 'title', 'about', 'examples', 'real'}]."""
    best = next((i for i in found if i['calm'] and i['calm']['held']), None) or next((i for i in found if i['calm']), None)
    real = best is not None
    c = best['calm'] if real else None
    heat = c['last_heat'] if real and c['last_heat'] else GENERIC['heat']
    calm = c['text'] if real else GENERIC['calm']
    follow = c['follow'] if real and c['follow'] else GENERIC['traction']
    held = (not real) or c['held']
    return [
        {'key': 'heat', 'title': 'Heat', 'about': 'Heated messages pile up in an incident.', 'examples': [heat], 'real': real,
         'part': best['when'] if real else ''},
        {'key': 'calm', 'title': 'Blue flag: someone calms it', 'about': 'A message that is not heated and shows care, politeness or encouragement, right after the heat.',
         'examples': [calm], 'real': real, 'part': ''},
        {'key': 'traction', 'title': 'Traction: the heat falls' if held else 'No traction yet: the heat comes back',
         'about': 'The messages that follow show whether the calming message took hold.', 'examples': follow, 'real': real, 'part': ''}]


def view(units, gap=incidents.DEFAULT_GAP):
    """Everything the Deescalation page and the traction panel need."""
    found = incidents.find(units, gap)
    tried = [i for i in found if i['calm']]
    held = [i for i in tried if i['calm']['held']]
    minutes = sorted(i['calm']['minutes_after'] for i in tried if i['calm']['minutes_after'] is not None)
    return {'incidents': found, 'tried': tried, 'held': held, 'stages': stages(found), 'real': bool(tried),
            'median_minutes': minutes[len(minutes) // 2] if minutes else None}
