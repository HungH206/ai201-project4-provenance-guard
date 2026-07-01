"""Threshold verification for scoring.py against planning.md §4.

Pure, deterministic, no API key needed. This is the check the milestone stresses:
does the scoring function actually match the specified ranges? Run:
    .venv/bin/python test_scoring.py
"""

from scoring import classify, combine_signals


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_combine_math():
    ai, agr = combine_signals(0.8, 0.6)
    assert approx(ai, 0.70), ai
    assert approx(agr, 0.80), agr
    ai, agr = combine_signals(0.9, 0.1)
    assert approx(ai, 0.50), ai
    assert approx(agr, 0.20), agr


def test_bands():
    # likely_ai: both high, agree
    assert classify(0.9, 0.9)["attribution"] == "likely_ai"
    # likely_human: both low, agree
    assert classify(0.1, 0.1)["attribution"] == "likely_human"
    # uncertain: mid, agree
    assert classify(0.5, 0.5)["attribution"] == "uncertain"


def test_band_boundaries():
    # exactly 0.65 -> likely_ai (>=)
    assert classify(0.65, 0.65)["attribution"] == "likely_ai"
    # 0.64 -> uncertain (just below AI band)
    assert classify(0.64, 0.64)["attribution"] == "uncertain"
    # exactly 0.35 -> uncertain (human band is strictly < 0.35)
    assert classify(0.35, 0.35)["attribution"] == "uncertain"
    # 0.34 -> likely_human
    assert classify(0.34, 0.34)["attribution"] == "likely_human"


def test_agreement_override():
    # High mean but signals contradict (0.9 vs 0.1): ai=0.5 anyway -> uncertain.
    r = classify(0.9, 0.1)
    assert r["agreement"] == 0.20
    assert r["attribution"] == "uncertain"
    # Would-be likely_ai by score, but agreement below 0.50 forces uncertain.
    # 1.0 & 0.3 -> ai=0.65 (AI band) but agreement=0.30 -> override to uncertain.
    r = classify(1.0, 0.3)
    assert r["ai_score"] == 0.65
    assert r["agreement"] == 0.30
    assert r["attribution"] == "uncertain", r


def test_agreement_just_above_override_keeps_band():
    # agreement exactly 0.50 is NOT below the 0.50 override -> band stands.
    # 0.9 & 0.4 -> ai=0.65, agreement=0.50 -> likely_ai.
    r = classify(0.9, 0.4)
    assert r["agreement"] == 0.50
    assert r["attribution"] == "likely_ai", r


def test_confidence_number():
    # Decisive AND agreeing -> high confidence number.
    r = classify(0.95, 0.95)          # ai=0.95, agr=1.0
    assert r["confidence"] == round(abs(0.95 - 0.5) * 2 * 1.0, 2)  # 0.9
    # Neutral score -> zero confidence regardless of agreement.
    r = classify(0.5, 0.5)
    assert r["confidence"] == 0.0
    # Disagreement drags confidence down.
    r = classify(0.9, 0.1)            # ai=0.5 -> confidence 0 anyway
    assert r["confidence"] == 0.0


def test_confidence_qualifier():
    assert classify(0.9, 0.9)["confidence_qualifier"] == "high confidence"      # agr 1.0
    assert classify(0.9, 0.4)["confidence_qualifier"] == "moderate confidence"  # agr 0.50
    assert "low confidence" in classify(0.9, 0.1)["confidence_qualifier"]       # agr 0.20


def test_degraded_signals_land_uncertain():
    # Both signals defaulted to 0.5 (e.g. missing API key) -> uncertain, conf 0.
    r = classify(0.5, 0.5)
    assert r["attribution"] == "uncertain"
    assert r["confidence"] == 0.0


def test_three_labels_reachable():
    labels = {
        classify(0.9, 0.85)["attribution"],   # likely_ai
        classify(0.1, 0.15)["attribution"],   # likely_human
        classify(0.5, 0.55)["attribution"],   # uncertain
    }
    assert labels == {"likely_ai", "likely_human", "uncertain"}, labels


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
    print(f"\nAll {len(tests)} scoring threshold tests passed.")
