import json
import logging

from backend.models.models import lite_llm
from backend.components.constraints import REWARD_EVALUATOR_PROMPT

logger = logging.getLogger("SASS Logger")

# Only source types where a bad answer is a factual/grounding risk despite good data.
# "conversational" is already covered by the anti-fabrication guardrail; "web" carries
# its own citation contract and is lower value to re-judge here.
REWARD_EVAL_SOURCE_TYPES = {"kb_strict", "kb_open", "tool_output"}

_PASS_VERDICT = {"verdict": "pass", "tag": None, "reason": None}


MAX_TOOLS_LISTED = 12


def turn_facts(tools) -> str:
    """What the assistant really did this turn, as the evaluator's ground truth: the tools it called (names and counts, from the turn's own record, never text). "" when it called none.
    WHY: the evaluator only sees the written summary the tool agent handed the voice, so with no record of the turn it guessed, and failed a true "I put the test file in place and ran it" because
    the summary said the file was "already in place" (it was: the person had just applied it). Names only; nothing the person wrote or any tool returned."""
    if not isinstance(tools, dict) or not tools:
        return ""
    parts = []
    for name, stats in list(tools.items())[:MAX_TOOLS_LISTED]:
        calls = int((stats or {}).get("calls", 1) or 1) if isinstance(stats, dict) else 1
        errors = int((stats or {}).get("errors", 0) or 0) if isinstance(stats, dict) else 0
        parts.append(f"{str(name)[:60]} x{calls}" + (f" ({errors} failed)" if errors else ""))
    return (
        "\nTOOLS THE ASSISTANT ACTUALLY CALLED THIS TURN (a real record, the source of truth for what it did in THIS turn): " + ", ".join(parts) + ".\n"
        "A claim that the assistant did that kind of work this turn is supported by this list. Words like \"already\" or \"in place\" in the DATA describe the state after this turn's work; they are not "
        "evidence that the work happened in an earlier turn. Do not fail the response for saying it did what this list shows it did.\n"
    )


async def evaluate_response(prompt: str, response: str, source_type: str, tools=None) -> dict:
    """Judges a generated response against the exact prompt it was given (and, when given, the tools really called this turn: see turn_facts).

    Fails open: any evaluator error must never block or delay a response that would
    otherwise have been fine, so an exception or malformed reply is treated as a pass.
    """
    if source_type not in REWARD_EVAL_SOURCE_TYPES:
        return dict(_PASS_VERDICT)

    try:
        formatted_prompt = REWARD_EVALUATOR_PROMPT.format(prompt=prompt, response=response, turn_facts=turn_facts(tools))
        result = await lite_llm.ainvoke(formatted_prompt)
        raw_content = result.content if hasattr(result, "content") else str(result)
        if isinstance(raw_content, list):
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
        else:
            raw_text = str(raw_content)
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_json)
        if parsed.get("verdict") not in ("pass", "fail"):
            return dict(_PASS_VERDICT)
        return {
            "verdict": parsed["verdict"],
            "tag": parsed.get("tag"),
            "reason": parsed.get("reason"),
        }
    except Exception:
        logger.exception("[RewardEvaluator] Evaluation failed; failing open to pass.")
        return dict(_PASS_VERDICT)


def build_correction_prompt(original_prompt: str, tag: str, reason: str) -> str:
    """Appends a self-correction note to the original prompt for a single regeneration attempt."""
    return (
        f"{original_prompt}\n\n"
        f"SELF-CORRECTION NOTE: Your previous attempt had a problem ({tag}): {reason}. "
        "Address this and answer again, using the same data and rules above.\n"
    )
