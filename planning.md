# Provenance Guard — Planning

> Status: Architecture, contract, and detection-spec design. **No application code yet.**
> Stack (`requirements.txt`): Python · Flask · flask-limiter · groq (LLM) · python-dotenv
>
> This document is the source of truth. Milestones 3–5 generate code *against* the
> concrete numbers, thresholds, and label strings written here. Anywhere this doc
> says "0.65" or quotes exact label text, that is a contract the code must honor.

---

## 1. What the system is

Provenance Guard takes submitted text, estimates how likely it is to be
AI-generated, attaches a **transparency label** that communicates that estimate
*with its uncertainty*, records everything in an **audit log**, and lets a
creator **appeal** a label they believe is wrong.

Guiding principle: this is a *transparency* tool, not a verdict machine. Every
label carries a confidence number and an appeal route. We never claim certainty.

### The seven features and how they connect

| # | Feature | Role |
|---|---------|------|
| 1 | **Submission / ingestion** | Validates + normalizes raw text. (`POST /submit`) |
| 2 | **Signal 1 — Predictability judge** | First independent AI-ness measurement. |
| 3 | **Signal 2 — Stylistic-fingerprint judge** | Second measurement, *different* property. |
| 4 | **Confidence scoring** | Combines the two signal scores into one `ai_score` + an `agreement` reliability measure. |
| 5 | **Transparency label** | Maps the numbers to one of three human-readable label variants. |
| 6 | **Audit log** | Append-only record of every submission, score, label, and appeal. |
| 7 | **Appeal handling** | Lets a creator contest a label; updates status; logs it. (`POST /appeal`) |

Cross-cutting: **rate limiting** (flask-limiter) on the LLM-backed endpoints.

---

## 2. Architecture narrative — the path of one piece of text

1. **Arrival (`POST /submit`).** JSON body with `text` and `creator_id` (the
   latter binds the submission to a creator so appeals are ownable, §6). The
   submission layer validates (both non-empty strings, `text` ≤ length bound,
   valid UTF-8), assigns a `content_id`, normalizes whitespace, and applies the
   rate limit. → passes `text` + `creator_id` + `content_id`.
2. **Signal 1 — Predictability judge (Groq).** Measures how low-surprise/templated
   the text reads (perplexity proxy). → `s1 ∈ [0,1]` + rationale.
3. **Signal 2 — Stylistic-fingerprint judge (Groq).** Independently prompted;
   measures human-voice markers vs. AI stylistic hallmarks. → `s2 ∈ [0,1]` + rationale.
4. **Confidence scoring.** `ai_score = 0.5·s1 + 0.5·s2`; `agreement = 1 − |s1 − s2|`.
   → `ai_score`, `agreement`.
5. **Transparency label.** Maps `ai_score` (which band) + `agreement` (confidence
   qualifier) to one of three label variants. → `label` text + numbers.
6. **Audit log.** Append entry: `content_id`, ts, text hash, `s1`, `s2`,
   rationales, `ai_score`, `agreement`, `label`, `status="active"`. → confirmation.
7. **Response.** Returns `content_id`, `label`, `ai_score`, `confidence`,
   per-signal breakdown.

**Appeal flow (separate request):** `POST /appeal` with `content_id` + reason →
appeal handler looks up the entry → status `active → under_review` → appends a
*new* audit entry (original preserved; log is append-only) → returns updated status.

---

## 3. Detection signals  *(Q1)*

Two LLM judges (Groq), each measuring a **different property** so they aren't
redundant. Both run at low temperature (`temperature=0.0–0.2`) for reproducibility.

### Signal output contract
Each judge is prompted to return strict JSON:
```json
{ "score": 0.0, "rationale": "one sentence" }
```
- `score` ∈ `[0.0, 1.0]` — continuous, **not** a binary flag. `0.0` = confidently
  human, `1.0` = confidently AI. The code clamps to `[0,1]` and, if parsing fails,
  treats that signal as `score=0.5` (maximally uncertain) and flags a parse error
  in the audit entry rather than crashing.

### Signal 1 — Predictability judge
- **Measures:** how predictable / low-surprise the text is (qualitative perplexity
  proxy) — templated phrasing, safe word choice, "expected" continuations.
- **Why it separates human/AI:** LMs emit high-probability (low-perplexity) tokens,
  so output trends smoother than spikier human choices.
