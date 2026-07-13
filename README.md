# AI Grading Prototype

A Python service that automatically grades **essay questions within Moodle quizzes** using the Google Gemini AI model.

The system retrieves completed quiz attempts from Moodle, extracts essay responses while preserving automatically graded objective questions (such as multiple-choice and true/false), sends each essay to Gemini for grading through RabbitMQ, then writes the marks back into Moodle using a custom web service.

The complete grading pipeline is:

**Moodle Quiz → Producer → RabbitMQ → AI Worker → Moodle Gradebook**

---

# Project Structure

```text
AI-Grading-Prototype/
├── worker.py
├── moodle_quiz_producer.py
├── moodle_client.py
├── grading_logger.py
├── grading_guide_quiz.json              
├── output.json
├── grading_log.json
├── requirements.txt
├── .env
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
* Uses RabbitMQ for asynchronous grading

---

# Prerequisites

| Requirement    | Version          |
| -------------- | ---------------- |
| Python         | 3.9+             |
| RabbitMQ       | Local or Docker  |
| Moodle         | 5.x              |
| Gemini API Key | Google AI Studio |

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
```

The Moodle token should belong to a dedicated service account with access only to the required grading web services.

---

# Usage

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
| --host          | RabbitMQ host              |

---

### worker.py

| Option   | Description   |
| -------- | ------------- |
| --host   | RabbitMQ host |
| --model  | Gemini model  |
| --output | Output JSON   |
| --log    | Log JSON      |

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

Logs are written to:

```
grading_log.json

output.json
```

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
```

---

# Technologies

* Python
* Moodle Web Services REST API
* Moodle Question Engine
* RabbitMQ
* Google Gemini API
* BeautifulSoup
* JSON
* Docker (for Moodle)
