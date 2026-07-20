"""
compare_human_vs_ai_grades.py - Score-only comparison of original human
grades vs AI-generated grades for Quiz 7 (Business Analysis), joined
on questionattemptid.

NOTE: this is score-only. The original human feedback comments were not
captured in the human-grades backup (only fraction/mark was saved before
the strip+regrade), so no feedback-quality comparison is possible for
these 4 attempts. Only the AI side has feedback text available.

Inputs:
    essay_grades_backup_quiz7_4learners.csv  - original human grades
                                                (fraction, quiz_attempt_id,
                                                userid, questionattemptid;
                                                no slot, no feedback)
    ai_grades_backup_quiz7_4learners.csv     - AI grades (adds slot and
                                                feedback_comment)

Output:
    human_vs_ai_comparison_quiz7.csv - one row per question, with both
    marks, the difference, and summary stats printed to the console.

Usage:
    python compare_human_vs_ai_grades.py \\
        --human essay_grades_backup_quiz7_4learners.csv \\
        --ai ai_grades_backup_quiz7_4learners.csv \\
        --output human_vs_ai_comparison_quiz7.csv
"""

import argparse
import csv
import logging
import statistics
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Slot -> max marks, Quiz 7 (Business Analysis)
SLOT_MAX_MARKS = {1: 5, 2: 5, 3: 4, 4: 4, 5: 5, 6: 4, 7: 4, 8: 3}

# userid -> learner_id, resolved earlier via Moodle username lookups
# (none of these 4 test accounts had idnumber set, so username was used
# as learner_id throughout the pipeline).
USERID_TO_LEARNER = {
    21: "anelisa-mjoni",
    22: "annah-masunga",
    23: "antonio-banze",
    25: "christopher-mothuli",
}

# questionattemptid -> slot, resolved via:
#   SELECT id AS questionattemptid, slot FROM mdl_question_attempts
#   WHERE id IN (...) ORDER BY id;
# (only needed as a fallback if a given human-grade row's questionattemptid
# isn't found directly in the AI CSV, since the AI CSV already carries slot.)
QUESTIONATTEMPTID_TO_SLOT = {
    4166: 1, 4167: 2, 4168: 3, 4169: 4, 4170: 5, 4171: 6, 4172: 7, 4173: 8,
    4278: 1, 4279: 2, 4280: 3, 4281: 4, 4282: 5, 4283: 6, 4284: 7, 4285: 8,
    4366: 1, 4367: 2, 4368: 3, 4369: 4, 4370: 5, 4371: 6, 4372: 7, 4373: 8,
    4374: 1, 4375: 2, 4376: 3, 4377: 4, 4378: 5, 4379: 6, 4380: 7, 4381: 8,
}


def load_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with p.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log.info("Loaded %d row(s) from %s", len(rows), path)
    return rows


def build_comparison(human_rows: list[dict], ai_rows: list[dict]) -> list[dict]:
    """Join human and AI rows on questionattemptid; compute marks and deltas."""
    ai_by_qaid = {int(r["questionattemptid"]): r for r in ai_rows}

    comparison = []
    unmatched = []

    for hrow in human_rows:
        qaid = int(hrow["questionattemptid"])
        ai_row = ai_by_qaid.get(qaid)

        if ai_row is None:
            unmatched.append(qaid)
            continue

        slot = int(ai_row["slot"]) if ai_row.get("slot") else QUESTIONATTEMPTID_TO_SLOT.get(qaid)
        max_mark = SLOT_MAX_MARKS.get(slot)
        if max_mark is None:
            log.warning("No max_mark known for slot=%s (questionattemptid=%s) — skipping", slot, qaid)
            continue

        human_fraction = float(hrow["fraction"])
        ai_fraction = float(ai_row["fraction"])

        human_mark = round(human_fraction * max_mark, 2)
        ai_mark = round(ai_fraction * max_mark, 2)
        delta = round(ai_mark - human_mark, 2)

        userid = int(hrow["userid"])
        learner_id = USERID_TO_LEARNER.get(userid, f"userid_{userid}")

        comparison.append({
            "learner_id": learner_id,
            "attempt_id": hrow["quiz_attempt_id"],
            "slot": slot,
            "max_mark": max_mark,
            "human_mark": human_mark,
            "ai_mark": ai_mark,
            "delta_ai_minus_human": delta,
            "human_fraction": round(human_fraction, 4),
            "ai_fraction": round(ai_fraction, 4),
        })

    if unmatched:
        log.warning(
            "%d human-grade row(s) had no matching AI row (questionattemptid not found in AI CSV): %s",
            len(unmatched), unmatched,
        )

    comparison.sort(key=lambda r: (r["attempt_id"], r["slot"]))
    return comparison


def write_comparison(rows: list[dict], output_path: str) -> None:
    if not rows:
        log.warning("No comparison rows to write.")
        return

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %d comparison row(s) to %s", len(rows), output_path)


def print_summary(rows: list[dict]) -> None:
    if not rows:
        return

    deltas = [r["delta_ai_minus_human"] for r in rows]
    abs_deltas = [abs(d) for d in deltas]

    exact_matches = sum(1 for d in deltas if d == 0)
    ai_higher = sum(1 for d in deltas if d > 0)
    ai_lower = sum(1 for d in deltas if d < 0)

    print("\n=== Human vs AI Grade Comparison Summary (Quiz 7) ===")
    print(f"Total questions compared : {len(rows)}")
    print(f"Exact matches (delta=0)  : {exact_matches} ({exact_matches / len(rows) * 100:.1f}%)")
    print(f"AI graded higher         : {ai_higher} ({ai_higher / len(rows) * 100:.1f}%)")
    print(f"AI graded lower          : {ai_lower} ({ai_lower / len(rows) * 100:.1f}%)")
    print(f"Mean delta (AI - human)  : {statistics.mean(deltas):+.2f}")
    print(f"Mean absolute delta      : {statistics.mean(abs_deltas):.2f}")
    print(f"Max absolute delta       : {max(abs_deltas):.2f}")

    # Per-learner breakdown
    by_learner: dict = {}
    for r in rows:
        by_learner.setdefault(r["learner_id"], []).append(r["delta_ai_minus_human"])

    print("\nPer-learner mean delta (AI - human):")
    for learner, ds in sorted(by_learner.items()):
        print(f"  {learner:22s}: {statistics.mean(ds):+.2f}  (n={len(ds)})")

    # Biggest disagreements
    biggest = sorted(rows, key=lambda r: abs(r["delta_ai_minus_human"]), reverse=True)[:5]
    print("\nLargest disagreements (top 5):")
    for r in biggest:
        print(
            f"  attempt={r['attempt_id']} slot={r['slot']} learner={r['learner_id']:22s} "
            f"human={r['human_mark']}/{r['max_mark']} ai={r['ai_mark']}/{r['max_mark']} "
            f"delta={r['delta_ai_minus_human']:+.2f}"
        )
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare human vs AI grades (score-only) for Quiz 7")
    parser.add_argument("--human", required=True, help="Path to the human-grades backup CSV")
    parser.add_argument("--ai", required=True, help="Path to the AI-grades backup CSV")
    parser.add_argument("--output", default="human_vs_ai_comparison_quiz7.csv", help="Output comparison CSV path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        human_rows = load_csv(args.human)
        ai_rows = load_csv(args.ai)
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    comparison = build_comparison(human_rows, ai_rows)
    write_comparison(comparison, args.output)
    print_summary(comparison)


if __name__ == "__main__":
    main()