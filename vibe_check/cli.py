"""Run Vibe Check from the command line, on any files, with a local Ollama model.

    python3 -m vibe_check chat.csv notes.txt --model qwen2.5:7b --out report.html --pdf report.pdf
    python3 -m vibe_check chat.csv --history runs.json      # each run adds a point to the progress charts
    python3 -m vibe_check chat.csv --stub                   # no model: layout check only, numbers are meaningless

The model call mirrors Felixa's local_model_call: Ollama's /api/generate, JSON-schema output, loopback only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

from . import engine, ingest
from .forecast import snapshot_from_result
from .render import pdf_flowables, render_html

PAGE_CSS = """
body{margin:0;background:#f5f6f8;color:#212121;font:18px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{width:min(1000px,calc(100% - 40px));margin:40px auto}
.panel{background:#fff;border:1px solid #dfe3e8;border-radius:16px;padding:28px}.panel p{color:#58616e;max-width:72ch}
.eyebrow{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:#76600d;font-weight:700}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th{text-align:left;font-size:13px;color:#58616e;border-bottom:1px solid #cbd1d9;padding:10px}td{padding:10px;border-bottom:1px solid #e9edf1}
.banner{background:#fff9df;border:1px solid #e4d28a;border-radius:10px;padding:14px 18px;margin-bottom:20px}
"""


def ollama_call(model: str, host: str = 'http://127.0.0.1:11434', timeout: int = 480):
    """A model function in the shape run_vibe_check expects. Bypasses any HTTP proxy: the model is local."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(instructions, input_text, schema=None):
        payload = {'model': model, 'system': instructions, 'prompt': input_text, 'stream': False,
                   'format': schema if schema is not None else 'json', 'keep_alive': '5m',
                   'options': {'temperature': 0.1, 'num_ctx': 8192, 'num_predict': 2600}}
        req = urllib.request.Request(host.rstrip('/') + '/api/generate', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            with opener.open(req, timeout=timeout) as response:
                result = json.load(response)
        except Exception as e:
            raise engine.ModelUnavailable(f'Could not reach the model at {host}: {e}') from e
        if result.get('done_reason') == 'length':
            raise RuntimeError('The response reached its limit. Try a smaller batch.')
        return result.get('response', ''), model

    return call


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python3 -m vibe_check', description=__doc__.split('\n')[0])
    ap.add_argument('files', nargs='+', help='CSV, JSON, TXT, MD, PDF or DOCX files')
    ap.add_argument('--sensitivity', choices=sorted(engine.SENSITIVITY_PROMPTS), default=engine.DEFAULT_SENSITIVITY)
    ap.add_argument('--model', default=os.getenv('FELIXA_LOCAL_MODEL', 'qwen2.5:7b'))
    ap.add_argument('--host', default=os.getenv('FELIXA_OLLAMA_URL', 'http://127.0.0.1:11434'))
    ap.add_argument('--out', default='vibe_check.html', help='HTML report to write')
    ap.add_argument('--pdf', help='also write a PDF report')
    ap.add_argument('--json', dest='json_out', help='also write the raw result as JSON')
    ap.add_argument('--history', help='JSON file of earlier snapshots; the new run is appended to it')
    ap.add_argument('--stub', action='store_true', help='use the keyword stub instead of a model (layout check only)')
    args = ap.parse_args(argv)
    if args.pdf:                                            # reportlab is optional: say so before any work or output
        try:
            import reportlab  # noqa: F401
        except ImportError:
            print('--pdf needs reportlab: run `pip install -r requirements.txt` (or leave --pdf out).', file=sys.stderr)
            return 2

    units = []
    for path in args.files:
        notes = []
        try:
            units += ingest.units_from_file(path, notes=notes)
        except (OSError, ValueError) as e:
            print(f'{path}: {e}', file=sys.stderr)
            return 2
        for note in notes:                                  # what the reader left out: say so, the report covers only the rest
            print(f'{path}: {note}', file=sys.stderr)
    history = []
    if args.history and os.path.exists(args.history):
        with open(args.history) as fh:
            history = json.load(fh)

    if args.stub:
        from .testing import KeywordStubModel
        call = KeywordStubModel()
        print('STUB MODEL: messages are sorted by keyword. These numbers are not an analysis.', file=sys.stderr)
    else:
        call = ollama_call(args.model, args.host)
    result = engine.run_vibe_check(units, call, sensitivity=args.sensitivity, history=history,
                                   progress=lambda m: print(m, file=sys.stderr))
    if result['status'] != 'ok':
        print(f'Vibe Check did not run: {result["reason"]}', file=sys.stderr)
        if result.get('detail'):                            # a command line is for developers: the exception is useful here
            print(f'Last error: {result["detail"]}', file=sys.stderr)
        return 1

    banner = ('<div class="banner"><strong>Stub run.</strong> Messages were sorted by keyword, not by a language model. Layout check only.</div>'
              if args.stub else '')
    with open(args.out, 'w') as fh:
        fh.write(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                 f'<title>Vibe Check</title><style>{PAGE_CSS}</style></head><body><main>{banner}{render_html(result)}</main></body></html>')
    print(f'wrote {args.out}', file=sys.stderr)
    if args.pdf:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate
        SimpleDocTemplate(args.pdf, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=60,
                          title='Vibe Check').build(pdf_flowables(result))
        print(f'wrote {args.pdf}', file=sys.stderr)
    if args.json_out:
        with open(args.json_out, 'w') as fh:
            json.dump(result, fh, indent=1, ensure_ascii=False)
    if args.history:
        with open(args.history, 'w') as fh:
            json.dump(history + [snapshot_from_result(result)], fh, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