- **Blind spot:** human-edited or high-temperature AI text raises surprise and
  evades it; weak on very short text.

### Signal 2 — Stylistic-fingerprint judge
- **Measures:** human-fingerprint markers (personal voice, concrete first-hand
  specifics, idiom, small imperfections) vs. AI hallmarks (even hedging,
  symmetrical "on one hand / on the other" structure, generic connectives,
  uniform polish).
- **Why it separates human/AI:** instruction-tuned models converge on a polished,
  hedged "house style"; humans leave idiosyncratic fingerprints.
- **Blind spot:** penalizes naturally-formal or non-native human writers
  (false-positive risk); defeated by "write casually with anecdotes" prompts.

### Combining the two signals
```
ai_score  = 0.5 * s1 + 0.5 * s2          # primary axis: P(AI), in [0,1]
agreement = 1 - abs(s1 - s2)             # reliability: 1 = identical, 0 = opposite
```
Equal weighting because both are LLM judges of comparable trustworthiness. We do
**not** average in a way that hides disagreement — disagreement is surfaced
separately as `agreement`, which drives the confidence qualifier (§4). This is the
deliberate guard against the two-LLM correlated-failure problem (§3 weakness, §6).

### Honest weakness of two LLM signals
Both run through Groq → **correlated failures**: nondeterministic, same model bias,
same paraphrase defeats both, added latency/cost. When both fail the *same* way
they *agree*, so high `agreement` can mask a shared error (the Maria case, §6).
Mitigations baked in: low temperature, deliberately divergent prompts, treat
agreement as reliability not proof, wide "uncertain" band (§4), mandatory appeals.

---

## 4. Uncertainty representation  *(Q2)*

Two distinct numbers, never conflated:

| Number | Meaning | Range |
|--------|---------|-------|
| `ai_score` | Estimated **likelihood the text is AI-generated**. | 0.0 (human) – 1.0 (AI) |
| `agreement` | How much the two signals **agree** = reliability of the call. | 0.0 – 1.0 |

**What `ai_score = 0.6` means to the system:** "We estimate a 60% likelihood this
text is AI-generated — inside the *uncertain* band, leaning slightly AI. This is
not a verdict." It is reported to two decimals and *always* accompanied by the
confidence qualifier from `agreement`, so a user never sees a bare `0.6`.

**Mapping raw signal outputs → calibrated score.** Each judge returns its own
`score` in `[0,1]`; we clamp, then `ai_score = mean(s1, s2)`. We deliberately keep
the combiner simple and instead make the *uncertain band wide* (§ thresholds
below) so the system errs toward humility rather than false precision. We do **not**
publish more decimal places than the signals justify (two decimals only). A future
calibration step (post-Milestone 5) could fit raw scores to labeled examples; until
then the wide uncertain band is the calibration safeguard, and this assumption is
documented as a known limitation.

**Threshold bands on `ai_score`** (separates likely-AI / uncertain / likely-human):

| `ai_score` | Band | Internal id |
|------------|------|-------------|
| `≥ 0.65` | Likely AI-generated | `likely_ai` |
| `0.35 – 0.649…` | Uncertain | `uncertain` |
| `< 0.35` | Likely human-written | `likely_human` |

**Confidence qualifier from `agreement`** (modifies the label wording):

| `agreement` | Qualifier |
|-------------|-----------|
| `≥ 0.75` | high confidence |
| `0.50 – 0.749…` | moderate confidence |
| `< 0.50` | low confidence — signals disagreed |

**Override rule:** if `agreement < 0.50`, the label is forced to the **Uncertain**
variant regardless of where `ai_score` falls — we refuse to assert "likely AI/human"
when the two detectors contradict each other.

---

## 5. Transparency label design  *(Q3)*

Templates with `{ai_score}` (two decimals) and `{conf}` (qualifier from §4) filled
in. Every variant states it is an automated estimate and points to the appeal route.

**Variant A — high-confidence AI** (`ai_score ≥ 0.65`, `agreement ≥ 0.75`):
```
🤖 Likely AI-generated — {conf}.
Both detectors agree this text shows strong signs of AI authorship
(AI-likelihood {ai_score}). This is an automated estimate, not proof.
If you wrote this yourself, you can appeal this label.
```

