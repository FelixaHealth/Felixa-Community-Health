# Vibe Check for Felixa Community Health

Vibe Check is a new section of the Felixa Community Health report. It reads any text a person uploads or pastes (chat exports, CSV rows, JSON, TXT, MD, PDF, DOCX) and reports how friendly the conversation feels, what is working, and where a small change would help. It ports the analytics of the Vibe Check Discord bot into the app and leaves the bot itself behind.

**Status.** The engine, charts, HTML section, PDF section and command line are built and tested (76 tests, all against a stand-in model). They are **not yet wired into the PTB app**, because that app's source has not been shared with this session, and **not yet run against a real language model**. The first run with your Ollama model is the real test of answer quality.

## What the section shows

| Block | What it contains |
|---|---|
| Latest-attempt notice | Only when the newest run failed after an earlier one succeeded: says that the section below is the earlier run, and why the newest did not finish. The host passes it as `notice` to `render_html` and `pdf_flowables`; Felixa Community Health also adds it to the CSV |
| Hero | Friendliness score out of 10 (Impact Score when there are several sources), rating, meter, vibe verdict |
| Sentiment mix | Friendly / neutral / unfriendly split |
| Strengths and recommendations | Positive-psychology wording. The model is told never to suggest bans, warnings or punishments, and a written part that still does is replaced with standard text (see Limits) |
| Toxicity breakdown | Insults, harassment, threats, hate speech, bullying, profanity, spam |
| Flagged messages | Ordered by severity, type and source; each message is copied from the upload |
| Impact score by source | One meter per file or channel, with the number of messages analysed beside it. The Impact Score is the average of these scores weighted by those counts, so a source that was sampled down counts as its sample |
| Progress over time | Score, sentiment, flagged and cumulative-toxicity charts, trend, health, predictions, recommendations (from the second run). The run table gives the sensitivity level of each run, and a "Mixed sensitivity" caution leads the health lines when the runs used different levels. Runs are named by date, or Run 1, Run 2 and so on when two share a day |
| Behaviour forecast | Chance the community improves or toxicity rises, confidence, the factors behind it. The caveat says so when the saved runs used different sensitivity levels |

Every chart has a table twin, hover and keyboard readouts, a phone layout and a PDF version. In Felixa Community Health the CSV export carries every block above too: each chart as its table, and the verdict, strengths, recommendations, trend, health, predictions and forecast as rows of text. The charts themselves and the "How Vibe Check works" notes stay in the page and the PDF.

On the page, the table twins, the flagged messages past the tenth, the forecast factors and the note on what limits the numbers are folded away (`<details>`). A closed fold prints as its summary line alone, so the page opens them for the printer: the script in `JS` opens every closed fold inside the section when the browser prints (Print, or Save as PDF) and shuts exactly those again afterwards, leaving a fold the reader had opened as it was, and the print stylesheet shows a closed fold's content in a browser that supports `::details-content` even when the script does not run. The print stylesheet also keeps each chart on the page with its heading and the note under it (the three sit in one `vc-keep` block that a page does not split; the table under the chart stays outside it, so a long table flows on), and does not cut a tile, a card or a flagged message in two.

## Try it now

```bash
python3 -I -m unittest discover -s tests                     # 76 tests
python3 demo/build_demo.py                                   # preview pages in demo/out/ (stand-in model, invented history)
python3 -m vibe_check your_chat.csv notes.pdf --model qwen2.5:7b --history runs.json --out report.html --pdf report.pdf
```

Run these from the repo root with the virtual environment active (`--pdf` needs reportlab, which `requirements.txt` installs). The last command needs Ollama running locally. Each run with the same `--history` file adds a point to the progress charts. Add `--stub` to check the layout without a model.

## Wiring it into a Felixa app (FastAPI, Jinja, SQLite, reportlab)

Written against the structure of `Felixa-PR`. Adjust the names once the PTB source is available.

A sketch, not a script to paste: `c`, `uploads`, `units`, `person`, `notes`, `report`, `aid`, `progress`, `load_vibe_history` and `save_vibe_snapshot` are the host app's own names.

```python
# 1. app.py: keep one snapshot per run
c.execute("""CREATE TABLE IF NOT EXISTS vibe_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,
             analysis_id INTEGER NOT NULL, created_at TEXT, snapshot_json TEXT)""")

# 2. intelligence.build(...), after the evidence is registered
from model import local_model_call as call   # the app's existing model function
from vibe_check import run_vibe_check, units_from_file
vc = [{'text': u['text'], 'source': u['source_label'] or 'Pasted text', 'timestamp': u['timestamp'], 'role': u['actor_role']}
      for u in units if person.get('audience_analysis') or u['actor_role'] != 'AUDIENCE']   # same consent rule as the rest of the app
for up in uploads:
    try:
        vc += units_from_file(up['value'], up.get('label'))
    except Exception as e:
        notes.append('Vibe Check could not read ' + str(up.get('label')) + ': ' + str(e)[:120])
report['vibe_check'] = run_vibe_check(vc, call, sensitivity=person.get('sensitivity') or 'medium',
                                      history=load_vibe_history(aid), progress=progress)
if report['vibe_check']['status'] == 'ok':
    save_vibe_snapshot(aid, report['vibe_check']['snapshot'])
```

