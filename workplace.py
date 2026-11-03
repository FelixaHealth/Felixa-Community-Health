"""What a workplace is, and what a check costs.

A workplace is the community or team being looked after: its kind, name, area, the place where people talk, who is asking, and what
they want to find out. A check is one run over fresh messages: what to read, how many, whether to hide names, which dates, how
sensitive, and which engine. This module holds the choices on offer, reads the forms that pick them, and works out the estimate shown
before a check starts. It touches no database and no model.
"""
import math
import re

import config

# ---- the choices -----------------------------------------------------------------------
ENVIRONMENTS = [('Online community', 'A forum, server or group that people join online'),
                ('Team', 'People who work together'),
                ('Chat', 'A group chat or channel'),
                ('Other', 'Something else')]
AREAS = ['Gaming', 'Cybersecurity', 'IT and software', 'Customer service', 'Education', 'Healthcare', 'Finance',
         'Retail and e-commerce', 'Media and creators', 'Non-profit', 'Government and public sector', 'Other']
CHANNELS = ['Discord', 'Slack', 'Microsoft Teams', 'WhatsApp', 'Telegram', 'Reddit', 'YouTube', 'Twitch', 'Google Chat', 'Zoom', 'Google Meet',
            'Messages (SMS or iMessage)', 'Facebook Messenger', 'Signal', 'Email', 'Forum or website', 'Social media comments', 'Other']
# Where the messages of a platform that has no export button come from. Shown on step 1 of a check when the workplace talks there.
CHANNEL_TIPS = {
    'Reddit': 'Reddit has no export button for a thread. Save the comments as a CSV or text file with a Reddit export tool or your own data request, then add the file. '
              'A public thread link can also work, but Reddit loads most comments after the page opens, so the link may bring only the post.',
    'YouTube': 'YouTube comments load after the page opens, so a link brings little. Export the comments to a CSV or text file with a comment export tool, then add the file.',
    'Twitch': 'Twitch chat is not saved by Twitch. Use a chat log export from your streaming or moderation tool, or a chat downloader for past broadcasts, and add the file (CSV, JSON or text).',
}
# The invitation to add the Vibe Check bot to a Discord server. The permissions in it are View Channels, Send Messages, Embed Links, Attach Files and
# Read Message History (117760). The bot runs inside Discord; it does not send anything to this app.
DISCORD_BOT_URL = ('https://discord.com/oauth2/authorize?client_id=1475337312303841401&permissions=117760&integration_type=0'
                   '&scope=bot+applications.commands')
USER_ROLES = ['Admin', 'Owner or founder', 'Manager', 'Team lead', 'Moderator', 'Community manager', 'HR or people team', 'Analyst', 'Other']
GOAL_OPTIONS = ['Is my community healthy?', 'Why are people leaving?', 'Why did my revenue go down?', 'How do people communicate?',
                'Are people engaged?']
OTHER = 'Other'
MAX_TEXT = 120

SETUP_STEPS = [(1, 'Environment'), (2, 'Workplace'), (3, 'Area'), (4, 'Chat'), (5, 'You'), (6, 'Goals')]
CHECK_STEPS = [(1, 'Upload'), (2, 'Volume'), (3, 'Incognito'), (4, 'Time frame'), (5, 'Sensitivity'), (6, 'Engine')]

VOLUMES = [100, 300, 1000, 3000]                 # the presets; "all of them" and a number the person types are always offered too
VOLUME_MIN = 5                                   # Vibe Check needs at least this many messages
DEFAULT_VOLUME = 300

SENSITIVITY_HELP = {
    'low': 'Treats banter and strong language as normal. Fits adult and gaming communities.',
    'medium': 'A balanced bar for most workplaces and communities.',
    'high': 'Flags any rude or negative tone. Fits children, families and formal settings.',
}


# ---- reading the setup forms ------------------------------------------------------------
def pick(form, name, options, label):
    """Reads one choice that may be 'Other'. Returns (value, error). When 'Other' is chosen the typed text is the value, and it is required."""
    choice = str(form.get(name, '')).strip()
    if choice not in options:
        return '', f'Choose {label}.'
    if choice != OTHER:
        return choice, ''
    typed = ' '.join(str(form.get(name + '_other', '')).split())[:MAX_TEXT]
    return (typed, '') if typed else ('', f'You chose Other: tell us {label}.')


