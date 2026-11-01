"""Incognito mode: takes people's names out of the messages before anything reads them.

What it removes: the speaker label at the start of a chat line ("Alice: ..." or a WhatsApp "12/03/2026, 10:15 - Alice Brown: ..."), which becomes
"Member 1", "Member 2" and so on (the same person keeps the same number across the whole check); @mentions and Discord <@123> mentions;
and email addresses. What it cannot find is a name typed inside a sentence ("thanks Alice for helping"): that stays, and the page says so.
Structured files (CSV, JSON) already lose their author column on import, so only the text is touched here.
"""
import re

MEMBER = re.compile(r'Member \d+')
NOT_NAMES = {'note', 'edit', 'update', 'reminder', 'warning', 'tip', 'q', 'a', 'ps', 'nb', 'important', 'question', 'answer', 'summary', 're',
             'fyi', 'todo', 'quote', 'source', 'example', 'result', 'results', 'problem', 'solution', 'step', 'steps', 'topic', 'subject'}
STAMP = r'(?:\[\s*)?\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?(?:\s*\])?'
LABEL = re.compile(rf'^(?P<stamp>\s*{STAMP}\s*[-–—]?\s*)?(?P<name>[^\W\d_][\w.\-]*(?: [^\W\d_][\w.\-]*){{0,2}}|\w[\w.\-]{{0,29}}):\s+(?=\S)')
MENTION = re.compile(r'<@[!&]?\d{5,}>|(?<![\w@])@[\w.\-]{2,32}')
EMAIL = re.compile(r'[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+')


class Pseudonyms:
    """The same name gets the same 'Member N' for as long as this object lives."""

    def __init__(self):
        self.numbers = {}

    def label(self, name):
        key = ' '.join(name.lower().split())
        if key not in self.numbers:
            self.numbers[key] = len(self.numbers) + 1
        return f'Member {self.numbers[key]}'


def scrub(text, names):
    """The text with speaker labels, mentions and emails replaced. `names` is a Pseudonyms shared by every message of the check."""
    def label(m):
        name = m.group('name').strip()
        if MEMBER.fullmatch(name):
            return m.group(0)                                    # already hidden by an earlier run
        if name.lower() in NOT_NAMES or len(name) > 40:
            return m.group(0)
        return (m.group('stamp') or '') + names.label(name) + ': '
    text = LABEL.sub(label, text, count=1)
    text = EMAIL.sub('[email]', text)
    return MENTION.sub('@member', text)