**Variant B — high-confidence human** (`ai_score < 0.35`, `agreement ≥ 0.75`):
```
✍️ Likely human-written — {conf}.
Both detectors agree this text reads as human-authored
(AI-likelihood {ai_score}). This is an automated estimate, not proof.
You can appeal if you believe this label is wrong.
```

**Variant C — uncertain** (`0.35 ≤ ai_score < 0.65`, *or* `agreement < 0.50` via
the override rule):
```
❓ Uncertain — {conf}.
Our detectors could not reach a confident conclusion (AI-likelihood {ai_score}).
This text may be AI-assisted, or simply written in a style our tools find hard
to judge. We are not making a determination. You can appeal to flag this for review.
```

(`{conf}` resolves to e.g. "high confidence", "moderate confidence", or
"low confidence — signals disagreed".)

---

## 6. Appeals workflow  *(Q4)*

- **Who can appeal:** the creator/submitter — in this project, anyone holding the
  `content_id` returned by `/submit`. (A production version would bind appeals
  to an authenticated owner; noted as a limitation.)
- **What they provide:** `content_id` (required), `reason` free-text (required),
  and optional `claimed_origin` ∈ {`human`, `ai_assisted`, `ai`} stating what they
  say the true provenance is.
- **What the system does on receipt:**
  1. Validate the `content_id` exists (else `404`).
  2. Reject a duplicate open appeal on the same id (`409`) — one open appeal at a time.
  3. Transition status `active → under_review`.
  4. Append a **new** audit entry of type `appeal` (the original `submission` entry
     is never mutated — log is append-only) capturing: timestamp, `reason`,
     `claimed_origin`, and a pointer to the original entry.
  5. Return `{ content_id, status: "under_review", message }`.
- **Status lifecycle:** `active → under_review → {overturned | upheld}`. A human
  reviewer (out of scope to *build* the reviewer UI this milestone, but the data
  supports it) sets the terminal status, which appends one more audit entry.
- **What a reviewer sees in the appeal queue:** a list of `under_review` items, each
  showing: `content_id`, submission timestamp, the **original label + `ai_score`
  + `agreement`**, **both signal rationales** (so they see *why* the system decided),
  the appellant's `reason` and `claimed_origin`, and actions to **uphold** or
  **overturn** (each writing a final audit entry).

---

## 7. Anticipated edge cases  *(Q5)*

Specific content types the system handles poorly, with the failure mechanism and
the mitigation:

1. **Repetitive simple-vocabulary verse (poems, song lyrics, nursery rhymes).**
   Heavy repetition + small vocabulary reads as low-surprise → Signal 1 scores it
   high (looks templated) → false "likely AI". *Mitigation:* such text usually
   makes the two signals disagree (Signal 2 may catch genuine voice), pushing it to
   the Uncertain band via the agreement override; documented so reviewers expect it.

2. **Formal or non-native ("ESL") human writing** — the Maria case. Polished,
   structured, lightly-hedged prose trips *both* judges the same way, so they
   **agree** and the system is **confidently wrong** (Variant A, high confidence).
   This is the most dangerous case because our reliability proxy (agreement) fails.
   *Mitigation:* label always carries the appeal route; we keep the AI threshold at
   0.65 (not lower) to reduce sensitivity; flagged as the top false-positive risk.

3. **Very short text (< ~40 words: a tweet, a comment, a title).** Too little
   signal; both judges guess and may spuriously agree. *Mitigation:* enforce a
   minimum length at `/submit`; below it, short text is auto-labeled Uncertain with
   an explicit "too short to judge reliably" note rather than scored.

4. **Human-edited AI text (hybrid authorship).** Genuinely ambiguous by nature; a
   human pass raises surprise and adds voice, evading both signals → lands in
   Uncertain. *Mitigation:* this is *correct* behavior — the Uncertain band + the
   `claimed_origin: ai_assisted` appeal option exist precisely for this.

5. **Non-prose input (code, tables, lists, data dumps).** The judges are prompted
   for natural-language prose and are unreliable here. *Mitigation:* documented;
   future work could detect and reject/flag non-prose at ingestion.

---

## 8. API surface (the contract)

