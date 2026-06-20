"""
moodle_producer.py - Pulls ungraded submissions directly from Moodle and
publishes them to the same RabbitMQ 'grading_jobs' queue that worker.py
already consumes from.

This REPLACES producer.py for the live Moodle workflow. worker.py and
grader.py require NO changes — they already consume a generic message
shape, and this script builds that exact same shape from live Moodle data
instead of input.json.

Usage:
    python moodle_producer.py --course-id 4 --assignment-id 2 \\
        --question-text "Apply Porter's Five Forces..." \\
        --max-grade 30 --grading-guide grading_guide.json \\
        [--host localhost]

Environment variables expected (.env):
    MOODLE_BASE_URL=http://localhost:8080
    MOODLE_TOKEN=your_api_gradingbot_token
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import pika

import moodle_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

QUEUE_NAME = "grading_jobs"


def load_grading_guide(path: str) -> list:
    """Load the grading rubric (same structure as used in input.json)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Grading guide file not found: {path}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def build_jobs(
    course_id: int,
    assignment_id: int,
    course_name: str,
    assignment_name: str,
    subject_area: str,
    question_text: str,
    max_grade: float,
    grading_guide: list,
) -> list[dict]:
    """
    Pull ungraded submissions from Moodle and build messages in the exact
    shape worker.py / grader.py already expect:

        {
          "course_name": ...,
          "assignment_name": ...,
          "subject_area": ...,
          "submission": {
              "learner_id": ...,        # Moodle idnumber, e.g. u12345678
              "question_text": ...,
              "learner_response": ...,  # extracted PDF text
              "max_grade": ...,
              "grading_guide": ...,
              "_moodle_userid": ...,    # internal, used later to push grade back
              "_moodle_assignment_id": ...,
          }
        }
    """
    submissions = moodle_client.get_submissions(assignment_id, only_ungraded=True)

    user_cache: dict = {}
    jobs = []

    for sub in submissions:
        moodle_userid = sub["userid"]
        idnumber = moodle_client.get_idnumber_for_userid(moodle_userid, user_cache)

        if not idnumber:
            log.warning(
                "Skipping userid=%s — no idnumber set on this Moodle account "
                "(set the 'ID number' field on the user).",
                moodle_userid,
            )
            continue

        learner_text = moodle_client.get_submission_text(sub)
        if not learner_text:
            log.warning("Skipping userid=%s — no extractable submission text found.", moodle_userid)
            continue

        submission_payload = {
            "learner_id": idnumber,
            "question_text": question_text,
            "learner_response": learner_text,
            "max_grade": max_grade,
            "grading_guide": grading_guide,
            # Internal fields, not part of the original input.json schema,
            # but harmless extras — worker.py only reads the keys it needs.
            # Used later by the result push-back step.
            "_moodle_userid": moodle_userid,
            "_moodle_assignment_id": assignment_id,
        }

        jobs.append({
            "course_name": course_name,
            "assignment_name": assignment_name,
            "subject_area": subject_area,
            "submission": submission_payload,
        })

    return jobs


def publish_jobs(jobs: list[dict], rabbitmq_host: str = "localhost") -> None:
    """Publish job messages to the grading_jobs queue (same as producer.py)."""
    if not jobs:
        log.warning("No jobs to publish — nothing ungraded found.")
        return

    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    for job in jobs:
        learner_id = job["submission"]["learner_id"]
        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(job),
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type="application/json",
            ),
        )
        log.info("[->] Queued Moodle submission for learner: %s", learner_id)

    log.info("[OK] %d grading job(s) submitted to '%s' queue.", len(jobs), QUEUE_NAME)
    connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull ungraded Moodle submissions and queue them for grading"
    )
    parser.add_argument("--course-id", type=int, required=True, help="Moodle course id")
    parser.add_argument("--assignment-id", type=int, required=True, help="Moodle assignment id")
    parser.add_argument("--course-name", required=True, help="Course name for the grading prompt")
    parser.add_argument("--assignment-name", required=True, help="Assignment name for the grading prompt")
    parser.add_argument("--subject-area", required=True, help="Subject area for the grading prompt")
    parser.add_argument("--question-text", required=True, help="The assignment question text")
    parser.add_argument("--max-grade", type=float, required=True, help="Maximum grade/marks")
    parser.add_argument("--grading-guide", required=True, help="Path to grading guide JSON file")
    parser.add_argument("--host", default="localhost", help="RabbitMQ host (default: localhost)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        grading_guide = load_grading_guide(args.grading_guide)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.error("Failed to load grading guide: %s", e)
        sys.exit(1)

    try:
        jobs = build_jobs(
            course_id=args.course_id,
            assignment_id=args.assignment_id,
            course_name=args.course_name,
            assignment_name=args.assignment_name,
            subject_area=args.subject_area,
            question_text=args.question_text,
            max_grade=args.max_grade,
            grading_guide=grading_guide,
        )
    except RuntimeError as e:
        log.error("Moodle API error: %s", e)
        sys.exit(1)

    try:
        publish_jobs(jobs, args.host)
    except pika.exceptions.AMQPConnectionError as e:
        log.error("Could not connect to RabbitMQ at '%s': %s", args.host, e)
        log.error("Make sure RabbitMQ is running before submitting jobs.")
        sys.exit(1)


if __name__ == "__main__":
    main()