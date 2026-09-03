"""
main.py - Entry point for the AI Grading worker.

Consumes grading jobs from RabbitMQ and grades them using Gemini.

Usage:
    python main.py [--host localhost] [--model gemini-3.6-flash]
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
  that hasn't actually been reviewed. Flagged jobs are NOT sent to the
  dead-letter queue - this is an intentional hold, not a failure, and a
  response missing a rubric won't fix itself on retry.

QUIZ WRITE-BACK IS FULLY ATOMIC:
  Individual essay marks are held in memory (and persisted to
  quiz_accumulator_state.json) and ALL marks for an attempt, plus the
  combined total, are written to Moodle together, in one batch, only once
  every essay question for that attempt has been graded. If any essay in
  the batch gets flagged for human review first, the whole batch is
  discarded and NOTHING is written to Moodle for that attempt - not even
  the other, non-flagged essays.

DEAD-LETTER QUEUE:
  Any failure that happens AFTER Gemini has already graded a submission -
  most importantly, a failure to write the grade back to Moodle - no
  longer silently reports "success" to the dashboard while losing the
  message. Instead:
    - a "fail" report (with the real error) is sent to the dashboard,
    - the message's "attempt" counter is incremented,
    - the message is re-published to a dead-letter queue
      (f"{QUEUE_NAME}.dlq") instead of being dropped,
    - the original message is acked off the main queue (a copy now lives
      safely in the DLQ).
  On startup, before consuming anything new, every message currently
  sitting in the dead-letter queue is moved back onto the main queue, so
  failed jobs are automatically retried the next time the worker starts -
  matching the pattern used in the moodle-autograder-service reference
  implementation.

  For the quiz flow specifically: since marks are held until a full batch
  of N essays is ready, a failure at the final Moodle-write step means we
  can't safely assume which of the N essays did or didn't get written.
  All N held essays for that attempt are dead-lettered (not just the one
  message that triggered the batch write), each with its own incremented
  attempt count and its own dashboard "fail" report. On retry, all N will
  be re-graded and re-attempted as a fresh batch. This is simpler and
  safer than trying to detect a partial write, at the cost of re-spending
  Gemini calls on essays that may have already succeeded individually.

  KNOWN LIMITATION: because the accumulator is cleared after every batch
  outcome (success or dead-lettered failure), there's no de-dup guard for
  a very narrow crash window - if the worker dies after
  save_accumulator_state() persists the final essay's data to disk but
  before that essay's message is acked or dead-lettered, RabbitMQ will
  redeliver the un-acked message on reconnect, which would be re-graded
  and re-appended to the accumulator restored from disk (already full),
  triggering a premature/corrupted batch. This is accepted for now as a
  rare edge case, not fixed, to keep the retry model simple.

DASHBOARD SUCCESS REPORTS ARE DEFERRED:
  Previously, a "success" report was sent to the dashboard as soon as
  Gemini finished grading - before the grade had actually been confirmed
  written to Moodle. If the Moodle write then failed, the dashboard had
  already recorded "success" for a grade that was, in fact, lost. Success
  is now only reported once Moodle has actually confirmed the write:
  immediately after mod_assign_save_grade for assignments, or once per
  essay after the full atomic batch write succeeds for quizzes.

GRADING DASHBOARD LOGGING:
  Every grading attempt (success or fail) is POSTed to the Grading
  Dashboard's log-ingestion API (see log_to_dashboard()), so admins can
  monitor and troubleshoot grading activity from the dashboard. This never
  raises — a dashboard-logging failure must not break actual grading.

ENVIRONMENT VARIABLES:
    GEMINI_API_KEY      - required, Gemini API key
    DASHBOARD_LOG_URL   - Grading Dashboard log-ingestion endpoint
    API_KEY             - Grading Dashboard API key (no insecure default -
                           if unset, dashboard requests go out unauthenticated
                           and a warning is logged at startup)
    RABBITMQ_HOST       - RabbitMQ host (overridable via --host)
    MQ_QUEUE_NAME       - main queue name (default: mqueue_grading_jobs)
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

# Queue name is env-overridable so it isn't hardcoded per lecturer feedback -
# still defaults to the existing value so nothing breaks if unset.
QUEUE_NAME = os.getenv("MQ_QUEUE_NAME", "mqueue_grading_jobs")
DEAD_LETTER_QUEUE = f"{QUEUE_NAME}.dlq"

# ---------------------------------------------------------------------------
# Grading Dashboard logging config
# ---------------------------------------------------------------------------
DASHBOARD_LOG_URL = os.getenv("DASHBOARD_LOG_URL", "http://localhost:5001/api/logs")

# No insecure hardcoded fallback here (unlike the old "test-api-key-123"
# default) - if API_KEY isn't set, dashboard requests go out unauthenticated
# and will most likely be rejected server-side, but we warn loudly at
# startup rather than silently authenticating with a placeholder that could
# happen to work against a misconfigured dashboard.
API_KEY = os.getenv("API_KEY")


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
# Dead-letter queue helpers
# ---------------------------------------------------------------------------

def send_to_dead_letter_queue(channel, message: dict, error_message: str) -> None:
    """
    Increment the message's attempt count and re-publish it to the
    dead-letter queue, so it is retried on the next worker startup rather
    than being lost. Never raises - a failure to dead-letter a message is
    logged, not propagated, since the caller still needs to ack the
    original message regardless.
    """
    try:
        message["attempt"] = message.get("attempt", 1) + 1
        message["mqstatus"] = "fail"
        message["mqstatusdetail"] = error_message

        channel.basic_publish(
            exchange="",
            routing_key=DEAD_LETTER_QUEUE,
            body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type="application/json",
            ),
        )
        log.info(
            "Sent message to dead-letter queue '%s' (attempt now %d): %s",
            DEAD_LETTER_QUEUE, message["attempt"], error_message,
        )
    except Exception as e:
        log.error("Failed to publish message to dead-letter queue: %s", e)


def drain_dead_letter_queue(channel) -> int:
    """
    Move every message currently sitting in the dead-letter queue back
    onto the main queue, so failed jobs from a previous run are retried
    automatically the next time the worker starts. Called once at
    startup, before consuming any new messages.

    Returns the number of messages moved.
    """
    moved = 0
    while True:
        method_frame, properties, body = channel.basic_get(queue=DEAD_LETTER_QUEUE, auto_ack=False)
        if method_frame is None:
            break  # DLQ is empty

        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=body,
            properties=properties,
        )
        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
        moved += 1

    return moved


# ---------------------------------------------------------------------------
# Quiz grade accumulator
# Holds EVERY essay mark for an attempt in memory - nothing is written to
# Moodle until all essay questions for that attempt have been graded, at
# which point every mark plus the combined total are written together as
# one atomic batch.
# Key: (moodle_userid, moodle_quiz_id, attempt_id)
# Value: {"scores": [...], "feedbacks": [...], "slots": [...],
#         "feedback_htmls": [...], "messages": [...], "learner_ids": [...],
#         "expected": int}
#
# "messages" holds each essay's ORIGINAL raw message dict (including its
# current "attempt" count), so that if the final batch write to Moodle
# fails, every held essay can be re-published to the dead-letter queue
# individually, with its own incremented attempt count - not just the one
# message that happened to trigger the batch write.
#
# PERSISTENCE: this dict is also mirrored to a local JSON file
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
    a mark is added to it, or an entry is removed (flagged/pushed/dead-
    lettered), so the file on disk always matches what's in memory.
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
# Update this if you add more essay questions to a quiz. Any quiz id
# missing from this dict silently defaults to 1, which causes the worker
# to push a grade after the FIRST essay it grades rather than waiting for
# the rest - always add an entry here before grading a quiz for the first
# time, including duplicate/copy courses (each copy has its own quiz id).
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
    place (totals accumulate across multiple main.py restarts on the same
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
    - Assignment: grade pushed immediately after grading, once Moodle
      confirms the write. On failure, the message is dead-lettered.
    - Quiz: every essay mark for an attempt is held in memory and written
      to Moodle together, in one batch, only once all essay questions for
      that attempt have been graded. On a batch-write failure, every held
      essay for that attempt is dead-lettered individually.
    Jobs from the original producer.py / input.json are unaffected.

    HOLD-BACK RULE: if grading["requires_human_review"] is true, the grade
    is never pushed to Moodle — it's recorded in the flagged-review log
    instead, and the question is left as-is in Moodle (still shows up in
    Moodle's own "needs grading" queue for a teacher to check). This is
    NOT dead-lettered - it's an intentional hold, not a failure. For
    quizzes, this also discards any other essay marks already accumulated
    in memory for that same attempt - a flagged essay means the WHOLE
    attempt's batch write is cancelled, not just that one question.

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
        # How many times this exact job has been attempted, including this
        # one. Present on messages coming back from the dead-letter queue;
        # defaults to 1 for a job's first-ever attempt.
        attempt          = message.get("attempt", 1)

        # Moodle-specific fields — present only on Moodle-sourced jobs
        moodle_userid        = submission.get("_moodle_userid")
        moodle_assignment_id = submission.get("_moodle_assignment_id")
        moodle_quiz_id       = submission.get("_moodle_quiz_id")
        moodle_attempt_id    = submission.get("_moodle_attempt_id")
        moodle_slot          = submission.get("_moodle_slot")

        log.info("[->] Received grading job for learner: %s (attempt %d)", learner_id, attempt)

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
            # quota resets (typically ~24h for the free tier). This is
            # deliberately NOT dead-lettered - it's not a per-job failure,
            # it's a global "stop everything for now" condition.
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
            # Grading itself failed (e.g. Gemini API error, malformed
            # response). Report the failure honestly, then dead-letter the
            # job with an incremented attempt count instead of losing it.
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

            send_to_dead_letter_queue(channel, message, error_msg)

            log_to_dashboard(
                data=learner_id,
                status="fail",
                details=error_msg,
                attempt=message.get("attempt", attempt + 1),
            )

            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        # --- Grading succeeded — pace, then record locally ---
        # NOTE: no dashboard report yet. Success is only reported once the
        # grade has actually been confirmed written to Moodle, further
        # down - reporting it here (before that's known) is exactly the
        # premature "success" bug that let failed Moodle writes vanish
        # silently in earlier versions of this worker.
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
        # HOLD-BACK CHECK: flagged submissions never reach Moodle, and
        # are NOT dead-lettered - this is an intentional hold, not a
        # failure that retrying would fix.
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

        # --- Push grade to Moodle, depending on job type ---
        if moodle_userid is not None:
            try:
                import moodle_client
                feedback_html = _build_feedback_html(grading)
                feedback_text = grading.get("feedback", "")

                # ASSIGNMENT FLOW — push grade immediately, report success
                # only once Moodle actually confirms the write.
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
                    log_to_dashboard(
                        data=learner_id,
                        status="success",
                        details=f"score={grading.get('score')}/{grading.get('max_grade')} ({grading.get('grade_label')})",
                        attempt=attempt,
                    )

                # QUIZ FLOW — hold every mark in memory; write nothing to
                # Moodle until the whole attempt is graded, then write all
                # marks + the combined total together as one atomic batch.
                elif moodle_quiz_id is not None and moodle_attempt_id is not None:

                    key = (str(moodle_userid), str(moodle_quiz_id), str(moodle_attempt_id))
                    expected = QUIZ_ESSAY_COUNT.get(int(moodle_quiz_id), 1)

                    if key not in _quiz_grade_accumulator:
                        _quiz_grade_accumulator[key] = {
                            "scores": [],
                            "feedbacks": [],
                            "slots": [],
                            "feedback_htmls": [],
                            "messages": [],
                            "learner_ids": [],
                            "expected": expected,
                        }
                    acc = _quiz_grade_accumulator[key]

                    # Hold this essay's grade in memory only - do NOT write
                    # to Moodle yet, and do NOT report success to the
                    # dashboard yet. Both happen only once the WHOLE
                    # attempt is graded and successfully written, so a
                    # partial/incomplete batch never leaves an unreviewed
                    # AI mark sitting live in Moodle, or a false "success"
                    # sitting on the dashboard, for a question graded ahead
                    # of the rest of the attempt.
                    acc["scores"].append(grading.get("score", 0))
                    acc["feedbacks"].append(feedback_text)
                    acc["slots"].append(moodle_slot)
                    acc["feedback_htmls"].append(feedback_html)
                    acc["messages"].append(message)
                    acc["learner_ids"].append(learner_id)
                    save_accumulator_state()

                    log.info(
                        "[QUIZ] Accumulated %d/%d essay grades for attempt=%s userid=%s (held - not yet written to Moodle)",
                        len(acc["scores"]), acc["expected"],
                        moodle_attempt_id, moodle_userid,
                    )

                    if len(acc["scores"]) >= acc["expected"]:
                        # All essays for this attempt are now graded and
                        # NONE were flagged for review - attempt to write
                        # every individual mark AND the combined total
                        # together, as one atomic batch.
                        try:
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

                            # Only now, with the batch write confirmed, do
                            # we report success - one report per essay in
                            # the batch, matching the per-message reporting
                            # convention used elsewhere in the worker.
                            for i, held_learner_id in enumerate(acc["learner_ids"]):
                                held_attempt = acc["messages"][i].get("attempt", 1)
                                # max_grade comes from each held message's
                                # own submission payload, not from the
                                # accumulator (which only stores graded
                                # outputs, not the original question data).
                                held_max_grade = (
                                    acc["messages"][i]
                                    .get("submission", {})
                                    .get("max_grade")
                                )
                                log_to_dashboard(
                                    data=held_learner_id,
                                    status="success",
                                    details=f"score={acc['scores'][i]}/{held_max_grade} (batch total {total})",
                                    attempt=held_attempt,
                                )

                        except Exception as batch_err:
                            # The batch write failed partway through, or
                            # before it even started. We cannot safely
                            # assume which (if any) of the N essays
                            # actually landed in Moodle, so dead-letter
                            # ALL N held essays individually, each with
                            # its own incremented attempt count - not just
                            # the message that happened to trigger this
                            # batch write. On retry, all N will be
                            # re-graded and re-attempted as a fresh batch.
                            error_msg = str(batch_err)
                            log.error(
                                "[MOODLE] Batch write failed for attempt=%s userid=%s: %s. "
                                "Dead-lettering all %d held essay(s) for retry.",
                                moodle_attempt_id, moodle_userid, error_msg, len(acc["messages"]),
                            )
                            for i, held_message in enumerate(acc["messages"]):
                                send_to_dead_letter_queue(channel, held_message, error_msg)
                                log_to_dashboard(
                                    data=acc["learner_ids"][i],
                                    status="fail",
                                    details=error_msg,
                                    attempt=held_message.get("attempt", 1),
                                )

                        # Whether the batch succeeded or failed, this
                        # attempt's accumulator entry is done with - either
                        # written to Moodle, or fully dead-lettered for a
                        # fresh retry. Either way, don't leave stale state
                        # sitting around.
                        # KNOWN LIMITATION: see module docstring - a crash
                        # between save_accumulator_state() above and this
                        # point could cause a redelivered message to be
                        # re-appended to state restored from disk. Accepted
                        # as a rare edge case for now.
                        del _quiz_grade_accumulator[key]
                        save_accumulator_state()

            except Exception as moodle_err:
                # Something failed OUTSIDE the inner batch-write try/except
                # above (e.g. the assignment save_grade call itself, or an
                # error before we even got into the quiz branch). Report
                # honestly and dead-letter rather than silently losing it.
                error_msg = str(moodle_err)
                log.error(
                    "[MOODLE] Failed to push grade for %s to Moodle: %s",
                    learner_id, error_msg,
                )
                send_to_dead_letter_queue(channel, message, error_msg)
                log_to_dashboard(
                    data=learner_id,
                    status="fail",
                    details=error_msg,
                    attempt=message.get("attempt", attempt + 1),
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

    if not API_KEY:
        log.warning(
            "API_KEY not set - dashboard log requests will be sent without "
            "authentication and will likely be rejected. Add API_KEY to .env."
        )

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
    channel.queue_declare(queue=DEAD_LETTER_QUEUE, durable=True)
    channel.basic_qos(prefetch_count=1)

    # Resume any in-progress quiz attempts from a previous run that got
    # interrupted (quota exhaustion, crash, etc.) before consuming begins.
    load_accumulator_state()

    # Move every message currently sitting in the dead-letter queue back
    # onto the main queue, so jobs that failed in a previous run are
    # retried automatically now, rather than sitting in the DLQ forever
    # waiting for someone to notice.
    moved = drain_dead_letter_queue(channel)
    if moved:
        log.info("Moved %d message(s) from the dead-letter queue back to the main queue for retry.", moved)

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
    log.info("Main queue  : %s", QUEUE_NAME)
    log.info("Dead letter : %s", DEAD_LETTER_QUEUE)
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
    parser.add_argument("--host",   default=os.getenv("RABBITMQ_HOST", "localhost"),
                        help="RabbitMQ host (default: $RABBITMQ_HOST env var, or localhost)")
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