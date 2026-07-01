"""Transparency label generation (planning.md §5).

Maps a scoring result (from scoring.classify) to the plain-language label a
non-technical reader sees. Three variants keyed on `attribution`; the confidence
WORDING is derived from the NUMERIC confidence — not raw agreement — so a call
that sits just over a threshold does not overstate certainty (this resolves the
coherence gap found in M4 calibration, planning.md §4).

  confidence >= 0.60  -> "high confidence"
  0.30 – 0.60         -> "moderate confidence"
  < 0.30              -> "low confidence"
"""

CONF_HIGH = 0.60
CONF_MODERATE = 0.30


def confidence_word(confidence):
    if confidence >= CONF_HIGH:
        return "high confidence"
    if confidence >= CONF_MODERATE:
        return "moderate confidence"
    return "low confidence"


def make_label(result):
    """Return the transparency label text for a scoring result.

    `result` is the dict from scoring.classify (ai_score, agreement,
    attribution, confidence, ...). The text changes with the confidence score.
    """
    attribution = result["attribution"]
    ai_score = result["ai_score"]
    word = confidence_word(result["confidence"])

    if attribution == "likely_ai":
        return (
            f"🤖 Likely AI-generated — {word}. "
            f"Our detectors estimate this text is likely AI-generated "
            f"(AI-likelihood {ai_score:.2f}). This is an automated estimate, "
            f"not proof. If you wrote this yourself, you can appeal this label."
        )

    if attribution == "likely_human":
        return (
            f"✍️ Likely human-written — {word}. "
            f"Our detectors estimate this text is likely human-authored "
            f"(AI-likelihood {ai_score:.2f}). This is an automated estimate, "
            f"not proof. You can appeal if you believe this label is wrong."
        )

    # uncertain
    return (
        f"❓ Uncertain — {word}. "
        f"Our detectors could not reach a confident conclusion "
        f"(AI-likelihood {ai_score:.2f}). This text may be AI-assisted, or "
        f"simply written in a style our tools find hard to judge. We are not "
        f"making a determination. You can appeal to flag this for review."
    )
