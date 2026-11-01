"""Felixa Vibe Check: friendliness, sentiment, toxicity and trend analytics for any uploaded text.

    from vibe_check import units_from_file, run_vibe_check, render_html, pdf_flowables

    units  = units_from_file('chat.csv') + units_from_text(pasted, 'Pasted text')
    result = run_vibe_check(units, call=local_model_call, history=saved_snapshots)
    save(result['snapshot'])                       # keep for the next run's progress charts
    section_html = render_html(result)             # web report
    story.extend(pdf_flowables(result))            # PDF report
"""
from .engine import DEFAULT_SENSITIVITY, SENSITIVITY_LABELS, SENSITIVITY_PROMPTS, VERSION, VIBE_SCHEMA, ModelUnavailable, run_vibe_check
from .forecast import behavior_probability, build_progress, snapshot_from_result
from .ingest import (UnsupportedFileError, group_by_source, sample_units, sanitize, units_from_file, units_from_rows,
                     units_from_text)
from .render import CSS, JS, assets_html, data_table, figure, pdf_flowables, render_html

__all__ = [
    'run_vibe_check', 'render_html', 'pdf_flowables', 'assets_html', 'CSS', 'JS', 'figure', 'data_table',
    'units_from_text', 'units_from_rows', 'units_from_file', 'sanitize', 'sample_units', 'group_by_source',
    'behavior_probability', 'build_progress', 'snapshot_from_result',
    'VERSION', 'VIBE_SCHEMA', 'SENSITIVITY_PROMPTS', 'SENSITIVITY_LABELS', 'DEFAULT_SENSITIVITY',
    'UnsupportedFileError', 'ModelUnavailable',
]