`call` is the app's existing model function. `local_model_call(instructions, input_text, model=None, schema=None)` already fits: it takes a schema and returns `(text, model)`. A model function that cannot reach the model, or gets no answer in time, should raise `vibe_check.ModelUnavailable` (`local_model_call` does), so that the engine does not split batches for a model that is not there; any other exception reads as a bad answer.

A run that gives no result returns `status` `unavailable` or `insufficient` with a `reason` in plain words: that is what the report, PDF and CSV print. When every batch failed, `detail` holds the last exception (`RuntimeError: ...`, cut to 160 characters) for a log or a developer. The renderers never print it, and the command line prints it after the reason as `Last error:`. A host should log it (Felixa Community Health does) and show only `reason`.

```jinja
{# report.html, after "Executive assessment" #}
{% if report.vibe_check %}{{ vibe_check_html(report.vibe_check)|safe }}{% endif %}
```
```python
templates.env.globals['vibe_check_html'] = vibe_check.render_html      # at startup
```
```python
# report_export.pdf(), after the executive assessment
if report.get('vibe_check'):
    story.append(PageBreak())
    story.extend(vibe_check.pdf_flowables(report['vibe_check'], fonts=('FelixaSans', 'FelixaSansBold')))
```

The result is plain JSON, so it saves inside the report snapshot as is. Add `requirements-vibe-check.txt` to the app's requirements (all three packages are already in `Felixa-PR`'s).

## How it differs from the Discord bot

**Kept.** The three sensitivity levels with the original prompt text, the positive-psychology rules, the 0–10 score and its ratings, the flagged list sorted by severity, the Impact Score weighted by the messages analysed from each source, the progress narrative, and the forecast factors and weights.

**Changed on purpose.**
- Messages go to the model in batches of 40, and a batch that fails is split in half. A local model's context window cannot take the bot's 30,000-character prompt. Splitting is for a model that answers badly (an answer cut short, malformed or invalid). When the model cannot be reached or does not answer in time, a smaller batch cannot help, so the batch is tried twice and not split. Three batches in a row that the model could not analyse end the run: the rest is not sent, the report keeps what was analysed and says how many messages were left out, and a model that is down or hanging costs a few calls instead of a retry-and-split ladder for every batch. One message the model cannot answer loses only the small batch it sits in.
- A message longer than 500 characters is read in full, as passages of up to 400 characters cut at sentence ends. The bot cut every message at 500, so the rest of a long one went unread. Each passage counts as a message, so a long document counts more messages than it has paragraphs, and the report says so.
- The model names a flagged message by its number and the engine copies the text from the upload, so a flagged message is always a real message (for a long one, the passage that was flagged).
- A toxicity count is a number of messages, so each type the model reports is checked against the number of messages in the batch it analysed (the prompt says so too). A type above that number, whether the model counted words or made the figure up, cannot be right: the model's whole tally is then set aside and counted again from the flagged messages by type, as it is when the model leaves the tally empty, and the rest of the answer is kept. Every batch is checked, so a run's count for any type can never pass the number of messages analysed in it. One message can carry several types, so the types are not capped together. Within the limit the tally is the model's own count and is not matched against its flagged list.
- Charts are drawn locally as SVG and PDF graphics. The bot sent chart data to QuickChart. The sentiment pie became a segmented bar.
- Runs made on the same day are told apart. The bot labelled its progress charts by date, but here running again after each upload is the normal flow, so several saved runs often share a day and every label would read "Nov 1". While each run has a day of its own the labels are dates, as in the bot. When two runs share a day, every run is named by its number instead (Run 1, Run 2, ...) on the chart axes, and the hover readout and the run tables read like "Run 3 · Nov 1, 2026 01:09 UTC" (run times are UTC; the time alone would not do, because runs often fall in the same minute). The number is the run's place in the saved history, which holds finished runs only, so a run that failed takes none.
- Large uploads are sampled evenly through the messages that were read instead of cut at the start (the app also stores at most 5,000 messages per analysis, and says so when an import had more). The section's header line says "500 of 600 messages analysed" whenever sampling, the overall cap or a failed batch left some out. The Impact Score weights each source by the messages analysed from it, so a sampled source counts as its sample and not its full size: with the default limit of 500 per source, a source of 1,000 messages and one of 500 weigh the same. The sentiment mix, toxicity and flagged counts are counts of the same sample, so the score and the mix describe the same messages.
- A source with a single message is not skipped. The bot skipped any channel with fewer than two messages, but here a source is a file, a thread or a CSV value, so a folder of one-line notes, or a CSV with a different thread on every row, would have been dropped whole. Sources with fewer than two messages are analysed together as one source named "Other small sources", and a note says how many were pooled. A flagged message still names its own file or thread. A single small source next to larger ones keeps its own name.
- At most 25 sources are scored on their own. The bot had a handful of channels that its owner picked, but here a source is a file, a thread or a CSV value, so one upload can hold thousands, and every source costs at least one model call. The 24 largest sources keep their names. Every other source, the ones with a single message included, is analysed together as "Other small sources", and a note gives the counts. The pool is sampled like any other source, and the overall cap of about 1,000 messages holds however many sources there are, so the number of model calls depends on how much is read and not on how many sources the upload has. `max_sources` on `run_vibe_check` changes the limit.
- A source name never goes into the model's instructions. The bot named its channels there, but here a name is whatever the upload says (a CSV cell can hold a paragraph), and the instructions are the text the model is most bound to follow. They say "one source". An upload reaches the model only as input: the numbered messages, and the source names in the combined-summary step. A name is also kept to one line, without control characters, and cut to 80 characters.
- A saved snapshot (`snapshot_from_result`) holds numbers, and each source only as a fingerprint of its name (`source_key`, `src-` and 12 hex digits). A host keeps the snapshots when it deletes the evidence (Felixa Community Health does), and a file or channel name can be as private as the messages in it. The forecast only has to tell sources apart across runs (more than one distinct source makes the score an Impact Score), which a fingerprint does as well as the name. Snapshots saved by an earlier version, which hold the names, still compare correctly: a name is fingerprinted when the runs are compared. The full result of a run, which the report shows, still names its sources.
- The bot's rule of no bans, warnings or punishments lived in the prompt alone. A model can ignore a prompt, and an instruction hidden in an upload can talk it out of one, so the engine also checks what comes back. A strengths, recommendation or verdict that suggests a ban, mute, kick, warning, suspension or punishment is replaced with standard positive text, and a note says how many parts were replaced. The numbers are never touched, and the batch is not asked again.

