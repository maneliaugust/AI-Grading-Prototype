# AI Grading Prototype

A Python service that grades long-format Moodle assessment submissions using the Google Gemini AI model. Submissions can be sourced either from a static `input.json` file (original prototype mode) or pulled live from a Moodle course via the Web Services REST API (production-track mode) — both flows feed the same RabbitMQ-based grading pipeline and the same grading logic.

---

## Project Structure

```
AI-Grading-Prototype/
├── grader.py             # Core grading logic — builds prompts, calls Gemini, parses results
├── producer.py           # [input.json mode] Queues jobs from a static input.json file
├── moodle_producer.py    # [Moodle mode] Pulls ungraded submissions live from Moodle and queues them
├── moodle_client.py       # All Moodle Web Services REST calls (submissions, file download, grade push-back)
├── worker.py              # Consumes queued jobs, grades via grader.py, writes results, pushes grades to Moodle
├── grading_logger.py      # Structured success/fail logging to grading_log.json
├── input.json             # Sample assessment data for input.json mode
├── grading_guide.json     # Standalone rubric file used by moodle_producer.py
├── output.json            # Generated grading results (git-ignored)
├── grading_log.json       # Generated per-attempt audit log (git-ignored)
├── requirements.txt
├── .env                   # API keys and Moodle config (git-ignored)
└── README.md
```

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.9+ |
| Gemini API key | Free tier works for prototyping — https://aistudio.google.com/app/apikey |
| RabbitMQ | Running locally (Windows service or Docker) |
| Moodle (for Moodle mode) | Local instance, e.g. via Docker — Web Services API enabled |

---

## Setup

```bash
# 1. Clone / download the project
cd AI-Grading-Prototype

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\Activate.ps1       # Windows PowerShell

# 3. Install dependencies
pip install -r requirements.txt
pip install pypdf                # required for Moodle PDF text extraction
```

### `.env` file

```dotenv
GEMINI_API_KEY="your-gemini-key"

# Required only for Moodle mode
MOODLE_BASE_URL=http://localhost:8080
MOODLE_TOKEN="your-api-gradingbot-token"
```

The Moodle token should belong to a dedicated service account (e.g. "API GradingBot") with web services access scoped to the grading functions only — not the Moodle admin account.

---

## Usage

There are two ways to get jobs into the grading queue. Both end up being processed identically by `worker.py`.

### Mode A — Static input.json

```bash
python producer.py --input input.json --host localhost
```

Reads `input.json`, publishes one job per submission to the `grading_jobs` queue.

### Mode B — Live from Moodle

```bash
python moodle_producer.py \
  --course-id 4 \
  --assignment-id 2 \
  --course-name "Business Strategy" \
  --assignment-name "Essay: Porter's Five Forces Analysis" \
  --subject-area "Business Strategy" \
  --question-text "Apply Porter's Five Forces framework..." \
  --max-grade 30 \
  --grading-guide grading_guide.json \
  --host localhost
```

Pulls ungraded submissions from Moodle (`mod_assign_get_submissions`), downloads each learner's PDF, extracts the text, maps Moodle's internal user ID to the learner's institutional ID (via the **ID number** profile field), and queues one job per submission.

> Use `mod_assign_get_assignments` to confirm the real `--assignment-id` — the ID shown in the browser URL when viewing an assignment is the *course-module ID*, not the assignment ID the API expects.

### Then, regardless of mode — start the worker

```bash
python worker.py --host localhost --model gemini-2.5-flash
```

The worker grades each queued submission with Gemini, writes results to `output.json` and `grading_log.json`, and — **if the job came from Moodle** — automatically pushes the score and feedback back to the Moodle gradebook via `mod_assign_save_grade`. Jobs sourced from `input.json` are graded and logged the same way, just without the Moodle push-back step (no Moodle user/assignment IDs are attached to them).

### Worker CLI Options

| Flag | Default | Description |
|---|---|---|
| `--host` | `localhost` | RabbitMQ host |
| `--model` | `gemini-2.5-flash` | Gemini model name |
| `--output` | `output.json` | Path for grading results |
| `--log` | `grading_log.json` | Path for the structured audit log |

---

## Input Format (`input.json` / `grading_guide.json`)

```json
{
  "course_name": "BUSA 301 – Business Strategy",
  "assignment_name": "Essay: Porter's Five Forces Analysis",
  "subject_area": "Business Strategy",
  "submissions": [
    {
      "learner_id": "u12345678",
      "question_text": "Apply Porter's Five Forces...",
      "max_grade": 30,
      "grading_guide": [
        {
          "criterion": "Understanding of the Framework",
          "marks": 10,
          "grade_bands": [
            { "range": "0", "description": "No attempt made." },
            { "range": "10", "description": "Fully accurate and well contextualised." }
          ]
        }
      ],
      "learner_response": "Porter's Five Forces is a model..."
    }
  ]
}
```