| Method | Endpoint | Accepts | Returns |
|--------|----------|---------|---------|
| `POST` | `/submit` | `{ "text": "<string>", "creator_id": "<string>" }` | `{ content_id, creator_id, attribution, confidence, llm_score, label, signals: { predictability, stylistic } }` |
| `POST` | `/appeal` | `{ "content_id", "reason", "claimed_origin"? }` | `{ content_id, status, message }` |
| `GET`  | `/result/{content_id}` | path param | stored label + scores for one submission |
| `GET`  | `/log` | optional `?limit=N` (default 50) | `{ "entries": [...] }` — most-recent audit entries, newest first |
| `GET`  | `/audit/{content_id}` | path param | full trail for one content_id (incl. appeals) |
| `GET`  | `/health` | — | `{ "status": "ok" }` |

- All bodies/responses JSON. `/submit` and `/appeal` are rate-limited.
- `/log` has no auth by design — it exists for documentation/grading visibility.
- Errors: JSON `{ "error": "<message>" }` with `400` (bad input), `404` (unknown id),
  `409` (duplicate open appeal), `429` (rate-limited).

**Audit entry schema** (one JSONL line per event; append-only). A `submission`
entry carries: `timestamp`, `content_id`, `creator_id`, `attribution`
(`likely_ai` | `uncertain` | `likely_human` — null until M4), `confidence`
(null until M4), `llm_score` (signal 1, present from M3), `status`
(`pending_scoring` → `classified` in M4 → `under_review` on appeal), plus
`text_hash` and signal-1 rationale/error for debugging.

---

## 9. Architecture

> This is the reference diagram that travels into Milestones 3–5. When prompting
> an AI tool to generate code, paste this section alongside the relevant spec section.

**Submission flow:** a client `POST`s raw text to `/submit`; the text is validated
and fanned out to two independent Groq judges (Signal 1 = predictability, Signal 2 =
stylistic fingerprint), whose `[0,1]` scores are combined into `ai_score` (the AI
likelihood) and `agreement` (reliability), mapped to one of three transparency-label
variants, written to the append-only audit log, and returned to the caller.
**Appeal flow:** a creator `POST`s the `content_id` plus a reason to `/appeal`;
the handler looks the entry up, transitions its status `active → under_review`, and
appends a *new* audit entry — the original record is never mutated — then returns the
updated status.

### Submission flow
```
            { text }
client ───────────────▶ POST /submit
                            │  validated, normalized text + content_id
                            ▼
                    ┌───────────────────┐      ┌───────────────────┐
                    │ Signal 1: LLM     │      │ Signal 2: LLM     │
                    │ predictability    │      │ stylistic-        │
                    │ judge (Groq)      │      │ fingerprint (Groq)│
                    └───────────────────┘      └───────────────────┘
                       │ s1∈[0,1]                  │ s2∈[0,1]
                       └─────────────┬─────────────┘
                                     ▼
                        ┌─────────────────────────┐
                        │ Confidence scoring       │
                        │ ai_score = .5·s1 + .5·s2 │
                        │ agreement = 1−|s1−s2|    │
                        └─────────────────────────┘
                                     │ ai_score + agreement
                                     ▼
                        ┌─────────────────────────┐
                        │ Transparency label       │
                        │ bands(§4) → Variant A/B/C │
                        └─────────────────────────┘
                                     │ label + numbers
                                     ▼
                        ┌─────────────────────────┐
                        │ Audit log (append)       │
                        │ id, ts, hash, s1, s2,    │
                        │ ai_score, agreement,     │
                        │ label, status=active     │
                        └─────────────────────────┘
                                     │ content_id + label + scores
                                     ▼
client ◀──────────────────────────────  JSON response
```

### Appeal flow
```
        { content_id, reason, claimed_origin? }
creator ─────────────────────────────────▶ POST /appeal
                                               │ content_id
                                               ▼
                                      ┌────────────────────┐
                                      │ Appeal handler      │
                                      │ look up entry;      │
                                      │ reject if missing/  │
                                      │ already appealing   │
                                      └────────────────────┘
                                               │ active → under_review
                                               ▼
                                      ┌────────────────────┐
                                      │ Audit log (append)  │  NEW entry; original
                                      │ type=appeal,        │  preserved (append-only)
                                      │ reason, origin      │
                                      └────────────────────┘
                                               │ updated status
                                               ▼
creator ◀───────────────────────────────────  { content_id, status, message }
```