**Fixed, because the original gave wrong answers.**
1. With exactly three reports, "older" was an empty list averaged as 0, so momentum always added the maximum +25 to the forecast. The baseline is now every earlier report.
2. A gain of 0 to 0.5 points was labelled a "slight dip".
3. Flagged counts from runs of different size were compared directly. Rates per 100 messages are compared now.
4. Sentiment rounding could produce a negative unfriendly count. It now uses largest-remainder rounding, and the unfriendly count can never fall below the number of flagged messages.
5. The progress lines contradicted each other. Fix 3 compared runs of different size by their rates per 100 messages, but the sentence still printed the two raw counts, so 10 flagged of 100 and then 12 of 200 read "Down from 10 to 12 flagged messages. Great progress." When the runs differ in size the sentence now prints the rates it compared and the counts behind them ("Down from 10.0 to 6.0 flagged messages per 100 analysed (10 of 100, then 12 of 200)"); rates that print alike read "Stable". Runs of one size, or with no size on record, still read as plain counts. The trend line, the health lines and the recommendation also cut a score change off in different places: a gain of exactly 0.5 points read "Trending upward" and "A stable community", and a gain of 0.3 read "Holding steady" and "an upward journey". One cut-off, 0.5 points, now serves all three: a gain of 0.5 or more is "Trending upward", "The score is rising" and "an upward journey", a smaller gain reads as steady (with no "upward journey" line), and a fall of more than 0.5 is "Declining" and "The score dipped".

**Dropped.** The Discord client, slash commands, channel permissions and member counts; OpenAI; Supabase; Stripe links, usage limits, cooldowns and the admin command; e-mail reports; "most reacted messages".

## Limits

