"""Detection signals for Provenance Guard.

Signal contract (planning.md §3): each signal function takes raw text and returns
a dict {"score": float in [0,1], "rationale": str}. score = P(AI-generated):
0.0 = confidently human, 1.0 = confidently AI. The score is CONTINUOUS, not a
binary flag. On any failure (API error, unparseable output) the signal degrades
to the maximally-uncertain 0.5 and reports the problem in "error" rather than
crashing the request.
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
    """Coerce to a float in [0, 1]; return None if it isn't a number."""
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
    """Signal 1 — estimate how AI-like the text is based on predictability.

    Returns {"score": float in [0,1], "rationale": str} and, on degradation,
    an additional "error": str with score defaulted to 0.5.
    """
    if not text or not text.strip():
        return {
            "score": 0.5,
            "rationale": "Empty text; cannot assess predictability.",
            "error": "empty_input",
        }

    try:
        response = _get_client().chat.completions.create(
            model=_MODEL,
            temperature=_TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PREDICTABILITY_SYSTEM},
                {"role": "user", "content": text},
            ],
        )
    except Exception as exc:  # network / auth / rate-limit — degrade, don't crash
        return {
            "score": 0.5,
            "rationale": "Predictability signal unavailable; defaulted to uncertain.",
            "error": f"api_error: {exc}",
        }

    raw = response.choices[0].message.content
    parsed = _extract_json(raw)
    if parsed is None:
        return {
            "score": 0.5,
            "rationale": "Could not parse predictability response; defaulted to uncertain.",
            "error": "parse_error",
        }

    score = _clamp01(parsed.get("score"))
    if score is None:
        return {
            "score": 0.5,
            "rationale": "Predictability response had no numeric score; defaulted to uncertain.",
            "error": "missing_score",
        }

    rationale = parsed.get("rationale") or "No rationale provided."
    return {"score": score, "rationale": str(rationale)}


if __name__ == "__main__":
    # Independent test harness (planning.md §10, M3 verification): call the
    # signal directly on a few inputs and inspect the output before wiring it
    # into the endpoint. Run: .venv/bin/python signals.py
    samples = {
        "clearly-AI": (
            "In today's fast-paced digital landscape, leveraging synergistic "
            "solutions is paramount. By harnessing the power of innovation, "
            "organizations can unlock unprecedented value and drive sustainable "
            "growth across all facets of their operations."
        ),
        "clearly-human": (
            "ok so i tried the new ramen place by my apartment last night and "
            "honestly? mid. the broth was way too salty and i waited like 40 min. "
            "my roommate loved it though so idk maybe it's just me lol"
        ),
        "short": "Nice work!",
        "empty": "",
    }
    for name, sample in samples.items():
        result = predictability_score(sample)
        print(f"[{name}] -> {json.dumps(result, ensure_ascii=False)}")
