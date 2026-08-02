"""
Quick test client for the local Grading Dashboard server.

Run the server first (python app.py), then run this script in another
terminal to fire a few sample log entries at it -- one success, one fail.

This mirrors the call you'd add inside worker.py right after a grading
attempt completes (success) or raises (fail).
"""

import requests

DASHBOARD_URL = "http://localhost:5001/api/logs"
API_KEY = "test-api-key-123"  # must match API_KEY in app.py / env var


def send_log(data: str, status: str, details: str, attempt: int):
    resp = requests.post(
        DASHBOARD_URL,
        headers={
            "apiKey": API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "data": data,
            "status": status,
            "details": details,
            "attempt": attempt,
        },
        timeout=5,
    )
    print(f"[{resp.status_code}] {resp.json()}")
    return resp


if __name__ == "__main__":
    # Example: a successful grading run
    send_log(
        data="quiz7/cmid=76/user=101",
        status="success",
        details="Graded via Gemini, score=18/20",
        attempt=1,
    )

    # Example: a failed grading run (e.g. Gemini timeout, bad rubric match)
    send_log(
        data="quiz7/cmid=76/user=104",
        status="fail",
        details="Gemini API timeout after 30s",
        attempt=2,
    )

    # Example: intentionally bad request to see validation errors
    send_log(
        data="quiz7/cmid=76/user=999",
        status="unknown",  # invalid -> should be rejected with 400
        details="bad status test",
        attempt=1,
    )