"""
moodle_client.py - Moodle Web Services API client for the AI Grading Prototype.

Wraps all Moodle REST calls used by moodle_producer.py and the grading
result push-back step. Keeps every Moodle-specific detail (token, base URL,
function names, file download quirks) in one place so the rest of the
pipeline (producer.py / worker.py / grader.py) never has to know Moodle
exists.

Environment variables expected (add to your .env):
    MOODLE_BASE_URL=http://localhost:8080
    MOODLE_TOKEN=your_api_gradingbot_token
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional
from bs4 import BeautifulSoup
import re


import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

MOODLE_BASE_URL = os.getenv("MOODLE_BASE_URL", "http://localhost:8080")
MOODLE_TOKEN = os.getenv("MOODLE_TOKEN")
REST_ENDPOINT = f"{MOODLE_BASE_URL}/webservice/rest/server.php"


def _check_token() -> None:
    if not MOODLE_TOKEN:
        raise RuntimeError(
            "MOODLE_TOKEN not found. Add it to your .env file "
            "(this should be the API GradingBot token, not the admin token)."
        )


def _call(wsfunction: str, params: dict, method: str = "GET") -> dict:
    """Low-level helper for any Moodle REST web service call."""
    _check_token()
    payload = {
        "wstoken": MOODLE_TOKEN,
        "wsfunction": wsfunction,
        "moodlewsrestformat": "json",
        **params,
    }

    if method == "POST":
        response = requests.post(REST_ENDPOINT, data=payload)
    else:
        response = requests.get(REST_ENDPOINT, params=payload)

    response.raise_for_status()
    data = response.json()

    # Moodle returns errors as 200 OK with an "exception" key, not HTTP errors
    if isinstance(data, dict) and "exception" in data:
        raise RuntimeError(
            f"Moodle error calling {wsfunction}: "
            f"{data.get('errorcode')} — {data.get('message')}"
        )

    return data


# ---------------------------------------------------------------------------
# Assignments & submissions
# ---------------------------------------------------------------------------

def get_assignments(course_id: int) -> list[dict]:
    """Return all assignments for a given course id."""
    data = _call(
        "mod_assign_get_assignments",
        {"courseids[0]": course_id},
    )
    courses = data.get("courses", [])
    if not courses:
        return []
    return courses[0].get("assignments", [])


def get_submissions(assignment_id: int, only_ungraded: bool = True) -> list[dict]:
    """
    Return submissions for a given assignment id.
    If only_ungraded=True, filters out submissions already marked 'graded'.
    """
    data = _call(
        "mod_assign_get_submissions",
        {"assignmentids[0]": assignment_id},
    )
    assignments = data.get("assignments", [])
    if not assignments:
        return []

    submissions = assignments[0].get("submissions", [])

    if only_ungraded:
        submissions = [
            s for s in submissions
            if s.get("gradingstatus") != "graded" and s.get("status") == "submitted"
        ]

    log.info("Found %d submission(s) for assignment %s", len(submissions), assignment_id)
    return submissions


def extract_file_url(submission: dict) -> Optional[str]:
    """Pull the first file submission URL out of a submission record, if any."""
    for plugin in submission.get("plugins", []):
        if plugin.get("type") != "file":
            continue
        for area in plugin.get("fileareas", []):
            files = area.get("files", [])
            if files:
                return files[0]["fileurl"]
    return None


# ---------------------------------------------------------------------------
# File download + text extraction
# ---------------------------------------------------------------------------

def download_submission_file(file_url: str, save_path: str) -> str:
    """Download a Moodle submission file using the token-based pluginfile download."""
    _check_token()
    response = requests.get(file_url, params={"token": MOODLE_TOKEN})
    response.raise_for_status()

    p = Path(save_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(response.content)

    log.info("Downloaded submission file -> %s", save_path)
    return save_path


def extract_pdf_text(pdf_path: str) -> str:
    """Extract plain text from a downloaded PDF submission."""
    import pypdf  # local import keeps the dependency optional for non-PDF flows

    reader = pypdf.PdfReader(pdf_path)
    text_parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(text_parts).strip()


def get_submission_text(submission: dict, tmp_dir: str = "tmp_submissions") -> str:
    """
    Convenience wrapper: given a submission record, download its file
    (if any) and return the extracted text. Returns "" if no file found.
    """
    file_url = extract_file_url(submission)
    if not file_url:
        log.warning("No file found for submission id=%s userid=%s", submission.get("id"), submission.get("userid"))
        return ""

    filename = file_url.rstrip("/").split("/")[-1]
    save_path = str(Path(tmp_dir) / f"{submission['userid']}_{filename}")
    download_submission_file(file_url, save_path)
    return extract_pdf_text(save_path)


# ---------------------------------------------------------------------------
# User lookup (idnumber / username <-> Moodle userid mapping)
# ---------------------------------------------------------------------------

def get_user_by_idnumber(idnumber: str) -> Optional[dict]:
    """Look up a Moodle user by their institutional ID number (e.g. u12345678)."""
    data = _call(
        "core_user_get_users",
        {
            "criteria[0][key]": "idnumber",
            "criteria[0][value]": idnumber,
        },
    )
    users = data.get("users", [])
    return users[0] if users else None


def _get_user_record(userid: int, user_cache: Optional[dict] = None) -> Optional[dict]:
    """
    Internal helper: Moodle userid -> full user record (dict), cached.

    Pass a user_cache dict ({userid: user_record}) to avoid repeat API
    calls when processing many submissions from the same batch. This
    cache stores the FULL user dict (not just idnumber), so both
    get_idnumber_for_userid() and get_username_for_userid() can share
    a single lookup per user.

    NOTE: if you were previously relying on user_cache holding a bare
    idnumber string (e.g. reading user_cache[userid] directly elsewhere),
    that call site needs updating too — it now holds a dict.
    """
    if user_cache is not None and userid in user_cache:
        return user_cache[userid]

    data = _call(
        "core_user_get_users",
        {
            "criteria[0][key]": "id",
            "criteria[0][value]": userid,
        },
    )
    users = data.get("users", [])
    record = users[0] if users else None

    if user_cache is not None:
        user_cache[userid] = record

    return record


def get_idnumber_for_userid(userid: int, user_cache: Optional[dict] = None) -> Optional[str]:
    """
    Reverse lookup: Moodle userid -> idnumber (e.g. u12345678).
    Returns None if the account has no idnumber set.
    """
    record = _get_user_record(userid, user_cache)
    return record.get("idnumber") if record else None


def get_username_for_userid(userid: int, user_cache: Optional[dict] = None) -> Optional[str]:
    """
    Reverse lookup: Moodle userid -> username (e.g. anelisa-mjoni).
    Useful as a fallback learner_id when idnumber isn't set.
    """
    record = _get_user_record(userid, user_cache)
    return record.get("username") if record else None


def get_learner_id_for_userid(userid: int, user_cache: Optional[dict] = None) -> tuple[Optional[str], str]:
    """
    Convenience wrapper for producer scripts: returns the best available
    learner identifier for a Moodle userid, preferring idnumber and
    falling back to username.

    Returns:
        (learner_id, source) where source is "idnumber", "username", or
        "none" (learner_id will be None in the "none" case).
    """
    record = _get_user_record(userid, user_cache)
    if not record:
        return None, "none"

    idnumber = record.get("idnumber")
    if idnumber:
        return idnumber, "idnumber"

    username = record.get("username")
    if username:
        return username, "username"

    return None, "none"


# ---------------------------------------------------------------------------
# Grade push-back
# ---------------------------------------------------------------------------

def save_grade(
    assignment_id: int,
    userid: int,
    grade: float,
    feedback_html: str = "",
    attempt_number: int = -1,
) -> None:
    """Push a grade + feedback comment back to Moodle for one learner."""
    params = {
        "assignmentid": assignment_id,
        "userid": userid,
        "grade": grade,
        "attemptnumber": attempt_number,
        "addattempt": 0,
        "workflowstate": "graded",
        "applytoall": 0,
        "plugindata[assignfeedbackcomments_editor][text]": feedback_html,
        "plugindata[assignfeedbackcomments_editor][format]": 1,
    }
    _call("mod_assign_save_grade", params, method="POST")
    log.info("Saved grade %s for userid=%s on assignment=%s", grade, userid, assignment_id)


#---------------------------------------------------------------------------
# Quiz functions (add to moodle_client.py)
# ---------------------------------------------------------------------------

def get_quiz_id_by_course(course_id: int) -> Optional[dict]:
    """Return the first quiz found in a course, with its id and name."""
    data = _call(
        "mod_quiz_get_quizzes_by_courses",
        {"courseids[0]": course_id},
    )
    quizzes = data.get("quizzes", [])
    return quizzes[0] if quizzes else None


def get_quiz_attempts(quiz_id: int, userid: int) -> list[dict]:
    """Return all finished attempts for a given user on a quiz."""
    data = _call(
        "mod_quiz_get_user_quiz_attempts",
        {
            "quizid": quiz_id,
            "userid": userid,
            "status": "finished",
        },
    )
    return data.get("attempts", [])


def extract_essay_responses_from_attempt(attempt_id: int) -> list[dict]:
    """
    Fetch a finished quiz attempt and extract essay question responses.

    Returns a list of dicts, one per essay question:
        {
            "slot": int,           # question slot number (1-based)
            "question_number": int,
            "question_text": str,  # the question prompt, plain text
            "response_text": str,  # the learner's typed answer, plain text
            "max_marks": float,
        }
    """
    from bs4 import BeautifulSoup

    data = _call(
        "mod_quiz_get_attempt_review",
        {"attemptid": attempt_id},
    )

    essay_responses = []

    for q in data.get("questions", []):
        if q.get("type") != "essay":
            continue
        if q.get("state") not in ("needsgrading", "manualgraded", "complete"):
            continue

        html = q.get("html", "")
        soup = BeautifulSoup(html, "html.parser")

        # Extract question text (inside .qtext div)
        qtext_div = soup.select_one(".qtext")
        question_text = qtext_div.get_text(separator=" ", strip=True) if qtext_div else ""

        # Extract learner response. In a finished/reviewed attempt, Moodle
        # renders the essay answer as read-only HTML inside a div with
        # class "qtype_essay_response" (confirmed against live Moodle 5.3
        # output: role="textbox" aria-readonly="true" ... class="qtype_essay_editor
        # qtype_essay_response readonly"). A <textarea> only appears while
        # an attempt is still in progress and editable, so we try that as
        # a fallback for completeness rather than relying on it.
        response_text = ""

        response_div = soup.select_one(".qtype_essay_response")
        if response_div:
            response_text = response_div.get_text(separator=" ", strip=True)

        if not response_text:
            pre_tag = soup.find("pre")
            if pre_tag:
                response_text = pre_tag.get_text(strip=True)

        if not response_text:
            textarea = soup.find("textarea")
            if textarea:
                response_text = textarea.get_text(strip=True)

        if not response_text:
            answer_div = soup.select_one(".answer")
            if answer_div:
                response_text = answer_div.get_text(strip=True)


        # Extract max marks from the grade div e.g. "Marked out of 10.00"
        grade_div = soup.select_one(".grade")
        max_marks = 0.0
        if grade_div:
            import re
            match = re.search(r"out of\s+([\d.]+)", grade_div.get_text())
            if match:
                max_marks = float(match.group(1))

        essay_responses.append({
            "slot": q.get("slot"),
            "question_number": q.get("number"),
            "question_text": question_text,
            "response_text": response_text,
            "max_marks": max_marks,
        })

    log.info(
        "Extracted %d essay response(s) from attempt %s",
        len(essay_responses), attempt_id,
    )
    return essay_responses


def save_quiz_essay_grade(
    attempt_id: int,
    slot: int,
    grade: float,
    feedback_html: str = "",
) -> dict:
    """
    Grade one essay question using the custom Moodle web service.

    Returns a dict combining Moodle's response with the feedback that
    was sent, so the caller can access the feedback text alongside the
    grade result instead of it being discarded:

        {
            "attemptid": int,
            "slot": int,
            "mark": float,
            "maxmark": float,
            "sumgrades": float,
            "quizgrade": float,
            "status": str,
            "feedback": str,   # <- the feedback_html that was sent
        }

    NOTE: Moodle's local_grades_set_essay_grade web service itself does
    NOT echo feedback text back in its response (its execute_returns()
    only defines attemptid/slot/mark/maxmark/sumgrades/quizgrade/status)
    — so "feedback" here is attached locally from the `feedback_html`
    argument, not round-tripped through the API. If you need to confirm
    what Moodle actually stored (rather than what was sent), that would
    require a separate read call, which isn't implemented here.
    """

    params = {
        "attemptid": attempt_id,
        "slot": slot,
        "grade": grade,
        "feedback": feedback_html,
        "feedbackformat": 1,
    }

    result = _call(
        "local_grades_set_essay_grade",
        params,
        method="POST",
    )

    result["feedback"] = feedback_html

    log.info(
        "Essay question graded: attempt=%s slot=%s result=%s",
        attempt_id, slot, result,
    )

    return result

def get_objective_score_from_attempt(attempt_id: int) -> float:
    """
    Calculate the total score from auto-graded questions (MCQ, True/False)
    in a finished quiz attempt, excluding essay questions.
    """

    data = _call(
        "mod_quiz_get_attempt_review",
        {"attemptid": attempt_id},
    )

    objective_score = 0.0

    for q in data.get("questions", []):
        if q.get("type") in ("essay",):
            continue  # skip essay questions

        # Extract marks from the HTML
        html = q.get("html", "")
        soup = BeautifulSoup(html, "html.parser")
        grade_div = soup.select_one(".grade")
        if grade_div:
            match = re.search(r"Mark\s+([\d.]+)\s+out of", grade_div.get_text())
            if match:
                objective_score += float(match.group(1))

    log.info("Objective score for attempt %s: %s", attempt_id, objective_score)
    return objective_score