"""Provenance Guard — Flask application.

Milestone 3 scope: the POST /submit ingestion endpoint wired to the first
detection signal (predictability). Confidence scoring, the second signal,
transparency labels, and appeals arrive in later milestones. See planning.md
§8 (API contract) and §9 (architecture).
"""

import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit
from signals import predictability_score

app = Flask(__name__)

# Rate limiting on the LLM-backed endpoints (planning.md §1 cross-cutting).
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["100 per hour"],
)

# Guard rails on ingestion (planning.md §2 step 1, §7 edge case 3).
MAX_TEXT_CHARS = 20_000
MIN_TEXT_CHARS = 1

# Placeholders until confidence scoring (M4) and transparency labels (M5) land.
# Kept explicitly null/marked so they can't be mistaken for a real result.
PLACEHOLDER_ATTRIBUTION = None
PLACEHOLDER_CONFIDENCE = None
PLACEHOLDER_LABEL = "PENDING — confidence scoring (M4) and transparency label (M5) not yet implemented"


@app.get("/health")
def health():
    """Liveness probe (planning.md §8)."""
    return jsonify({"status": "ok"})


@app.get("/log")
def log():
    """Return the most recent audit log entries (planning.md §6, §8).

    No auth by design — this exists for documentation and grading visibility.
    Optional ?limit=N (default 50).
    """
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        return jsonify({"error": "'limit' must be an integer."}), 400
    return jsonify({"entries": audit.get_recent(limit)})


@app.post("/submit")
@limiter.limit("20 per minute")
def submit():
    """Accept a submission and run the first detection signal.

    Body: {"text": <str>, "creator_id": <str>}
    Returns: {submission_id, creator_id, signals: {predictability}}
    (ai_score / label are added once scoring + labeling land in M4–M5.)
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    text = data.get("text")
    creator_id = data.get("creator_id")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' is required and must be a non-empty string."}), 400
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "Field 'creator_id' is required and must be a non-empty string."}), 400
    if len(text) > MAX_TEXT_CHARS:
        return jsonify({"error": f"Field 'text' exceeds {MAX_TEXT_CHARS} characters."}), 400

    content_id = str(uuid.uuid4())
    text = text.strip()

    # Signal 1 (planning.md §3). Signal 2 + scoring + label follow in M4/M5.
    signal1 = predictability_score(text)

    # Structured audit entry (planning.md §6). `attribution` and `confidence`
    # are null until M4 adds the second signal + combined scoring; `llm_score`
    # is signal 1's output, available now. status="pending_scoring" -> "classified"
    # once M4 lands. The content_id lives here so /appeal can look it up.
    audit.append(
        {
            "type": "submission",
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": PLACEHOLDER_ATTRIBUTION,
            "confidence": PLACEHOLDER_CONFIDENCE,
            "llm_score": signal1["score"],
            "status": "pending_scoring",
            "text_hash": audit.text_hash(text),
            "signal_1_rationale": signal1.get("rationale"),
            "signal_1_error": signal1.get("error"),
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": PLACEHOLDER_ATTRIBUTION,
            "confidence": PLACEHOLDER_CONFIDENCE,
            "llm_score": signal1["score"],
            "label": PLACEHOLDER_LABEL,
            "signals": {"predictability": signal1},
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
