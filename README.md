# AI Grading Prototype

A standalone Python prototype that reads Moodle long-format assessment data from `input.json`, grades each submission using the Google Gemini AI model, and writes structured results to `output.json`.

---

## Project Structure

```
grader/
├── grader.py          # Main application
├── input.json         # Sample assessment data (edit or replace)
├── output.json        # Generated after running (git-ignored)
├── requirements.txt   # Python dependencies
└── README.md
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.9+ |
| Gemini API key | Free tier works for prototyping |

Get a free Gemini API key at: https://aistudio.google.com/app/apikey

---

## Setup

```bash
# 1. Clone / download the project
cd grader

# 2. Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key
export GEMINI_API_KEY="your-key-here"   # macOS/Linux
set GEMINI_API_KEY=your-key-here        # Windows CMD
$env:GEMINI_API_KEY="your-key-here"     # Windows PowerShell
```

---

## Usage

```bash
# Run with defaults (reads input.json, writes output.json)
python grader.py

# Custom paths and model
python grader.py --input my_data.json --output results.json --model gemini-1.5-flash

# Lower temperature for more deterministic scoring
python grader.py --temperature 0.1
```

### CLI Options

| Flag | Default | Description |
|---|---|---|
| `--input` | `input.json` | Path to the input assessment file |
| `--output` | `output.json` | Path for the grading results |
| `--model` | `gemini-1.5-pro` | Gemini model name |
| `--temperature` | `0.2` | Generation temperature (0 = deterministic, 1 = creative) |

---

## Input Format (`input.json`)

```json
{
  "course_name": "BUSA 301 – Business Strategy",
  "assignment_name": "Essay: Porter's Five Forces Analysis",
  "submissions": [
    {
      "learner_id": "u12345678",
      "question_text": "Apply Porter's Five Forces...",
      "max_grade": 30,
      "grading_guide": [
        {
          "criterion": "Understanding of the Framework",
          "marks": 10,
          "description": "Correctly identifies and explains..."
        }
      ],
      "learner_response": "Porter's Five Forces is a model..."
    }
  ]
}
```

> The `grading_guide` field accepts either a **list of criterion objects** (as above) or a **plain string**.

---

## Output Format (`output.json`)

```json
{
  "metadata": {
    "course_name": "BUSA 301 – Business Strategy",
    "assignment_name": "Essay: Porter's Five Forces Analysis",
    "graded_at": "2024-06-01T10:23:45Z",
    "total_submissions": 2,
    "graded_count": 2,
    "error_count": 0
  },
  "results": [
    {
      "learner_id": "u12345678",
      "status": "graded",
      "grading": {
        "score": 24,
        "max_grade": 30,
        "percentage": 80.0,
        "grade_label": "Distinction",
        "feedback": "...",
        "strengths": ["..."],
        "improvements": ["..."],
        "rubric_breakdown": [
          {
            "criterion": "Understanding of the Framework",
            "marks_awarded": 8,
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

### Grade Labels

| Percentage | Label |
|---|---|
| ≥ 75% | Excellent |
| 65–74% | Satisfactory |
| 50–64% | Pass |
| < 50% | Fail |

---

## Customising the Grading Prompt

The prompt template lives in `grader.py` as `GRADING_PROMPT_TEMPLATE`. Edit it to:
- Change the tone or scoring criteria
- Add institution-specific instructions
- Request additional output fields (e.g., `plagiarism_flag`, `model_answer_comparison`)

After adding new fields to the prompt, update the schema description inside the template accordingly.

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All submissions graded successfully |
| `1` | Fatal error (missing API key, invalid input file) |
| `2` | Partial failure (some submissions failed to grade) |

---

## Recommended Models

| Model | Best For |
|---|---|
| `gemini-1.5-pro` | Highest quality grading (default) |
| `gemini-1.5-flash` | Faster & cheaper, good for bulk batches |
| `gemini-1.0-pro` | Fallback if 1.5 is unavailable |

---

## Rate Limits (Free Tier)

The free Gemini API tier allows ~15 requests/minute. The application automatically inserts a 1-second delay between submissions. For large batches, consider upgrading to a paid tier or adding `--temperature` tuning.

---

## Future Integration Notes (Moodle Service)

When productionising this prototype:
1. Replace `input.json` reading with a Moodle Web Services API call.
2. Replace `output.json` writing with a POST back to the Moodle grade book endpoint.
3. Add a database layer to persist grading results and enable re-grading audits.
4. Wrap `grader.py` logic in a FastAPI or Django REST service.
5. Add educator review/override workflow before scores are finalised.