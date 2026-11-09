"""Asking the person to choose between a few approaches, in the same turn: Sonic's version of Claude Code's AskUserQuestion.

THE PROBLEM (2026-11-08, a real thread). When Sonic needed to know which of several things the person meant, it asked an open question in its reply and ended the turn. The answer came back as a new message, the next turn started without the
earlier turn's tool results, and the investigation began again: six turns in ten minutes for one small change. An open question is also more work to answer than a tap.

THE FIX (Jack: "it should work similar to how yours does"). Sonic calls `ask_choice` with ONE question and two to four options (a label, and a short description of what choosing it means), its recommendation first. The page shows a card with a button for each
option and a box for something else. The turn WAITS here, at no model cost, for the person to answer (POST /api/chat/choice/{id}), then the loop carries on in the same turn with the answer as the tool's result, with everything it had read still in front of it. The same
waiting as a proposed edit's Apply or Decline (backend/services/local_edit_decisions.py).

- ONE QUESTION, FEW OPTIONS. The card is validated here: a question, two to four distinct options with short labels, no "other" option of its own (the card always has a "Something else" box), at most two asks in a turn so it never becomes an interview.
- ONE PERSON'S. An answer is accepted only from the person the card was shown to.
- BOUNDED. CHOICE_WAIT_SECONDS (default 600); a turn that is cancelled (Stop, or the person left) stops waiting at once. No answer means nothing was decided, and the turn says so and stops: Sonic does not guess.
- ONLY WHEN THE PAGE CAN ANSWER. A page from before this existed would never answer; the chat request carries `choice_cards`, and the tool is only offered when it is true.
- NO TEXT IS LOGGED: the question, the options and the person's own words are never written to a log; only that a wait ended, and how.
"""
import asyncio
import logging
import os
import re
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("SASS Logger")

WAIT_SECONDS = float(os.getenv("CHOICE_WAIT_SECONDS", "600"))
HEARTBEAT_SECONDS = 20.0
MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_QUESTION_CHARS = 280
MAX_LABEL_CHARS = 60
MAX_DESCRIPTION_CHARS = 200
MAX_OTHER_CHARS = 500
MAX_ASKS_PER_TURN = 2
ENDINGS = ("picked", "other", "timeout")

_RECOMMENDED_SUFFIX_RE = re.compile(r"\s*\(\s*recommended\s*\)\s*$", re.IGNORECASE)
_OTHER_LABEL_RE = re.compile(r"^\W*(?:other|something else|none of (?:these|the above)|neither|n/?a)\W*$", re.IGNORECASE)
_waiting: Dict[str, Dict[str, Any]] = {}


def enabled() -> bool:
    return os.getenv("CHOICE_CARDS", "on").strip().lower() not in ("0", "false", "no", "off")


def _clip(text: Any) -> str:
    return " ".join(str(text or "").split())


