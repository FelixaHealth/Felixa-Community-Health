# High Risk: the algorithm for the AI engine

Status: design, not built. Every output is a candidate for a person to read. Nothing here sends an alert, confirms anything or judges a person.

## What it must do

1. Read every message and say which of the 40 High Risk behaviours it shows, how strongly, and with which exact words.
2. Add the cyber behaviours: a **cyberbullying pattern** (repeated targeting) and a ninth category, **Cyber Attacks and Security Threats**.
3. Group the matches into **High Risk incidents**: moments that could hurt someone emotionally, mentally or physically, and that earn the black flag.
4. Connect each High Risk incident to the escalation incident it falls in, so Escalation, Conflict and High Risk read as one story.

It reuses what the app already has: the rating engine (Ollama or Claude), the incident finder, the held-for-review rule and the exact-quote views.

## Inputs

| Input | Today | Needed later |
|---|---|---|
| Message text, time, source | yes | |
| Who wrote it | no | sender id (hashed when Incognito is on) |
| Who it answers or targets | no | reply-to or mention |
| Context | the 3 messages before and after | |

Without sender and target, the algorithm reads every message as one speaker and says "repeated targeting wording", never "person A targeted person B".

## Stage 1: message reading (AI, one pass over everything)

One batched call for each 20 to 40 messages, temperature 0, a fixed JSON schema. For each message the engine returns, for every behaviour it finds:

```json
{
  "uid": 412,
  "findings": [
    {
      "behaviour": "Threats",
      "score": 3,
      "quote": "I'll make sure you regret this.",
      "target": "person",
      "stance": "direct",
      "harm": ["emotional", "physical"],
      "why": "States intent to cause the reader harm."
    }
  ]
}
```

- **score** 0 to 3, as with the 13 tones: 1 mild, 2 clear, 3 severe. A match is 2 or more. Threats, blackmail, self-harm encouragement, grooming and doxxing count from 1.
- **quote** must be an exact span of the message. A finding whose quote is not in the text is dropped.
- **stance** is one of `direct`, `quoted`, `joked`, `reported`, `negated`, `hypothetical`. Only `direct` matches go forward. This is the stance filter the Behavioural Intelligence engine already uses, applied by the model.
- **target** is `person`, `group`, `self` or `none`.
- **harm** lists the channels: emotional, mental, physical, financial, digital.
- **why** is one sentence, shown only in the review view.

The prompt holds, for each behaviour: a one-line definition, two examples, and two counter-examples (banter between friends, a news quote, a reclaimed word, game talk). The counter-examples carry most of the accuracy.

## Stage 2: verification (AI, candidates only)

Every finding with score 2 or more in a safety group (Threats, Blackmail, Coercion, Self-Harm, Suicide, Grooming, Sexual Exploitation, Doxxing, Extremist Recruitment) goes to a second, independent call. It sees the message and 3 messages either side, and is asked to **refute** the finding. Outcomes:

- both agree: `confirmed`
- the checker refutes: `disputed` (kept, shown with that word, never counted as a black flag)
- the checker cannot tell: `unclear`

Only candidates are checked, so the cost stays small.

## Stage 3: word and link rules (no AI)

A backstop that runs on every message:

- the existing Behavioural Intelligence literal rules (threat, exposure threat, kept identity papers, stated silence)
- new cyber rules: links with urgency ("verify your account"), requests for passwords or one-time codes, unexpected executable attachments, "hack", "DDoS", "leak your IP"

Each match carries its source: `ai`, `rules` or `both`. `both` is the strongest evidence. Rule-only matches in the cyber group are always `unclear` until validated.

## Stage 4: High Risk incidents

A **High Risk incident** is one or more confirmed matches that follow each other within the quiet gap (30 minutes, or 5 messages when the chat has no times; the same setting as Escalation).

For each incident the algorithm stores:

- start, end and the exact messages
- the behaviours and categories inside it, in order
- the harm channels
- the highest score
- `escalation_incident`: the number of the escalation incident it overlaps, or none
- the position: before the turn into conflict, at it, after it, or outside every escalation incident

A High Risk incident earns the **black flag** when its severity (Stage 6) reaches the line, or when it contains a safety-group behaviour.

## Stage 5: patterns

**Cyberbullying pattern.** At least 3 matches among Bullying, Harassment, Personal Attacks, Public Humiliation, Social Exclusion, Victim Blaming, Doxxing and Stalking, inside one incident or within 24 hours, or on 2 or more separate days. With sender and target known: the same target at least 3 times, from any sender. It names the pattern and never the person.

**Cyber attack wave.** A phishing, credential or malicious-link match that repeats in 3 or more messages, or a Coordinated Attack match with 3 or more messages mentioning the same target within 10 minutes. With sender known: from 3 or more different senders.

## Stage 6: severity and routing

```
severity = 40 * (score / 3)
         + 25 * (safety group ? 1 : 0)
         + 15 * (physical or financial harm ? 1 : 0)
         + 10 * (target is a person ? 1 : 0)
         + 10 * (repeats within the incident or becomes a pattern ? 1 : 0)
```

| Severity | What the page does |
|---|---|
| 60 or more | black flag, held for a person to read |
| 30 to 59 | listed, with a question to ask, no flag |
| below 30 | counted in the category totals only |

Until a named reviewer, a second reviewer and an approved emergency protocol exist, everything stays **held**. The app never alerts or acts on its own. Each match comes with the questions a reader should ask ("Was this meant to hurt? Was it a joke?").

## What is stored for each match

`uid`, behaviour, category, score, quote span, stance, harm channels, source (ai, rules, both), verification, model and prompt version, severity, incident id. A hash of the text plus the prompt version is the cache key, so a message is read again only when the prompt or the model changes.

## How to know it works

1. A **gold set** of 600 messages, read by two trained people: 15 for each behaviour, plus 150 harmless look-alikes (banter, quotes, news, reclaimed words, gaming talk, sarcasm).
2. Per behaviour: precision, recall and agreement on severity. Safety groups must reach recall of 0.9 before a black flag is allowed to be live. A behaviour that cannot reach 0.7 precision stays "Not read yet".
3. Tests for bias: dialects, non-English text, and identity terms that are not abuse when a person uses them about themselves.
4. **Shadow mode** first: the engine runs, nothing shows a black flag, and people compare its readings with their own.
5. Drift check on every model or prompt change, with the gold set re-run.

## Privacy and cost

- Incognito removes names before any call. Messages go to Claude only with the existing consent box.
- Batching and the cache keep a 1,000-message chat to about 40 reading calls and a handful of verification calls.
- No message text is kept in logs. The stored quote is the same exact span the review page already shows.

## Open decisions

1. Is a High Risk incident its own bar on the Over time graph, separate from escalation incidents? (Recommended: yes.)
2. Should upload capture sender and reply-to? It unlocks the two-person detectors and the strict form of the cyberbullying pattern.
3. Is there an existing Felixa security detector for the Cyber Attacks category, or does the app build its own rules?
