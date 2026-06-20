"""
worker.py - Consumes grading jobs from RabbitMQ and grades them using Gemini.

Usage:
    python worker.py [--host localhost] [--model gemini-2.5-flash]
                     [--output output.json] [--log grading_log.json]

Reuses all grading logic from grader.py directly.
Logs every attempt (success or fail) to a structured JSON log file.
Multiple workers can run in parallel — RabbitMQ distributes jobs between them.
"""
from datetime import datetime, timezone
import argparse
import json
import logging
import os
import sys

import pika
from dotenv import load_dotenv

load_dotenv()

# Reuse all logic directly from grader.py — no duplication
from grader import build_prompt, grade_submission
from grading_logger import log_success, log_failure

from google import genai
from google.genai import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

QUEUE_NAME = "grading_jobs"


from datetime import datetime, timezone # Make sure this is at the top of your file!

from datetime import datetime, timezone  # Make sure this is at the top of your file!

def save_result(result: dict, output_path: str, course_name: str = "", assignment_name: str = "") -> None:
    """
    Append a grading result to the output JSON file.
    Guarantees the schema: { "metadata": [...], "results": [...], "errors": [...] }

    "metadata" is now a LIST of per-day snapshots. A new entry is created the
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

    # Normalize data: Force it into the correct schema if it isn't already
    if not isinstance(data, dict):
        data = {}

    # --- Migrate / normalise "metadata" into a list ---
    if "metadata" not in data:
        data["metadata"] = []
    elif isinstance(data["metadata"], dict):
        # Old single-object format from a previous version — wrap it so
        # existing history isn't lost.
        data["metadata"] = [data["metadata"]] if data["metadata"] else []
    elif not isinstance(data["metadata"], list):
        data["metadata"] = []

    if "results" not in data or not isinstance(data["results"], list):
        data["results"] = []

    if "errors" not in data or not isinstance(data["errors"], list):
        data["errors"] = []

    now_local = datetime.now().astimezone()
    today_str = now_local.date().isoformat()  # e.g. "2026-06-20"

    # Find today's metadata entry, if one already exists
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

    # Update course/assignment name if provided and currently empty
    if course_name and not today_entry.get("course_name"):
        today_entry["course_name"] = course_name
    if assignment_name and not today_entry.get("assignment_name"):
        today_entry["assignment_name"] = assignment_name

    # Append to the correct list based on status
    status = result.get("status", "graded")
    if status == "error":
        data["errors"].append(result)
        today_entry["error_count"] = today_entry.get("error_count", 0) + 1
    else:
        data["results"].append(result)
        today_entry["graded_count"] = today_entry.get("graded_count", 0) + 1

    # Update today's totals and refresh its "last updated" timestamp
    today_entry["total_submissions"] = today_entry["graded_count"] + today_entry["error_count"]
    today_entry["graded_at"] = now_local.isoformat()

    # Write back to file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def make_callback(client, gen_config, model_name, output_path, log_path):
    """
    Factory function: returns a pika callback with Gemini client injected.
    Called once per message received from the queue.

    If the submission dict contains Moodle-specific fields
    (_moodle_userid + _moodle_assignment_id, added by moodle_producer.py),
    the resulting grade is automatically pushed back to Moodle via
    mod_assign_save_grade after a successful grading call. Jobs sourced
    from the original producer.py / input.json (which never set these
    fields) are unaffected — they just skip the push-back step.
    """

    def process_job(channel, method, properties, body):
        message     = json.loads(body)
        submission  = message["submission"]
        learner_id  = submission.get("learner_id", "unknown")
        course_name = message.get("course_name", "")
        assignment_name = message.get("assignment_name", "")

        # Present only on jobs built by moodle_producer.py
        moodle_userid       = submission.get("_moodle_userid")
        moodle_assignment_id = submission.get("_moodle_assignment_id")

        log.info("[->] Received grading job for learner: %s", learner_id)

        try:
            # Build prompt and call Gemini using grader.py functions directly
            top_level = {
                "subject_area":    message.get("subject_area", ""),
                "assignment_name": assignment_name,
            }
            prompt  = build_prompt(course_name, submission, top_level)
            grading = grade_submission(client, prompt, gen_config, model_name)

            # Save result to output file
            result = {
                "learner_id":   learner_id,
                "status":       "graded",
                "grading":      grading,
            }
            save_result(result, output_path, course_name=course_name, assignment_name=assignment_name)

            # Log success
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

            # --- Push grade back to Moodle, if this job came from Moodle ---
            if moodle_userid is not None and moodle_assignment_id is not None:
                try:
                    import moodle_client

                    feedback_html = _build_feedback_html(grading)
                    moodle_client.save_grade(
                        assignment_id=moodle_assignment_id,
                        userid=moodle_userid,
                        grade=grading.get("score"),
                        feedback_html=feedback_html,
                    )
                    log.info(
                        "[MOODLE] Grade pushed back for %s (userid=%s, assignment=%s)",
                        learner_id, moodle_userid, moodle_assignment_id,
                    )
                except Exception as moodle_err:  # pylint: disable=broad-except
                    # Grading itself succeeded and is safely recorded above —
                    # a failed push-back should not be treated as a failed grade.
                    # It just means Moodle's gradebook is out of sync for now.
                    log.error(
                        "[MOODLE] Failed to push grade for %s to Moodle: %s",
                        learner_id, moodle_err,
                    )

        except Exception as e:
            error_msg = str(e)
            log.error("[FAIL] Could not grade %s: %s", learner_id, error_msg)

            # Save failed result to output file
            save_result({
                "learner_id":  learner_id,
                "status":      "error",
                "error":       error_msg,
            }, output_path, course_name=course_name, assignment_name=assignment_name)

            # Log failure
            log_failure(
                learner_id=learner_id,
                course_name=course_name,
                assignment_name=assignment_name,
                error_message=error_msg,
                log_path=log_path,
            )

        finally:
            # Always ACK — remove message from queue whether grading succeeded or failed
            # This prevents the message from being re-queued infinitely on persistent errors
            channel.basic_ack(delivery_tag=method.delivery_tag)

    return process_job


def _build_feedback_html(grading: dict) -> str:
    """
    Build a readable HTML feedback comment for Moodle from the Gemini
    grading result — shown to the learner in the assignment feedback box.
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


