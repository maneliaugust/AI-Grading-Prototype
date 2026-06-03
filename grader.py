"""
Moodle Long-Format AI Grading Prototype
Uses the Google Generative AI SDK (Gemini) to grade learner assessments.

Usage:
    python grader.py [--input input.json] [--output output.json] [--model gemini-1.5-pro]
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()


from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Grading prompt template
# ---------------------------------------------------------------------------
GRADING_PROMPT_TEMPLATE = """You are an expert academic grader for {subject_area}.  
Your task is to fairly, consistently, and constructively grade a learner's written response 
using the provided rubric. 

ASSESSMENT CONTEXT 
- **Course Name**: {course_name}
- **Assignment Name**: {assignment_name} 
- **Question Text**: {question_text} 
- **Maximum Score**: {max_grade} points 
- **Grading Guide**: {grading_guide} 

LEARNER SUBMISSION 
{learner_response}
 
GRADING INSTRUCTIONS 
1. ANALYZE: Carefully read the question and learner response. Identify what the question is asking and whether the response addresses it.
 
2. RUBRIC APPLICATION: Apply the grading guide point-by-point. For each criterion: 
- Note evidence from the response that meets or misses the criterion 
- Assign partial credit where appropriate (use decimals if needed) 

3. FEEDBACK GENERATION: Write constructive feedback that: 
- Starts with 1 strength (specific praise) - Identifies 1-2 actionable areas for improvement (with guidance) 
- Avoids vague phrases like "good job" or "needs work" 
- Uses a supportive, professional tone appropriate for entry-level learner 

4. SCORE CALCULATION: Calculate a final score out of {max_grade}. Justify the score briefly based on rubric alignment.
 
5. SELF-CHECK: Before finalizing, verify: 
- Did I apply the rubric consistently, not my personal opinion? 
- Is feedback specific, kind, and useful for learning? 
- Is the score mathematically consistent with the rubric breakdown?
             
CONSTRAINTS & SAFEGUARDS
- NEVER invent facts not in the learner's response or grading guide 
- If the response is off-topic, blank, or nonsensical: assign 0 and explain why constructively 
- If the rubric is ambiguous, prioritize the question's learning objective 
- Do not grade based on writing style unless explicitly required by the rubric 
- Flag responses that may require human review (e.g., potential plagiarism, emotional distress, edge cases)