- In Felixa Community Health the analysis's review period (the Time frame step of a New check) is applied before Vibe Check reads anything: a message dated outside it is not sent to the model, a message with no readable date is kept, and the report, PDF and CSV say how many were left out. The engine itself takes whatever units the host passes in.
- In Felixa Community Health a file whose bytes are identical to one already imported into the analysis is skipped, and the page says so. Two different files that share messages are both read in full, so a message in both counts twice and can move the score: the stored messages carry no author, so a repeat cannot be told from a copy. Upload only new messages, or delete the evidence and upload the complete export on its own.
- In Felixa Community Health a file that names no channels of its own is one source, labelled with its file name in any script. The folder part is dropped, control characters are removed, and a name over 80 characters (the width at which the section shows a source name) is shortened with its extension kept. Two files with exactly the same name are scored as one source, so rename one of them first.
- The check for punishing words is a list of English words, matched whole, not a reading of meaning. It lets a synonym through ("send them away"), and now and then it replaces a harmless sentence that uses one of the words. The model is told that messages are data and to ignore any request inside them, but a small model can still be swayed by instructions written in an upload: that can move its score, its sentiment mix and its flags, and the report prints what it writes (escaped, and never acted on). Treat the written parts as suggestions, not findings.
- Classification quality depends on the model. A 3B to 7B local model will misjudge some messages. The forecast is a rules-based indicator and the page says it is not a calibrated probability. Nothing here has been validated against trained human raters.
- The trend and the forecast compare the saved runs as they are. The sensitivity level changes what counts as unfriendly (Low treats banter as neutral, High marks a message in doubt as unfriendly), so a score from one level is not like for like with a score from another, and a change of level can read as a change in the community. When the saved runs used different levels, the progress section leads its health lines with a "Mixed sensitivity" caution naming the levels, the forecast caveat says the same, the run table (page and CSV) gives the level of each run, and the PDF carries the caution and the caveat. Nothing is dropped or adjusted. A saved run with no level on record is left out of that comparison. The numbers of earlier checks (scores and counts, never messages, names or file names) are kept when the evidence is deleted, so once levels have been mixed the caution stays with the analysis: keep one level for its whole life, or start a new analysis for a clean trend.
- Flagged messages show the author's own words. Treat the report as sensitive.
- One file or paste is read up to 4 MB of text and 50,000 messages (a CSV counts its rows), and a PDF for its first 100 pages and its first 30 seconds (`MAX_PDF_SECONDS`: an ordinary 100-page PDF takes about a second, but pypdf only caps what one stream unpacks to, not how often a crafted file asks for it, so a 55 KB file could otherwise keep a request busy for hours). `units_from_file(path, notes=[])` adds a phrase to that list for each CSV or PDF part it left out ("only the first 100 of 150 pages were read", "reading stopped after 30 seconds, with 12 of 80 pages read", or "columns share a header ('text'), so only the last of each was read") and when characters of a text file could not be read ("some characters could not be read and were replaced"); the command line prints it to stderr and the app shows it after the import. A Word file that unzips to more than 10 MB of XML, or 100 MB in all, is refused: save it as PDF or TXT, or paste the text.
- In Felixa Community Health a file may be up to 15 MB, and a paste is held to the same 15 MB as the browser sends it (a letter outside A-Z takes 6 to 9 bytes there, an emoji 12). A paste over that is refused with a note to save it as a `.txt` file and import it. One upload may carry 75 MB of files in all, and a request over its size limit is refused with a 413 before it is read (see the README).
- CSV, JSON, TXT, MD and LOG files are read as UTF-8; as UTF-16 or UTF-32 when they start with a byte order mark (Excel's "Unicode Text", Notepad's "Unicode", PowerShell's `>`); and as Windows-1252 when they are not valid UTF-8 (Excel's "CSV (Comma delimited)", Notepad's "ANSI"). A byte that cannot be read becomes `�` and is noted as above. Another code page, such as Cyrillic or Japanese, is not detected and comes out as the wrong letters: save the file as UTF-8. Without a byte order mark, a UTF-8 file with a stray invalid byte is read as Windows-1252 as a whole, so its non-ASCII letters come out wrong.
- PDF text outside Latin-1 (for example Chinese) prints as `?` in the standard fonts. The web report shows it correctly.
- Printing the web report from the browser needs the folds to open (see above). A browser that sends neither `beforeprint` nor a change of the print media to the script, and has no `::details-content`, prints a fold as its summary line alone. **Download PDF** does not depend on it. The server PDF has no table twins: the table under each chart is in the web report and, as rows, in the CSV. Where a page breaks is the browser's call: the rules that keep a heading with its chart, and a tile, card or message whole, were checked in Chromium (Chrome, Edge) only, with the page margins and paper sizes in DEV.md.
- Chart labels are small on a phone. Every chart is drawn at a fixed size (720 units wide, 360 on a phone) and scaled to the screen, so its smallest labels (12 units) come out at 9 px on a 360 px phone and 11 px on a 414 px one, and at 9 to 12 px in a window of 641 to 841 px, against 18 px of body text. The 0, 5 and 10 under the score meter are drawn larger (16 units, 12 px or more from 360 px up), the hero is one column up to 1,000 px so that the meter has room, and every chart has a table twin in full-size text that repeats all its values.
- `demo/host_style.css` is a copy of the app stylesheet, used only so the preview matches the app.
