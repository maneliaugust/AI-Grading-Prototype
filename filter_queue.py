"""
filter_queue.py - Drains the grading_jobs queue and re-publishes only jobs
belonging to a specific set of Moodle userids, discarding the rest.

Use this after reducing your sample size (e.g. from 21 learners down to 8)
so leftover jobs for dropped learners don't get processed and pushed to
Moodle by mistake.

Usage:
    python filter_queue.py --keep-userids 21 26 32 33 34 35 36 37 [--host localhost]

This is a ONE-TIME operation: it consumes every message currently in the
queue, checks each job's "_moodle_userid" field, and either re-publishes it
(if the userid is in --keep-userids) or discards it (if not). Run it once,
then start the worker normally afterward.
"""

import argparse
import json
import logging

import pika

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

QUEUE_NAME = "mqueue_grading_jobs"


def filter_queue(rabbitmq_host: str, keep_userids: set[int]) -> None:
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    kept_jobs = []
    discarded_count = 0
    total_seen = 0

    log.info("Draining queue '%s'...", QUEUE_NAME)

    while True:
        method_frame, properties, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=False)
        if method_frame is None:
            break  # queue is empty

        total_seen += 1
        job = json.loads(body)
        submission = job.get("submission", {})
        userid = submission.get("_moodle_userid")
        learner_id = submission.get("learner_id", "unknown")
        slot = submission.get("_moodle_slot")

        if userid in keep_userids:
            kept_jobs.append(job)
            log.info("  [keep] %s (userid=%s) slot=%s", learner_id, userid, slot)
        else:
            discarded_count += 1
            log.info("  [drop] %s (userid=%s) slot=%s", learner_id, userid, slot)

        channel.basic_ack(delivery_tag=method_frame.delivery_tag)

    log.info(
        "Drained %d total message(s): keeping %d, discarding %d.",
        total_seen, len(kept_jobs), discarded_count,
    )

    if kept_jobs:
        log.info("Re-publishing %d kept job(s)...", len(kept_jobs))
        for job in kept_jobs:
            channel.basic_publish(
                exchange="",
                routing_key=QUEUE_NAME,
                body=json.dumps(job),
                properties=pika.BasicProperties(
                    delivery_mode=pika.DeliveryMode.Persistent,
                    content_type="application/json",
                ),
            )
        log.info("[OK] Re-published %d job(s) back to '%s'.", len(kept_jobs), QUEUE_NAME)
    else:
        log.warning("No jobs matched the keep list — queue is now empty.")

    connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drain the grading_jobs queue and keep only jobs for specific userids"
    )
    parser.add_argument("--keep-userids", nargs="+", type=int, required=True,
                        help="Moodle userids whose jobs should be kept; everything else is discarded")
    parser.add_argument("--host", default="localhost", help="RabbitMQ host (default: localhost)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    filter_queue(args.host, set(args.keep_userids))


if __name__ == "__main__":
    main()
