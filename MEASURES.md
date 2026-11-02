# Measures over time, alerts and badges

Felixa Community Health already rates every message on 13 tones (0 to 3) and runs Vibe Check. This adds four things on top, all computed from those same ratings, so they cost no extra model calls and work on every check that has ratings:

1. **Measures over time**: Escalation, Conflict, Empathy and Passive-aggressive, each followed through the conversation, plus a 0 to 100 **health score**.
2. **Alerts**: plain-language heads-ups, ranked High, Medium, Low, with a kind next step for each. Good news is listed too.
3. **Badges**: eight things a community can earn, each with the exact rule, shown earned or still to earn.
4. **Check by check**: the same numbers saved after every check, so the next check shows what moved.

Everything here describes wording in the messages. It says nothing about any person. A language model made the ratings and trained human raters have not checked them yet, and every cut-off below is our own starting choice, not a validated norm. The report says so.

## What each measure means

| Measure | In plain words | Good direction |
|---|---|---|
| **Escalation** | How fast tension is climbing. It is zero while tension is flat or falling, and jumps when a calm stretch turns tense. | down |
| **Conflict** | How much of the talk is open hostility: aggressive, rude, disrespectful or judgmental wording. | down |
| **Empathy** | How much of the talk shows care and understanding. | up |
| **Passive-aggressive** | Digs and sarcasm that stay polite on the surface, without open hostility. | down |
| **Health score** | One number from all 13 tones. Positive tones lift it, tense and toxic tones pull it down, harsher tones count for more. | up |

## How a message is scored

Each message has 13 ratings from 0 (absent) to 3 (strong). Write `x(Tone) = rating / 3`, so every x runs from 0 to 1.

| Per-message value | Formula |
|---|---|
| overt hostility | `max(x(Aggressive), x(Rude), x(Disrespectful), x(Judgmental))` |
| conflict | overt hostility |
| passive-aggressive | `x(Sarcastic) * (1 - overt hostility)`. Sarcasm only counts to the degree that it is not open hostility, so the same message never counts twice as conflict and as a dig. |
| empathy | `x(Empathetic)` |
| tension | `max(overt hostility, x(Agitated), x(Sarcastic))` |
| positive weight | `3x(Polite) + 3x(Empathetic) + 2x(Encouraging) + 1x(Funny)` |
| negative weight | `1x(Ignorant) + 1x(Egocentric) + 1x(Neurotic) + 2x(Sarcastic) + 2x(Agitated) + 3x(Aggressive) + 3x(Judgmental) + 3x(Disrespectful) + 3x(Rude)` |
| hostile message | a message whose overt hostility is at least 2/3 (some hostile tone rated 2 or 3) |

The health weights are the ones in the Felixa backend (`ATTRIBUTE_WEIGHTS`), applied to strength (0 to 1) where the backend used present or absent.

## Following the conversation: stretches

* **Order.** If at least 60% of the rated messages have a readable date, they are put in time order (ties keep upload order) and messages without a date are left off the timeline. Otherwise they stay in the order they were added, and the page says so.
* **Stretches.** The ordered messages are cut into equal-sized stretches of at least 8 messages, at most 24 stretches. A timeline needs at least 24 rated messages (three stretches). With fewer, the page gives the overall figures only.
* **Stretch value.** For each measure the stretch value is the mean of the per-message values times 100, so it runs 0 to 100. The health score of a stretch is `clamp(50 + 50 * (mean positive weight - mean negative weight) / 9, 0, 100)`, which is the backend formula (9 is the sum of the positive weights).
* **Escalation.** With `T[i]` the tension of stretch `i` (mean tension times 100): `E[0] = 0`, `E[1] = max(0, T[1] - T[0])`, and for `i >= 2`, `E[i] = max(0, T[i] - min(T[i-1], T[i-2]))`. So it measures the climb above the recent low and fades once tension stops rising.
* **Episode.** A run of consecutive stretches whose escalation is at least 15. Its baseline is the recent low it climbed from, its peak is the biggest escalation in it, and the tension reached is the highest tension in it.
* **Direction.** The mean of the first third of the stretches against the mean of the last third (at least one stretch each). Rising or falling when the change is at least `max(3, 20% of the first-third mean)` points (health: 5 points), otherwise steady. Escalation has no direction: it has a peak and a count of episodes.
* **Level words.** Conflict: Low under 8, Moderate 8 to 20, High over 20. Passive-aggressive: Low under 5, Moderate 5 to 12, High over 12. Empathy: Low under 3, Moderate 3 to 10, High over 10. Escalation peak: None under 10, Mild 10 to 25, Marked 25 to 40, Sharp from 40. Health: Excellent from 80, Good from 60, Fair from 40, Needs attention under 40.
* **The figure for "this check"** is the mean over every rated message (so it agrees with the signal profile), not over the stretches. "Latest stretch" is the last-third mean.

