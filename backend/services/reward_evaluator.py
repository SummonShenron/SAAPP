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


async def evaluate_response(prompt: str, response: str, source_type: str) -> dict:
    """Judges a generated response against the exact prompt it was given.

    Fails open: any evaluator error must never block or delay a response that would
    otherwise have been fine, so an exception or malformed reply is treated as a pass.
    """
    if source_type not in REWARD_EVAL_SOURCE_TYPES:
        return dict(_PASS_VERDICT)

    try:
        formatted_prompt = REWARD_EVALUATOR_PROMPT.format(prompt=prompt, response=response)
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
