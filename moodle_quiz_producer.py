"""
moodle_quiz_producer.py - Pulls essay question responses from finished
Moodle quiz attempts and publishes them to the same RabbitMQ
'grading_jobs' queue that worker.py already consumes from.

This is the quiz equivalent of moodle_producer.py. worker.py and
grader.py require NO changes — they consume the same generic message
shape regardless of whether the job came from an Assignment or a Quiz.

Usage:
    python moodle_quiz_producer.py \\
        --course-id 4 \\
        --quiz-id 1 \\
        --course-name "Business Strategy" \\
        --quiz-name "Business Strategy Fundamentals Quiz" \\
        --subject-area "Business Strategy" \\
        --grading-guide grading_guide_quiz.json \\
        [--host localhost] \\
        [--force]

Each essay question in the quiz becomes a SEPARATE grading job per
learner — so if there are 2 essay questions and 2 learners, 4 jobs
are published.

DEDUPLICATION — two layers, catching two different situations:

    1. ALREADY GRADED (Moodle-state check): moodle_client's
       extract_essay_responses_from_attempt() already skips any essay
       slot whose Moodle state is no longer "needsgrading" (e.g. it was
       already graded by a previous run, or a teacher graded it
       manually). This protects you if you re-run the producer AFTER
       a previous batch has finished grading. --force now also bypasses
       THIS check (passed through to extract_essay_responses_from_attempt),
       not just the queue-depth check below — previously --force only
       skipped the local slot_states check in this file, so an
       already-graded attempt (state e.g. "mangrright", "mangrpartial")
       would still silently return 0 essay responses even with --force.

    2. STILL BEING PROCESSED (queue-depth check): if you re-run the
       producer WHILE the worker is still mid-batch on a previous run,
       nothing has been graded in Moodle yet — so check #1 above can't
       catch it, since everything still looks "needsgrading". Instead,
       publish_jobs() checks RabbitMQ's own queue depth before
       publishing: if there are still unprocessed messages waiting, it
       refuses to publish and tells you to wait, rather than silently
       adding duplicates on top of an unfinished batch.

    Use --force to bypass BOTH checks and queue everything again
    regardless of current state (e.g. if you deliberately want to
    re-grade with an updated rubric/prompt, or you're certain the
    queue depth reading is stale).

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

QUEUE_NAME = "mqueue_grading_jobs"

# Question text for Q11 and Q12 — hardcoded here since the quiz question
# text is embedded in the attempt HTML and we already know what it is.
# Update this dict if you change the quiz questions in Moodle.
QUIZ_QUESTION_TEXTS = {
    11: 'In your own words, explain what "market segmentation" means and give one example of how a South African business uses it.',
    12: "Choose ONE of the following businesses: Capitec Bank, Pick n Pay, or MTN. Briefly describe the business strategy you think they use and explain why you believe it gives them a competitive advantage in South Africa.",
}

# States that mean "this question has already been graded" (either by a
# previous AI run or by a teacher manually). Anything NOT in this "still
# needs grading" state gets skipped unless --force is passed.
STILL_NEEDS_GRADING_STATE = "needsgrading"


def load_grading_guide(path: str) -> list:
    """Load the grading rubric JSON array from a file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Grading guide not found: {path}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def get_slot_states(attempt_id: int) -> dict:
    """
    Fetch the current grading state of every question slot in an attempt,
    via mod_quiz_get_attempt_review (already available to the token used
    by this pipeline). Returns {slot_number: state_string}.

    Used to skip essay slots that have already been graded, so re-running
    the producer doesn't queue duplicate jobs for the same slot.
    """
    try:
        review_data = moodle_client._call(
            "mod_quiz_get_attempt_review",
            {"attemptid": attempt_id},
        )
    except Exception as e:
        log.warning(
            "Could not fetch attempt review for attempt=%s (%s) — "
            "proceeding WITHOUT deduplication for this attempt.",
            attempt_id, e,
        )
        return {}

    states = {}
    for q in review_data.get("questions", []):
        slot = q.get("slot")
        state = q.get("state")
        if slot is not None:
            states[slot] = state
    return states


