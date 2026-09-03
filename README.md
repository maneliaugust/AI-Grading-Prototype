# AI Grading Prototype

A Python service that automatically grades **essay questions within Moodle quizzes** using the Google Gemini AI model.

The system retrieves completed quiz attempts from Moodle, extracts essay responses while preserving automatically graded objective questions (such as multiple-choice and true/false), sends each essay to Gemini for grading through RabbitMQ, then writes the marks back into Moodle using a custom web service. Every grading attempt is also logged to the Grading Dashboard for monitoring and troubleshooting.

The complete grading pipeline is:

**Moodle Quiz → Producer → RabbitMQ → AI Worker → Moodle Gradebook**
**(with every attempt also logged to the Grading Dashboard)**

> **Note:** the producer (`moodle_quiz_producer.py`), the Grading Dashboard mock (`app.py`), and related analysis/dev tooling now live in a separate companion repo, **[AI-Grading-Dashboard-Producer](../AI-Grading-Dashboard-Producer)**. This repo (`AI-Grading-Prototype`) contains only the grading worker itself and its direct dependencies. See [Companion Repo](#companion-repo) below.

---

# Project Structure

```text
AI-Grading-Prototype/
├── main.py                   # entry point — the grading worker (formerly worker.py)
├── moodle_client.py          # Moodle Web Services API client
├── grader.py                 # Gemini prompt building + grading logic
├── grading_logger.py         # local success/failure log helpers
├── requirements.txt
├── .env                       # not tracked in git — see Environment Variables below
├── .gitignore
└── README.md
```

Runtime-generated files (created automatically as the worker runs, **not tracked in git** — see [Logging](#logging)):

```text
output.json
grading_log.json
flagged_for_review.json
quiz_accumulator_state.json
```

---

# Companion Repo

**[AI-Grading-Dashboard-Producer](../AI-Grading-Dashboard-Producer)** contains everything that isn't the worker itself:

```text
AI-Grading-Dashboard-Producer/
├── moodle_quiz_producer.py   # pulls quiz attempts from Moodle, queues grading jobs
├── moodle_client.py           # copy — shared dependency, see note below
├── grading_guide_quiz7.json   # grading rubric(s), used only by the producer
├── app.py                     # local mock of the Grading Dashboard, for dev/testing
├── test_send_log.py           # test client for the mock dashboard
├── filter_queue.py            # queue inspection/dedup tooling
├── human_vs_ai_grades.py      # human-vs-AI grade comparison tooling
├── restore_human_grades.py    # recovery tooling for restoring human-graded marks
└── requirements.txt
```

`moodle_client.py` is duplicated (not shared as a package) between the two repos, since both the worker (for grade push-back) and the producer (for pulling attempts) depend on it directly. If you change one copy, mirror the change in the other.

---

# Features

* Retrieves completed Moodle quiz attempts
* Extracts essay responses from quiz questions
* Preserves objective question marks already graded by Moodle
* Uses Google Gemini to grade essays
* Supports grading multiple learners
* Pushes essay grades back into Moodle
* Recalculates the final quiz grade
* Writes grading logs and JSON audit reports
* **Persists every grading attempt (success or failure) to the Grading Dashboard**, so admins can monitor and troubleshoot grading activity without reading server logs directly
* **Dead-letter queue for failed jobs** — a failure grading a submission, or a failure pushing a grade back to Moodle, is never silently lost. The job is dead-lettered with an incremented attempt count and a "fail" report is sent to the dashboard; on the next worker startup, dead-lettered jobs are automatically moved back onto the main queue and retried. See [Dead-Letter Queue](#dead-letter-queue) below.
* `--force` flag (on the producer) to re-queue and re-grade attempts regardless of current Moodle grading state
* Uses RabbitMQ for asynchronous grading

---

# Prerequisites

| Requirement    | Version          |
| -------------- | ---------------- |
| Python         | 3.9+             |
| RabbitMQ       | Local or Docker  |
| Moodle         | 5.x              |
| Gemini API Key | Google AI Studio |
| Grading Dashboard API Key | Local mock (dev) or real dashboard (prod) |

---

# Installation

```bash
git clone <repository>

cd AI-Grading-Prototype

python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

The producer and dashboard mock have their own installation steps — see the [companion repo's README](../AI-Grading-Dashboard-Producer).

---

# Environment Variables

Add a `.env` file in the repo root (never commit this file — see [Secrets](#secrets)):

```text
GEMINI_API_KEY=xxxxxxxx

MOODLE_BASE_URL=http://localhost:8080
MOODLE_TOKEN=xxxxxxxx

# Grading Dashboard logging
# Points at the local mock server (app.py, in the companion repo) by default.
# Swap both values for the real dashboard's endpoint/key once available —
# no code changes are needed elsewhere in the pipeline.
DASHBOARD_LOG_URL=http://localhost:5001/api/logs
API_KEY=xxxxxxxx

# RabbitMQ
RABBITMQ_HOST=localhost
MQ_QUEUE_NAME=mqueue_grading_jobs
```

The Moodle token should belong to a dedicated service account with access only to the required grading web services.

`API_KEY` has no hardcoded fallback — if it's unset, the worker logs a startup warning and dashboard requests go out unauthenticated (and will likely be rejected). `RABBITMQ_HOST` defaults to the `--host` CLI flag if passed, falling back to this env var, falling back to `localhost`.

---

# Usage

## Step 0 — (Local testing only) Start the mock Grading Dashboard

If the real Grading Dashboard isn't available yet, run the local mock server (from the companion repo) so grading logs have somewhere to go:

```bash
cd ../AI-Grading-Dashboard-Producer
python app.py
```

Serves on `http://localhost:5001` by default, with a live-updating dashboard page at `http://localhost:5001/` and the log-ingestion endpoint at `http://localhost:5001/api/logs`. Leave this running in its own terminal alongside the worker.

## Step 1 — Produce grading jobs

From the companion repo:

```bash
cd ../AI-Grading-Dashboard-Producer
python moodle_quiz_producer.py \
    --course-id 2 \
    --quiz-id 1 \
    --course-name "Business Strategy" \
    --quiz-name "Business Strategy Fundamentals Quiz" \
    --subject-area "Business Strategy" \
    --grading-guide grading_guide_quiz7.json \
    --userids 4 5 6 \
    --host localhost
```

This command:

* retrieves quiz attempts
* extracts essay responses
* calculates the objective score
* queues each essay for grading

Add `--force` to re-queue essay slots regardless of whether they've already been graded (e.g. to re-grade with an updated rubric or prompt).

---

## Step 2 — Start the grading worker

From this repo:

```bash
python main.py --host localhost
```

The worker:

* moves any previously failed jobs from the dead-letter queue back onto the main queue for retry
* receives jobs from RabbitMQ
* builds the Gemini prompt
* grades each essay
* writes grades back to Moodle
* recalculates the quiz total
* updates the Moodle gradebook
* logs the attempt (success or failure) to the Grading Dashboard — only once the Moodle write is actually confirmed
* on any failure after grading (e.g. a Moodle write failure), dead-letters the job for automatic retry on next startup instead of losing it

---

# Command Line Options

### moodle_quiz_producer.py (companion repo)

| Option          | Description                |
| --------------- | -------------------------- |
| --course-id     | Moodle course ID           |
| --quiz-id       | Moodle quiz ID             |
| --course-name   | Course name                |
| --quiz-name     | Quiz name                  |
| --subject-area  | Subject area               |
| --grading-guide | JSON grading rubric        |
| --userids       | Moodle user IDs to process |
| --host          | RabbitMQ host               |
| --force         | Skip deduplication and re-queue all essay slots regardless of current grading state |

---

### main.py

| Option            | Description                              |
| ----------------- | ----------------------------------------- |
| --host            | RabbitMQ host (default: `$RABBITMQ_HOST` env var, or `localhost`) |
| --model           | Gemini model                              |
| --output          | Output JSON                               |
| --log             | Log JSON                                  |
| --flagged-log     | File for submissions flagged for human review |
| --pace-seconds    | Delay between grading calls (rate-limit pacing) |

Grading Dashboard settings (`DASHBOARD_LOG_URL`, `API_KEY`) and the queue name (`MQ_QUEUE_NAME`) are read from `.env`, not passed as CLI flags.

---

# Grading Workflow

```text
Completed Quiz Attempt
          │
          ▼
moodle_quiz_producer.py  (companion repo)
          │
Extract essay questions
          │
Retrieve objective score
          │
Queue jobs
          ▼
RabbitMQ
          ▼
main.py
          │
Gemini AI grading
          │
Essay marks held in memory until the whole attempt is graded
          │
Batch write: essay marks + total → local_grades_set_essay_grade
          │
Quiz total recalculated
          ▼
Updated Moodle Gradebook
          │
          ▼
Grading Dashboard log entry (only once the write above is confirmed)
```

Note: assignment submissions (as opposed to quiz essay questions) are pushed to Moodle immediately after grading, without the batching step above — batching only applies to quiz essay attempts, since a quiz's final grade depends on every essay in the attempt being graded together.

---

# Dead-Letter Queue

Any failure that happens **after** Gemini has already graded a submission — most importantly, a failure to write the grade back to Moodle — no longer silently reports "success" to the dashboard while losing the job. Instead:

* a **"fail"** report (with the real error) is sent to the dashboard,
* the message's `attempt` counter is incremented,
* the message is re-published to a dead-letter queue (`<queue_name>.dlq`) instead of being dropped,
* the original message is acknowledged off the main queue (a copy now lives safely in the DLQ).

On startup, before consuming anything new, every message sitting in the dead-letter queue is moved back onto the main queue, so failed jobs are automatically retried the next time the worker starts.

**Quiz essay batches:** since essay marks are held in memory until the whole attempt (N essays) is ready to write as one batch, a failure at that final write step means it isn't safe to assume which of the N essays did or didn't actually get written. All N held essays for that attempt are dead-lettered individually — not just the message that triggered the batch write — each with its own incremented attempt count and its own "fail" dashboard report. On retry, all N are re-graded and re-attempted as a fresh batch. This trades some extra Gemini API usage (essays that already graded successfully get re-graded on retry) for a simpler, safer retry model that doesn't need to detect a partial write.

**Flagged-for-review jobs are never dead-lettered.** A submission held back because `requires_human_review` is `true` is an intentional hold, not a failure — retrying it wouldn't fix a missing rubric or an ambiguous response, so it's left for a teacher to check instead.

---

# Secrets

`.env` is listed in `.gitignore` and must never be committed. If you rotate or regenerate any credential (Gemini API key, Moodle token, dashboard API key), update `.env` locally — there's no other place these values need to change.

If a credential is ever accidentally exposed (e.g. pasted somewhere outside this repo), rotate it immediately rather than assuming exposure alone is harmless.

---

# Moodle Web Services Required

Your Moodle external service should include:

```
mod_quiz_get_user_attempts

mod_quiz_get_attempt_review

core_user_get_users

core_webservice_get_site_info

local_grades_set_essay_grade
```

---

# Custom Moodle Plugin

This project includes a Moodle local plugin exposing the custom web service:

```
local_grades_set_essay_grade
```

The service:

* manually grades a single essay question
* updates the Question Engine
* recalculates the quiz grade
* synchronises the Moodle gradebook

---

# Grading Logic

For each learner:

1. Moodle grades objective questions automatically.
2. Essay questions are extracted.
3. Gemini grades each essay.
4. Once every essay in the attempt is graded, essay marks are written back into Moodle together, as one batch.
5. Objective marks are added to essay marks.
6. Moodle updates the final quiz grade.
7. The attempt (success or failure) is logged to the Grading Dashboard, once the write above is confirmed.

Final score:

```
Final Quiz Grade

=

Objective Score

+

Essay Score
```

---

# Example

Suppose a learner has:

Objective questions

```
9 / 15
```

Essay Question 1

```
4 / 5
```

Essay Question 2

```
8 / 10
```

Final quiz mark:

```
9 + 4 + 8 = 21 / 30
```

---

# Logging

The system records:

* successful grading
* failed grading
* AI feedback
* rubric scores
* Moodle synchronisation
* grading timestamps

Local logs are written to:

```
grading_log.json

output.json

flagged_for_review.json

quiz_accumulator_state.json
```

**These files are not tracked in git.** They contain real learner data (names, scores, AI-generated feedback text) and grow indefinitely as the worker runs — they're runtime output, not source, and are listed in `.gitignore` accordingly. If you need to share or back up grading history, do so as a deliberate, separate step (e.g. a scheduled export), not via git.

Every grading attempt is also POSTed to the **Grading Dashboard** (`DASHBOARD_LOG_URL`), recording `status` (`success`/`fail`), `details` (outcome or error message), and `attempt` number — so grading activity can be monitored centrally rather than by reading local log files. Dashboard logging is non-blocking: if the dashboard is unreachable, grading continues and the failure is logged locally instead.

---

# RabbitMQ

The grading pipeline uses RabbitMQ to decouple submission retrieval from AI grading, plus a dead-letter queue for automatic retry of failed jobs.

```
Producer (companion repo)
     │
     ▼
RabbitMQ Queue ◄──── on startup, drained back in ──── Dead-Letter Queue
     │                                                        ▲
     ▼                                                        │
Worker (main.py)  ── failure after grading ────────────────────┘
     │
     ▼
Gemini AI
     │
     ▼
Moodle
```

---

# Architecture

```text
            Moodle Quiz
                 │
                 ▼
     moodle_quiz_producer.py  (companion repo)
                 │
                 ▼
             RabbitMQ
                 │
                 ▼
              main.py
                 │
                 ▼
            Google Gemini
                 │
                 ▼
      local_grades Plugin
                 │
                 ▼
        Moodle Question Engine
                 │
                 ▼
         Moodle Gradebook
                 │
                 ▼
        Grading Dashboard
```

---

# Technologies

* Python
* Moodle Web Services REST API
* Moodle Question Engine
* RabbitMQ
* Google Gemini API
* BeautifulSoup
* Flask (local Grading Dashboard mock — companion repo)
* python-dotenv
* JSON
* Docker (for Moodle)