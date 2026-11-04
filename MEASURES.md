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

**Health score.** The formula of the Felixa backend (`community_health.py`). A tone counts as present in a message when it is rated 2 or 3. For the whole check, `positive` is the sum of the weights of the positive tones present, over all messages: Polite 3, Empathetic 3, Encouraging 2, Funny 1. `negative` is the same for the negative tones: Ignorant 1, Egocentric 1, Neurotic 1, Sarcastic 2, Agitated 2, Aggressive 3, Judgmental 3, Disrespectful 3, Rude 3. The score is `clamp(50 + 50 x (positive - negative) / (messages x 9), 0, 100)`. Bands: 80 and over Excellent, 60 Good, 40 Fair, under 40 Needs attention, judged on the whole number the page prints. A chat with no tone at all scores exactly 50. It is a separate read from the Vibe Check score out of 10, which stays the headline.

## Following the conversation

* **Order.** If at least 3 in 5 of the rated messages carry a usable date (a date between 1990-01-01 and tomorrow, and at least three different moments among them), they are put in time order (ties keep upload order) and the others are left off the timeline. Otherwise the messages stay in the order they were added, and the page says so.
* **Stretches.** The timeline messages are cut into equal stretches of at least 8 messages, at most 24 (their sizes differ by at most one). A timeline needs **24** messages. Below that the page shows the overall figures only.
* **Verdict floor: 48.** A direction (rising or falling), a heated moment and anything built on them need **48** timeline messages, so that each end third holds at least 16. Between 24 and 47 the lines show what happened and the page says it is too early to call a direction.
* **Labels.** An axis label is a time of day when the data has times, the stretches fall within three days and Incognito is off; otherwise it is a date, or "Part 3" when everything falls on one day. A stretch is named "Part 3 of 9" with its dates or times only in the tooltip and the table. An alert never contains a clock time: it says where in the check ("around the middle") and, when the data is dated, the days. Times are called UTC only when every timestamp that has a time of day carried an offset. Messages ordered by upload are labelled "rated messages 25 to 36". A chat whose first and last messages are more than 300 days apart names the year in every date it prints, so two parts never share a label. While Incognito is on, even the raw start and end of each part in the analysis are days, never times of day.

## Trends and heated moments (the chance tests)