def build_jobs(
    quiz_id: int,
    course_name: str,
    quiz_name: str,
    subject_area: str,
    grading_guide_by_question: dict,
    learner_userids: list[int],
    force: bool = False,
) -> list[dict]:
    """
    For each learner, fetch their finished quiz attempt, extract essay
    responses, and build one job per essay question per learner —
    skipping any essay slot that's already been graded (unless --force).
    """
    user_cache: dict = {}
    jobs = []
    skipped_count = 0

    for moodle_userid in learner_userids:
        idnumber = moodle_client.get_idnumber_for_userid(moodle_userid, user_cache)
        if not idnumber:
            log.warning(
                "Skipping userid=%s — no idnumber set on this Moodle account.",
                moodle_userid,
            )
            continue

        attempts = moodle_client.get_quiz_attempts(quiz_id, moodle_userid)
        if not attempts:
            log.warning("No finished attempts found for userid=%s", moodle_userid)
            continue

        # Use the most recent finished attempt
        latest_attempt = sorted(attempts, key=lambda a: a["timefinish"])[-1]
        attempt_id = latest_attempt["id"]
        log.info("Processing attempt %s for userid=%s (%s)", attempt_id, moodle_userid, idnumber)

        # --- Deduplication: check current state of every slot up front ---
        if force:
            slot_states = {}
            log.info("  --force passed: skipping deduplication check for this attempt.")
        else:
            slot_states = get_slot_states(attempt_id)

        # NOTE: force is now also passed through here — previously this
        # call had no force parameter at all, so extract_essay_responses_
        # from_attempt()'s own internal state filter (which excludes
        # already-graded states like "mangrright"/"mangrpartial", not
        # just the literal string "needsgrading") would still silently
        # drop every essay response for an already-graded attempt, even
        # when --force was passed on the command line.
        essay_responses = moodle_client.extract_essay_responses_from_attempt(
            attempt_id, force=force
        )

        objective_score = moodle_client.get_objective_score_from_attempt(attempt_id)

        for essay in essay_responses:
            q_num = essay["question_number"]
            slot = essay.get("slot", q_num)

            # Skip slots already graded, unless --force
            if not force:
                current_state = slot_states.get(slot)
                if current_state is not None and current_state != STILL_NEEDS_GRADING_STATE:
                    log.info(
                        "  [skip] Q%s (slot=%s) for userid=%s already graded "
                        "(state=%s) — not re-queuing.",
                        q_num, slot, moodle_userid, current_state,
                    )
                    skipped_count += 1
                    continue

            question_text = QUIZ_QUESTION_TEXTS.get(q_num, essay["question_text"])
            grading_guide = grading_guide_by_question.get(q_num, grading_guide_by_question.get("default", []))

            if not essay["response_text"]:
                log.warning(
                    "Skipping Q%s for userid=%s — no response text found.",
                    q_num, moodle_userid,
                )
                continue

            submission_payload = {
                "learner_id": idnumber,
                "question_text": question_text,
                "learner_response": essay["response_text"],
                "max_grade": essay["max_marks"],
                "grading_guide": grading_guide,
                # Internal Moodle fields for reference/audit
                "_moodle_userid": moodle_userid,
                "_moodle_quiz_id": quiz_id,
                "_moodle_attempt_id": attempt_id,
                "_moodle_slot": essay["slot"],
                "_moodle_question_number": q_num,
                "_moodle_objective_score": objective_score,
                # Note: quiz essay grades are pushed back manually/via
                # Moodle's gradebook rather than per-slot REST — see
                # moodle_client_quiz_additions.py for details.
            }

            jobs.append({
                "course_name": course_name,
                "assignment_name": f"{quiz_name} — Q{q_num}",
                "subject_area": subject_area,
                "submission": submission_payload,
            })
            log.info(
                "  [+] Job queued: %s | Q%s | attempt=%s",
                idnumber, q_num, attempt_id,
            )

    if skipped_count:
        log.info(
            "Deduplication: skipped %d already-graded slot(s). "
            "Use --force to re-queue everything regardless of current state.",
            skipped_count,
        )

    return jobs


