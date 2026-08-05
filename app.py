"""
Grading Dashboard - local test server
--------------------------------------
A minimal Flask app that mimics the Grading Dashboard's log-ingestion API.
Autograder (worker.py) can POST grading execution logs here while you
develop/test, instead of hitting the real dashboard backend.

Endpoints:
  POST /api/logs   -> ingest a grading log entry (this is what the autograder calls)
  GET  /api/logs   -> list stored log entries as JSON (?status=success|fail, ?limit=N)
  GET  /           -> simple HTML dashboard to eyeball the logs while testing

Run:
  pip install -r requirements.txt
  python app.py

Server listens on http://localhost:5001 by default.
"""

import os
import sqlite3
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, render_template_string

APP_PORT = int(os.environ.get("PORT", 5001))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grading_logs.db")

# Set this to whatever your autograder sends in the "apiKey" header.
# Override at runtime with: set API_KEY=your-key (Windows) / export API_KEY=your-key
VALID_API_KEY = os.environ.get("API_KEY", "test-api-key-123")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS grading_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            status TEXT NOT NULL CHECK(status IN ('success', 'fail')),
            details TEXT,
            attempt INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# POST /api/logs  -- what the autograder sends to
# ---------------------------------------------------------------------------
@app.route("/api/logs", methods=["POST"])
def create_log():
    # --- auth check ---
    api_key = request.headers.get("apiKey")
    if not api_key or api_key != VALID_API_KEY:
        return jsonify({"error": "Unauthorized: missing or invalid apiKey header"}), 401

    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Invalid or empty JSON body"}), 400

    # --- schema validation ---
    errors = []
    data = payload.get("data")
    status = payload.get("status")
    details = payload.get("details")
    attempt = payload.get("attempt")

    if data is not None and not isinstance(data, str):
        errors.append("`data` must be a string")
    if status not in ("success", "fail"):
        errors.append("`status` must be 'success' or 'fail'")
    if details is not None and not isinstance(details, str):
        errors.append("`details` must be a string")
    if not isinstance(attempt, int) or isinstance(attempt, bool):
        errors.append("`attempt` must be a number")

    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    created_at = datetime.now().astimezone().isoformat()

    db = get_db()
    cur = db.execute(
        "INSERT INTO grading_logs (data, status, details, attempt, created_at) VALUES (?, ?, ?, ?, ?)",
        (data, status, details, attempt, created_at),
    )
    db.commit()

    print(f"[LOG RECEIVED] id={cur.lastrowid} status={status} attempt={attempt} details={details}")

    return jsonify({"message": "Log recorded", "id": cur.lastrowid, "created_at": created_at}), 201


# ---------------------------------------------------------------------------
# GET /api/logs  -- inspect what's been stored (handy for testing)
# ---------------------------------------------------------------------------
@app.route("/api/logs", methods=["GET"])
def list_logs():
    status_filter = request.args.get("status")
    limit = request.args.get("limit", default=50, type=int)

    query = "SELECT * FROM grading_logs"
    params = []
    if status_filter in ("success", "fail"):
        query += " WHERE status = ?"
        params.append(status_filter)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    db = get_db()
    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /  -- tiny HTML dashboard for eyeballing logs while you test
# ---------------------------------------------------------------------------
DASHBOARD_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Grading Dashboard (local test server)</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #f7f7f8; }
    h1 { font-size: 1.3rem; }
    table { border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    th, td { border-bottom: 1px solid #eee; padding: 8px 12px; text-align: left; font-size: 0.85rem; vertical-align: top; }
    th { background: #fafafa; }
    .success { color: #0a7d2c; font-weight: 600; }
    .fail { color: #c0292a; font-weight: 600; }
    .meta { color: #666; font-size: 0.8rem; margin-bottom: 1rem; }
    code { background: #eee; padding: 2px 5px; border-radius: 3px; }
  </style>
</head>
<body>
  <h1>Grading Dashboard &mdash; local test server</h1>
  <p class="meta">POST logs to <code>http://localhost:{{ port }}/api/logs</code> &middot; auto-refreshes every 5s &middot; {{ count }} entries</p>
  <table>
    <tr><th>ID</th><th>Status</th><th>Attempt</th><th>Data</th><th>Details</th><th>Created At (UTC)</th></tr>
    {% for row in rows %}
    <tr>
      <td>{{ row['id'] }}</td>
      <td class="{{ row['status'] }}">{{ row['status'] }}</td>
      <td>{{ row['attempt'] }}</td>
      <td>{{ row['data'] }}</td>
      <td>{{ row['details'] }}</td>
      <td>{{ row['created_at'] }}</td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def dashboard():
    db = get_db()
    rows = db.execute("SELECT * FROM grading_logs ORDER BY id DESC LIMIT 200").fetchall()
    return render_template_string(DASHBOARD_TEMPLATE, rows=rows, count=len(rows), port=APP_PORT)


if __name__ == "__main__":
    init_db()
    print(f"Grading Dashboard test server running at http://localhost:{APP_PORT}")
    print(f"POST logs to  http://localhost:{APP_PORT}/api/logs")
    print(f"Required header: apiKey: {VALID_API_KEY}")
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)