* **Direction.** The first third of the stretches against the last third (at least one stretch each), pooling their messages. A rate is called **rising** or **falling** when it moved by at least `max(3 points, 20% of where it started)` and by at least **2.0 standard errors**; otherwise "No clear change". Alerts and Good news that rest on a direction need **2.5 standard errors**. Badges use no direction; their rules are under Badges. The standard error is `100 x sqrt(v1/n1 + v2/n2)` with `v = p(1-p)` and `p = (count + 1) / (n + 2)`. The health score uses the same test on the per-message difference (positive minus negative), and its move must be at least 5 points on the 0 to 100 scale (it has no 20% rule).
* **Heated moment.** For each stretch after the first, take the rate of heated messages in the stretch and the pooled rate over up to **3** stretches before it. The **climb** is the gap, never below 0. A stretch counts when the climb is at least **15 points**, at least **3.5 standard errors** (`100 x sqrt(p(1-p) x (1/stretch size + 1/baseline size))` with `p` the community's own overall rate of heated messages, smoothed), and the stretch holds at least **3** heated messages. Consecutive counted stretches form one heated moment. Escalation has no direction: it has a count of heated moments and the biggest climb. Escalation words by biggest climb: None under 15, Small 15 to 24, Clear 25 to 39, Steep 40 and over. Below 48 timeline messages nothing can be judged, so the card reads **Too early** with a neutral mark, never a green None. If the very first stretch is already tense (30 or more in 100 heated), the Escalation card says that a climb before the start cannot be seen. Otherwise, when a part of the chat has 30 or more heated messages in 100 but no heated moment passed the chance test, the card says 'Some parts were heated, but not by more than chance alone would give.'
* **Level alerts** (Conflict, sarcasm) read the latest third when it holds at least 24 messages (a timeline of 72 or more) and every rated message is on the timeline, else the whole check. A message left off the timeline for having no date is still a message, so the whole check is read then. They need at least **3** messages that clearly show the tone and the rate to clear the cut-off by more than **one standard error** (`100p - 100 sqrt(p(1-p)/n) >= cut`).

## Level words

Per 100 messages, on the whole number printed: **Conflict** Rare under 5, Some 5 to 11, Often 12 and over. **Passive-aggressive** the same. **Empathy** Rare under 3, Some 3 to 9, Often 10 and over. At **Low sensitivity** (adult and gaming chat, where banter is normal) the Conflict and Passive-aggressive cut-offs, and the Medium heated-moment cut-offs, are raised (by 1.5 and 1.2), and a quiet note says so: 'Your sensitivity is Low, so banter counts as normal. The bars for Conflict, Passive-aggressive and heated moments are higher.' The Conflict, Empathy and Passive-aggressive charts shade the Rare, Some and Often bands behind their lines and name them, every chart shades its heated moments darker, and the key under the charts says so; the key adds 'Times are UTC.' only when the part labels show a time of day and every timestamp carried an offset. Level words describe how often; the icon says whether that is good or not. Each of the three level cards also names the level of the latest third ("Latest part: Some", or "Last part" when the lines follow the order you added the messages); the PDF and the CSV print it too. Below **48** rated messages the mark of Conflict, Empathy and Passive-aggressive is neutral grey (too few messages to call it good or bad). The marks are also neutral when almost no tone was found (19 in 20 messages rated all 0), and Escalation's None is neutral when the chat was already tense in its first part. The health tile's mark is neutral below 48 messages or when no tone was found. Escalation is grey and reads Too early below 48 messages on the timeline. A surface that cannot show the icon (the PDF and the CSV) says the mark in words instead, from `tone_word` on every card and on the health tile: 'A good sign', 'Neutral', 'Worth a look' or 'Look at this first'.

## Since your last check

After each rating run the app saves one small record of numbers and the names of the settings used (`pipeline.py` does it right after the ratings, before the guidance is written, so a cancel later cannot lose it). The record holds: the date and time it was saved (a record without a date, a count of messages or a health score is dropped as damaged, and a count of hostile, care, dig or heated messages that is higher than the number of messages is left out of its record), how many messages were rated and how many there were to rate, whether the rated ones were a sample, the model name (up to 120 characters of letters, digits and . _ : / @ + - ( ) and spaces), the sensitivity, the review period, the number of members the workplace had when the check was made (if it named one), the basis (time or order), how many parts the chat was cut into, the health score and its spread, the counts of hostile, care, dig and heated messages, the number of heated moments and the biggest climb, a fingerprint of the model name (so two names the store cannot keep are never taken for one model), a number that names the check (see One record per check) and a short code made of the check's totals. Nothing else can be saved: the store checks every record against an allow-list when it writes and when it reads (`store.MEASURE_FIELDS`); a record without a date, a count or a health score, or with a health score off the 0 to 100 scale, is dropped as damaged, and so is any count above its own total; and no message, name or file name is in it. Delete evidence and results keeps the records, and so does New check.

* **One record per check.** A check is the set of messages behind it, named by the lowest message id (`store.evidence_generation`), which only grows. Running the same evidence again replaces its record, which keeps the date the check first ran, so viewing a check later never moves it; New check starts a new one. Records stay when messages are deleted or a new check starts. A record with no such number is not a check of its own, and two records of one check count once (the newest).
* **Like for like.** The current check is compared with the newest earlier record only when the model name and the sensitivity match, both checks rated at least 48 messages, and the larger is at most 3 times the smaller. Otherwise the page says why and calls no change. A change counts when it is at least `max(5 points, 2 standard errors)` (health: `max(8, 2 standard errors)`) and "strong" at 2.5 standard errors. The health row needs both records to have kept the spread of their scores; without it the row shows both scores and calls no change. The table has five rows (Health score, Conflict, Empathy, Passive-aggressive and Heated messages), written as "x of n" (messages; or "Not known" when the store had to drop that count) except the health score, and says Better, Worse or About the same. A damaged count (below 0 or above the number of messages) is clamped to that range before the two checks are compared. When the two checks are not alike, the table still shows the last check and this check, with no Change column, and a note above it says why.
* **Members.** The record keeps the number of members the check was made with (the workplace's Members box, set on the edit page or in step 1 of a check). Under Since your last check the page, the PDF and the CSV print "Members: 200 at your last check, 260 now (60 more)" ("an earlier check" when the last one named none), and a first count reads "Members at this check: 250. Later checks show how the group changes." When the group grew or shrank by a quarter or more (now at least 1.25 times, or at most 4 in 5 of, the earlier count) a caution follows: "The group changed by a quarter or more, so part of any change may come from who is in it." The check-by-check table gets a Members column once any check names its members. Nothing is said when this check names no members, and the members never change the verdicts: they are context for reading them.
* A line chart of the health score, check by check, appears from the third check, with its own table; before that the comparison table says enough. A check is named by its number and its date, never by the time it ran, so the chart and its table are the same under Incognito.

## Alerts

Two priorities and good news. Each alert has a title, what was seen (with counts), a next step, a base line ("Based on 24 messages in the latest part of the chat", or "Based on 72 messages in each check" when two checks of the same size are compared) and, for a heated moment, where it fell (in words and days, never a clock time). There is one verdict per measure: a Good news line is dropped when an alert about the same measure stands. Quiet **notes** (not alerts) explain the basis: too few messages for lines over time (under 24) or for a direction (under 48), no usable dates (or only some, or too few different moments, so the lines follow the order the messages were added in), some messages with no date, a sample, incomplete ratings, messages inside the review period that were not rated, rated messages that the review period now leaves out, almost no tone found, Low sensitivity, or that Vibe Check has not run (so the list is not an all clear). The reason two checks are not alike (another model, another sensitivity, a very different number of messages) is said once, with the comparison under Since your last check, not as a note; a tense start is said once, on the Escalation card; the note about a sample does not repeat the basis line. Each note says which section it belongs to (`about`: 'alerts' for the Vibe Check note, 'measures' for the rest) and every surface prints it once, in that section. `ms['all_clear']` is a claim, so it has several grounds. It is true only when Vibe Check ran, read at least 30 messages and at least 90 in 100 of the check, and its newest run finished; the check holds at least 24 rated messages; no High or Medium alert stands; no card and not the health tile is marked Worth a look or Look at this first; and the model found some tone (19 in 20 rated all 0 is silence; the note about it needs at least 30 messages). Only then may a page say 'Nothing needs urgent attention in these 70 messages.'

| Alert | Priority | Fires when |
|---|---|---|
| Vibe Check flagged serious messages | High if threats, hate speech or a message scoring 7 or more; else Medium | Vibe Check counted any threats, hate speech, harassment or bullying (the larger of its tally and its flagged messages) |
| The mood heated up fast | High: climb 40 and level reached 45. Medium: climb 25 and level 30 | A heated moment with at least 3 heated messages (verdict floor met). Each heated moment is ranked on its own, High before Medium and then by climb, and the alert describes the strongest one. It says whether the moment fell in one part of the chat or in N parts in a row |
| Many / Some messages were openly hostile ("recent messages" when it reads the latest part) | High at 20 per 100, Medium at 12 | At least 24 rated messages, 3 hostile messages in the window, and the rate clears the cut-off by a standard error |
| Hostile wording is picking up / Hostile wording is higher than at your last check | Medium | Only when no "openly hostile" alert stands. Conflict strongly rising inside the check, with at least 3 such messages lately and at least 5 per 100 lately (the first title); or strongly up on the previous check (like for like), to at least 5 per 100 (the second) |
| Sarcastic wording is common / growing | Medium | Same rules at 12 per 100, or strongly rising to at least 5 (inside the check only) |
| Fewer messages show care | Medium | Care strongly falling with at least 5 care messages early on; or strongly down on the previous check (like for like) from at least 5 per 100 |
| The overall mood slipped since your last check | Medium | Health score down strongly on the previous check (like for like): at least 8 points and 2.5 standard errors |
| Care is growing / Hostility is easing / Hostility is lower than at your last check / The overall mood improved | Good news | The mirror of the above |

The serious-flags alert lists the kinds Vibe Check counted with their own counts ("threats (2), harassment (1)") and never adds them into one total, because one message can carry more than one flag. A single kind reads "1 message as a threat" or "3 messages as harassment". It says how many messages scored 7 or more out of 10, and whether the flagged messages are quoted under Flagged messages, only some are, or none are ("counted, not quoted"); the next step points to quotes only when there are some. For a threat or hate speech it adds the safety sentence.

At Low sensitivity the cut-offs for the hostile and sarcastic alerts (and the "at least 5 per 100" floor of the two "picking up" alerts) are 1.5 times higher, and the Medium cut-offs of the heated-moment alert are 1.2 times higher (see Level words). The page shows every High and Medium alert and at most three lines of good news; the first five show at once and the rest wait under "Show N more". The PDF and the CSV list them all.

Wording rules, enforced by the tests: short plain sentences of at most 22 words; no jargon (no FAS, stretch, episode, baseline, peak, toxic, "communication unit", standard error or z-score, and no formula, in any of the new sections or in the method text); next steps address the group's habits and the situation, never "the members who" or "the people involved", and never suggest a ban, warning, mute or any punishment; alerts never quote or name a message or a person; the tests run every string through Vibe Check's own word filter (`vibe_check.engine._PUNITIVE`) and a second, stricter list. The threats and hate-speech alert says a model can be wrong, points to the quoted messages, and says that if a message describes a real risk to someone the reader should follow their community's safety process or contact local emergency services.

## Badges

Seven badges, none for fewer than **30** rated messages. The No serious flags tile is held back while a High priority alert stands, or any alert about serious flags (it would read as praise beside it); the fold says so. Positive-tone badges also need the rate to clear the cut-off by one standard error. The three absence badges need every message read (no sample, nothing added since rating): otherwise the page says "Can't tell yet: only part of the chat was read."

| Badge | Earned when |
|---|---|
| Courteous | Clearly polite wording in at least 20 messages in 100, and fewer than 3 in 100 openly hostile |
| Supportive | Care or understanding in at least 12 messages in 100 |
| Encouraging | Encouraging wording in at least 12 messages in 100 |
| Light-hearted | Funny wording in at least 12 messages in 100 |
| Calm waters | A timeline of 48 or more, no heated moments, fewer than 10 in 100 messages heated, and fewer than 3 in 100 openly hostile |
| Cools fast | A heated moment (climb 25 or more) after which the heated rate is back within 10 points of its starting level inside three stretches. It shows how hot the moment got and how far it fell back |
| No serious flags | Vibe Check read at least 30 messages (90% of them or more), fewer than 1 serious flag per 200, and none scoring 7 or more. A serious flag is a threat, hate speech, harassment or bullying |

If 19 in 20 messages have all 13 ratings at 0, no badge is given and the page says almost no tone was found. The page shows earned badges as tiles with the figure behind them; the other rules sit in a closed "How the other badges work" fold. There are no locks, scores, streaks or "x of 7". Badges never name a person, never rank teams, and never cancel an alert.

## Where it appears

Page order, on the workplace page and the report page: **Summary strip** (the Vibe Check score, the number of things to look at, the badges earned, and links; when Vibe Check did not run the first tile says 'Not run' and the count says 'Not checked' instead of 0) → **Alerts** → **Measures over time** (four cards with level word, count, trend and chart; the health tile; Since your last check; from the third check, the health score check by check) → **Badges** → the signal profile → guidance → Vibe Check. On the workplace page the results sit above the plan, engine and messages panels once there are results. The report page ends with How this report works, whose fifth entry, "Measures, alerts and badges", is the method in plain words (at most 8 sentences of at most 16 words; it is in `analysis.HOW_IT_WORKS`, so the PDF prints it too). The PDF has two sections after the cover, Alerts and badges and Measures over time, each of which can run to two pages, and every page after the cover carries the footer line "Model-rated wording. Not a verdict on people." The CSV carries the same blocks as rows (Alerts, Measures over time with a Mark column that says each card's icon in words, Since your last check, Badges), each opening with a "How to read this" row that holds its caveat. A part of the chat that could not be judged (under 48 messages) has neither Yes nor No in the column "Counted as a heated moment", and the cards' latest-part column reads "Level in the last part" when the lines follow the order added. In the PDF the level of the latest part sits in the line of each card's trend. The four small charts share one table twin (every part of the chat, with its rates and its health score) and the check-by-check chart has its own. The caveats are always shown, never folded. Every fixed line comes from `measures.py`, so the page, the PDF and the CSV print the same words: the five caveats (`ms['caveats']`: alerts, empty, measures, badges, since), the two fixed sentences (`ms['texts']`: "This is your first check. Run another after your next batch of messages and this panel can show what moved, if both checks used the same settings." and "No badges this time. Each one is earned only when the messages clearly show it.") and the notes, each once in its own section. Without the 13 ratings (the rating did not finish, or only Vibe Check ran) these sections are not built, and the gap under Measured behaviour says that alerts, badges and the measures over time need the ratings too. The home list shows only the Vibe score, so teams are not ranked side by side.

## Left out of the Felixa backend, and why

Stripe payments, e-mailing the report, the OpenAI call, the YouTube and Reddit fetchers and the duplicate-payment guard belong to the paid web service. The backend's flagged comments, content categories, sentiment bars and recommendations already exist here through Vibe Check, so the page links to them instead of repeating them. The backend's "stricter moderation" and "warnings before bans" lines were not carried over, because this app never suggests punishment. The health score is the backend's, rescaled as described, so it will not match its numbers exactly.

## How it was checked

The cut-offs were set against made-up chats, not by feel. The generator is in the repository: `tools/simulate_measures.py`, pure Python with fixed seeds. For each case below it makes 100 chats at each of 24, 76, 300 and 1,000 messages. Then it runs the real `measures.analyse`, `measures.alerts` and `measures.badges` on them. The tables do not count badges. `python3 tools/simulate_measures.py` prints the tables below in about half a minute, and `--markdown` prints them as Markdown. DEV.md lists the options.

Each message is one of a few types with fixed odds for its 13 ratings. The types are polite, supportive, funny, friendly banter, trash talk, agitated, openly hostile, no tone at all and a few more. The hostile tones are rated together, as a model does. Every 0 has a 1.5% chance to become a weak 1 (the neutral community stays at 0). Vibe Check is taken to have flagged nothing serious. Six communities do not change:

* **calm**: polite and warm. In 100 messages, about 17 are clearly polite, 11 encouraging, 8 caring and 2 heated.
* **support**: care and understanding in about 20 messages in 100.
* **banter**: gaming talk. In 100 messages, about 5 are openly hostile, 6 sarcastic and 13 heated.
* **banter_pure**: friendly sarcasm and jokes, with no trash talk. About 5 sarcastic and 12 funny messages in 100.
* **neutral**: no tone at all.
* **hostile**: open hostility all along. About 27 openly hostile and 45 heated messages in 100.

Six events are planted in a calm chat. **outburst** is 20 messages of mostly open hostility and agitation in the middle. **spat5** is 5 openly hostile messages in the middle. **blip** is one very hostile message. **slow_burn** drifts from calm to hostile over the whole chat. **start_burst** and **end_burst** are an outburst of 20 messages at the start or at the end.

Each cell is the percent of 100 runs at 24 / 76 / 300 / 1,000 messages. "Medium or High alert" is an alert of that priority, of any kind. "Heated moment" means a heated moment passed the chance test, so the Escalation card shows it. "Heated-moment alert" is the alert "The mood heated up fast". "Rising or falling call" means Conflict, Empathy, Passive-aggressive or the health score is called rising or falling.

| Community (percent of runs at 24 / 76 / 300 / 1,000 messages) | Medium or High alert | Heated moment | Heated-moment alert | Rising or falling call |
|---|---|---|---|---|
| calm | 0 / 1 / 0 / 1 | 0 / 1 / 1 / 0 | 0 / 1 / 0 / 0 | 0 / 11 / 3 / 6 |
| support | 0 / 0 / 2 / 0 | 0 / 0 / 6 / 0 | 0 / 0 / 2 / 0 | 0 / 5 / 7 / 2 |
| banter | 2 / 0 / 2 / 1 | 0 / 0 / 2 / 3 | 0 / 0 / 2 / 1 | 0 / 10 / 14 / 4 |
| banter_pure | 1 / 0 / 5 / 0 | 0 / 0 / 5 / 3 | 0 / 0 / 5 / 0 | 0 / 5 / 4 / 8 |
| neutral | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| hostile | 86 / 75 / 99 / 100 | 0 / 0 / 0 / 1 | 0 / 0 / 0 / 1 | 0 / 9 / 8 / 15 |

| Event (percent of runs at 24 / 76 / 300 / 1,000 messages) | Medium or High alert | Heated moment | Heated-moment alert | Rising or falling call |
|---|---|---|---|---|
| outburst | 97 / 46 / 98 / 2 | 0 / 46 / 98 / 60 | 0 / 46 / 98 / 0 | 0 / 12 / 7 / 7 |
| spat5 | 22 / 74 / 2 / 0 | 0 / 74 / 21 / 1 | 0 / 74 / 2 / 0 | 0 / 10 / 9 / 1 |
| blip | 0 / 1 / 0 / 0 | 0 / 0 / 3 / 0 | 0 / 0 / 0 / 0 | 0 / 9 / 4 / 6 |
| slow_burn | 15 / 51 / 97 / 100 | 0 / 2 / 7 / 1 | 0 / 2 / 7 / 1 | 0 / 87 / 100 / 100 |
| start_burst | 97 / 1 / 0 / 0 | 0 / 1 / 0 / 0 | 0 / 1 / 0 / 0 | 0 / 100 / 99 / 26 |
| end_burst | 98 / 99 / 100 / 56 | 0 / 57 / 100 / 100 | 0 / 57 / 100 / 56 | 0 / 100 / 99 / 22 |

What the tables show:

* **Communities that do not change.** In calm, support, banter and banter_pure, a Medium or High alert appeared in 0 to 5% of the checks. A heated moment appeared in 0 to 6%. From 76 messages, at least one of the four lines was called rising or falling in 2 to 14% of the checks. The neutral community raised nothing. The hostile one raised a Medium or High alert, mostly about Conflict, in 75 to 100% of the checks. It had a heated moment in 1% of the checks at most, because it never climbs.
* **One very hostile message** raised an alert in 1 check in 100 at most, as calm chats do. At 300 messages the card showed a heated moment in 3% of the checks. Two other heated messages stood in the same part of the chat.
* **A 20-message outburst** raised the heated-moment alert in 46% of the checks at 76 messages and in 98% at 300. At 1,000 messages it is spread thin. The card showed it in 60% of the checks, and none raised the alert.
* **A 5-message spat** raised the alert in 74% of the checks at 76 messages, in 2% at 300 (the card showed it in 21%) and in none at 1,000.
* **A burst at the end** raised the alert in 57% of the checks at 76 messages, in 100% at 300 and in 56% at 1,000.
* **A burst at the start** has nothing before it to climb from. It raised an alert and a heated moment in 1% of the checks at 76 messages and in none from 300. It was called rising or falling in 99 to 100% of the checks at 76 and 300 messages.
* **A slow burn** is not a heated moment (7% of the checks at most). It raised a Medium or High alert, mostly about Conflict, in 51% of the checks at 76 messages and in 97% or more from 300. It was called rising or falling in 87% at 76 messages and in every check from 300.
* **At 24 messages** nothing can be called a direction or a heated moment, but the level alerts still work. Where most messages are hostile (hostile, outburst, start_burst, end_burst), a Medium or High alert appeared in 86 to 98% of the checks.

Run with `--sensitivity low`, banter and banter_pure raised an alert in 0 to 2% of the checks (0 to 5% at Medium), and the hostile community in 45 to 99% (75 to 100% at Medium). Low trades some detection for fewer false alarms on banter.

These chats are our assumptions, not real ones, so the cut-offs remain starting choices. A cell rests on 100 runs, so it can be off by a few points, and by up to 10 in the middle of the range. A second run with 300 chats per cell and another seed (`--reps 300 --seed 2`) gave the same picture. After the fixes of the final review the tables were run again and did not move.

`FalseAlarmTests` in `tests/test_measures.py` is a smaller pure-Python version with its own copy of the message types, and it runs with the test suite: 150 calm chats of 300 messages must rarely raise a Medium or High alert or a trend, and a planted 20-message outburst must usually raise the heated-moment alert. DEV.md says when to run the full simulation.

## Code map

`measures.py` (all rules and wording; `analyse`, `cards`, `alerts`, `badges`, `compare`, `record`, `build`), `store.py` (`measure_history`, the allow-list, `rated_units`, `evidence_generation`), `pipeline.py` (saves a record right after the ratings), `report.py` (`ctx['measures']`), `bei_charts.py` (`measure_chart`, `trail_chart`, `health_meter`), `measures_view.py` (HTML for the page), `measures_export.py` (PDF pages and CSV rows), `report_export.py` (the PDF: it calls `measures_export` and prints the footer line), `analysis.py` (`HOW_IT_WORKS`, the method text), the templates `_summary.html`, `_alerts.html`, `_over_time.html`, `_badges.html`, and `static/style.css` (the `vc-*` rules of the new sections). The tests are `tests/test_measures.py` (the rules, number by number), `tests/test_measures_store.py` (what is saved), `tests/test_measures_export.py` (the PDF and the CSV) and `tests/test_measures_app.py` (whole journeys through the app). The simulation behind the cut-offs is `tools/simulate_measures.py`: it makes made-up chats, runs the real `measures` code on them and prints how often each alert and call comes out (see How it was checked).
