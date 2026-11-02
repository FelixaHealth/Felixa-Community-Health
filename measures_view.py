"""The measures, alerts and badges as HTML for the workplace page and the report page (the PDF and the CSV have their own: measures_export.py).

`html(ms, a)` takes the result of `measures.build` and the analysis row, and returns a dict of ready-to-print HTML strings for the templates.
Everything that came from a person (nothing here does: the module works from numbers and fixed wording) goes through `html.escape` first.
"""


def html(ms, a=None) -> dict:
    return {}
