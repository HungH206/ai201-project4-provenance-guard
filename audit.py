"""Append-only audit log for Provenance Guard (planning.md §6, feature 6).

Storage decision: newline-delimited JSON (JSONL) on disk. Chosen over in-memory
(survives restarts, inspectable during grading) and over SQLite (no query needs
yet). Every event — submissions, appeals, review outcomes — is appended as one
line and NEVER mutated in place; status changes are recorded as new entries, so
the full history of any content_id is always reconstructable.
"""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone

_AUDIT_PATH = os.getenv("AUDIT_LOG_PATH", "audit_log.jsonl")
_lock = threading.Lock()  # serialize concurrent appends under Flask's dev server


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def text_hash(text):
    """SHA-256 of the raw text — we log a hash, not the content itself."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def append(entry):
    """Append one event to the audit log. Stamps `timestamp` if absent and
    returns the stored entry. Never overwrites existing entries."""
    record = dict(entry)
    record.setdefault("timestamp", _now_iso())
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with open(_AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return record


def read_all():
    """Return every audit entry in write order (empty list if none yet)."""
    if not os.path.exists(_AUDIT_PATH):
        return []
    entries = []
    with _lock:
        with open(_AUDIT_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries


def get_recent(limit=50):
    """Return up to `limit` most-recent entries, newest first (for GET /log)."""
    entries = read_all()
    entries.reverse()
    if limit is not None:
        entries = entries[:limit]
    return entries


def find_by_content_id(content_id):
    """All entries (submission + any appeals/outcomes) for one content_id."""
    return [e for e in read_all() if e.get("content_id") == content_id]
