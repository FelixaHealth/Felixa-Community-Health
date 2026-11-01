"""Builds a preview of the Vibe Check report section (HTML + PDF) from the synthetic demo data.

DEMO ONLY. Messages are labelled by a keyword stub, not a language model, and the earlier runs
are invented so the progress charts have something to draw. Nothing here is real analysis.

    python3 demo/build_demo.py            -> demo/out/vibe_check_demo.html, vibe_check_demo.pdf
"""
import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vibe_check import pdf_flowables, render_html, run_vibe_check, units_from_file  # noqa: E402
from vibe_check.testing import KeywordStubModel  # noqa: E402

OUT = ROOT / 'demo' / 'out'
OUT.mkdir(parents=True, exist_ok=True)
HOST_CSS = ROOT / 'demo' / 'host_style.css'   # copy of the app stylesheet, so the preview matches the app

# Invented earlier runs (oldest first) so the trend, sentiment and forecast views have data.
HISTORY = [
    dict(created_at='2026-02-08T12:00:00', score=3.9, friendly=22, neutral=38, unfriendly=20, flagged_count=16,
         toxicity_types=dict(insults=7, harassment=5, threats=1, profanity=4, spam=2), sources=['#general', '#ranked'], analyzed_n=80, sensitivity='medium'),
    dict(created_at='2026-02-15T12:00:00', score=4.4, friendly=26, neutral=40, unfriendly=16, flagged_count=12,
         toxicity_types=dict(insults=6, harassment=4, threats=1, profanity=3, spam=1), sources=['#general', '#ranked'], analyzed_n=82, sensitivity='medium'),
    dict(created_at='2026-02-22T12:00:00', score=4.9, friendly=29, neutral=41, unfriendly=14, flagged_count=11,
         toxicity_types=dict(insults=5, harassment=4, threats=0, profanity=3, spam=1), sources=['#general', '#ranked', '#lfg'], analyzed_n=84, sensitivity='medium'),
    dict(created_at='2026-03-01T12:00:00', score=5.2, friendly=30, neutral=42, unfriendly=12, flagged_count=10,
         toxicity_types=dict(insults=4, harassment=3, threats=0, profanity=2, spam=1), sources=['#general', '#ranked', '#lfg'], analyzed_n=84, sensitivity='medium'),
]


def main():
    units = units_from_file(ROOT / 'sample_data/sample_guild_chat.csv') + units_from_file(ROOT / 'sample_data/sample_team_retro.txt')
    call = KeywordStubModel()
    now = lambda: dt.datetime(2026, 3, 8, 12, 0, 0)   # noqa: E731
    first = run_vibe_check(units, call, sleep=lambda s: None, now=now)                   # a first-ever run: no history
    full = run_vibe_check(units, call, history=HISTORY, sleep=lambda s: None, now=now)   # with history: charts and forecast

    css = HOST_CSS.read_text() if HOST_CSS.exists() else ''
    banner = ('<div class="flash"><strong>Demo data.</strong> Messages are labelled by a keyword stub, not a language model, and the '
              'earlier runs are invented. This page previews layout only.</div>')

    def page(title, body):
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{title}</title><style>{css}</style></head><body><header class="topbar"><div class="brand"><div><strong>FELIXA</strong>'
                f'<small>BEHAVIOURAL INTELLIGENCE</small></div></div></header><main class="page">{banner}'
                f'<div class="report-content">{body}</div></main></body></html>')

    (OUT / 'vibe_check_demo.html').write_text(page('Vibe Check preview', render_html(full)))
    (OUT / 'vibe_check_first_run.html').write_text(page('Vibe Check preview, first run', render_html(first)))
    (OUT / 'vibe_check_unavailable.html').write_text(page('Vibe Check preview, unavailable', render_html(
        run_vibe_check(units, None, now=now))))

    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate
    SimpleDocTemplate(str(OUT / 'vibe_check_demo.pdf'), pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54,
                      bottomMargin=60, title='Vibe Check preview').build(pdf_flowables(full))
    print('wrote', *sorted(p.name for p in OUT.iterdir()))


if __name__ == '__main__':
    main()