## Check by check

After each successful rating run the app saves one small record of numbers (no text, no names, no file names): the date, how many messages were rated, the health score, the mean Empathy, Conflict and Passive-aggressive, the escalation peak, the number of episodes. Records stay when messages are deleted or a new check starts, like the Vibe Check history. The report compares the current check with the one before it.

## Alerts

Each alert has a level, a title, what was seen (with the numbers), when, and a next step written in the same positive-psychology voice as Vibe Check. No alert ever suggests a ban, a warning, a mute or any punishment, and the tests enforce that with Vibe Check's own word filter.

| Alert | Level | Fires when |
|---|---|---|
| Threats or hate speech were flagged | High | Vibe Check counted at least one message as threats or hate speech |
| Tension climbed quickly | High / Medium | An episode with escalation peak at least 40 and tension reached at least 45 (High); peak at least 25 and tension at least 30 (Medium) |
| Conflict is high / noticeable / rising | High / Medium / Medium | Latest stretch at least 20 (High); at least 12 (Medium); or direction rising with the latest at least 8 (Medium) |
| Passive-aggressive tone is common / growing | High / Medium / Medium | Latest stretch at least 25 (High); at least 12 (Medium); or rising with the latest at least 5 (Medium) |
| Empathy is fading | Medium | Direction falling with the first third at least 5, or down at least 5 points on the previous check (which was at least 5) |
| Health score needs attention | High | Health score under 40 |
| Health score fell | Medium | Down at least 5 points on the previous check |
| Spam is crowding the chat | Low | Spam at least 5% of the messages Vibe Check read (at least 20 read) |
| Strong language is frequent | Low | Profanity at least 10% of the messages Vibe Check read (at least 20 read) |
| Not enough messages for a timeline | Low | Fewer than 24 rated messages |
| Many messages have no date | Low | At least 40% of the rated messages have no readable date |
| Empathy is growing / Conflict is easing / Health score rose | Good news | Empathy or health up, or conflict down, by the same direction rules or by at least 5 points on the previous check |

If the latest-stretch figure is not available (no timeline), the overall mean is used instead.

## Badges

A badge needs at least 20 rated messages (Clean chat needs 30 read by Vibe Check). Earned badges are shown with a tick and the figure behind them; the rest are listed with how to earn them.

| Badge | Earned when |
|---|---|
| Welcoming | Polite wording in at least 25% of the messages, and Conflict under 8 |
| Supportive | Empathy at least 8 |
| Encouraging | Encouraging FAS at least 8 |
| Light-hearted | Funny wording in at least 15% of the messages |
| Calm waters | A timeline exists, Conflict under 8, no stretch with Conflict at 15 or more, and no escalation episode |
| Quick to cool | An episode with peak at least 25, and tension back within 10 points of its baseline within three stretches after it |
| Clean chat | Vibe Check read at least 30 messages and flagged no threats, hate speech, harassment or bullying |
| On the rise | Health score up at least 5 points on the previous check, or rising across this check |

## Where it appears

* The workplace page and the report page: Alerts, Badges, then Over time (a card and a chart for each measure, a health card, and a table twin of every chart; a check-by-check chart and table from the second check on), before the signal profile.
* The PDF: an Alerts and badges page and an Over time page after the cover.
* The CSV: the same blocks as rows.

## Left out of the Felixa backend, and why

Stripe payments, e-mailing the report, the OpenAI call, the YouTube and Reddit fetchers and the duplicate-payment guard belong to the paid web service, not to this app. The backend's flagged comments, content categories (profanity, hate speech, violence, spam), sentiment bars and recommendations already exist here through Vibe Check, which also follows the rule of never suggesting punishment, so the backend's "stricter moderation" and "warnings before bans" lines were not carried over.

## Code map

`measures.py` holds every formula (pure functions; the one database read is `store.rated_units`). `store.py` keeps the check-by-check records. `pipeline.py` saves one after the ratings. `report.py` puts the results in the report context. `bei_charts.py` draws the charts (web and PDF share one drawing). `templates/_alerts.html`, `_badges.html` and `_over_time.html` show them; `report_export.py` adds the PDF pages and `report.csv_rows` the CSV rows.