`grading_guide.json` (used by `moodle_producer.py`) is just the `grading_guide` array on its own, extracted from this same structure — it's passed once via `--grading-guide` rather than embedded per submission, since in Moodle mode the rubric is shared across all submissions for a given assignment.

> The `grading_guide` field accepts either a **list of criterion objects** (as above, with optional `grade_bands` for partial-credit precision) or a **plain string**.

---

## Output Format (`output.json`)

```json
{
  "metadata": [
    {
      "course_name": "BUSA 301 – Business Strategy",
      "assignment_name": "Essay: Porter's Five Forces Analysis",
      "graded_at": "2026-06-20T22:15:18+02:00",
      "total_submissions": 2,
      "graded_count": 2,
      "error_count": 0
    }
  ],
  "results": [
    {
      "learner_id": "u12345678",
      "status": "graded",
      "grading": {
        "score": 27,
        "max_grade": 30,
        "percentage": 90.0,
        "grade_label": "Excellent",
        "feedback": "...",
        "strengths": ["..."],
        "improvements": ["..."],
        "requires_human_review": false,
        "human_review_reason": null,
        "rubric_breakdown": [
          {
            "criterion": "Understanding of the Framework",
            "marks_awarded": 9,
            "marks_available": 10,
            "comment": "..."
          }
        ]
      }
    }
  ],
  "errors": []
}
```

**`metadata` is a list, not a single object** — one entry per calendar day. Running `worker.py` multiple times on the same day updates that day's entry in place; running it again the next day appends a new entry, so historical run statistics are never lost.

### Grade Labels

| Percentage | Label |
|---|---|
| ≥ 75% | Excellent |
| 65–74% | Satisfactory |
| 50–64% | Pass |
| < 50% | Fail |

---

## Moodle Web Services Setup (One-Time)

1. **Enable web services** — Site administration → General → Advanced features.
2. **Enable the REST protocol** — Site administration → Server → Web services → Manage protocols.
3. **Create a custom external service**, add the required functions:
   - `mod_assign_get_assignments`
   - `mod_assign_get_submissions`
   - `mod_assign_save_grade`
   - `core_user_get_users`
   - `core_webservice_get_site_info`
4. **Create a dedicated service account** (e.g. "API GradingBot") using **Web services authentication**.
5. **Authorise the account on the service**, then generate a token for it.
6. **Grant `moodle/user:viewalldetails`** to the account's role — required for `core_user_get_users` to return each learner's `idnumber` field, which is how Moodle's internal user IDs map back to institutional learner IDs (e.g. `u12345678`).
7. **Enable file downloads on the service** ("Can download files") — required for `moodle_client.py` to fetch submitted PDFs.
8. On each learner's Moodle profile, set the **ID number** field to their institutional ID — this is the value `moodle_producer.py` uses as `learner_id`.

---

## Customising the Grading Prompt

The prompt template lives in `grader.py` as `GRADING_PROMPT_TEMPLATE`. Edit it to:
- Change the tone or scoring criteria
- Add institution-specific instructions
- Request additional output fields (e.g., `plagiarism_flag`, `model_answer_comparison`)

After adding new fields to the prompt, update the schema description inside the template accordingly. Since `worker.py` imports `build_prompt` and `grade_submission` directly from `grader.py`, changes here apply to both `input.json` mode and Moodle mode automatically.

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All submissions queued/graded successfully |
| `1` | Fatal error (missing API key, invalid input file, RabbitMQ unreachable, Moodle API error) |
| `2` | Partial failure (some submissions failed to grade) |

---

## Recommended Models

| Model | Best For |
|---|---|
| `gemini-2.5-flash` | Fast, cost-effective, current default |
| `gemini-2.5-pro` | Highest quality grading for complex/long rubrics |

---

## Rate Limits (Free Tier)

The free Gemini API tier allows a limited number of requests per minute. `grader.py` automatically retries with backoff on `503` (model overloaded) responses, and `process_all` (input.json mode) inserts a short delay between submissions.

---

## Architecture Notes

- `worker.py` and `grader.py` are intentionally agnostic to where a job came from — both producers publish the same message shape to the `grading_jobs` queue, so adding a new submission source in the future (e.g. a different LMS) only requires writing a new producer, not touching the grading or push-back logic.
- A failed Moodle grade push-back does **not** count as a failed grading attempt — the AI's grade is still recorded locally in `output.json`/`grading_log.json` even if Moodle is temporarily unreachable, so no grading work is lost; only the gradebook sync needs retrying.
- `requires_human_review` and `human_review_reason` from Gemini's response are surfaced in the feedback comment pushed to Moodle, so flagged submissions are visible to a human reviewer directly in the gradebook.
