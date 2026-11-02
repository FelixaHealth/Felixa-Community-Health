# Measures over time, alerts and badges

Felixa Community Health already rates every message on 13 tones (0 to 3) and runs Vibe Check. This adds, on top of those same ratings and with no extra model calls:

1. **Four measures that follow the conversation**: Escalation, Conflict, Empathy and Passive-aggressive, plus a 0 to 100 **health score**.
2. **Alerts**: short, plain heads-ups with a kind next step for each. Good news is listed too.
3. **Badges**: seven things a community can earn, each with its rule.
4. **Since your last check**: the same numbers saved after every check, so the next check shows what moved, when the two checks are alike enough to compare.

Everything here describes wording in the messages. It says nothing about any person or team. A language model made the ratings, trained people have not checked them yet, and every cut-off below is our own starting choice, not a validated norm. The page, the PDF and the CSV all say so.

All the rules, and the wording of the cards, alerts, notes and badges, live in `measures.py`. The templates and `measures_export.py` add only headings, column names and a few short lines. This file explains the rules.

## What each measure means

| Measure | What the page says | Good direction |
|---|---|---|
| **Escalation** | How quickly the mood heats up. | down |
| **Conflict** | How much of the talk is openly hostile. | down |
| **Empathy** | How much of the talk shows care and understanding. | up |
| **Passive-aggressive** | Sarcasm without open hostility. Friendly banter can count too. | down |
| **Health score** | One number that adds up all 13 tones. 50 means neutral and higher is warmer. | up |

## How a message is counted

A tone is **clearly shown** when the model rated it 2 or 3. A 1 is a weak, tentative reading and never counts. Each message gets these yes or no flags:

| Flag | A message gets it when |
|---|---|
| hostile (Conflict) | any of Aggressive, Rude, Disrespectful or Judgmental is rated 2 or 3 |
| care (Empathy) | Empathetic is rated 2 or 3 |
| dig (Passive-aggressive) | Sarcastic is rated 2 or 3, the message is not hostile, and Funny is below 2 (a joke is banter) |
| heated (the basis of Escalation) | hostile, or Agitated rated 2 or 3, or a dig |
| polite, encouraging, funny | that tone is rated 2 or 3 |

The page counts messages: "8 of 70 messages were openly hostile". A rate is the count per 100 messages. A message is never counted both as hostile and as a dig.

**Health score.** For each message, `positive = 3x(Polite) + 3x(Empathetic) + 2x(Encouraging) + 1x(Funny)` and `negative = 1x(Ignorant) + 1x(Egocentric) + 1x(Neurotic) + 2x(Sarcastic) + 2x(Agitated) + 3x(Aggressive) + 3x(Judgmental) + 3x(Disrespectful) + 3x(Rude)`, where `x = rating / 3` (0 to 1). Those are the weights of the Felixa backend. The group score is `clamp(50 + 50 x (mean positive - mean negative) / 4.5, 0, 100)`. The backend divides by 9 because it counts a tone as present or absent; a tone has about half its full strength when it is present, so dividing by 4.5 keeps the backend's scale and lets all four bands be reached. Bands: 80 and over Excellent, 60 Good, 40 Fair, under 40 Needs attention, judged on the whole number the page prints. A chat with no tone at all scores exactly 50. It is a separate read from the Vibe Check score out of 10, which stays the headline; the two can differ.

## Following the conversation

* **Order.** If at least 3 in 5 of the rated messages carry a usable date (a date between 1990-01-01 and tomorrow, and at least three different moments among them), they are put in time order (ties keep upload order) and the others are left off the timeline. Otherwise the messages stay in the order they were added, and the page says so.
* **Stretches.** The timeline messages are cut into equal stretches of at least 8 messages, at most 24 (their sizes differ by at most one). A timeline needs **24** messages. Below that the page shows the overall figures only.
* **Verdict floor: 48.** A direction (rising or falling), a heated moment and anything built on them need **48** timeline messages, so that each end third holds at least 16. Between 24 and 47 the lines show what happened and the page says it is too early to call a direction.
* **Labels.** An axis label is a time of day when the data has times, the stretches fall within three days and Incognito is off; otherwise it is a date, or "Part 3" when everything falls on one day. A stretch is named "Part 3 of 9" with its dates or times only in the tooltip and the table. An alert never contains a clock time: it says where in the check ("around the middle") and, when the data is dated, the days. Times are called UTC only when every timestamp that has a time of day carried an offset. Messages ordered by upload are labelled "rated messages 25 to 36". While Incognito is on, even the raw start and end of each part in the analysis are days, never times of day.

