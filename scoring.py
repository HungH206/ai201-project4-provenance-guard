"""Confidence scoring for Provenance Guard (planning.md §3 combiner + §4 bands).

Combines the two signal scores into a single calibrated result. All thresholds
here are the CONTRACT from planning.md — if you change one, change the doc too.

  ai_score  = 0.5*s1 + 0.5*s2         # primary axis: P(AI-generated), in [0,1]
  agreement = 1 - |s1 - s2|           # reliability: 1 = identical, 0 = opposite

Bands on ai_score:   >=0.65 likely_ai | 0.35-0.65 uncertain | <0.35 likely_human
Override:            agreement < 0.50 forces `uncertain` (signals contradict).
Confidence (numeric): |ai_score-0.5|*2 * agreement — strength of the call,
                      discounted by how much the two signals agree. High only when
                      the score is decisive AND the signals concur.
"""

# --- Thresholds (contract with planning.md §4) -----------------------------
BAND_AI = 0.65          # ai_score >= this  -> likely_ai
BAND_HUMAN = 0.35       # ai_score <  this  -> likely_human
AGREEMENT_OVERRIDE = 0.50   # agreement <  this -> force uncertain
AGREEMENT_HIGH = 0.75       # agreement >= this -> "high confidence"
AGREEMENT_MODERATE = 0.50   # agreement >= this -> "moderate confidence"


def combine_signals(s1, s2):
    """Return (ai_score, agreement), each rounded to 2 decimals (planning.md §4:
    never publish more precision than the signals justify)."""
    ai_score = round(0.5 * s1 + 0.5 * s2, 2)
    agreement = round(1.0 - abs(s1 - s2), 2)
    return ai_score, agreement


def _band(ai_score):
    if ai_score >= BAND_AI:
        return "likely_ai"
    if ai_score < BAND_HUMAN:
        return "likely_human"
    return "uncertain"


def confidence_qualifier(agreement):
    """Categorical qualifier used in the M5 label wording (planning.md §4)."""
    if agreement >= AGREEMENT_HIGH:
        return "high confidence"
    if agreement >= AGREEMENT_MODERATE:
        return "moderate confidence"
    return "low confidence (signals disagreed)"


def classify(s1, s2):
    """Combine two signal scores into the full scoring result.

    Returns: {ai_score, agreement, attribution, confidence, confidence_qualifier}
    attribution ∈ {likely_ai, uncertain, likely_human}.
    """
    ai_score, agreement = combine_signals(s1, s2)
    band = _band(ai_score)

    # Override: if the two detectors contradict each other, we refuse to assert
    # a confident direction (planning.md §4).
    attribution = "uncertain" if agreement < AGREEMENT_OVERRIDE else band

    # Numeric confidence in the stated attribution: decisive score * agreement.
    confidence = round(abs(ai_score - 0.5) * 2 * agreement, 2)

    return {
        "ai_score": ai_score,
        "agreement": agreement,
        "attribution": attribution,
        "confidence": confidence,
        "confidence_qualifier": confidence_qualifier(agreement),
    }