def start_worker(
    rabbitmq_host: str,
    model_name: str,
    output_path: str,
    log_path: str,
) -> None:
    """Connect to RabbitMQ and start consuming grading jobs."""

    # Load Gemini API key from .env
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY not found. Add it to your .env file.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    gen_config = types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
    )

    # Connect to RabbitMQ
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    except pika.exceptions.AMQPConnectionError as e:
        log.error("Could not connect to RabbitMQ at '%s': %s", rabbitmq_host, e)
        log.error("Make sure RabbitMQ is running before starting the worker.")
        sys.exit(1)

    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    # Process one job at a time — fair dispatch across multiple workers
    channel.basic_qos(prefetch_count=1)

    callback = make_callback(client, gen_config, model_name, output_path, log_path)
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=callback)

    log.info("=== AI Grading Worker Started ===")
    log.info("Model   : %s", model_name)
    log.info("Output  : %s", output_path)
    log.info("Log     : %s", log_path)
    log.info("Host    : %s", rabbitmq_host)
    log.info("Waiting for grading jobs... (Ctrl+C to stop)")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        log.info("Worker stopped.")
        channel.stop_consuming()

    connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RabbitMQ AI grading worker")
    parser.add_argument("--host",   default="localhost",        help="RabbitMQ host (default: localhost)")
    parser.add_argument("--model",  default="gemini-2.5-flash", help="Gemini model (default: gemini-2.5-flash)")
    parser.add_argument("--output", default="output.json",      help="Output file (default: output.json)")
    parser.add_argument("--log",    default="grading_log.json", help="Log file (default: grading_log.json)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    start_worker(args.host, args.model, args.output, args.log)