def split_choice(value, options):
    """The reverse of `pick`, for showing a saved value again: (choice, typed). A value that is not on the list was typed under Other."""
    if not value:
        return '', ''
    return (value, '') if value in options and value != OTHER else (OTHER, value)


MEMBERS_MAX = 100_000_000


def read_members(form, name='members'):
    """The number of members, an optional whole number. Returns (number or None, error). Commas and spaces are fine: '1,200' is 1200."""
    text = str(form.get(name, '')).replace(',', '').replace(' ', '').strip()
    if not text:
        return None, ''
    if not text.isdigit() or not 1 <= int(text) <= MEMBERS_MAX:
        return None, f'Type the number of members as a whole number from 1 to {MEMBERS_MAX:,}, or leave it blank.'
    return int(text), ''


def time_zones() -> list:
    """The time zone names offered in the list ('Europe/London'), UTC first. Empty when this computer has no time zone data."""
    try:
        import zoneinfo
        return ['UTC'] + sorted(z for z in zoneinfo.available_timezones() if '/' in z and not z.startswith(('Etc/', 'posix', 'right', 'SystemV')))
    except Exception:
        return []


def read_time_zone(form, name='time_zone'):
    """The time zone, optional. Returns (name or '', error). Only a name from the list is kept, so nothing else is ever saved or shown."""
    text = ' '.join(str(form.get(name, '')).split())
    if not text:
        return '', ''
    return (text, '') if text in time_zones() else ('', 'Choose a time zone from the list, for example Europe/London, or leave it blank.')


def read_goals(form):
    """The goal checkboxes and the Other box. Returns (goals, other_text, error). Other, when ticked, needs text."""
    chosen = [g for g in form.getlist('goals') if g in GOAL_OPTIONS or g == OTHER]
    other = ' '.join(str(form.get('goal_other', '')).split())[:MAX_TEXT * 4]
    wants_other = OTHER in chosen
    goals = [g for g in GOAL_OPTIONS if g in chosen]
    if wants_other and not other:
        return goals, '', 'You ticked Other: tell us what you want to find out.'
    if not goals and not wants_other:
        return [], '', 'Choose at least one thing you want to find out.'
    return goals, other if wants_other else '', ''


# ---- engines and the estimate ------------------------------------------------------------
SECONDS_PER_BILLION = 1.4        # a model call on a typical laptop CPU takes about 3 s plus this many seconds per billion parameters
BASE_SECONDS = 3.0
FAST, SLOW = 0.6, 1.7            # the range around that: a good GPU is faster, a busy or older machine is slower


def params_billions(name, details=None):
    """The size of a model in billions of parameters: from Ollama's own 'parameter_size' when it gave one, else from the name ('qwen2.5:7b')."""
    size = str((details or {}).get('parameter_size') or '')
    found = re.search(r'(\d+(?:\.\d+)?)\s*([BM])', size, re.I) or re.search(r'(?<![\d.])(\d+(?:\.\d+)?)([bm])\b', name, re.I)
    if not found:
        return 7.0
    value = float(found.group(1))
    return value / 1000 if found.group(2).upper() == 'M' else value


def seconds_per_call(billions):
    return BASE_SECONDS + SECONDS_PER_BILLION * billions


def calls_for(messages, volume, sources=1, rate_signals=True):
    """How many model calls a check makes over `messages` stored messages with the chosen volume. Returns a dict of the parts."""
    n = min(messages, volume) if volume else messages
    rating = math.ceil(n / config.RATING_BATCH) if rate_signals else 0
    vibe = math.ceil(n / 40) + max(1, sources) if n else 0                 # Vibe Check reads 40 messages at a time, per source
    extra = 2 if n else 0                                                    # the written guidance, and Vibe Check's closing summary
    return {'messages': n, 'rating': rating, 'vibe': vibe, 'extra': extra, 'total': rating + vibe + extra}


