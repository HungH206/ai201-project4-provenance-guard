# Provenance Guard

Provenance Guard is a small service that estimates how likely a piece of text was
written by AI, attaches a plain language transparency label that carries its own
uncertainty, records every decision in an audit log, and lets a creator contest a
label they believe is wrong. It is built as a transparency tool, not a verdict
machine: every label states that it is an automated estimate and offers a way to
appeal.

The full design rationale, the flow diagrams, and the threshold contract live in
[planning.md](planning.md). This README covers what the system does, how to run
it, and the reasoning behind the two big engineering choices (the signals and the
confidence scoring).

## What it does

1. You send a piece of text to `POST /submit`.
2. Two independent detectors score it.
3. The scores are combined into one AI likelihood plus a measure of how much the
   two detectors agree.
4. Those numbers become one of three transparency labels.
5. The whole decision is written to an append only audit log.
6. If the creator disagrees, `POST /appeal` records their reasoning and moves the
   content into review.

## Setup and running

Requirements are in `requirements.txt` (Flask, `flask-limiter`, `groq`,
`python-dotenv`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The two detectors call the Groq API, so you need a key. Put a real key in `.env`
in the project root:

```
GROQ_API_KEY=gsk_your_real_key_here
```

Optionally override the model with `GROQ_MODEL` (the default is
`llama-3.3-70b-versatile`).

Run the server:

```bash
python app.py            # serves on http://127.0.0.1:5000
```

Run the tests (these do not need an API key, since they exercise the pure scoring
and label logic against the thresholds in planning.md):

```bash
python test_scoring.py   # 9 threshold tests
python test_labels.py    # 6 label variant tests, also prints all three variants
```

## API endpoints

**POST /submit** accepts `{"text": "...", "creator_id": "..."}` and returns the
attribution result, the confidence score, and the transparency label text.

```bash
curl -s -X POST http://127.0.0.1:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text":"Some prose to analyze.","creator_id":"writer-01"}'
```

```json
{
  "content_id": "a6442190-ec61-434d-a9c8-bed3723660b7",
  "creator_id": "writer-01",
  "attribution": "likely_ai",
  "confidence": 0.8,
  "ai_score": 0.9,
  "agreement": 1.0,
  "label": "🤖 Likely AI generated (high confidence). ...",
  "signals": { "predictability": {...}, "stylistic": {...} }
}
```

**POST /appeal** accepts `{"content_id": "...", "creator_reasoning": "..."}` and
returns a confirmation with the new status.

**GET /log** returns the most recent audit entries as JSON (`?limit=N`, default
50). It has no auth on purpose, so decisions are easy to inspect for grading.

**GET /health** returns `{"status": "ok"}`.

## Detection signals

The pipeline uses two distinct signals. Single signal detection is easy to fool
and gives you no way to notice when a call is shaky, so two independent readings
are the minimum that lets the system express real uncertainty.

Both signals are LLM judges (Groq), and each is asked to measure a **different
property**, so that they are not just two copies of the same opinion.

**Signal 1, the predictability judge.** It estimates how predictable and low
surprise the writing is, as a stand in for perplexity. The idea: language models
are trained to emit high probability continuations, so their output tends to read
smoother and safer than the spikier, less expected word choices a person makes.
Its blind spot is text that a human has edited, or text produced at high
temperature: editing raises the surprise and hides the machine origin. It is also
weak on very short text, where there is simply not enough signal.

**Signal 2, the stylistic fingerprint judge.** It looks at voice rather than
predictability: personal specifics, idiom, humor, uneven rhythm, and small
imperfections point to a human, while even handed hedging, symmetrical structure,
generic connectives, and a uniform polished register point to a model. Its blind
spot is the mirror image of a real person who happens to write in a formal,
polished style. It will read that as machine like.

**Why these two.** They fail in different places. Editing that defeats the
predictability judge does not necessarily add human voice, and formal human prose
that fools the style judge may still contain surprising word choices. When they
disagree, that disagreement is itself useful information (see scoring below).

**What I would change for a real deployment.** Both signals run through the same
model, so they share a failure mode: the same model bias, and the same paraphrase
can fool both at once. For production I would make at least one signal genuinely
different in kind, for example a deterministic stylometric measure computed in
code (sentence length variance, type token ratio, punctuation regularity) that
does not depend on any model at all. That would break the shared bias and would
also let the service keep working, in degraded form, when the API is unavailable.

## Confidence scoring and uncertainty

A binary "AI or human" answer hides the thing that matters most, which is how sure
the system is. So the pipeline reports a continuous confidence, and a submission
that barely clears a threshold produces a visibly weaker label than a clear cut
one.

Each signal returns a score in the range 0 to 1, where 1 means "looks like AI".
The combiner is deliberately simple and transparent:

```
ai_score  = 0.5 * signal_1 + 0.5 * signal_2     # the AI likelihood, 0 to 1
agreement = 1 - abs(signal_1 - signal_2)         # how much the two detectors concur
confidence = abs(ai_score - 0.5) * 2 * agreement # strength of the call, 0 to 1
```

`ai_score` places the text into one of three bands: `likely_ai` at 0.65 and above,
`likely_human` below 0.35, and `uncertain` in between. If `agreement` drops below
0.50, the two detectors are contradicting each other and the result is forced to
`uncertain` no matter what the average says. The numeric `confidence` is high only
when the score is decisive **and** the two signals concur, so a call sitting near a
boundary reads as low confidence even if both detectors happen to agree.

**How I tested that the scores are meaningful.** Two ways. First, the scoring and
label logic are unit tested against the exact thresholds in planning.md, including
the band boundaries at 0.35 and 0.65 and the agreement override
(`test_scoring.py`, `test_labels.py`, 15 tests total). These need no API key
because they feed synthetic signal values straight into the scoring function.
Second, I ran a calibration pass (`calibration_check.py`) over deliberately chosen
inputs and printed each signal separately, so a misbehaving signal would be
obvious.

**Two example submissions with different confidence.** These are real outputs from
the calibration pass.

High confidence case, a corporate buzzword paragraph:

```
"In today's rapidly evolving digital landscape, it is important to note that
 leveraging synergistic solutions enables organizations to unlock unprecedented
 value and drive sustainable growth."

signal_1 (predictability) = 0.90
signal_2 (stylistic)      = 0.90
ai_score  = 0.90   agreement = 1.00   confidence = 0.80
attribution = likely_ai
```

Lower confidence case, a balanced piece that pulls the two signals apart:

```
"The new policy has advantages and disadvantages. On one hand it improves
 efficiency. On the other, some team members found it a bit confusing at first,
 which honestly surprised me."

signal_1 (predictability) = 0.40
signal_2 (stylistic)      = 0.60
ai_score  = 0.50   agreement = 0.80   confidence = 0.00
attribution = uncertain
```

The first lands at confidence 0.80 with a firm label; the second sits right on the
midpoint and reports confidence 0.00 with an explicitly uncertain label. That gap
is the point: the score varies with the evidence, it is not a constant.

**What I would change for a real deployment.** The 0.5 and 0.5 weights and the
0.35 and 0.65 bands are reasonable starting points, not calibrated values. With a
labeled dataset I would fit the weights and the band edges to real human and AI
examples and measure the false positive rate directly, rather than relying on a
wide uncertain band as a safety margin.

## Transparency label

The label is what a reader actually sees. It has to state the result in plain
language and make the confidence meaningful to someone who is not technical. There
are three variants. The confidence word ("high confidence", "moderate confidence",
"low confidence") comes from the numeric confidence, so it never overstates a near
boundary call. `{ai_score}` is filled to two decimals.

**Variant A, likely AI:**

```
🤖 Likely AI generated (high confidence). Our detectors estimate this text was
probably written by AI (AI likelihood 0.90). This is an automated estimate, not
proof. If you wrote this yourself, you can appeal this label.
```

**Variant B, likely written by a person:**

```
✍️ Likely written by a person (high confidence). Our detectors estimate this text
was probably written by a human (AI likelihood 0.10). This is an automated
estimate, not proof. You can appeal if you believe this label is wrong.
```

**Variant C, uncertain:**

```
❓ Uncertain (low confidence). Our detectors could not reach a confident conclusion
(AI likelihood 0.53). This text may be partly AI assisted, or simply written in a
style our tools find hard to judge. We are not making a determination. You can
appeal to flag this for review.
```

## Appeals workflow

A creator who disagrees with a label sends the `content_id` from their submission
plus their reasoning:

```bash
curl -s -X POST http://127.0.0.1:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id":"PASTE-ID","creator_reasoning":"I wrote this myself; English is my second language so my style reads more formal than usual."}'
```

The endpoint validates that the `content_id` has a submission on record (otherwise
404), rejects a second appeal on the same content (409, one open appeal at a
time), appends a new audit entry with status `under_review` and the reasoning, and
returns a confirmation:

```json
{
  "content_id": "a6442190-ec61-434d-a9c8-bed3723660b7",
  "status": "under_review",
  "message": "Appeal received and logged for human review.",
  "appeal_logged_at": "2026-07-01T05:10:50.555153+00:00"
}
```

The original decision is never overwritten. The appeal is a new record that points
back at it, so the log always shows both the original call and the challenge.
There is no automated re classification; the design assumes a human makes the final
call.

## Rate limiting

`POST /submit` is limited to **10 per minute and 100 per day** per client address,
using `flask-limiter` with in memory storage.

The reasoning: a real writer checking their own work submits a handful of times a
minute at most, even when they edit and resubmit, so 10 per minute leaves them
comfortable while stopping a script that floods the endpoint. Each submission
costs two Groq calls, so an unbounded endpoint is both an abuse risk and a cost
risk, and the 100 per day ceiling caps sustained hammering from one source.
`POST /appeal` has a separate, looser limit of 20 per hour, since appealing is a
rare human action.

Evidence from a burst of 12 rapid submissions (the eleventh and twelfth are
rejected):

```
request 1  -> 200
request 2  -> 200
request 3  -> 200
request 4  -> 200
request 5  -> 200
request 6  -> 200
request 7  -> 200
request 8  -> 200
request 9  -> 200
request 10 -> 200
request 11 -> 429
request 12 -> 429
```

## Audit log

Every decision is written to `audit_log.jsonl`, one JSON object per line, append
only. A submission entry captures the timestamp, content id, creator id, the
attribution, the numeric confidence, the combined `ai_score` and `agreement`, both
individual signal scores, the label, the status, and a hash of the text (the raw
text is never stored). An appeal entry captures the reasoning and points back at
the original decision. You can read it with `GET /log`.

Sample output from `GET /log` after two submissions and one appeal (newest first):

```json
{
  "entries": [
    {
      "type": "appeal",
      "content_id": "a6442190-ec61-434d-a9c8-bed3723660b7",
      "creator_id": "writer-01",
      "status": "under_review",
      "appeal_reasoning": "I wrote this myself. English is my second language so my style reads more formal than usual.",
      "original_attribution": "likely_ai",
      "original_confidence": 0.8,
      "original_timestamp": "2026-07-01T05:10:49.793935+00:00",
      "timestamp": "2026-07-01T05:10:50.555153+00:00"
    },
    {
      "type": "submission",
      "content_id": "9f41cf52-1f4b-41df-9f8c-314200928f75",
      "creator_id": "writer-02",
      "attribution": "likely_human",
      "confidence": 0.63,
      "ai_score": 0.15,
      "agreement": 0.9,
      "signal_1_score": 0.2,
      "signal_2_score": 0.1,
      "label": "✍️ Likely written by a person (high confidence). ...",
      "status": "classified",
      "appealed": false,
      "text_hash": "69aa1a50dde093965ee0da7b44ea8816e293608a5df03d491cb3c716840546d2",
      "timestamp": "2026-07-01T05:10:50.552445+00:00"
    },
    {
      "type": "submission",
      "content_id": "a6442190-ec61-434d-a9c8-bed3723660b7",
      "creator_id": "writer-01",
      "attribution": "likely_ai",
      "confidence": 0.8,
      "ai_score": 0.9,
      "agreement": 1.0,
      "signal_1_score": 0.9,
      "signal_2_score": 0.9,
      "label": "🤖 Likely AI generated (high confidence). ...",
      "status": "classified",
      "appealed": false,
      "text_hash": "0ab9247c7b31eabe4050de7cdd97df737e96ae09a97785602103fdd169fee373",
      "timestamp": "2026-07-01T05:10:49.793935+00:00"
    }
  ]
}
```

## Known limitations

The clearest failure case is **formal writing by a real person, especially by
someone whose first language is not English.** Polished, structured, lightly
hedged prose is exactly the surface profile both detectors read as machine like.
This is not a data problem, it is a property of the signals: the predictability
judge reads careful formal prose as low surprise, and the style judge reads its
even structure as an AI hallmark. Because both signals fail the same way, they
agree, and agreement is what the system uses as its reliability proxy, so the
result is a confident wrong answer. This is precisely why the appeal path exists
and is not optional. The sample audit log above shows this case: a formal paragraph
flagged `likely_ai` at confidence 0.80, then appealed by a writer who says English
is their second language.

Two more, briefly. Repetitive simple verse (a poem or nursery rhyme) reads as low
surprise and can be scored as AI. Very short text (a title, a one line comment)
gives the judges too little to work with, and they may agree by accident. In both
cases the honest answer is uncertain, which is why the uncertain band and the
appeal route matter.

## Spec reflection

**One way the spec helped.** Writing the concrete thresholds and the exact label
text in planning.md before any code meant the implementation had a fixed target.
The scoring function was written straight against the 0.35 and 0.65 bands and the
agreement override, and the unit tests assert those exact numbers, so there was no
drift between "what I designed" and "what I built". Deciding the label wording up
front also caught vagueness early, before it could turn into vague code.

**One way the implementation diverged.** The plan originally derived the label's
confidence wording from `agreement` alone. During the Milestone 4 calibration I
found this produced incoherent labels: a text scoring 0.70, only just over the AI
line, would say "high confidence" simply because the two detectors agreed. That
overstates certainty near a boundary. So I changed the implementation to derive the
confidence word from the numeric `confidence` instead, which folds in both the
decisiveness of the score and the agreement, and I updated planning.md to match.
The divergence came from real evidence, not from convenience.

## AI usage

This project was built with an AI coding assistant acting as the implementation
tool, guided section by section from planning.md. Specific instances:

1. **Generating the confidence scoring logic.** I directed it to implement the
   combiner and classifier from the planning spec. It produced a clean
   `classify()` that averaged the two scores and applied the bands. I revised it in
   two ways: I had it add the `agreement < 0.50` override so contradicting signals
   are never reported as a confident call, and after calibration I overrode its
   original agreement based confidence wording in favor of a numeric confidence, to
   fix the near boundary overstatement described above.

2. **Generating the second signal.** I directed it to write a second LLM judge
   distinct from the first. Its first version was a reasonable style judge, but its
   prompt overlapped with the predictability judge. I revised the system prompt to
   explicitly tell the judge to assess voice and style only and not to reuse a
   predictability heuristic, so the two signals stay as independent as two LLM
   calls can be.

3. **Guarding against a dead detector.** When the first end to end test failed on
   an invalid API key, I had it make each signal degrade to a neutral 0.5 with an
   error field instead of crashing the request, so one broken detector cannot take
   down the endpoint. I kept this behavior and it is now covered by a test.

## Portfolio walkthrough

A short recorded walkthrough (a couple of minutes) accompanies this project.

`[ recording link goes here ]`

Rough script for the recording:

1. Show `POST /submit` on a clearly machine written paragraph, point out the
   `likely_ai` label at high confidence.
2. Submit a casual human note, show it flips to "likely written by a person".
3. Submit the formal balanced text, show it lands `uncertain`, and explain that
   this is the two signals disagreeing rather than a failure.
4. Appeal the first result with the second language reasoning, then show
   `GET /log` with the original decision and the `under_review` appeal side by
   side.
5. Close on one design decision: why disagreement between the two detectors is
   treated as low confidence rather than averaged away.
