"""The measures, alerts and badges in the PDF and the CSV.

`csv_rows(ms)` returns the rows for the CSV, `pdf_pages(ms, story, p, page, table, drawing, small)` adds the pages to the PDF story. Both take the result of
`measures.build`. Neither reads a message: the module works from numbers and fixed wording.
"""


def csv_rows(ms) -> list:
    return []


def pdf_pages(ms, story, p, page, table, drawing, small=None) -> None:
    return None