def publish_jobs(jobs: list[dict], rabbitmq_host: str = "localhost", force: bool = False) -> None:
    """
    Publish job messages to the grading_jobs queue.

    Before publishing, checks whether the queue already has messages
    waiting (i.e. a previous producer run's jobs haven't all been
    consumed by the worker yet). This catches the race condition where
    you re-run the producer while the worker is still mid-batch — the
    Moodle-side state check alone can't catch this, since nothing has
    been graded yet from Moodle's point of view, so it looks identical
    to a fresh run.

    Use --force to publish anyway even if the queue isn't empty.
    """
    if not jobs:
        log.warning("No jobs to publish.")
        return

    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()

    # passive=True just checks the existing queue's state without
    # creating/modifying it, and gives us message_count.
    try:
        declare_result = channel.queue_declare(queue=QUEUE_NAME, durable=True, passive=True)
        pending_count = declare_result.method.message_count
    except Exception:
        # Queue doesn't exist yet (first-ever run) — nothing pending.
        connection.close()
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE_NAME, durable=True)
        pending_count = 0

    if pending_count > 0 and not force:
        log.error(
            "[BLOCKED] Queue '%s' already has %d unprocessed message(s) waiting. "
            "This usually means a previous producer run's jobs haven't all been "
            "graded yet by the worker — publishing now would create duplicates.",
            QUEUE_NAME, pending_count,
        )
        log.error(
            "Wait for the worker to finish (check its log output / queue depth "
            "in the RabbitMQ management UI), or pass --force to publish anyway."
        )
        connection.close()
        raise SystemExit(1)

    if pending_count > 0 and force:
        log.warning(
            "[FORCED] Queue already has %d unprocessed message(s), but --force "
            "was passed — publishing anyway. This WILL create duplicate jobs "
            "for anything still unprocessed.",
            pending_count,
        )

    for job in jobs:
        learner_id = job["submission"]["learner_id"]
        assignment_name = job["assignment_name"]
        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(job),
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type="application/json",
            ),
        )
        log.info("[->] Queued: %s | %s", learner_id, assignment_name)

    log.info("[OK] %d grading job(s) submitted to '%s' queue.", len(jobs), QUEUE_NAME)
    connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull quiz essay responses from Moodle and queue them for grading"
    )
    parser.add_argument("--course-id", type=int, required=True, help="Moodle course id")
    parser.add_argument("--quiz-id", type=int, required=True, help="Moodle quiz id")
    parser.add_argument("--course-name", required=True, help="Course name for grading prompt")
    parser.add_argument("--quiz-name", required=True, help="Quiz name for grading prompt")
    parser.add_argument("--subject-area", required=True, help="Subject area for grading prompt")
    parser.add_argument("--grading-guide", required=True, help="Path to grading guide JSON file")
    parser.add_argument("--userids", nargs="+", type=int, default=[5, 6],
                        help="Moodle user IDs to process (default: 5 6)")
    parser.add_argument("--host", default="localhost", help="RabbitMQ host (default: localhost)")
    parser.add_argument("--force", action="store_true",
                        help="Skip deduplication check and queue all essay slots regardless "
                             "of whether they've already been graded")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        raw_guide = load_grading_guide(args.grading_guide)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.error("Failed to load grading guide: %s", e)
        sys.exit(1)

    # The grading guide can be either:
    # - A dict keyed by question number: {11: [...], 12: [...]}
    # - A flat list (applied to all essay questions as "default")
    if isinstance(raw_guide, dict):
        grading_guide_by_question = {int(k): v for k, v in raw_guide.items()}
    else:
        grading_guide_by_question = {"default": raw_guide}

    try:
        jobs = build_jobs(
            quiz_id=args.quiz_id,
            course_name=args.course_name,
            quiz_name=args.quiz_name,
            subject_area=args.subject_area,
            grading_guide_by_question=grading_guide_by_question,
            learner_userids=args.userids,
            force=args.force,
        )
    except RuntimeError as e:
        log.error("Moodle API error: %s", e)
        sys.exit(1)

    try:
        publish_jobs(jobs, args.host, force=args.force)
    except pika.exceptions.AMQPConnectionError as e:
        log.error("Could not connect to RabbitMQ at '%s': %s", args.host, e)
        sys.exit(1)


if __name__ == "__main__":
    main()
