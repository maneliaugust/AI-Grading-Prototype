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
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types


class DailyQuotaExceededError(Exception):
    """
    Raised when Gemini's PER-DAY request quota is exhausted (as opposed to
    the per-minute rate limit). Retrying is futile until the quota resets
    (typically at midnight Pacific for the free tier) — callers should NOT
    retry this, and should instead stop processing and preserve the job
    for later (e.g. re-queue it) rather than burning time on doomed retries.
    """
    pass


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
#
# CHANGES vs previous version (per supervisor feedback):
# 1. Added an explicit RUBRIC-ONLY constraint. The model was observed
#    deducting marks for missing specific terminology/methodologies/examples
#    that the rubric never actually required — this is now called out and
#    forbidden directly, with a worked example of the failure mode to avoid.
# 2. Tightened feedback length and reduced templated repetition: feedback
#    field is capped to 2-3 sentences, strengths to exactly 1 item,
#    improvements to at most 2 items, and the model is told to vary its
#    phrasing rather than reuse the same praise/structure boilerplate.
# 3. Added an explicit DEPTH-IS-A-REAL-RUBRIC-DIMENSION clause, after an
#    AI-vs-human comparison run showed the model over-crediting correct-
#    but-generic/shallow answers to the top mark band, when the rubric's
#    own grade bands distinguish "comprehensive/detailed" from "basic/
#    lacks depth" - i.e. depth was being treated as an unstated expectation
#    (forbidden by the rubric-only rule) when it was actually IN the rubric.
# ---------------------------------------------------------------------------
GRADING_PROMPT_TEMPLATE = """You are an expert academic grader for {subject_area}.  
Your task is to fairly, consistently, and constructively grade a learner's written response 
using ONLY the provided rubric. 

ASSESSMENT CONTEXT 
- **Course Name**: {course_name}
- **Assignment Name**: {assignment_name} 
- **Question Text**: {question_text} 
- **Maximum Score**: {max_grade} points 
- **Grading Guide**: {grading_guide} 

LEARNER SUBMISSION 
{learner_response}
 
GRADING INSTRUCTIONS 

1. ANALYZE: Read the question and learner response carefully. Identify what the question is asking and whether the response addresses it.
 
2. RUBRIC APPLICATION — STRICT RUBRIC-ONLY RULE:
- Award or deduct marks ONLY for what the rubric explicitly states. Nothing more, nothing less.
- Never deduct marks for a missing term, framework name, methodology, or example unless the rubric text explicitly names that requirement. A correct explanation in the learner's own words, or using a different valid example, fully satisfies a criterion written in general terms.
- Bad deduction (do not do this): rubric says "explains why a stakeholder is high-priority," learner gives a correct, well-reasoned explanation without naming "the influence-interest matrix" → do NOT deduct. Only deduct if the rubric names that framework as required.
- Before every deduction, ask: is this because the rubric criterion is genuinely unmet, or because the answer didn't match phrasing/terminology/an example I expected? Only deduct for the former.
- Use decimals for partial credit ONLY where the grading guide itself explicitly defines decimal/fractional point values (e.g. "4.5 marks" as its own named level).
- WHOLE-NUMBER-ONLY RUBRICS — READ THIS CAREFULLY: if the grading guide lists only whole-number point values (e.g. "5 points: ...", "4 points: ...", "3 points: ..." down to "0 points: ..."), you MUST award one of those EXACT whole numbers and nothing else — no 3.5, no 4.5, no interpolating between two listed levels. This rule applies no matter how the grading guide is formatted or presented to you — a plain paragraph of text describing point levels is just as binding as a structured list. Before writing your score, re-read the grading guide and ask: "does it ever mention a non-whole number as one of its defined levels?" If not, your score for that question must be a whole number, full stop.
- DEPTH IS A REAL RUBRIC DIMENSION, NOT AN UNSTATED EXPECTATION: when a rubric's grade bands are themselves differentiated by depth, detail, or specificity (e.g. "comprehensive, detailed explanation" vs "basic/vague explanation" as separate bands), that distinction must be applied strictly. A response that is topically on-target and factually correct but stays generic, high-level, or surface-level does NOT automatically earn the top band just because nothing in it is wrong — it earns the band matching the level of depth the rubric describes for that answer. Do not default to full marks for "not incorrect"; match the actual band description, including its depth/detail language, not just its topic.
- Bad leniency (do not do this): rubric's top band requires "comprehensive, detailed explanation with specific examples" and a lower band requires only "basic explanation, lacks depth" — a correct-but-generic answer with no concrete examples belongs in the lower band, even though nothing it says is wrong.

3. FEEDBACK — strict, concise, non-repetitive:
- Maximum 2 sentences, maximum 40 words. No exceptions.
- State the single strongest aspect, then the single most important gap (if any). Nothing else.
- No preamble, no restated rubric, no filler ("Overall...", "In this response..."). Every word must carry information.
- Never open with the same phrase two learners in a row. Banned as sentence openers: "Your response...", "You correctly...", "You demonstrated...", "The response...", "Good job...", "Overall...". Vary structure — sometimes lead with the gap, sometimes with the strength, sometimes with the topic itself.
- Example — BAD: "The response clearly outlines a targeted strategy for high-power, low-interest stakeholders, supported by specific examples." (banned opener "The response")
- Example — GOOD: "Clear stakeholder categorization with concrete examples, though the escalation path for late-emerging concerns is underdeveloped." (leads with the topic itself, no banned opener)
- No praise unless the response earns it. Weak or wrong work gets a direct, professional statement of the main issue — not softened, not padded.
- "strengths": exactly 1 item, only if genuinely supported by the response.
- "improvements": at most 2 items, only the most actionable — skip minor nitpicks.
- Two learners with the same score must still read as two different pieces of writing. If your feedback could be pasted onto another learner's submission unchanged, it's too generic — rewrite it with a detail specific to this response.

4. SCORE CALCULATION: Calculate a final score out of {max_grade}. Put the full rubric-based justification in rubric_breakdown comments, NOT in the feedback field — feedback stays a summary, not a breakdown.
 
5. SELF-CHECK before finalizing — verify all of the following:
- Every deduction traces to an explicit rubric requirement, not an assumption of mine.
- Feedback is ≤2 sentences, ≤40 words, and contains no banned opening phrase.
- Feedback would NOT work unchanged for a different learner — it references something specific to this response.
- No unearned praise is present.
- Score is mathematically consistent with the rubric breakdown.
             
CONSTRAINTS & SAFEGUARDS
- NEVER invent facts not in the learner's response or grading guide.
- If the response is off-topic, blank, or nonsensical: assign 0 and state why in one direct sentence.
- If the rubric is ambiguous, resolve it toward the question's learning objective — never toward an unstated personal expectation.
- Do not grade based on writing style unless the rubric explicitly requires it.
- Flag responses that may require human review (e.g., potential plagiarism, emotional distress, edge cases) by setting requires_human_review to true and explaining why in human_review_reason.


Respond ONLY with a valid JSON object matching this exact schema (no markdown fences, no extra text):
{{
  "score": <number>,
  "max_grade": {max_grade},
  "percentage": <number>,
  "grade_label": "<string>",
  "feedback": "<string, max 2 sentences, max 40 words>",
  "strengths": ["<string, exactly 1 item>"],
  "improvements": ["<string>", "<string, at most 2 items total>"],
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
    """
    Send the prompt to Gemini and parse the JSON response.
    Retries on:
    - 503 (model overloaded): fixed backoff, 10s * attempt number.
    - 429 (rate limit / quota exceeded): reads Gemini's own suggested
      "Please retry in Xs" wait time from the error message when present,
      and waits that long (plus a small buffer) before retrying. Falls
      back to a fixed wait if the delay can't be parsed.
    """
    import re as _re

    max_retries = 6
    response = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            break
        except Exception as e:
            err_str = str(e)
            is_last_attempt = attempt >= max_retries - 1

            if "429" in err_str:
                # Daily quota exhaustion (GenerateRequestsPerDayPerProjectPerModel)
                # cannot be fixed by waiting a minute — it only resets on its
                # own schedule (typically ~24h). Retrying here just wastes
                # time; fail fast with a distinct exception so the caller
                # (worker.py) can preserve the job instead of discarding it.
                if "PerDay" in err_str:
                    raise DailyQuotaExceededError(err_str) from e

                # Try to parse Gemini's suggested wait time, e.g.
                # "Please retry in 43.048587604s." or "retryDelay': '43s'"
                match = _re.search(r"retry in ([\d.]+)s", err_str)
                if not match:
                    match = _re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", err_str)
                wait = float(match.group(1)) + 2 if match else 20.0  # +2s buffer

                if is_last_attempt:
                    raise
                log.warning(
                    "Rate limit hit (429), waiting %.1fs before retry (attempt %d/%d)...",
                    wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
            elif "503" in err_str:
                if is_last_attempt:
                    raise
                wait = 10 * (attempt + 1)
                log.warning("Model overloaded, retrying in %ds (attempt %d/%d)...", wait, attempt + 1, max_retries)
                time.sleep(wait)
            else:
                raise

    if response is None:
        raise RuntimeError(f"Failed to get a response from Gemini after {max_retries} attempts.")

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
        learner_id = submission["learner_id"]
        log.info("Grading submission %d/%d — learner: %s", i + 1, len(input_data["submissions"]), learner_id)

        try:
            prompt = build_prompt(course, submission, input_data)
            grading = grade_submission(client, prompt, config, model_name)
            results.append({
                "learner_id": learner_id,
                "status": "graded",
                "grading": grading,
            })
            log.info("  ✓ %s → %s/%s (%s%%)",
                     learner_id,
                     grading.get("score"),
                     grading.get("max_grade"),
                     grading.get("percentage"))
        except json.JSONDecodeError as e:
            log.error("  ✗ %s — failed to parse AI response as JSON: %s", learner_id, e)
            errors.append({"learner_id": learner_id, "error": f"JSON parse error: {e}"})
        except Exception as e:  # pylint: disable=broad-except
            log.error("  ✗ %s — unexpected error: %s", learner_id, e)
            errors.append({"learner_id": learner_id, "error": str(e)})

        # Small delay to stay within free-tier rate limits
        if i < len(input_data["submissions"]) - 1:
            time.sleep(1)

    return {
        "metadata": {
            "course_name": course,
            "assignment_name": input_data.get("assignment_name", ""),
            "graded_at": datetime.now(timezone.utc).isoformat() + "Z",
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
    parser.add_argument("--model",  default="gemini-3.6-flash", help="Gemini model name (default: gemini-3.6-flash)")
    parser.add_argument("--temperature", type=float, default=0.2,
                        help="Generation temperature 0-1 (default: 0.2, lower = more deterministic)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # ---- API key ----
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("No API key found. Set GEMINI_API_KEY environment variable.")
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

    # ---- Load -> Grade -> Write ----
    try:
        input_data = load_input(args.input)
    except (FileNotFoundError, ValueError) as e:
        log.error("Failed to load input: %s", e)
        sys.exit(1)

    output_data = process_all(input_data, client, gen_config, args.model)
    write_output(output_data, args.output)

    meta = output_data["metadata"]
    log.info("=== Done - %d graded, %d errors ===", meta["graded_count"], meta["error_count"])
    if meta["error_count"]:
        sys.exit(2)  # partial failure


if __name__ == "__main__":
    main()