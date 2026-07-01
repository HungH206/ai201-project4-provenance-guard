"""Label variant verification (planning.md §5). No API key needed.

Confirms all three variants are reachable and that label text tracks the numeric
confidence score. Run: .venv/bin/python test_labels.py
"""

from labels import confidence_word, make_label
from scoring import classify


def test_confidence_wording_thresholds():
    assert confidence_word(0.80) == "high confidence"
    assert confidence_word(0.60) == "high confidence"      # boundary
    assert confidence_word(0.45) == "moderate confidence"
    assert confidence_word(0.30) == "moderate confidence"  # boundary
    assert confidence_word(0.10) == "low confidence"


def test_variant_ai():
    label = make_label(classify(0.9, 0.9))  # likely_ai, conf 0.8
    assert label.startswith("🤖 Likely AI generated (high confidence)")
    assert "AI likelihood 0.90" in label
    assert "appeal" in label


def test_variant_human():
    label = make_label(classify(0.1, 0.15))  # likely_human
    assert label.startswith("✍️ Likely written by a person")
    assert "appeal" in label


def test_variant_uncertain():
    label = make_label(classify(0.5, 0.55))  # uncertain
    assert label.startswith("❓ Uncertain")
    assert "could not reach a confident conclusion" in label


def test_label_changes_with_score():
    # Same attribution direction, different decisiveness -> different wording.
    near_boundary = make_label(classify(0.60, 0.80))   # ai 0.70, conf 0.32 -> moderate
    decisive = make_label(classify(0.95, 0.95))        # ai 0.95, conf 0.90 -> high
    assert "moderate confidence" in near_boundary, near_boundary
    assert "high confidence" in decisive, decisive
    assert near_boundary != decisive


def test_all_three_variants_distinct():
    labels = {
        make_label(classify(0.9, 0.85)),
        make_label(classify(0.1, 0.15)),
        make_label(classify(0.5, 0.55)),
    }
    assert len(labels) == 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
    print(f"\nAll {len(tests)} label tests passed.\n")
    # Show the three variants verbatim for the README.
    print("--- Variant A (high-confidence AI) ---")
    print(make_label(classify(0.9, 0.9)))
    print("\n--- Variant B (high-confidence human) ---")
    print(make_label(classify(0.1, 0.1)))
    print("\n--- Variant C (uncertain) ---")
    print(make_label(classify(0.5, 0.55)))
