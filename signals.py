"""Detection signals for Provenance Guard.

Two independent LLM judges (planning.md §3), each measuring a DIFFERENT property
so they aren't redundant:
  - Signal 1 predictability_score: how low-surprise / templated the text reads.
  - Signal 2 stylistic_score: human-voice fingerprints vs. AI stylistic hallmarks.

Signal contract: each function takes raw text and returns
  {"score": float in [0,1], "rationale": str}
score = P(AI-generated): 0.0 = confidently human, 1.0 = confidently AI.
CONTINUOUS, not a binary flag. On any failure (API error, unparseable output)
the signal degrades to the maximally-uncertain 0.5 and reports the problem in
"error" rather than crashing the request.
"""

import json
import os
import re

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# Low temperature for reproducibility (planning.md §3).
_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
_TEMPERATURE = 0.0

_client = None


def _get_client():
    """Lazily construct the Groq client so importing this module never fails
    just because the key is missing (tests can import and monkeypatch)."""
    global _client
    if _client is None:
        _client = Groq()  # reads GROQ_API_KEY from the environment
    return _client


def _clamp01(value):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _extract_json(raw):
    """Parse a JSON object out of the model's reply, tolerating stray prose or
    code fences around it. Returns a dict or None."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _run_judge(system_prompt, text):
    """Shared LLM-judge machinery for both signals. Returns
    {"score": float, "rationale": str} and, on degradation, an "error" str
    with score defaulted to 0.5."""
    if not text or not text.strip():
        return {
            "score": 0.5,
            "rationale": "Empty text; cannot assess.",
            "error": "empty_input",
        }

    try:
        response = _get_client().chat.completions.create(
            model=_MODEL,
            temperature=_TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )
    except Exception as exc:  # network / auth / rate-limit — degrade, don't crash
        return {
            "score": 0.5,
            "rationale": "Signal unavailable; defaulted to uncertain.",
            "error": f"api_error: {exc}",
        }

    raw = response.choices[0].message.content
    parsed = _extract_json(raw)
    if parsed is None:
        return {
            "score": 0.5,
            "rationale": "Could not parse signal response; defaulted to uncertain.",
            "error": "parse_error",
        }

    score = _clamp01(parsed.get("score"))
    if score is None:
        return {
            "score": 0.5,
            "rationale": "Signal response had no numeric score; defaulted to uncertain.",
            "error": "missing_score",
        }

    rationale = parsed.get("rationale") or "No rationale provided."
    return {"score": score, "rationale": str(rationale)}


# --- Signal 1: Predictability judge (planning.md §3) -----------------------

_PREDICTABILITY_SYSTEM = (
    "You are a text-forensics detector that estimates how PREDICTABLE a piece of "
    "writing is, as a proxy for machine authorship. Predictable writing uses "
    "safe, high-probability word choices, templated phrasing, and 'expected' "
    "continuations — hallmarks of AI generation. Surprising, spiky, idiosyncratic "
    "word choices are more typical of humans. Judge ONLY predictability; ignore "
    "topic, correctness, and formatting.\n\n"
    "Respond with ONLY a JSON object, no other text, in this exact shape:\n"
    '{"score": <number 0.0-1.0>, "rationale": "<one sentence>"}\n'
    "score = probability the text is AI-generated based on predictability: "
    "0.0 = very human/surprising, 1.0 = very predictable/AI-like."
)


def predictability_score(text):
    """Signal 1 — estimate how AI-like the text is based on predictability."""
    return _run_judge(_PREDICTABILITY_SYSTEM, text)


# --- Signal 2: Stylistic-fingerprint judge (planning.md §3) ----------------

_STYLISTIC_SYSTEM = (
    "You are a stylometric analyst that estimates machine authorship from STYLE "
    "alone. Human writing tends to carry a personal voice: concrete first-hand "
    "specifics, idiom, humor, opinion, uneven rhythm, and small imperfections. "
    "AI writing tends toward stylistic hallmarks: even-handed hedging, symmetrical "
    "'on one hand / on the other' structure, generic transitions (moreover, "
    "furthermore, it is important to note), uniform politeness, and a polished, "
    "risk-free register. Judge ONLY style and voice; ignore topic and factual "
    "correctness. Do NOT reuse a predictability heuristic — assess voice.\n\n"
    "Respond with ONLY a JSON object, no other text, in this exact shape:\n"
    '{"score": <number 0.0-1.0>, "rationale": "<one sentence>"}\n'
    "score = probability the text is AI-generated based on style: "
    "0.0 = strong human voice, 1.0 = strong AI stylistic hallmarks."
)


def stylistic_score(text):
    """Signal 2 — estimate how AI-like the text is based on style/voice."""
    return _run_judge(_STYLISTIC_SYSTEM, text)


if __name__ == "__main__":
    # Independent test harness (planning.md §10): call each signal directly and
    # compare. Run: .venv/bin/python signals.py
    samples = {
        "clearly-AI": (
            "In today's fast-paced digital landscape, leveraging synergistic "
            "solutions is paramount. By harnessing the power of innovation, "
            "organizations can unlock unprecedented value."
        ),
        "clearly-human": (
            "ok so i tried the new ramen place last night and honestly? mid. "
            "broth was way too salty and i waited like 40 min lol"
        ),
        "short": "Nice work!",
        "empty": "",
    }
    for name, sample in samples.items():
        s1 = predictability_score(sample)
        s2 = stylistic_score(sample)
        print(f"[{name}]")
        print(f"   signal 1 (predictability): {json.dumps(s1, ensure_ascii=False)}")
        print(f"   signal 2 (stylistic):      {json.dumps(s2, ensure_ascii=False)}")
