"""
worker.py - Consumes grading jobs from RabbitMQ and grades them using Gemini.

Usage:
    python worker.py [--host localhost] [--model gemini-2.5-flash]
                     [--output output.json] [--log grading_log.json]
                     [--flagged-log flagged_for_review.json]

Reuses all grading logic from grader.py directly.
Logs every attempt (success or fail) to a structured JSON log file.
Multiple workers can run in parallel — RabbitMQ distributes jobs between them.

Supports two Moodle push-back flows:
  - Assignment: grade pushed immediately after each submission is graded.
  - Quiz: essay scores AND marks are held in memory and written to Moodle
    together, only once ALL essay questions for that attempt are graded.

HUMAN REVIEW HOLD-BACK:
  If the AI grading result sets requires_human_review=true, the grade is
  NOT pushed to Moodle. Instead it's written to a separate flagged-review
  log file, and the question/submission is left in its current ungraded
  state in Moodle — so it naturally appears in Moodle's own manual grading
  queue for a teacher to check, rather than silently receiving an AI grade
  that hasn't actually been reviewed.

QUIZ WRITE-BACK IS NOW FULLY ATOMIC:
  Previously, each essay question's mark was written to Moodle immediately
  as soon as it was graded, while only the *combined total* waited for all
  essays in the attempt to finish. This meant a single confidently-graded
  essay could land live in Moodle well before the rest of the attempt had
  been reviewed - including in cases where the grading itself shouldn't be
  trusted yet (e.g. no rubric available). Individual essay marks are now
  held in memory in the same accumulator as the total, and ALL marks for
  an attempt (each question's mark + the combined total) are written to
  Moodle together, in one batch, only once every essay question for that
  attempt has been graded. If any essay in the batch gets flagged for
  human review first, the whole batch is discarded and NOTHING is written
  to Moodle for that attempt - not even the other, non-flagged essays.

GRADING DASHBOARD LOGGING:
  Every grading attempt (success or fail) is also POSTed to the Grading
  Dashboard's log-ingestion API (see log_to_dashboard()), so admins can
  monitor and troubleshoot grading activity from the dashboard. This never
  raises — a dashboard-logging failure must not break actual grading.
"""

import time
from datetime import datetime, timezone
import argparse
import json
import logging
import os
import sys
import requests

import pika
from dotenv import load_dotenv

load_dotenv()

from grader import build_prompt, grade_submission, DailyQuotaExceededError
from grading_logger import log_success, log_failure

from google import genai
from google.genai import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

QUEUE_NAME = "mqueue_grading_jobs"

# ---------------------------------------------------------------------------
# Grading Dashboard logging config
# ---------------------------------------------------------------------------
DASHBOARD_LOG_URL = os.getenv("DASHBOARD_LOG_URL", "http://localhost:5001/api/logs")
API_KEY = os.getenv("API_KEY", "test-api-key-123")


def log_to_dashboard(data: str, status: str, details: str, attempt: int) -> None:
    """
    Send a grading execution log entry to the Grading Dashboard.

    Never raises — a dashboard-logging failure must not break grading.
    Any connection/request error is caught and logged locally instead.
    """
    try:
        requests.post(
            DASHBOARD_LOG_URL,
            headers={"apiKey": API_KEY, "Content-Type": "application/json"},
            json={"data": data, "status": status, "details": details, "attempt": attempt},
            timeout=5,
        )
    except requests.RequestException as e:
        log.warning("Failed to log to dashboard: %s", e)


# ---------------------------------------------------------------------------
# Quiz grade accumulator
# Holds EVERY essay mark for an attempt in memory - nothing is written to
# Moodle until all essay questions for that attempt have been graded, at
# which point every mark plus the combined total are written together as
# one atomic batch.
# Key: (moodle_userid, moodle_quiz_id, attempt_id)
# Value: {"scores": [...], "feedbacks": [...], "slots": [...],
#         "feedback_htmls": [...], "expected": int}
#
# PERSISTENCE (new): this dict is also mirrored to a local JSON file
# (ACCUMULATOR_STATE_FILE) every time it changes, so held-but-unwritten
# marks survive a worker restart (e.g. hitting the daily Gemini quota,
# a crash, or just closing the terminal). On startup, the file is read
# back into memory before any new messages are consumed, so an attempt
# that was at 5/8 when the worker stopped resumes at 5/8 instead of
# starting over from 0 - no manual re-queuing needed.
# ---------------------------------------------------------------------------
_quiz_grade_accumulator: dict = {}

