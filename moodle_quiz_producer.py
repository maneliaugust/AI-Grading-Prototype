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
        [--host localhost]

Each essay question in the quiz becomes a SEPARATE grading job per
learner — so if there are 2 essay questions and 2 learners, 4 jobs
are published.

Learner identification: each learner is keyed by their Moodle idnumber
if one is set on their account. If idnumber is blank, the learner's
Moodle username is used instead as a fallback so they aren't silently
skipped. Check the logs for "using username" warnings if you want to
know which learners fell back to username — worth setting proper
idnumbers on those accounts eventually for consistency downstream
(e.g. gradebook write-back, reporting).

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

# Question text for Q11 and Q12 — hardcoded here since the quiz question
# text is embedded in the attempt HTML and we already know what it is.
# Update this dict if you change the quiz questions in Moodle.
QUIZ_QUESTION_TEXTS = {
    11: 'In your own words, explain what "market segmentation" means and give one example of how a South African business uses it.',
    12: "Choose ONE of the following businesses: Capitec Bank, Pick n Pay, or MTN. Briefly describe the business strategy you think they use and explain why you believe it gives them a competitive advantage in South Africa.",
}


def load_grading_guide(path: str) -> list:
    """Load the grading rubric JSON array from a file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Grading guide not found: {path}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def build_jobs(
    quiz_id: int,
    course_name: str,
    quiz_name: str,
    subject_area: str,
    grading_guide_by_question: dict,
    learner_userids: list[int],
) -> list[dict]:
    """
    For each learner, fetch their finished quiz attempt, extract essay
    responses, and build one job per essay question per learner.
    """
    user_cache: dict = {}
    jobs = []

    for moodle_userid in learner_userids:
        # Prefer idnumber; fall back to username if idnumber isn't set
        # on this account, rather than skipping the learner entirely.
        learner_id, source = moodle_client.get_learner_id_for_userid(moodle_userid, user_cache)

        if source == "none":
            log.warning(
                "Skipping userid=%s — no idnumber or username available on this Moodle account.",
                moodle_userid,
            )
            continue

        if source == "username":
            log.warning(
                "userid=%s has no idnumber set — using username '%s' as learner_id instead.",
                moodle_userid, learner_id,
            )

        attempts = moodle_client.get_quiz_attempts(quiz_id, moodle_userid)
        if not attempts:
            log.warning("No finished attempts found for userid=%s", moodle_userid)
            continue

        # Use the most recent finished attempt
        latest_attempt = sorted(attempts, key=lambda a: a["timefinish"])[-1]
        attempt_id = latest_attempt["id"]
        log.info("Processing attempt %s for userid=%s (%s)", attempt_id, moodle_userid, learner_id)

        essay_responses = moodle_client.extract_essay_responses_from_attempt(attempt_id)

        # review_data = moodle_client._call(
        #     "mod_quiz_get_attempt_review",
        #     {"attemptid": attempt_id}
        #     )

        objective_score = moodle_client.get_objective_score_from_attempt(attempt_id)

        for essay in essay_responses:
            q_num = essay["question_number"]
            question_text = QUIZ_QUESTION_TEXTS.get(q_num, essay["question_text"])
            grading_guide = grading_guide_by_question.get(q_num, grading_guide_by_question.get("default", []))

            if not essay["response_text"]:
                log.warning(
                    "Skipping Q%s for userid=%s — no response text found.",
                    q_num, moodle_userid,
                )
                continue

            submission_payload = {
                "learner_id": learner_id,
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
                "_moodle_learner_id_source": source,  # "idnumber" or "username"
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
                learner_id, q_num, attempt_id,
            )

    return jobs


def publish_jobs(jobs: list[dict], rabbitmq_host: str = "localhost") -> None:
    """Publish job messages to the grading_jobs queue."""
    if not jobs:
        log.warning("No jobs to publish.")
        return

    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

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
        )
    except RuntimeError as e:
        log.error("Moodle API error: %s", e)
        sys.exit(1)

    try:
        publish_jobs(jobs, args.host)
    except pika.exceptions.AMQPConnectionError as e:
        log.error("Could not connect to RabbitMQ at '%s': %s", args.host, e)
        sys.exit(1)


if __name__ == "__main__":
    main()