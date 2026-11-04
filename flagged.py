"""The full list of flagged messages of the latest Vibe Check: filtered by kind, severity, source and a word, never cut off."""
from vibe_check.common import short_date, type_label

LEVELS = (('', 'Any severity'), ('7', 'Serious (7 or more)'), ('4', 'Moderate (4 or more)'))
SORTS = (('severity', 'Most severe first'), ('time', 'Oldest first'), ('source', 'By source'))
PER_PAGE = 25
SAFETY_TYPES = ('threat', 'hate_speech')


def level_of(severity) -> str:
    severity = int(severity or 0)
    return 'serious' if severity >= 7 else 'moderate' if severity >= 4 else 'mild'


def build(vibe, *, kind='', level='', source='', q='', sort='severity', page=1) -> dict:
    """Everything the page needs. `vibe` is the latest Vibe Check; nothing here reads outside it."""
    items = [dict(f) for f in (vibe or {}).get('flagged') or []]
    kinds = sorted({f.get('type') or 'other' for f in items})
    sources = sorted({f.get('source') for f in items if f.get('source')})
    floor = int(level) if level in ('4', '7') else 0
    q = (q or '').strip().lower()
    shown = [f for f in items
             if (not kind or (f.get('type') or 'other') == kind) and int(f.get('severity') or 0) >= floor
             and (not source or f.get('source') == source) and (not q or q in str(f.get('message') or '').lower())]
    if sort == 'time':
        shown.sort(key=lambda f: (str(f.get('timestamp') or '~'), f.get('index') or 0))
    elif sort == 'source':
        shown.sort(key=lambda f: (str(f.get('source') or ''), -int(f.get('severity') or 0)))
    else:
        shown.sort(key=lambda f: (-int(f.get('severity') or 0), f.get('index') or 0))
    pages = max(1, -(-len(shown) // PER_PAGE))
    page = min(max(1, page), pages)
    rows = []
    for f in shown[(page - 1) * PER_PAGE:page * PER_PAGE]:
        who = {'SUBJECT': 'Person', 'AUDIENCE': 'Audience'}.get(str(f.get('role') or '').upper(), '')
        rows.append({'kind': type_label(f.get('type') or 'other'), 'severity': int(f.get('severity') or 0), 'level': level_of(f.get('severity')), 'message': f.get('message') or '',
                     'meta': ' · '.join(x for x in (f.get('source'), who, short_date(f['timestamp']) if f.get('timestamp') else '') if x)})
    return {'rows': rows, 'total': len(items), 'matching': len(shown), 'page': page, 'pages': pages, 'kinds': [(k, type_label(k)) for k in kinds], 'sources': sources,
            'serious': sum(1 for f in items if int(f.get('severity') or 0) >= 7), 'safety': any(f.get('type') in SAFETY_TYPES for f in shown),
            'filtered': bool(kind or floor or source or q), 'all': shown}


def csv_rows(shown) -> list:
    rows = [['Severity', 'Type', 'Source', 'Date', 'Flagged message']]
    rows += [[f.get('severity'), type_label(f.get('type') or 'other'), f.get('source') or '', f.get('timestamp') or '', f.get('message') or ''] for f in shown]
    return rows