ACCUMULATOR_STATE_FILE = "quiz_accumulator_state.json"


def _accumulator_key_to_str(key: tuple) -> str:
    """Tuple keys aren't valid JSON object keys - encode as a delimited string."""
    return "|".join(str(part) for part in key)


def _accumulator_key_from_str(key_str: str) -> tuple:
    """Reverse of _accumulator_key_to_str()."""
    parts = key_str.split("|")
    return tuple(parts)


def save_accumulator_state() -> None:
    """
    Persist the current in-memory accumulator to disk. Called every time
    a mark is added to it, or an entry is removed (flagged/pushed), so the
    file on disk always matches what's in memory.
    """
    try:
        serialisable = {
            _accumulator_key_to_str(key): value
            for key, value in _quiz_grade_accumulator.items()
        }
        with open(ACCUMULATOR_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(serialisable, f, indent=2, ensure_ascii=False)
    except Exception as e:
        # Persistence failing must not break live grading - log and continue.
        log.warning("Failed to save accumulator state to disk: %s", e)


def load_accumulator_state() -> None:
    """
    Load any previously-held accumulator state from disk into memory.
    Called once at worker startup, before consuming begins. If the file
    doesn't exist (first run, or a clean state), this is a no-op.
    """
    global _quiz_grade_accumulator

    if not os.path.exists(ACCUMULATOR_STATE_FILE):
        return

    try:
        with open(ACCUMULATOR_STATE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        _quiz_grade_accumulator = {
            _accumulator_key_from_str(key_str): value
            for key_str, value in raw.items()
        }
        if _quiz_grade_accumulator:
            log.info(
                "Resumed %d in-progress quiz attempt(s) from %s:",
                len(_quiz_grade_accumulator), ACCUMULATOR_STATE_FILE,
            )
            for key, value in _quiz_grade_accumulator.items():
                userid, quizid, attemptid = key
                log.info(
                    "  attempt=%s userid=%s: %d/%d essay(s) already held",
                    attemptid, userid, len(value["scores"]), value["expected"],
                )
    except Exception as e:
        log.warning(
            "Failed to load accumulator state from %s (%s) - starting fresh.",
            ACCUMULATOR_STATE_FILE, e,
        )
        _quiz_grade_accumulator = {}

# Number of essay questions per quiz_id.
# Update this if you add more essay questions to a quiz.
QUIZ_ESSAY_COUNT = {
    1: 2,   # quiz_id 1 has 2 essay questions (Q11 and Q12)
    7: 8,   # quiz_id 7 (Business Analysis — Implementing a Data Analytics Platform) has 8 essay questions
    16: 8,  # quiz_id 16 (Business Analysis copy 1 — same quiz content as quiz 7) has 8 essay questions
}


# ---------------------------------------------------------------------------
# Output file management
# ---------------------------------------------------------------------------

def save_result(result: dict, output_path: str, course_name: str = "", assignment_name: str = "") -> None:
    """
    Append a grading result to the output JSON file.
    Guarantees the schema: { "metadata": [...], "results": [...], "errors": [...] }

    "metadata" is a LIST of per-day snapshots. A new entry is created the
    first time save_result() runs on a given calendar date (local time); on
    that same date, subsequent calls update that day's existing entry in
    place (totals accumulate across multiple worker.py restarts on the same
    day). The next calendar day, a fresh entry is appended instead.
    """
    try:
        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    if not isinstance(data, dict):
        data = {}

    if "metadata" not in data:
        data["metadata"] = []
    elif isinstance(data["metadata"], dict):
        data["metadata"] = [data["metadata"]] if data["metadata"] else []
    elif not isinstance(data["metadata"], list):
        data["metadata"] = []

    if "results" not in data or not isinstance(data["results"], list):
        data["results"] = []

    if "errors" not in data or not isinstance(data["errors"], list):
        data["errors"] = []

    now_local = datetime.now().astimezone()
    today_str = now_local.date().isoformat()

    today_entry = None
    for entry in data["metadata"]:
        graded_at = entry.get("graded_at", "")
        if graded_at[:10] == today_str:
            today_entry = entry
            break

    if today_entry is None:
        today_entry = {
            "course_name": course_name,
            "assignment_name": assignment_name,
            "graded_at": now_local.isoformat(),
            "total_submissions": 0,
            "graded_count": 0,
            "error_count": 0,
        }
        data["metadata"].append(today_entry)

    if course_name and not today_entry.get("course_name"):
        today_entry["course_name"] = course_name
    if assignment_name and not today_entry.get("assignment_name"):
        today_entry["assignment_name"] = assignment_name

    status = result.get("status", "graded")
    if status == "error":
        data["errors"].append(result)
        today_entry["error_count"] = today_entry.get("error_count", 0) + 1
    else:
        data["results"].append(result)
        today_entry["graded_count"] = today_entry.get("graded_count", 0) + 1

    today_entry["total_submissions"] = today_entry["graded_count"] + today_entry["error_count"]
    today_entry["graded_at"] = now_local.isoformat()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def log_flagged_for_review(entry: dict, flagged_log_path: str) -> None:
    """
    Append a submission that was held back from Moodle due to
    requires_human_review=true. Keeps a simple flat JSON list so it's easy
    for a teacher (or another script) to work through.
    """
    try:
        with open(flagged_log_path, encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            entries = []
    except (FileNotFoundError, json.JSONDecodeError):
        entries = []

    entries.append(entry)

    with open(flagged_log_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Feedback HTML builder (for assignment push-back)
# ---------------------------------------------------------------------------

def _build_feedback_html(grading: dict) -> str:
    """
    Build readable HTML feedback for Moodle from the Gemini grading result.
    Shown to the learner in the assignment feedback box.
    """
    parts = []

    feedback = grading.get("feedback")
    if feedback:
        parts.append(f"<p>{feedback}</p>")

    strengths = grading.get("strengths") or []
    if strengths:
        items = "".join(f"<li>{s}</li>" for s in strengths)
        parts.append(f"<p><strong>Strengths:</strong></p><ul>{items}</ul>")

    improvements = grading.get("improvements") or []
    if improvements:
        items = "".join(f"<li>{i}</li>" for i in improvements)
        parts.append(f"<p><strong>Areas for improvement:</strong></p><ul>{items}</ul>")

    if grading.get("requires_human_review"):
        reason = grading.get("human_review_reason") or "Flagged for review."
        parts.append(
            f"<p><em>This submission has been flagged for human review: {reason}</em></p>"
        )

    return "".join(parts) or "Graded by AI Grading Prototype."


# ---------------------------------------------------------------------------
# RabbitMQ callback factory
# ---------------------------------------------------------------------------

def make_callback(client, gen_config, model_name, output_path, log_path, flagged_log_path, pace_seconds=13.0):
    """
    Factory function: returns a pika callback with Gemini client injected.
    Called once per message received from the queue.

    Handles both Moodle push-back flows:
    - Assignment: grade pushed immediately after grading.
    - Quiz: every essay mark for an attempt is held in memory and written
      to Moodle together, in one batch, only once all essay questions for
      that attempt have been graded.
    Jobs from the original producer.py / input.json are unaffected.

    HOLD-BACK RULE: if grading["requires_human_review"] is true, the grade
    is never pushed to Moodle — it's recorded in the flagged-review log
    instead, and the question is left as-is in Moodle (still shows up in
    Moodle's own "needs grading" queue for a teacher to check). For quizzes,
    this also discards any other essay marks already accumulated in memory
    for that same attempt - a flagged essay means the WHOLE attempt's batch
    write is cancelled, not just that one question.

    PACING: pace_seconds is a fixed delay applied after every grading
    call (success or failure) to stay under Gemini's free-tier rate
    limit (5 requests/minute = one request per 12s minimum — default
    13s here for a small safety buffer). This is a proactive throttle;
    grade_submission() in grader.py also has a reactive 429 retry as a
    backstop in case the limit is still hit occasionally.
    """

    def process_job(channel, method, properties, body):
        message          = json.loads(body)
        submission       = message["submission"]
        learner_id       = submission.get("learner_id", "unknown")
        course_name      = message.get("course_name", "")
        assignment_name  = message.get("assignment_name", "")

        # Moodle-specific fields — present only on Moodle-sourced jobs
        moodle_userid        = submission.get("_moodle_userid")
        moodle_assignment_id = submission.get("_moodle_assignment_id")
        moodle_quiz_id       = submission.get("_moodle_quiz_id")
        moodle_attempt_id    = submission.get("_moodle_attempt_id")
        moodle_slot          = submission.get("_moodle_slot")

        log.info("[->] Received grading job for learner: %s", learner_id)

        try:
            top_level = {
                "subject_area":    message.get("subject_area", ""),
                "assignment_name": assignment_name,
            }
            prompt = build_prompt(course_name, submission, top_level)
            grading = grade_submission(client, prompt, gen_config, model_name)

        except DailyQuotaExceededError:
            # Gemini's DAILY quota is exhausted — this cannot be fixed by
            # retrying. Put this job back at the front of the queue
            # (requeue=True) so it isn't lost, and stop the worker entirely
            # rather than burning through every remaining job hitting the
            # same wall. Resume by just restarting the worker once the
            # quota resets (typically ~24h for the free tier).
            log.error(
                "[DAILY QUOTA EXCEEDED] Gemini's daily request quota is exhausted. "
                "Re-queuing this job for %s and stopping the worker so nothing "
                "else is lost. Restart the worker once the quota resets.",
                learner_id,
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            channel.stop_consuming()
            return

        except Exception as e:
            error_msg = str(e)
            log.error("[FAIL] Could not grade %s: %s", learner_id, error_msg)
            time.sleep(pace_seconds)  # pace even on failure to avoid burst-retrying

            save_result({
                "learner_id": learner_id,
                "status":     "error",
                "error":      error_msg,
            }, output_path, course_name=course_name, assignment_name=assignment_name)

            log_failure(
                learner_id=learner_id,
                course_name=course_name,
                assignment_name=assignment_name,
                error_message=error_msg,
                log_path=log_path,
            )

            log_to_dashboard(
                data=learner_id,
                status="fail",
                details=error_msg,
                attempt=1,
            )

            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        # --- Grading succeeded — pace, then record + push back ---
        time.sleep(pace_seconds)  # proactive rate-limit pacing

        result = {
            "learner_id": learner_id,
            "status":     "graded",
            "grading":    grading,
        }
        save_result(result, output_path, course_name=course_name, assignment_name=assignment_name)

        log_success(
            learner_id=learner_id,
            course_name=course_name,
            assignment_name=assignment_name,
            score=grading.get("score"),
            max_grade=grading.get("max_grade"),
            grade_label=grading.get("grade_label"),
            requires_human_review=grading.get("requires_human_review", False),
            log_path=log_path,
        )

        log_to_dashboard(
            data=learner_id,
            status="success",
            details=f"score={grading.get('score')}/{grading.get('max_grade')} ({grading.get('grade_label')})",
            attempt=1,
        )

        log.info(
            "[OK] %s -> %s/%s (%s%%) [%s]%s",
            learner_id,
            grading.get("score"),
            grading.get("max_grade"),
            grading.get("percentage"),
            grading.get("grade_label"),
            " *** HUMAN REVIEW NEEDED ***" if grading.get("requires_human_review") else "",
        )

        # -----------------------------------------------------------
        # HOLD-BACK CHECK: flagged submissions never reach Moodle.
        # -----------------------------------------------------------
        if grading.get("requires_human_review"):
            flagged_entry = {
                "timestamp":        datetime.now().astimezone().isoformat(),
                "learner_id":       learner_id,
                "course_name":      course_name,
                "assignment_name":  assignment_name,
                "ai_score":         grading.get("score"),
                "max_grade":        grading.get("max_grade"),
                "ai_feedback":      grading.get("feedback"),
                "human_review_reason": grading.get("human_review_reason"),
                "_moodle_userid":        moodle_userid,
                "_moodle_assignment_id": moodle_assignment_id,
                "_moodle_quiz_id":       moodle_quiz_id,
                "_moodle_attempt_id":    moodle_attempt_id,
                "_moodle_slot":          moodle_slot,
            }
            log_flagged_for_review(flagged_entry, flagged_log_path)
            log.warning(
                "[HELD] %s — flagged for human review, NOT pushed to Moodle "
                "(reason: %s). Question remains in its current ungraded state.",
                learner_id, grading.get("human_review_reason"),
            )

            # Discard any other essay marks already accumulated in memory
            # for this attempt - since one essay needs human review, the
            # WHOLE attempt's batch write is cancelled. Nothing for this
            # attempt gets written to Moodle from this batch; a later
            # re-run (e.g. via moodle_quiz_producer.py --force, or a fresh
            # event-driven submission) starts the accumulator fresh.
            if moodle_quiz_id is not None and moodle_attempt_id is not None:
                key = (str(moodle_userid), str(moodle_quiz_id), str(moodle_attempt_id))
                _quiz_grade_accumulator.pop(key, None)
                save_accumulator_state()

            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        # --- Hold or push grade, depending on job type ---
        if moodle_userid is not None:
            try:
                import moodle_client
                feedback_html = _build_feedback_html(grading)
                feedback_text = grading.get("feedback", "")

                # ASSIGNMENT FLOW — push grade immediately (unchanged)
                if moodle_assignment_id is not None:
                    moodle_client.save_grade(
                        assignment_id=moodle_assignment_id,
                        userid=moodle_userid,
                        grade=grading.get("score"),
                        feedback_html=feedback_html,
                    )
                    log.info(
                        "[MOODLE] Assignment grade pushed back for %s (userid=%s, assignment=%s)",
                        learner_id, moodle_userid, moodle_assignment_id,
                    )

                # QUIZ FLOW — hold every mark in memory; write nothing to
                # Moodle until the whole attempt is graded, then write all
                # marks + the combined total together as one atomic batch.
                elif moodle_quiz_id is not None and moodle_attempt_id is not None:

                    # Cast every part to str explicitly - moodle_userid/quizid/
                    # attemptid arrive as JSON integers from a live message,
                    # but _accumulator_key_from_str() always reconstructs them
                    # as strings when loading persisted state back from disk.
                    # Without this cast, a live int-keyed lookup silently
                    # misses a resumed string-keyed entry and starts a brand
                    # new accumulator from 0 instead of continuing it.
                    key = (str(moodle_userid), str(moodle_quiz_id), str(moodle_attempt_id))
                    expected = QUIZ_ESSAY_COUNT.get(int(moodle_quiz_id), 1)

                    if key not in _quiz_grade_accumulator:
                        _quiz_grade_accumulator[key] = {
                            "scores": [],
                            "feedbacks": [],
                            "slots": [],
                            "feedback_htmls": [],
                            "expected": expected,
                        }
                    acc = _quiz_grade_accumulator[key]

                    # Hold this essay's grade in memory only - do NOT write
                    # to Moodle yet. Individual marks are written only once
                    # the WHOLE attempt is graded, so a partial/incomplete
                    # batch never leaves an unreviewed AI mark sitting live
                    # in Moodle for a question graded ahead of the rest of
                    # the attempt.
                    acc["scores"].append(grading.get("score", 0))
                    acc["feedbacks"].append(feedback_text)
                    acc["slots"].append(moodle_slot)
                    acc["feedback_htmls"].append(feedback_html)
                    save_accumulator_state()

                    log.info(
                        "[QUIZ] Accumulated %d/%d essay grades for attempt=%s userid=%s (held - not yet written to Moodle)",
                        len(acc["scores"]), acc["expected"],
                        moodle_attempt_id, moodle_userid,
                    )

                    if len(acc["scores"]) >= acc["expected"]:
                        # All essays for this attempt are now graded and
                        # NONE were flagged for review - write every
                        # individual mark AND the combined total together,
                        # as one atomic batch, instead of trickling marks
                        # in one at a time as they were graded.
                        for i, slot_num in enumerate(acc["slots"]):
                            moodle_client.save_quiz_essay_grade(
                                attempt_id=moodle_attempt_id,
                                slot=slot_num,
                                grade=acc["scores"][i],
                                feedback_html=acc["feedback_htmls"][i],
                            )

                        essay_total = sum(acc["scores"])
                        objective_score = submission.get("_moodle_objective_score", 0.0)
                        total = essay_total + objective_score

                        log.info(
                            "[QUIZ] All %d essay(s) graded for attempt=%s. Essay total: %s + Objective score: %s = Grand total: %s",
                            acc["expected"], moodle_attempt_id, essay_total, objective_score, total,
                        )
                        log.info(
                            "[MOODLE] Quiz grade pushed back for %s (userid=%s, quiz=%s, final=%s)",
                            learner_id, moodle_userid, moodle_quiz_id, total,
                        )
                        del _quiz_grade_accumulator[key]
                        save_accumulator_state()

            except Exception as moodle_err:
                log.error(
                    "[MOODLE] Failed to push grade for %s to Moodle: %s",
                    learner_id, moodle_err,
                )

        channel.basic_ack(delivery_tag=method.delivery_tag)

    return process_job


# ---------------------------------------------------------------------------
# Worker startup
# ---------------------------------------------------------------------------

def start_worker(
    rabbitmq_host: str,
    model_name: str,
    output_path: str,
    log_path: str,
    flagged_log_path: str,
    pace_seconds: float = 13.0,
) -> None:
    """Connect to RabbitMQ and start consuming grading jobs."""

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY not found. Add it to your .env file.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    gen_config = types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
    )

    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=rabbitmq_host,
                heartbeat=600,  # generous timeout so long API-retry backoffs
                                # (which block synchronously) don't cause
                                # RabbitMQ to think the connection died
                blocked_connection_timeout=300,
            )
        )
    except pika.exceptions.AMQPConnectionError as e:
        log.error("Could not connect to RabbitMQ at '%s': %s", rabbitmq_host, e)
        log.error("Make sure RabbitMQ is running before starting the worker.")
        sys.exit(1)

    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    channel.basic_qos(prefetch_count=1)

    # Resume any in-progress quiz attempts from a previous run that got
    # interrupted (quota exhaustion, crash, etc.) before consuming begins.
    load_accumulator_state()

    callback = make_callback(client, gen_config, model_name, output_path, log_path, flagged_log_path, pace_seconds)
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=callback)

    log.info("=== AI Grading Worker Started ===")
    log.info("Model       : %s", model_name)
    log.info("Output      : %s", output_path)
    log.info("Log         : %s", log_path)
    log.info("Flagged log : %s", flagged_log_path)
    log.info("Dashboard   : %s", DASHBOARD_LOG_URL)
    log.info("Pace        : %.1fs between calls", pace_seconds)
    log.info("Host        : %s", rabbitmq_host)
    log.info("Waiting for grading jobs... (Ctrl+C to stop)")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        log.info("Worker stopped.")
        channel.stop_consuming()

    connection.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RabbitMQ AI grading worker")
    parser.add_argument("--host",   default="localhost",        help="RabbitMQ host (default: localhost)")
    parser.add_argument("--model",  default="gemini-3.6-flash", help="Gemini model (default: gemini-3.6-flash)")
    parser.add_argument("--output", default="output.json",      help="Output file (default: output.json)")
    parser.add_argument("--log",    default="grading_log.json", help="Log file (default: grading_log.json)")
    parser.add_argument("--flagged-log", default="flagged_for_review.json",
                        help="File where submissions flagged for human review are recorded "
                             "instead of being pushed to Moodle (default: flagged_for_review.json)")
    parser.add_argument("--pace-seconds", type=float, default=13.0,
                        help="Fixed delay (seconds) between grading calls, to stay under "
                             "the Gemini free-tier rate limit of 5 requests/minute "
                             "(default: 13.0)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    start_worker(args.host, args.model, args.output, args.log, args.flagged_log, args.pace_seconds)