---

## 10. AI Tool Plan

How each implementation milestone will use this spec to drive AI code generation.
For every milestone: paste the listed spec sections **plus the §9 Architecture
diagram**, ask for the listed artifacts, and run the listed verification *before*
moving on.

### M3 — Submission endpoint + first signal
- **Spec sections to provide:** §3 Detection signals (esp. the output contract +
  Signal 1) · §8 API surface (`/submit`) · §9 Architecture diagram.
- **Ask the AI tool to generate:**
  - A Flask app skeleton: `/submit` and `/health`, JSON in/out, flask-limiter on
    `/submit`, env-based Groq client init (python-dotenv), error handling
    (`400`/`429`).
  - The **Signal 1 function** `predictability_score(text) -> {score, rationale}`:
    a low-temperature Groq call returning strict JSON, clamped to `[0,1]`, with the
    parse-failure fallback to `0.5` from §3.
- **How I'll verify:** call `predictability_score` **directly** (not through the
  endpoint) on a few inputs — an obvious ChatGPT paragraph, a messy human comment,
  and an empty/short string — and confirm scores point the right way and JSON parses.
  Only then wire it into `/submit` and smoke-test the endpoint with curl.

### M4 — Second signal + confidence scoring
- **Spec sections to provide:** §3 Detection signals (Signal 2 + the combiner) ·
  §4 Uncertainty representation (bands + agreement override) · §9 diagram.
- **Ask the AI tool to generate:**
  - The **Signal 2 function** `stylistic_score(text) -> {score, rationale}`,
    independently prompted from Signal 1 so the judges don't collapse.
  - The **scoring logic**: `ai_score = 0.5·s1 + 0.5·s2`, `agreement = 1 − |s1−s2|`,
    and a `classify(ai_score, agreement)` that returns the band id, honoring the
    `agreement < 0.50 → uncertain` override.
- **What I'll check:** run clearly-AI vs. clearly-human samples and confirm
  `ai_score` **varies meaningfully** between them (AI text well above 0.65, human
  well below 0.35); feed a borderline/formal-human sample and confirm it lands in
  uncertain rather than a confident wrong call.

### M5 — Production layer (labels + appeals)
- **Spec sections to provide:** §5 Label variants (exact strings) · §6 Appeals
  workflow · §8 API (`/appeal`, `/result`, `/audit`) · §9 diagram.
- **Ask the AI tool to generate:**
  - **Label generation** `make_label(ai_score, agreement) -> label_text` filling the
    three exact Variant A/B/C templates with `{ai_score}`/`{conf}`.
  - The **`/appeal` endpoint** + append-only audit log writes, status transition
    `active → under_review`, duplicate-appeal `409`, unknown-id `404`.
- **How I'll verify:** assert all **three label variants are reachable** by feeding
  scores in each band (and a low-agreement case to trigger the override → Variant C);
  submit an appeal and confirm status flips to `under_review`, a new audit entry is
  appended, and the **original entry is untouched**.

---

## 11. Milestone checkpoint — self-check

- [x] **Q1 Detection signals:** 2 LLM signals, output = `{score∈[0,1], rationale}`,
      combined `ai_score = .5·s1+.5·s2` with separate `agreement` (§3).
- [x] **Q2 Uncertainty:** meaning of 0.6 defined, raw→calibrated mapping stated,
      thresholds 0.35 / 0.65 + agreement override (§4).
- [x] **Q3 Labels:** three exact variants written out (§5).
- [x] **Q4 Appeals:** who/what/status/logging/reviewer-queue all specified (§6).
- [x] **Q5 Edge cases:** 5 specific scenarios w/ mechanism + mitigation (§7).
- [x] Full text path + both flow diagrams (§2, §9); API contract (§8).
- [x] **Architecture** section with reference diagram + 2–3 sentence narrative (§9).
- [x] **AI Tool Plan** for M3/M4/M5: spec sections, asks, verification (§10).
- [x] Label variants reviewed — kept as-is (each carries score, confidence, and appeal route).

### Open decision for Milestone 2
Where does the audit log live — **in-memory** (simplest, resets on restart),
**JSON file** (survives restart, easy to inspect), or **SQLite** (queryable,
survives restart)? Leaning JSON file for inspectability during grading; revisit
if appeal-queue queries get complex.
