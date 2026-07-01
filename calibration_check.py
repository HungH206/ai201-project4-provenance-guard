"""Calibration harness (planning.md §10 M4 verification).

Runs the 4 deliberately-chosen inputs through BOTH signals + the scoring logic
and prints each signal score separately, so a miscalibrated signal is visible.
Requires a valid GROQ_API_KEY in .env — without it both LLM signals return the
0.5 fallback and every row collapses to `uncertain` (that's expected, not a bug).

Run: .venv/bin/python calibration_check.py
"""

import scoring
from signals import predictability_score, stylistic_score

CASES = [
    (
        "clearly AI (expect HIGH / likely_ai)",
        "Artificial intelligence represents a transformative paradigm shift in "
        "modern society. It is important to note that while the benefits of AI are "
        "numerous, it is equally essential to consider the ethical implications. "
        "Furthermore, stakeholders across various sectors must collaborate to "
        "ensure responsible deployment.",
    ),
    (
        "clearly human (expect LOW / likely_human)",
        "ok so i finally tried that new ramen place downtown and honestly? "
        "underwhelming. the broth was fine but they put WAY too much sodium in it "
        "and i was thirsty for like three hours after. my friend got the spicy "
        "version and said it was better. probably won't go back unless someone "
        "drags me there",
    ),
    (
        "borderline: formal human (may score mid-high)",
        "The relationship between monetary policy and asset price inflation has "
        "been extensively studied in the literature. Central banks face a "
        "fundamental tension between their mandate for price stability and the "
        "unintended consequences of prolonged low interest rates on equity and "
        "real estate valuations.",
    ),
    (
        "borderline: lightly edited AI (expect mid-range)",
        "I've been thinking a lot about remote work lately. There are genuine "
        "tradeoffs — flexibility and no commute on one side, isolation and blurred "
        "work-life boundaries on the other. Studies show productivity varies "
        "widely by individual and role type.",
    ),
]


def main():
    degraded = False
    print(f"{'case':<44} {'s1':>5} {'s2':>5} {'ai':>5} {'agr':>5}  attribution / conf")
    print("-" * 96)
    for label, text in CASES:
        s1 = predictability_score(text)
        s2 = stylistic_score(text)
        r = scoring.classify(s1["score"], s2["score"])
        if s1.get("error") or s2.get("error"):
            degraded = True
        print(
            f"{label:<44} {s1['score']:>5.2f} {s2['score']:>5.2f} "
            f"{r['ai_score']:>5.2f} {r['agreement']:>5.2f}  "
            f"{r['attribution']} (conf {r['confidence']}, {r['confidence_qualifier']})"
        )
        # Print rationales so a misbehaving signal is easy to spot.
        print(f"      s1: {s1.get('rationale')}")
        print(f"      s2: {s2.get('rationale')}")
        if s1.get("error"):
            print(f"      s1 ERROR: {s1['error']}")
        if s2.get("error"):
            print(f"      s2 ERROR: {s2['error']}")
    if degraded:
        print(
            "\n⚠️  One or more signals degraded to 0.5 (see errors above). This is "
            "almost certainly a missing/invalid GROQ_API_KEY — set a real key in "
            ".env and re-run to get meaningful calibration."
        )


if __name__ == "__main__":
    main()
