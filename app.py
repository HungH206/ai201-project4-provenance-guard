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
import scoring
from labels import make_label
from signals import predictability_score, stylistic_score

app = Flask(__name__)

# Rate limiting on the LLM-backed endpoints (planning.md §1; limits justified in
# README). Explicit in-memory storage (Flask-Limiter ≥3 requires storage_uri).
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# /submit limits: a real writer submits their own work a handful of times a
# minute at most; 10/min absorbs edit-resubmits while blocking a flood, and
# 100/day caps sustained abuse (each submit costs two Groq calls).
SUBMIT_LIMITS = "10 per minute;100 per day"
# Appeals are rare human actions — a generous ceiling that still stops scripting.
APPEAL_LIMITS = "20 per hour"

# Guard rails on ingestion (planning.md §2 step 1, §7 edge case 3).
MAX_TEXT_CHARS = 20_000
MIN_TEXT_CHARS = 1


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
@limiter.limit(SUBMIT_LIMITS)
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

    # Two independent signals (planning.md §3), then combined scoring (§4).
    signal1 = predictability_score(text)   # Signal 1: predictability
    signal2 = stylistic_score(text)        # Signal 2: stylistic fingerprint
    result = scoring.classify(signal1["score"], signal2["score"])
    label = make_label(result)             # transparency label text (planning.md §5)

    # Structured audit entry (planning.md §6): both signals' individual scores
    # alongside the combined ai_score/agreement/attribution/confidence + label.
    audit.append(
        {
            "type": "submission",
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": result["attribution"],
            "confidence": result["confidence"],
            "ai_score": result["ai_score"],
            "agreement": result["agreement"],
            "signal_1_score": signal1["score"],
            "signal_2_score": signal2["score"],
            "label": label,
            "status": "classified",
            "appealed": False,
            "text_hash": audit.text_hash(text),
            "signal_1_rationale": signal1.get("rationale"),
            "signal_1_error": signal1.get("error"),
            "signal_2_rationale": signal2.get("rationale"),
            "signal_2_error": signal2.get("error"),
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": result["attribution"],
            "confidence": result["confidence"],
            "ai_score": result["ai_score"],
            "agreement": result["agreement"],
            "label": label,
            "signals": {"predictability": signal1, "stylistic": signal2},
        }
    )


@app.post("/appeal")
@limiter.limit(APPEAL_LIMITS)
def appeal():
    """Let a creator contest a classification (planning.md §6).

    Body: {"content_id": <str>, "creator_reasoning": <str>}
    Logs the appeal alongside the original decision, sets status "under_review",
    and returns a confirmation. No automated re-classification.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    content_id = data.get("content_id")
    reasoning = data.get("creator_reasoning")
    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "Field 'content_id' is required and must be a non-empty string."}), 400
    if not isinstance(reasoning, str) or not reasoning.strip():
        return jsonify({"error": "Field 'creator_reasoning' is required and must be a non-empty string."}), 400

    history = audit.find_by_content_id(content_id)
    submission = next((e for e in history if e.get("type") == "submission"), None)
    if submission is None:
        return jsonify({"error": f"Unknown content_id: {content_id}"}), 404

    # One open appeal at a time (planning.md §6).
    if any(e.get("type") == "appeal" for e in history):
        return jsonify({"error": "An appeal for this content_id is already on record."}), 409

    # Append the appeal alongside the original decision. Log is append-only:
    # the original submission entry is preserved; this new entry carries the
    # under_review status and the creator's reasoning.
    entry = audit.append(
        {
            "type": "appeal",
            "content_id": content_id,
            "creator_id": submission.get("creator_id"),
            "status": "under_review",
            "appeal_reasoning": reasoning.strip(),
            "original_attribution": submission.get("attribution"),
            "original_confidence": submission.get("confidence"),
            "original_timestamp": submission.get("timestamp"),
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "status": "under_review",
            "message": "Appeal received and logged for human review.",
            "appeal_logged_at": entry["timestamp"],
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
