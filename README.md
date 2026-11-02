# Felixa Community Health

Upload or paste any communication: chat exports, comments, posts, transcripts, PDFs, Word files. Felixa Community Health reads it on a local language model and reports:

- **Measured behaviour.** 13 observable communication signals (Polite, Funny, Empathetic, Encouraging, Ignorant, Egocentric, Neurotic, Sarcastic, Agitated, Aggressive, Rude, Disrespectful, Judgmental) rated 0–3, with FAS, detection rate and expression strength for each, grouped as positive, tense and toxic.
- **Evidence-linked guidance.** What appears to be working, what needs investigation, what to do next, and how confident the evidence allows anyone to be.
- **Vibe Check.** Friendliness score, sentiment mix, toxicity types, flagged messages, an Impact Score across sources, progress charts from run to run, and a trend forecast. See [VIBE_CHECK.md](VIBE_CHECK.md).

Everything runs on [Ollama](https://ollama.com) on your own machine, with no cloud API needed. Claude is an optional cloud engine for testing (see the Engine step below). The web report, a PDF and a CSV export carry the same results.

## Quick start with your own Claude login (no key, no Ollama)

This is the easiest way to try the app. It uses [Claude Code](https://claude.com/claude-code) on your computer, signed in as you.

1. Install Claude Code, then run `claude` once in a terminal and sign in.
2. Mac: double-click `run_claude_mac.command` (or run `./run_claude_mac.command`). Windows: double-click `run_claude_windows.bat`. It checks your Python, sets everything up and opens the app in your browser (at the address it prints, usually http://127.0.0.1:5050). If something is missing it says what, and waits so you can read it.
3. Create a workplace, press New, add `sample_data/sample_guild_chat.csv`, and on the last step choose **Your Claude login**, tick the box and run. If it does not work, press **Test my Claude login** on that step: it tells you what is wrong.

## Quick start with a model on your computer (Mac)

1. Install Ollama and start it.
2. Run `./run_mac.command`. It creates a virtual environment, installs the requirements, downloads the model if it is not installed yet (`qwen2.5:7b` by default, or the model named by `FELIXA_LOCAL_MODEL` in `.env`; another size of the same model, such as `qwen2.5:3b`, counts as a different model) and starts the app.
3. Open http://127.0.0.1:5050, create a workplace, press New, and add `sample_data/sample_guild_chat.csv`.

Windows: `run_windows.bat`. Manual (Python 3.10 or newer; 3.11 and 3.13 are tested): `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && python app.py`. The virtual environment is needed on systems whose Python is externally managed (Ubuntu 24.04, Homebrew).

No model yet? [DEV.md](DEV.md) shows how to run the whole app against a fake model to see every page. Its numbers are meaningless; it only proves the plumbing.

## How it works

**1. Set up a workplace (six steps, once).** The kind of place it is (online community, team, chat or something else), its name, the area it works in, where people talk (Discord, Slack, Microsoft Teams, WhatsApp and more), who you are and your role, and what you want to find out (is my community healthy, why are people leaving, why did revenue go down, how do people communicate, are people engaged, or your own question). Wherever you choose Other, you must say what you mean. Choosing Discord shows an in-app note about the Vibe Check bot: connecting it from inside the app is not switched on yet, so for now you export a channel and add the file.

**2. Press New to run a check (six steps).**

1. **Upload.** Almost any file: spreadsheets (Excel XLSX, XLSM, XLS, ODS, CSV, TSV), documents (Word DOCX and DOC, RTF, ODT, PDF, PowerPoint PPTX), web and mail files (HTML, XML, EML), subtitles (SRT, VTT), and plain text (TXT, MD, LOG, JSON), pasted text, a voice recording, or public web pages from links. A spreadsheet or CSV needs a `text`, `comment`, `content`, `message`, `transcript` or `body` column; a `source`, `channel`, `channel_name`, `room` or `thread` column splits it into sources. A TXT, MD or LOG file is read line by line. A file that repeats an earlier one is skipped.
2. **Volume.** How many messages to read: 100, 300, 1,000, 3,000 or all. Fewer than all are picked evenly across the whole upload. Without a choice, the 13 signals rate a sample of 300 and Vibe Check reads at most 1,000 messages in all; a volume you choose sets both.
3. **Incognito.** Takes names out of the messages before anything reads them: the speaker label at the start of a chat line becomes Member 1, Member 2 and so on, and @mentions and email addresses go. It changes the saved messages too. A name typed inside a sentence cannot be found reliably, and file and channel names are kept.
4. **Time frame.** Quick choices (last 24 hours, 7, 30 or 90 days, counted back from your newest message, or all dates), or a start and an end date, each with an optional time, so you can check one episode or one evening. You type times in your own time zone and the app saves and reports them in UTC. Only messages dated inside the window are read. Messages with no readable date are always kept.
5. **Sensitivity.** Low, medium or high: where the bar sits for what counts as unfriendly.
6. **Engine.** Any model downloaded in Ollama, or Claude (Haiku, Sonnet or Opus) in the cloud, each with an estimate of the model calls and the time before you start. The estimate comes from typical speeds, so your computer or connection may be faster or slower. Local models cost nothing. Claude is free while testing: it runs through the server's own key, `FELIXA_ANTHROPIC_API_KEY` (set it in `.env`, and the Claude engines switch on). Because Claude reads your messages on Anthropic's servers, the page makes you tick a box first, and Incognito is worth turning on. Nothing leaves your computer for a model unless you do both.

   **Claude without a key (for testing).** If [Claude Code](https://claude.com/claude-code) is installed on the same computer and you have signed in once (run `claude` in a terminal), the Engine step also offers **Your Claude login** (Haiku, Sonnet or Opus). It asks Claude Code to read the messages as you, so no API key is needed. It runs with every tool switched off, in an empty folder, and it uses your own Claude usage. It is slower than the API because the program starts again for each step. It is only for trying the app on your own computer: it is switched off whenever `FELIXA_PASSWORD` is set (a hosted app), so nobody can use the host's Claude login through the app, and `FELIXA_CLAUDE_LOGIN=0` switches it off anywhere. A shared or hosted app should use the API key instead.

**3. Read the report.** The workplace page shows everything inline; **Open report** gives a printable page, plus a PDF and a CSV export. Press **New** again after the next batch of messages. A new check replaces the messages and the report, and keeps the workplace and the numeric Vibe Check history, so the second check adds progress charts and a forecast. Keep the same Vibe Check sensitivity between checks if you want them to compare cleanly.

Two things need more than the default install. **Links** fetch one public page each from the internet (the only time the app connects outside your computer); the fetch refuses localhost, private networks and cloud metadata addresses. **Voice** needs the speech-to-text add-on, `pip install faster-whisper`, which downloads a speech model the first time it runs; without it the Voice tab says how to install it.

## What the numbers mean

The report explains itself in plain words under "How this report works". In short: a model on your computer scores each message from 0 to 3 for 13 tones; the FAS number shows how strongly a tone appears across all messages, out of 100, and the detection rate shows how many messages contain it. Vibe Check sorts messages into friendly, neutral and unfriendly and gives a score out of 10. A language model makes these calls and trained human raters have not checked them yet, so use the results to start a conversation and never to judge a person.

## Configuration

Copy [.env.example](.env.example) to `.env` (`run_mac.command` and `run_windows.bat` both read it) and take the `#` off a setting to use it. Write a comment only on a line of its own, never after a value: `run_windows.bat` takes everything after the first `=` as the value. The ones that matter most: `FELIXA_LOCAL_MODEL`, `FELIXA_OLLAMA_URL`, `FELIXA_PASSWORD` (set it whenever the app is hosted; sign-in needs https, see Hosting), `FELIXA_DATA_DIR`, `FELIXA_MAX_RATED_UNITS`. Two more are for hosting: `FELIXA_REQUIRE_AUTH=1` makes the app refuse every page until `FELIXA_PASSWORD` is set (the Dockerfile sets it), and `FELIXA_REMOVE_OLD_MODEL` names a model that `start_online.py` removes with `ollama rm` before it pulls the one in `FELIXA_LOCAL_MODEL`. Only the launch scripts read `.env`; running `python app.py` or `uvicorn` directly does not, so export the settings in the shell instead.

## Hosting

Railway with a persistent volume and a password. The recommended light setup runs only the web app and uses Claude through an API key (`FELIXA_NO_LOCAL_MODEL=1`); a heavier one runs its own Ollama on the server. See [DEPLOY.md](DEPLOY.md). It is one shared, password-protected workspace, not separate customer accounts.

**Sign-in needs https.** With `FELIXA_PASSWORD` set, the session cookie is `Secure`, and a browser keeps a `Secure` cookie only for an https page or for `http://127.0.0.1` and `http://localhost`, which always mean the browser's own computer (Safari may refuse it there too; then reach the app through https, as below). At any other plain-http address, such as `http://192.168.1.20:5000` after `FELIXA_HOST=0.0.0.0`, the app accepts the right password, the browser drops the cookie and the sign-in form comes back. The form shows a notice when that is the case, and `python app.py` logs a warning at start-up. To use a password-protected app from another computer, either put a TLS reverse proxy (Caddy, nginx) in front of it, passing the original `Host` header through (or the app refuses every form post as coming from another site), or open an SSH tunnel with `ssh -L 5000:127.0.0.1:5000 <host>` and browse to `http://127.0.0.1:5000` on your own computer. A hosted deploy on an https domain, as in [DEPLOY.md](DEPLOY.md), is not affected.

## Tests

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -I -W ignore -m unittest discover -s tests
```

## Privacy and limits

- Messages stay on the machine that runs the model, unless you pick a Claude engine and tick the box that says they may be sent to Anthropic to be read. Charts are drawn locally; nothing is fetched from a third party. The one exception is a link you add in a New check: the app fetches that single public page, refuses private and local addresses, and keeps only its text.
- Without a password the app answers only to localhost names (127.0.0.1, localhost, [::1]) and refuses writes started by other websites, so a page open in your browser cannot drive it. To open it under another name, such as a LAN address, list the name in `FELIXA_ALLOWED_HOSTS`. A hosted run uses `FELIXA_PASSWORD` instead (see [DEPLOY.md](DEPLOY.md)).
- With a password, wrong passwords are slowed down per client address, at the sign-in form and for HTTP Basic alike: five in a row start a wait that doubles from 2 seconds to 5 minutes. A session lasts 8 hours and is kept only in the app's memory, so a restart signs everyone out and `POST /logout` ends it for good (a copy of the cookie stops working too). See [DEPLOY.md](DEPLOY.md).
- What is stored, and for how long: the messages, the results and the analysis details you enter live in `felixa.db` in the data directory (`FELIXA_DATA_DIR`; the app folder unless you set it) until you delete them. The database file is readable by its owner only, and so is a data directory that the app creates. **Delete evidence and results** overwrites the deleted messages in the database file and clears the write-ahead log beside it (once no other program is reading the file). The app turns SQLite's `secure_delete` on for this itself, because a build that is not compiled with it on (Ubuntu's is) only marks the space free and leaves the text in the file. It leaves the analysis details and the numeric Vibe Check history. There is no delete for a single analysis: to erase everything, including the name, role and goals of every analysis, stop the app and delete the data directory (on a hosted deploy, the `felixa.db` files on the volume). Copies made outside the app (a PDF or CSV you downloaded, a backup, a disk snapshot) and whatever a disk keeps in its free space are outside its control.
- An uploaded file is read from a temporary copy in the `uploads` folder of the data directory. The copy is deleted as soon as the file has been read, and any copy a crash left behind is deleted when the app next starts.
- Reading limits: one file or paste is read up to 4 MB of text and 50,000 messages (a CSV counts its rows), and a PDF for its first 100 pages or its first 30 seconds of reading, whichever ends first (an ordinary 100-page PDF takes about a second). The app keeps the first 5,000 messages per analysis (`FELIXA_MAX_UNITS`). When a file or paste has more than an analysis keeps, a PDF has more than 100 pages, or a PDF is not read in 30 seconds, the page says how much was left out. A Word file that unpacks to more than 10 MB of XML, or 100 MB in all, is refused; save it as PDF or TXT, or paste the text.
- A file may be up to 15 MB. A paste is held to the same 15 MB, counted as the browser sends it, where a letter outside A-Z (accented, Cyrillic, Chinese, Japanese) takes 6 to 9 bytes and an emoji 12. A paste over that is refused with a note to save it as a `.txt` file and import it.
- A request is also held to a size before it is read, because the web framework spools every file in an upload to disk first: one upload may carry up to 75 MB of files in all (five of the full size), a paste about 17 MB, and every other form, sign-in included, 2 MB. A request that declares more is refused at once, unread, with a 413 and a short note; one that declares nothing (chunked, so not a browser's) is cut off when it passes the limit. The upload forms check the sizes in the browser first, so a file the app would refuse is not sent at all.
- Flagged messages quote the author's own words: treat reports as sensitive.
- The signals describe the language in the supplied text, not personality, intent or mental state.
- The Vibe Check forecast is a rules-based indicator and says so on the page.