Respond ONLY with a valid JSON object matching this exact schema (no markdown fences, no extra text):
{{
  "score": <number>,
  "max_grade": {max_grade},
  "percentage": <number>,
  "grade_label": "<string>",
  "feedback": "<string>",
  "strengths": ["<string>", ...],
  "improvements": ["<string>", ...],
  "requires_human_review": <boolean>,
  "human_review_reason": "<string or null>",
  "rubric_breakdown": [
    {{
      "criterion": "<string>",
      "marks_awarded": <number>,
      "marks_available": <number>,
      "comment": "<string>"
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Grade label helper
# ---------------------------------------------------------------------------
def percentage_to_label(pct: float) -> str:
    if pct >= 75:
        return "Excellent"
    if pct >= 65:
        return "Satisfactory"
    if pct >= 50:
        return "Pass"
    return "Fail"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def load_input(path: str) -> dict:
    """Load and validate the input JSON file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with p.open(encoding="utf-8") as f:
        data = json.load(f)

    required_top = {"course_name", "assignment_name", "submissions"}
    missing = required_top - data.keys()
    if missing:
        raise ValueError(f"Input JSON missing required top-level keys: {missing}")

    for i, sub in enumerate(data["submissions"]):
        required_sub = {"learner_id", "question_text", "learner_response", "max_grade", "grading_guide"}
        required_top_extra = {"subject_area"}
        missing_top = required_top_extra - data.keys()
        if missing_top:
            raise ValueError(f"Input JSON missing required top-level keys: {missing_top}")
        missing_sub = required_sub - sub.keys()
        if missing_sub:
            raise ValueError(f"Submission [{i}] missing keys: {missing_sub}")

    log.info("Loaded %d submission(s) from %s", len(data["submissions"]), path)
    return data


def build_prompt(course_name: str, submission: dict, top: dict) -> str:
    """Inject submission data into the grading prompt template."""
    rubric = submission["grading_guide"]
    if isinstance(rubric, list):
        rubric_lines = []
        for r in rubric:
            bands = r.get("grade_bands", [])
            band_text = "\n".join(
                f"    - {b['range']} marks: {b['description']}"
                for b in bands
            )
            line = f"- **{r['criterion']}** ({r['marks']} marks)"
            if band_text:
                line += f":\n{band_text}"
            rubric_lines.append(line)
        grading_guide = "\n".join(rubric_lines)
    else:
        grading_guide = str(rubric)

    return GRADING_PROMPT_TEMPLATE.format(
        course_name=course_name,
        subject_area=top["subject_area"],
        assignment_name=top.get("assignment_name", ""),
        question_text=submission["question_text"],
        max_grade=submission["max_grade"],
        grading_guide=grading_guide,
        learner_response=submission["learner_response"],
    )


def grade_submission(client: genai.Client, prompt: str, config: types.GenerateContentConfig, model_name: str) -> dict:
    """Send the prompt to Gemini and parse the JSON response."""
    response = client.models.generate_content(model=model_name, contents=prompt, config=config)
    raw_text = response.text.strip()

    # Strip markdown fences if the model wraps output anyway
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    result = json.loads(raw_text)

    # Ensure grade_label is present
    if "grade_label" not in result or not result["grade_label"]:
        result["grade_label"] = percentage_to_label(result.get("percentage", 0))
    # Normalise key name
    if "max_marks" in result and "max_grade" not in result:
        result["max_grade"] = result.pop("max_marks")

    return result


def process_all(input_data: dict, client: genai.Client, config: types.GenerateContentConfig, model_name: str) -> dict:
    """Grade every submission and assemble the output structure."""
    course = input_data["course_name"]
    results = []
    errors = []

    for i, submission in enumerate(input_data["submissions"]):
        student_id = submission["learner_id"]
        log.info("Grading submission %d/%d — student: %s", i + 1, len(input_data["submissions"]), student_id)

        try:
            prompt = build_prompt(course, submission, input_data)
            grading = grade_submission(client, prompt, config, model_name)
            results.append({
                "learner_id": student_id,
                "status": "graded",
                "grading": grading,
            })
            log.info("  ✓ %s → %s/%s (%s%%)",
                     student_id,
                     grading.get("score"),
                     grading.get("max_grade"),
                     grading.get("percentage"))
        except json.JSONDecodeError as e:
            log.error("  ✗ %s — failed to parse AI response as JSON: %s", student_id, e)
            errors.append({"learner_id": student_id, "error": f"JSON parse error: {e}"})
        except Exception as e:  # pylint: disable=broad-except
            log.error("  ✗ %s — unexpected error: %s", student_id, e)
            errors.append({"learner_id": student_id, "error": str(e)})

        # Small delay to stay within free-tier rate limits
        if i < len(input_data["submissions"]) - 1:
            time.sleep(1)

    return {
        "metadata": {
            "course_name": course,
            "assignment_name": input_data.get("assignment_name", ""),
            "graded_at": datetime.utcnow().isoformat() + "Z",
            "total_submissions": len(input_data["submissions"]),
            "graded_count": len(results),
            "error_count": len(errors),
        },
        "results": results,
        "errors": errors,
    }


def write_output(data: dict, path: str) -> None:
    """Write results to the output JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info("Results written to %s", path)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Moodle AI Grading Prototype — grades long-format responses via Gemini"
    )
    parser.add_argument("--input",  default="input.json",  help="Path to input JSON file  (default: input.json)")
    parser.add_argument("--output", default="output.json", help="Path to output JSON file (default: output.json)")
    parser.add_argument("--model",  default="gemini-2.0-flash", help="Gemini model name (default: gemini-2.0-flash)")
    parser.add_argument("--temperature", type=float, default=0.2,
                        help="Generation temperature 0–1 (default: 0.2, lower = more deterministic)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # ---- API key ----
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        log.error("No API key found. Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    gen_config = types.GenerateContentConfig(
        temperature=args.temperature,
        response_mime_type="application/json",  # ask Gemini to return JSON directly
    )

    log.info("=== Moodle AI Grading Prototype ===")
    log.info("Model   : %s", args.model)
    log.info("Input   : %s", args.input)
    log.info("Output  : %s", args.output)

    # ---- Load → Grade → Write ----
    try:
        input_data = load_input(args.input)
    except (FileNotFoundError, ValueError) as e:
        log.error("Failed to load input: %s", e)
        sys.exit(1)

    output_data = process_all(input_data, client, gen_config, args.model)
    write_output(output_data, args.output)

    meta = output_data["metadata"]
    log.info("=== Done — %d graded, %d errors ===", meta["graded_count"], meta["error_count"])
    if meta["error_count"]:
        sys.exit(2)  # partial failure


if __name__ == "__main__":
    main()