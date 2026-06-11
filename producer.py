"""
producer.py - Submits grading jobs from input.json to RabbitMQ queue.

Usage:
    python producer.py --input input.json [--host localhost]

Each submission in input.json becomes one message in the 'grading_jobs' queue.
The worker.py consumes and processes these messages asynchronously.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import pika

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

QUEUE_NAME = "grading_jobs"


def load_input(path: str) -> dict:
    """Load and validate input JSON file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with p.open(encoding="utf-8") as f:
        data = json.load(f)

    required = {"course_name", "assignment_name", "subject_area", "submissions"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Input JSON missing keys: {missing}")

    return data


def submit_jobs(input_path: str, rabbitmq_host: str = "localhost") -> None:
    """Read input.json and publish each submission as a RabbitMQ message."""
    data = load_input(input_path)
    submissions = data.get("submissions", [])

    if not submissions:
        log.warning("No submissions found in %s", input_path)
        return

    # Connect to RabbitMQ
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()

    # Durable queue survives RabbitMQ restarts
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    submitted = 0
    for submission in submissions:
        learner_id = submission.get("learner_id", "unknown")

        # Each message carries everything the worker needs to grade one learner
        message = {
            "course_name":     data["course_name"],
            "assignment_name": data["assignment_name"],
            "subject_area":    data["subject_area"],
            "submission":      submission,
        }

        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,  # survive broker restart
                content_type="application/json",
            ),
        )
        log.info("[->] Queued job for learner: %s", learner_id)
        submitted += 1

    log.info("[OK] %d grading job(s) submitted to '%s' queue.", submitted, QUEUE_NAME)
    connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit grading jobs from input.json to RabbitMQ"
    )
    parser.add_argument("--input", default="input.json", help="Path to input JSON (default: input.json)")
    parser.add_argument("--host",  default="localhost",  help="RabbitMQ host (default: localhost)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        submit_jobs(args.input, args.host)
    except (FileNotFoundError, ValueError) as e:
        log.error("Input error: %s", e)
        sys.exit(1)
    except pika.exceptions.AMQPConnectionError as e:
        log.error("Could not connect to RabbitMQ at '%s': %s", args.host, e)
        log.error("Make sure RabbitMQ is running before submitting jobs.")
        sys.exit(1)