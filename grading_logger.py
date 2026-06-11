"""
grading_logger.py - Structured logger for grading attempts.

Writes every grading attempt to a JSON log file with success/fail status.
Each log entry includes timestamp, learner_id, status, and error details if any.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

# Console logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Default log file path
DEFAULT_LOG_FILE = "grading_log.json"


def _load_log(log_path: str) -> list:
    """Load existing log entries from file."""
    p = Path(log_path)
    if not p.exists():
        return []
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_log(entries: list, log_path: str) -> None:
    """Save log entries to file."""
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def log_success(
    learner_id: str,
    course_name: str,
    assignment_name: str,
    score: float,
    max_grade: float,
    grade_label: str,
    requires_human_review: bool,
    log_path: str = DEFAULT_LOG_FILE,
) -> None:
    """Log a successful grading attempt."""
    entry = {
        "timestamp":             datetime.now(timezone.utc).isoformat() + "Z",
        "status":                "success",
        "learner_id":            learner_id,
        "course_name":           course_name,
        "assignment_name":       assignment_name,
        "score":                 score,
        "max_grade":             max_grade,
        "grade_label":           grade_label,
        "requires_human_review": requires_human_review,
        "error":                 None,
    }
    entries = _load_log(log_path)
    entries.append(entry)
    _save_log(entries, log_path)
    log.info("[LOG:success] %s -> %s/%s (%s)", learner_id, score, max_grade, grade_label)


def log_failure(
    learner_id: str,
    course_name: str,
    assignment_name: str,
    error_message: str,
    log_path: str = DEFAULT_LOG_FILE,
) -> None:
    """Log a failed grading attempt."""
    entry = {
        "timestamp":             datetime.now(timezone.utc).isoformat() + "Z",
        "status":                "fail",
        "learner_id":            learner_id,
        "course_name":           course_name,
        "assignment_name":       assignment_name,
        "score":                 None,
        "max_grade":             None,
        "grade_label":           None,
        "requires_human_review": True,
        "error":                 error_message,
    }
    entries = _load_log(log_path)
    entries.append(entry)
    _save_log(entries, log_path)
    log.error("[LOG:fail] %s -> %s", learner_id, error_message)