def duration_text(seconds):
    if seconds < 60:
        return 'under a minute'
    minutes = seconds / 60
    if minutes < 90:
        return f'{round(minutes)} minute{"" if round(minutes) == 1 else "s"}'
    hours = minutes / 60
    return f'{hours:.1f} hours'


def estimate(messages, volume, billions=None, sources=1, per_call=None):
    """The estimate for one engine: the model calls and a range of time, as numbers and as a sentence. A guess from typical speeds, said so.
    A local model's speed follows its size (`billions`); a cloud model's is `per_call` seconds."""
    parts = calls_for(messages, volume, sources)
    each = per_call if per_call else seconds_per_call(billions or 7.0)
    low, high = parts['total'] * each * FAST, parts['total'] * each * SLOW
    if not parts['total']:
        text = 'Add messages to see an estimate.'
    else:
        a, b = duration_text(low), duration_text(high)
        text = 'Under a minute' if high < 60 else f'Up to {b}' if low < 60 else f'About {a}' if a == b else f'About {a} to {b}'
    return {**parts, 'low': low, 'high': high, 'text': text}


def tier(name):
    """The tag an engine wears: Fastest, Balanced or Best, from the kind of Claude model it is."""
    name = str(name).lower()
    return 'Best' if 'opus' in name else 'Balanced' if 'sonnet' in name else 'Fastest'


def engine_options(models, default, messages, volume, sources=1, cloud_models=(), cloud_ready=False, login_models=(), login_ready=False, openai_models=(), openai_ready=False, rules=False):
    """The engines to choose from, each with its estimate. `models` are what the local model server has downloaded: [{'name', 'details'}].
    The configured default comes first and is marked, whether or not it is downloaded yet. `cloud_models` are (id, name, about, seconds a
    call takes); they follow the local ones, and are marked not ready when the server has no key for them."""
    seen, out = set(), []
    for m in [{'name': default, 'details': {}}] + list(models):
        name = m['name']
        if name in seen:
            continue
        seen.add(name)
        installed = any(x['name'] == name for x in models)
        billions = params_billions(name, m.get('details'))
        if name == default:                                            # the default may be a bare name, whose installed copy carries the details
            installed_copy = next((x for x in models if x['name'] in (name, name + ':latest')), None)
            if installed_copy:
                installed, billions = True, params_billions(name, installed_copy.get('details'))
        out.append({'name': name, 'label': name, 'kind': 'local', 'default': name == default, 'installed': installed, 'billions': billions,
                    'size': f'{billions:g}B parameters', 'estimate': estimate(messages, volume, billions, sources),
                    'tag': 'Fastest' if billions <= 4 else 'Balanced' if billions <= 9 else 'Best', 'price': 'Free, runs on this computer'})
    for model, label, about, seconds in cloud_models:
        out.append({'name': 'claude:' + model, 'label': label, 'kind': 'cloud', 'default': False, 'installed': cloud_ready, 'billions': None,
                    'size': about, 'estimate': estimate(messages, volume, None, sources, per_call=seconds),
                    'tag': tier(model), 'price': 'Free while testing'})
    for alias, label, about, seconds in login_models:
        out.append({'name': 'claudecode:' + alias, 'label': label, 'kind': 'login', 'default': False, 'installed': login_ready, 'billions': None,
                    'size': about, 'estimate': estimate(messages, volume, None, sources, per_call=seconds),
                    'tag': tier(alias), 'price': 'Uses your Claude plan, no key'})
    if rules:
        out.append({'name': 'rules:words', 'label': 'Word rules (no AI)', 'kind': 'rules', 'default': False, 'installed': True, 'billions': None,
                    'size': 'Word lists, no language model', 'estimate': estimate(messages, volume, None, sources, per_call=0.02), 'tag': 'Fastest', 'price': 'Free, nothing leaves this computer'})
    for model, label, about, seconds in openai_models:
        out.append({'name': 'openai:' + model, 'label': label, 'kind': 'openai', 'default': False, 'installed': openai_ready, 'billions': None,
                    'size': about, 'estimate': estimate(messages, volume, None, sources, per_call=seconds),
                    'tag': 'Fastest' if 'mini' in model else 'Balanced', 'price': 'Uses your OpenAI key'})
    return out