## Trends and heated moments (the chance tests)

* **Direction.** The first third of the stretches against the last third (at least one stretch each), pooling their messages. A rate is called **rising** or **falling** when it moved by at least `max(3 points, 20% of where it started)` and by at least **2.0 standard errors**; otherwise "No clear change". Alerts and Good news that rest on a direction need **2.5 standard errors**. Badges use no direction; their rules are under Badges. The standard error is `100 x sqrt(v1/n1 + v2/n2)` with `v = p(1-p)` and `p = (count + 1) / (n + 2)`. The health score uses the same test on the per-message difference (positive minus negative) with a 5-point floor.
* **Heated moment.** For each stretch after the first, take the rate of heated messages in the stretch and the pooled rate over up to **3** stretches before it. The **climb** is the gap, never below 0. A stretch counts when the climb is at least **15 points**, at least **3.5 standard errors** (`100 x sqrt(p(1-p) x (1/stretch size + 1/baseline size))` with `p` the community's own overall rate of heated messages, smoothed), and the stretch holds at least **3** heated messages. Consecutive counted stretches form one heated moment. Escalation has no direction: it has a count of heated moments and the biggest climb. Escalation words by biggest climb: None under 15, Small 15 to 24, Clear 25 to 39, Steep 40 and over. Below 48 timeline messages nothing can be judged, so the card reads **Too early** with a neutral mark, never a green None. When the chart shows a part with 30 or more heated messages in 100 but no heated moment passed the chance test, the card says 'Some parts were heated, but not by more than chance alone would give.' If the very first stretch is already tense (30 or more in 100 heated), the page says a climb before it cannot be seen.
* **Level alerts** (Conflict, sarcasm) read the latest third when it holds at least 24 messages (a timeline of 72 or more), else the whole check. They need at least **3** messages that clearly show the tone and the rate to clear the cut-off by more than **one standard error** (`100p - 100 sqrt(p(1-p)/n) >= cut`).

## Level words

Per 100 messages, on the whole number printed: **Conflict** Rare under 5, Some 5 to 11, Often 12 and over. **Passive-aggressive** the same. **Empathy** Rare under 3, Some 3 to 9, Often 10 and over. At **Low sensitivity** (adult and gaming chat, where banter is normal) the Conflict and Passive-aggressive cut-offs, and the Medium heated-moment cut-offs, are raised (by 1.5 and 1.2) and the page says so. Level words describe how often; the icon says whether that is good or not, except that below **48** rated messages the mark is neutral grey for all four cards (too few messages to call it good or bad). A quiet note says so when the setting is Low: 'Your sensitivity is Low, so banter counts as normal. The bars for Conflict, Passive-aggressive and heated moments are higher.'

## Since your last check

After each rating run the app saves one small record of numbers and the names of the settings used (`pipeline.py` does it right after the ratings, before the guidance is written, so a cancel later cannot lose it). The record holds: the date and time it was saved (a record without a health score or a count is dropped as damaged, and a count above its own total is dropped), how many messages were rated and how many there were to rate, whether the rated ones were a sample, the model name (up to 120 characters of letters, digits and . _ : / @ + - ( ) and spaces), the sensitivity, the review period, the basis (time or order), how many parts the chat was cut into, the health score and its spread, the counts of hostile, care, dig and heated messages, the number of heated moments and the biggest climb, a number that names the check (see One record per check) and a short code made of the check's totals. Nothing else can be saved: the store checks every record against an allow-list when it writes and when it reads (`store.MEASURE_FIELDS`), and no message, name or file name is in it. Delete evidence and results keeps the records, and so does New check.

* **One record per check.** A check is the set of messages behind it, named by the lowest message id (`store.evidence_generation`), which only grows. Running the same evidence again replaces its record; New check starts a new one. Records stay when messages are deleted or a new check starts. A record with no such number is not a check of its own, and two records of one check count once (the newest).
* **Like for like.** The current check is compared with the newest earlier record only when the model name and the sensitivity match, both checks rated at least 48 messages, and the larger is at most 3 times the smaller. Otherwise the page says why and calls no change. A change counts when it is at least `max(5 points, 2 standard errors)` (health: `max(8, 2 standard errors)`) and "strong" at 2.5 standard errors. The table has five rows (Health score, Conflict, Empathy, Passive-aggressive and Heated messages), written as "x of n messages" except the health score, and says Better, Worse or About the same. When the two checks are not alike, the table still shows the last check and this check, with no Change column, and a note above it says why.
* A line chart of the health score, check by check, appears from the third check, with its own table; before that the comparison table says enough. Under Incognito the chart is left out when it would have to name two checks of one day by the time they ran, and its table stays.

## Alerts

Two priorities and good news. Each alert has a title, what was seen (with counts), a next step, a base line ("Based on 24 messages in the latest part of the chat") and, for a heated moment, where it fell (in words and days, never a clock time). There is one verdict per measure: a Good news line is dropped when an alert about the same measure stands. Quiet **notes** (not alerts) explain the basis: too few messages, no dates (or only some, or too few to draw lines), a sample, incomplete ratings, messages added after the check, a tense start, almost no tone found, Low sensitivity, a check that is not like the one before (another model, another sensitivity or a very different number of messages), or that Vibe Check has not run (so the list is not an all clear). Each note says which section it belongs to (`about`: 'alerts' for the Vibe Check and not-alike notes, 'measures' for the rest) and every surface prints it once, in that section. `ms['all_clear']` is true only when Vibe Check ran, no High or Medium alert stands and the model found some tone; only then may a page say 'Nothing needs urgent attention'.

| Alert | Priority | Fires when |
|---|---|---|
| Vibe Check flagged serious messages | High if threats, hate speech or a message scoring 7 or more; else Medium | Vibe Check counted any threats, hate speech, harassment or bullying (the larger of its tally and its flagged messages) |
| The mood heated up fast | High: climb 40 and level reached 45. Medium: climb 25 and level 30 | A heated moment with at least 3 heated messages (verdict floor met) |
| Many / Some messages were openly hostile | High at 20 per 100, Medium at 12 | At least 24 rated messages, 3 hostile messages in the window, and the rate clears the cut-off by a standard error |
| Hostile wording is picking up | Medium | Only when no "openly hostile" alert stands. Conflict strongly rising inside the check, with at least 3 such messages lately and at least 5 per 100 lately; or strongly up on the previous check (like for like), to at least 5 per 100 |
| Sarcastic wording is common / growing | Medium | Same rules at 12 per 100, or strongly rising to at least 5 (inside the check only) |
| Fewer messages show care | Medium | Care strongly falling with at least 5 care messages early on; or strongly down on the previous check (like for like) from at least 5 per 100 |
| The overall mood slipped since your last check | Medium | Health score down strongly on the previous check (like for like): at least 8 points and 2.5 standard errors |
| Care is growing / Hostility is easing / The overall mood improved | Good news | The mirror of the above |

At Low sensitivity the cut-offs for the hostile and sarcastic alerts (and the "at least 5 per 100" floor of the two "picking up" alerts) are 1.5 times higher, and the Medium cut-offs of the heated-moment alert are 1.2 times higher (see Level words). The page shows every High and Medium alert and at most three lines of good news; the first five show at once and the rest wait under "Show N more". The PDF and the CSV list them all.

Wording rules, enforced by the tests: short plain sentences; no jargon (no FAS, stretch, episode, baseline, peak, toxic, "communication unit", standard error or z-score, and no formula, in any of the new sections or in the method text); next steps address the group's habits and the situation, never "the members who" or "the people involved", and never suggest a ban, warning, mute or any punishment; alerts never quote or name a message or a person; the tests run every string through Vibe Check's own word filter (`vibe_check.engine._PUNITIVE`) and a second, stricter list. The threats and hate-speech alert says a model can be wrong, points to the quoted messages, and says that if a message describes a real risk to someone the reader should follow their community's safety process or contact local emergency services.

## Badges

Seven badges, none for fewer than **30** rated messages. Positive-tone badges also need the rate to clear the cut-off by one standard error. The three absence badges need every message read (no sample, nothing added since rating): otherwise the page says "Can't tell yet: only part of the chat was read."

| Badge | Earned when |
|---|---|
| Courteous | Clearly polite wording in at least 20 messages in 100, and fewer than 3 in 100 openly hostile |
| Supportive | Care or understanding in at least 12 messages in 100 |
| Encouraging | Encouraging wording in at least 12 messages in 100 |
| Light-hearted | Funny wording in at least 12 messages in 100 |
| Calm waters | A timeline of 48 or more, no heated moments, and fewer than 3 in 100 messages openly hostile |
| Cools fast | A heated moment (climb 25 or more) after which the heated rate is back within 10 points of its starting level inside three stretches |
| No serious flags | Vibe Check read at least 30 messages (90% of them or more), fewer than 1 serious flag per 200, and none scoring 7 or more |

If 19 in 20 messages have all 13 ratings at 0, no badge is given and the page says almost no tone was found. The page shows earned badges as tiles with the figure behind them; the other rules sit in a closed "How the other badges work" fold. There are no locks, scores, streaks or "x of 7". Badges never name a person, never rank teams, and never cancel an alert.

## Where it appears

Page order, on the workplace page and the report page: **Summary strip** (the Vibe Check score, the number of things to look at, the badges earned, and links) → **Alerts** → **Measures over time** (four cards with level word, count, trend and chart; the health tile; Since your last check; from the third check, the health score check by check) → **Badges** → the signal profile → guidance → Vibe Check. On the workplace page the results sit above the plan, engine and messages panels once there are results. The report page ends with How this report works, whose fifth entry, "Measures, alerts and badges", is the method in plain words (at most 8 sentences of at most 16 words; it is in `analysis.HOW_IT_WORKS`, so the PDF prints it too). The PDF has an Alerts and badges page and a Measures over time page after the cover, and every page after the cover carries the footer line "Model-rated wording. Not a verdict on people." The CSV carries the same blocks as rows (Alerts, Measures over time, Since your last check, Badges), each opening with a "How to read this" row that holds its caveat. The four small charts share one table twin (every part of the chat, with its rates and its health score) and the check-by-check chart has its own. The caveats are always shown, never folded. The home list shows only the Vibe score, so teams are not ranked side by side.

## Left out of the Felixa backend, and why

Stripe payments, e-mailing the report, the OpenAI call, the YouTube and Reddit fetchers and the duplicate-payment guard belong to the paid web service. The backend's flagged comments, content categories, sentiment bars and recommendations already exist here through Vibe Check, so the page links to them instead of repeating them. The backend's "stricter moderation" and "warnings before bans" lines were not carried over, because this app never suggests punishment. The health score is the backend's, rescaled as described, so it will not match its numbers exactly.

## How it was checked

A simulation of synthetic rating tables (calm, supportive, banter-heavy, steadily hostile, neutral chats; a 20-message outburst, a 5-message spat, a single hostile message, a slow burn, a burst at the start or the end) at 24, 76, 300 and 1,000 messages ran the real code. In communities that do not change, a Medium or High alert appeared in 0 to 5% of checks (the first draft: 17 to 54%), and a trend was called in about 5% or fewer. A 20-message outburst is caught in about half the checks at 76 messages and in nearly all at 300; a single hostile message on its own never raises an alert (with three other heated messages in the same part of the chat it can complete a heated moment). A short flare-up in a long chat is spread thin: a 5-message spat is caught in about 4 checks in 5 at 76 messages, about 1 in 9 at 300 and almost never at 1,000, and a 20-message outburst in 1,000 messages shows on the Escalation card in about half the checks but seldom reaches an alert. The simulation was run again on the finished code with 200 chats per case and gave the same picture. Those communities are our assumptions, not real chats, so the cut-offs remain starting choices.

The generator of the simulation is not in the repository (it used numpy, which the app does not need). `FalseAlarmTests` in `tests/test_measures.py` is a small pure-Python version with its own assumptions: 150 calm chats of 300 messages must rarely raise a Medium or High alert or a trend, and a planted 20-message outburst must usually raise the heated-moment alert. DEV.md says how to build the full one.

## Code map

`measures.py` (all rules and wording; `analyse`, `cards`, `alerts`, `badges`, `compare`, `record`, `build`), `store.py` (`measure_history`, the allow-list, `rated_units`, `evidence_generation`), `pipeline.py` (saves a record right after the ratings), `report.py` (`ctx['measures']`), `bei_charts.py` (`measure_chart`, `trail_chart`, `health_meter`), `measures_view.py` (HTML for the page), `measures_export.py` (PDF pages and CSV rows), `report_export.py` (the PDF: it calls `measures_export` and prints the footer line), `analysis.py` (`HOW_IT_WORKS`, the method text), the templates `_summary.html`, `_alerts.html`, `_over_time.html`, `_badges.html`, and `static/style.css` (the `vc-*` rules of the new sections). The tests are `tests/test_measures.py` (the rules, number by number), `tests/test_measures_store.py` (what is saved), `tests/test_measures_export.py` (the PDF and the CSV) and `tests/test_measures_app.py` (whole journeys through the app).
