# AI Grading Prototype

A Python service that automatically grades **essay questions within Moodle quizzes** using the Google Gemini AI model.

The system retrieves completed quiz attempts from Moodle, extracts essay responses while preserving automatically graded objective questions (such as multiple-choice and true/false), sends each essay to Gemini for grading through RabbitMQ, then writes the marks back into Moodle using a custom web service. Every grading attempt is also logged to the Grading Dashboard for monitoring and troubleshooting.

The complete grading pipeline is:

**Moodle Quiz → Producer → RabbitMQ → AI Worker → Moodle Gradebook**
**(with every attempt also logged to the Grading Dashboard)**

---

# Project Structure

```text
AI-Grading-Prototype/
├── worker.py
├── moodle_quiz_producer.py
├── moodle_client.py
├── grader.py
├── grading_logger.py
├── grading_guide_quiz7.json
├── output.json
├── grading_log.json
├── flagged_for_review.json
├── requirements.txt
├── .env
├── app.py                    # local mock of the Grading Dashboard, for dev/testing
├── test_send_log.py          # test client for the mock dashboard
└── README.md
```

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
* `--force` flag to re-queue and re-grade attempts regardless of current Moodle grading state
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

---

# Environment Variables

```text
GEMINI_API_KEY=xxxxxxxx

MOODLE_BASE_URL=http://localhost:8080

MOODLE_TOKEN=xxxxxxxx

# Grading Dashboard logging
# Points at the local mock server by default (app.py in this same folder).
# Swap both values for the real dashboard's endpoint/key once available —
# no code changes are needed elsewhere in the pipeline.
DASHBOARD_LOG_URL=http://localhost:5001/api/logs
API_KEY=xxxxxxxx
```

The Moodle token should belong to a dedicated service account with access only to the required grading web services.

---

# Usage

## Step 0 — (Local testing only) Start the mock Grading Dashboard

If the real Grading Dashboard isn't available yet, run the local mock server so grading logs have somewhere to go:

```bash
python app.py
```

Serves on `http://localhost:5001` by default, with a live-updating dashboard page at `http://localhost:5001/` and the log-ingestion endpoint at `http://localhost:5001/api/logs`. Leave this running in its own terminal alongside the worker.

## Step 1 — Produce grading jobs

```bash
python moodle_quiz_producer.py \
    --course-id 2 \
    --quiz-id 1 \
    --course-name "Business Strategy" \
    --quiz-name "Business Strategy Fundamentals Quiz" \
    --subject-area "Business Strategy" \
    --grading-guide grading_guide_quiz.json \
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

```bash
python worker.py --host localhost
```

The worker:

* receives jobs from RabbitMQ
* builds the Gemini prompt
* grades each essay
* writes grades back to Moodle
* recalculates the quiz total
* updates the Moodle gradebook
* logs the attempt (success or failure) to the Grading Dashboard

---

# Command Line Options

### moodle_quiz_producer.py

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

### worker.py

| Option            | Description                              |
| ----------------- | ----------------------------------------- |
| --host            | RabbitMQ host                             |
| --model           | Gemini model                              |
| --output          | Output JSON                               |
| --log             | Log JSON                                  |
| --flagged-log     | File for submissions flagged for human review |
| --pace-seconds    | Delay between grading calls (rate-limit pacing) |

Grading Dashboard settings (`DASHBOARD_LOG_URL`, `API_KEY`) are read from `.env`, not passed as CLI flags.

---

# Grading Workflow

```text
Completed Quiz Attempt
          │
          ▼
moodle_quiz_producer.py
          │
Extract essay questions
          │
Retrieve objective score
          │
Queue jobs
          ▼
RabbitMQ
          ▼
worker.py
          │
Gemini AI grading
          │
Essay marks returned
          ▼
local_grades_set_essay_grade
          │
Manual grading
          │
Quiz total recalculated
          ▼
Updated Moodle Gradebook
          │
          ▼
Grading Dashboard log entry
```

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
4. Essay marks are written back into Moodle.
5. Objective marks are added to essay marks.
6. Moodle updates the final quiz grade.
7. The attempt (success or failure) is logged to the Grading Dashboard.

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
```

Every grading attempt is also POSTed to the **Grading Dashboard** (`DASHBOARD_LOG_URL`), recording `status` (`success`/`fail`), `details` (outcome or error message), and `attempt` number — so grading activity can be monitored centrally rather than by reading local log files. Dashboard logging is non-blocking: if the dashboard is unreachable, grading continues and the failure is logged locally instead.

---

# RabbitMQ

The grading pipeline uses RabbitMQ to decouple submission retrieval from AI grading.

```
Producer
     │
     ▼
RabbitMQ Queue
     │
     ▼
Worker
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
     moodle_quiz_producer.py
                 │
                 ▼
             RabbitMQ
                 │
                 ▼
             worker.py
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
* Flask (local Grading Dashboard mock)
* python-dotenv
* JSON
* Docker (for Moodle)