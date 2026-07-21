"""
compare_human_vs_ai_grades.py - Full comparison (marks AND feedback) of
original human grading vs AI-generated grading for Quiz 7 (Business
Analysis).

History: the original human feedback comments were not captured in the
first backup taken during testing — only the numeric mark was saved
before a strip+regrade wiped the grading step history. The original
feedback text was later recovered by restoring a pre-existing Moodle
course backup (.mbz, taken 2026-07-14, before any testing) as a
brand-new course and re-querying the quiz attempts there
(original_human_feedback_quiz7_4learners.csv). That recovery CSV
already contains fraction, slot, and userid directly, so it's the only
human-side input needed now — the earlier grades-only backup
(essay_grades_backup_quiz7_4learners.csv) is no longer used.

Because the restored course has entirely different internal Moodle IDs
than the live course, this script joins the human-feedback and AI CSVs
on (userid, slot) — the one pair of values that's stable across both.

Inputs:
    ai_grades_backup_quiz7_4learners.csv          - AI grades + feedback
                                                     (has slot, userid)
    original_human_feedback_quiz7_4learners.csv   - original human grades
                                                     + feedback, recovered
                                                     from the restored
                                                     course backup (has
                                                     slot, userid)

Output:
    human_vs_ai_comparison_quiz7.csv - one row per question: both marks,
    the delta, and both feedback texts side by side. Summary stats
    printed to the console.

Usage:
    python compare_human_vs_ai_grades.py \\
        --ai ai_grades_backup_quiz7_4learners.csv \\
        --human-feedback original_human_feedback_quiz7_4learners.csv \\
        --output human_vs_ai_comparison_quiz7.csv
"""

import argparse
import csv
import logging
import re
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

# userid -> learner_id (none of these 4 test accounts had idnumber set,
# so username was used as learner_id throughout the pipeline).
USERID_TO_LEARNER = {
    21: "anelisa-mjoni",
    22: "annah-masunga",
    23: "antonio-banze",
    25: "christopher-mothuli",
}


def load_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with p.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log.info("Loaded %d row(s) from %s", len(rows), path)
    return rows


def strip_html(text: str) -> str:
    """Light HTML stripping for readable console/CSV output."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def build_comparison(ai_rows: list[dict], feedback_rows: list[dict]) -> list[dict]:
    """Join AI and human-feedback rows on (userid, slot); compute marks and deltas."""
    ai_by_key = {(int(r["userid"]), int(r["slot"])): r for r in ai_rows}

    comparison = []
    unmatched_ai = []

    for fb_row in feedback_rows:
        userid = int(fb_row["userid"])
        slot = int(fb_row["slot"])
        key = (userid, slot)

        max_mark = SLOT_MAX_MARKS.get(slot)
        if max_mark is None:
            log.warning("No max_mark known for slot=%s — skipping", slot)
            continue

        ai_row = ai_by_key.get(key)
        if ai_row is None:
            unmatched_ai.append(key)
            continue

        human_fraction = float(fb_row["fraction"])
        ai_fraction = float(ai_row["fraction"])

        human_mark = round(human_fraction * max_mark, 2)
        ai_mark = round(ai_fraction * max_mark, 2)
        delta = round(ai_mark - human_mark, 2)

        learner_id = USERID_TO_LEARNER.get(userid, f"userid_{userid}")

        comparison.append({
            "learner_id": learner_id,
            "attempt_id": fb_row["quiz_attempt_id"],
            "slot": slot,
            "max_mark": max_mark,
            "human_mark": human_mark,
            "ai_mark": ai_mark,
            "delta_ai_minus_human": delta,
            "human_feedback": strip_html(fb_row["feedback_comment"]),
            "ai_feedback": strip_html(ai_row.get("feedback_comment", "")),
        })

    if unmatched_ai:
        log.warning("%d (userid, slot) pair(s) had no matching AI row: %s", len(unmatched_ai), unmatched_ai)

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

    by_learner: dict = {}
    for r in rows:
        by_learner.setdefault(r["learner_id"], []).append(r["delta_ai_minus_human"])

    print("\nPer-learner mean delta (AI - human):")
    for learner, ds in sorted(by_learner.items()):
        print(f"  {learner:22s}: {statistics.mean(ds):+.2f}  (n={len(ds)})")

    biggest = sorted(rows, key=lambda r: abs(r["delta_ai_minus_human"]), reverse=True)[:5]
    print("\nLargest disagreements (top 5) — with both feedback texts:")
    for r in biggest:
        print(
            f"\n  attempt={r['attempt_id']} slot={r['slot']} learner={r['learner_id']} "
            f"human={r['human_mark']}/{r['max_mark']} ai={r['ai_mark']}/{r['max_mark']} "
            f"delta={r['delta_ai_minus_human']:+.2f}"
        )
        print(f"    Human feedback: {r['human_feedback'][:200]}")
        print(f"    AI feedback:    {r['ai_feedback'][:200]}")
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare human vs AI grades AND feedback for Quiz 7")
    parser.add_argument("--ai", required=True, help="Path to the AI-grades backup CSV (marks + feedback)")
    parser.add_argument("--human-feedback", required=True, help="Path to the recovered human feedback CSV (from restored course)")
    parser.add_argument("--output", default="human_vs_ai_comparison_quiz7.csv", help="Output comparison CSV path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        ai_rows = load_csv(args.ai)
        feedback_rows = load_csv(args.human_feedback)
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    comparison = build_comparison(ai_rows, feedback_rows)
    write_comparison(comparison, args.output)
    print_summary(comparison)


if __name__ == "__main__":
    main()