def validate_card(args: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """The card for these arguments, or (None, why not) in words the model can act on. `recommended` is the NUMBER (1 to 4) of the option Sonic recommends; the recommended one is moved first and marked. A label that ends in
    "(Recommended)" is read as the recommendation too. Pure apart from the new id."""
    args = args if isinstance(args, dict) else {}
    question = _clip(args.get("question"))
    if not question:
        return None, "give one question to ask."
    if len(question) > MAX_QUESTION_CHARS:
        return None, f"the question is too long ({len(question)} characters, at most {MAX_QUESTION_CHARS}): ask one short question."
    raw = args.get("options")
    if not isinstance(raw, list) or not MIN_OPTIONS <= len(raw) <= MAX_OPTIONS:
        return None, f"give {MIN_OPTIONS} to {MAX_OPTIONS} options."
    options: List[Dict[str, Any]] = []
    seen = set()
    suffix_pick: Optional[int] = None
    for position, item in enumerate(raw):
        label, description = (_clip(item.get("label")), _clip(item.get("description"))) if isinstance(item, dict) else (_clip(item), "")
        if _RECOMMENDED_SUFFIX_RE.search(label):
            label = _RECOMMENDED_SUFFIX_RE.sub("", label).strip()
            suffix_pick = position
        if not label:
            return None, f"option {position + 1} has no label."
        if len(label) > MAX_LABEL_CHARS:
            return None, f"option {position + 1}'s label is too long ({len(label)} characters, at most {MAX_LABEL_CHARS}): a few words, with the detail in its description."
        if len(description) > MAX_DESCRIPTION_CHARS:
            return None, f"option {position + 1}'s description is too long (at most {MAX_DESCRIPTION_CHARS} characters)."
        if _OTHER_LABEL_RE.match(label):
            return None, "do not add an 'other' option: the card always has a 'Something else' box."
        if label.lower() in seen:
            return None, f"two options are both called '{label}': each needs its own label."
        seen.add(label.lower())
        options.append({"label": label, "description": description, "recommended": False})
    pick = suffix_pick
    try:
        number = int(args.get("recommended")) if args.get("recommended") not in (None, "") else None
    except (TypeError, ValueError):
        number = None
    if number is not None and 1 <= number <= len(options):
        pick = number - 1
    if pick is not None:
        options[pick]["recommended"] = True
        options.insert(0, options.pop(pick))
    return {"choice_id": uuid.uuid4().hex, "question": question, "options": options}, ""


def register(username: str, card: Dict[str, Any]) -> None:
    """Starts waiting for `card`'s answer. Call before the card is streamed, so a very quick tap cannot arrive first."""
    _waiting[card["choice_id"]] = {"username": username, "count": len(card["options"]), "event": asyncio.Event(), "answer": None}


def forget(choice_id: str) -> None:
    _waiting.pop(choice_id, None)


def waiting_count() -> int:
    return len(_waiting)


def answer(username: str, choice_id: str, index: Any = None, other: Any = None) -> str:
    """Records the person's answer: `index` (0 to the number of options minus one) for an option, or `other` for their own words. Returns "recorded", "no_waiter" (no turn is waiting: it ended or timed out, or the id is not theirs) or
    "invalid" (neither a valid option nor any words). Nothing is revealed about another person's card."""
    entry = _waiting.get(choice_id)
    if entry is None or entry["username"] != username:
        return "no_waiter"
    words = _clip(other)[:MAX_OTHER_CHARS]
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < entry["count"]:
        entry["answer"] = {"kind": "picked", "index": index}
    elif words:
        entry["answer"] = {"kind": "other", "text": words}
    else:
        return "invalid"
    entry["event"].set()
    return "recorded"


async def wait_for(choice_id: str, timeout: Optional[float] = None, on_tick: Optional[Callable[[int], Awaitable[None]]] = None) -> Optional[Dict[str, Any]]:
    """Waits for the answer to `choice_id` and returns it ({"kind": "picked", "index"} or {"kind": "other", "text"}), or None if there was none within `timeout` seconds. `on_tick(seconds_waited)` is called every HEARTBEAT_SECONDS so the
    stream stays open. Always forgets the card afterwards, including when the wait is cancelled."""
    entry = _waiting.get(choice_id)
    if entry is None:
        return None
    limit = WAIT_SECONDS if timeout is None else timeout
    waited = 0.0
    ended = "timeout"
    try:
        while waited < limit:
            step = min(HEARTBEAT_SECONDS, limit - waited)
            try:
                await asyncio.wait_for(entry["event"].wait(), timeout=step)
                ended = entry["answer"]["kind"]
                return entry["answer"]
            except asyncio.TimeoutError:
                waited += step
                if on_tick is not None and waited < limit:
                    try:
                        await on_tick(int(waited))
                    except Exception:
                        pass
        return None
    except asyncio.CancelledError:
        ended = "cancelled"
        raise
    finally:
        logger.info("[choice_cards] a wait for a choice ended: %s after about %ds.", ended, int(waited))        # no question, no option, no answer
        forget(choice_id)


def result_text(card: Dict[str, Any], answer_given: Optional[Dict[str, Any]]) -> str:
    """What the tool returns to the loop. Pure."""
    if answer_given is None:
        minutes = max(int(WAIT_SECONDS // 60), 1)
        return (f"No answer came within {minutes} minutes, so nothing was decided. Do not guess and do not carry on as if they had chosen: say in your final answer what you were going to ask and why, and stop. "
                "They can answer in their next message.")
    if answer_given["kind"] == "picked":
        option = card["options"][answer_given["index"]]
        detail = f" ({option['description']})" if option.get("description") else ""
        return f"The person chose option {answer_given['index'] + 1}: \"{option['label']}\"{detail}" + (", your recommendation" if option.get("recommended") else "") + ". Carry on with that choice."
    return f"The person did not pick one of the options; they answered in their own words: \"{answer_given['text']}\". Treat that as their answer to your question and carry on."
