"""
restore_human_grades.py - Restore the original human-graded marks for
Quiz 7 (Business Analysis) essay questions, overwriting the AI grades
that are currently in Moodle from testing.

Context: the original human grades were stripped from Moodle and a
"Regrade attempts" action was run, which wiped the grading step history
in mdl_question_attempt_steps. The only surviving record of the
original human marks is a CSV backup taken before the strip
(essay_grades_backup_quiz7_4learners.csv), which was pulled from
mdl_question_attempt_steps and does NOT include slot numbers or
feedback text directly — both are reconstructed here.

This script pushes each original mark back through the SAME tested,
working pathway used by worker.py (moodle_client.save_quiz_essay_grade),
which internally calls Moodle's own recompute_final_grade() and
correctly updates both the per-question mark and the gradebook total.
No raw database writes are performed.

Usage:
    python restore_human_grades.py --backup essay_grades_backup_quiz7_4learners.csv [--dry-run]

Requires moodle_client.py's MOODLE_BASE_URL / MOODLE_TOKEN to be set
via .env, same as the rest of the pipeline.
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

import moodle_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# questionattemptid -> slot, resolved via:
#   SELECT id AS questionattemptid, slot FROM mdl_question_attempts
#   WHERE id IN (...) ORDER BY id;
# against the live Moodle DB. This mapping is specific to the 32 question
# attempts backed up for attempts 270, 284, 295, 296 (Quiz 7, 4 learners).
QUESTIONATTEMPTID_TO_SLOT = {
    4166: 1, 4167: 2, 4168: 3, 4169: 4, 4170: 5, 4171: 6, 4172: 7, 4173: 8,
    4278: 1, 4279: 2, 4280: 3, 4281: 4, 4282: 5, 4283: 6, 4284: 7, 4285: 8,
    4366: 1, 4367: 2, 4368: 3, 4369: 4, 4370: 5, 4371: 6, 4372: 7, 4373: 8,
    4374: 1, 4375: 2, 4376: 3, 4377: 4, 4378: 5, 4379: 6, 4380: 7, 4381: 8,
}

# max_marks per slot for Quiz 7, from the original slot->question mapping
# (Stakeholder Analysis=5, High-Power Low-Interest=5, Unidentified
# Stakeholder=4, Stakeholder Engagement=4, Requirements Alignment=5,
# Functional Requirements Challenges=4, Distinguish Between Requirements=4,
# Missing Key Requirement=3).
SLOT_MAX_MARKS = {1: 5, 2: 5, 3: 4, 4: 4, 5: 5, 6: 4, 7: 4, 8: 3}

RESTORE_FEEDBACK_NOTE = (
    "<p><em>Original human-assigned grade, restored from backup after "
    "AI grading test run. Original written feedback comment was not "
    "captured in the backup — only the numeric mark was recoverable.</em></p>"
)


def load_backup_rows(path: str) -> list[dict]:
    """Load and validate the human-grades backup CSV."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Backup CSV not found: {path}")

    with p.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    required_cols = {"questionattemptid", "fraction", "quiz_attempt_id"}
    if rows and not required_cols.issubset(rows[0].keys()):
        missing = required_cols - rows[0].keys()
        raise ValueError(f"Backup CSV missing required columns: {missing}")

    log.info("Loaded %d row(s) from %s", len(rows), path)
    return rows


def build_restore_plan(rows: list[dict]) -> list[dict]:
    """
    Turn raw backup rows into a list of restore actions:
        {attempt_id, slot, mark, max_mark}
    Skips (and logs) any row whose questionattemptid isn't in our known
    mapping, rather than guessing or silently dropping it.
    """
    plan = []
    skipped = []

    for row in rows:
        qaid = int(row["questionattemptid"])
        slot = QUESTIONATTEMPTID_TO_SLOT.get(qaid)

        if slot is None:
            skipped.append(row)
            continue

        max_mark = SLOT_MAX_MARKS.get(slot)
        if max_mark is None:
            skipped.append(row)
            continue

        fraction = float(row["fraction"])
        mark = round(fraction * max_mark, 4)

        plan.append({
            "attempt_id": int(row["quiz_attempt_id"]),
            "slot": slot,
            "mark": mark,
            "max_mark": max_mark,
            "fraction": fraction,
            "questionattemptid": qaid,
        })

    if skipped:
        log.warning(
            "Skipped %d row(s) with unknown questionattemptid/slot mapping: %s",
            len(skipped), [r["questionattemptid"] for r in skipped],
        )

    return plan


def run_restore(plan: list[dict], dry_run: bool = True) -> None:
    """Push each restore action to Moodle via save_quiz_essay_grade."""
    log.info("=== %s: restoring %d essay grade(s) ===",
              "DRY RUN" if dry_run else "LIVE RUN", len(plan))

    succeeded = 0
    failed = 0

    for action in plan:
        log.info(
            "Attempt %s, slot %s: restoring mark %.2f/%s (fraction %.4f, questionattemptid=%s)",
            action["attempt_id"], action["slot"], action["mark"], action["max_mark"],
            action["fraction"], action["questionattemptid"],
        )

        if dry_run:
            continue

        try:
            result = moodle_client.save_quiz_essay_grade(
                attempt_id=action["attempt_id"],
                slot=action["slot"],
                grade=action["mark"],
                feedback_html=RESTORE_FEEDBACK_NOTE,
            )
            log.info("  [OK] %s", result)
            succeeded += 1
        except Exception as e:
            log.error("  [FAIL] attempt=%s slot=%s: %s", action["attempt_id"], action["slot"], e)
            failed += 1

    if dry_run:
        log.info("Dry run complete — no changes were made. Re-run with --live to apply.")
    else:
        log.info("=== Restore complete: %d succeeded, %d failed ===", succeeded, failed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore original human-graded marks for Quiz 7 essay questions"
    )
    parser.add_argument("--backup", required=True, help="Path to the human-grades backup CSV")
    parser.add_argument(
        "--live", action="store_true",
        help="Actually push changes to Moodle. Without this flag, runs as a dry run (prints the plan only)."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        rows = load_backup_rows(args.backup)
    except (FileNotFoundError, ValueError) as e:
        log.error("Failed to load backup: %s", e)
        sys.exit(1)

    plan = build_restore_plan(rows)

    if len(plan) != len(rows):
        log.warning(
            "Plan has %d action(s) but backup had %d row(s) — some rows were skipped, see warnings above.",
            len(plan), len(rows),
        )

    run_restore(plan, dry_run=not args.live)


if __name__ == "__main__":
    main()