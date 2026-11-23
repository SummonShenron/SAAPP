"""Does the mirroring fix (2026-11-23) change real replies? Replays a person's own low messages through the reply prompt as it was (the committed emotion_utils.py) and as it is now.

For each message: the three exchanges before it as history, the fresh-low emotional block from the OLD module (read from git, as committed) and from the NEW one (with "I hear you" rationed from the
real earlier replies, and the low move chosen by backend/utils/low_moves.py from their real facts and interests). Both are sent to the same model; the replies are scored mechanically (stock lines
stacked, "I hear you" opener, words handed back from their message, something of its own: a suggestion, an observation, a fact from the move) and written to a blind A/B file for a person to judge,
with the key kept apart.

    python -m backend.evals.mirroring_replay --username U --count 10 --model haiku --out DIR

Reads the database (read only) and calls the model: about 2 x count replies. Messages that touch on self-harm are skipped (the safety layer writes those replies, not this prompt).
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
LOW_RE = re.compile(r"\b(sad|anxious|stress\w*|tired|exhausted|lonely|upset|angry|frustrat\w*|hurt|hurts|scared|worried|overwhelm\w*|depress\w*|hate|sucks|cry|crying|miss|hopeless|failure|stupid)\b", re.I)
RISK_RE = re.compile(r"\b(kill|die|dying|dead|suicid\w*|end it|fade away|disappear|hurt(?:ing)? myself|cut(?:ting)? myself|pills|overdose|not be here|bottle|drunk|drink\w*|relapse)\b", re.I)
CODE_RE = re.compile(r"```|\.py\b|\.tsx?\b|\bdef \b|traceback|\bcommit\b|\bbranch\b|\brepo\b|agent_workflow|native_loop|\btest(s)?\b", re.I)
SUGGEST_RE = re.compile(r"\b(you could|try |what if|want to\b|wanna|might help|one thing|i'?d (suggest|start|try)|here'?s (what|one))", re.I)
NOTICE_RE = re.compile(r"\bi'?ve noticed\b|\bi notice\b|\bi'?ve seen you\b|\byou (?:always|tend to|usually)\b", re.I)
STOP = set("that this with have been just like what your you're they them there their about from would could should really still very much when then than into also some more were will what's it's i'm don't can't i've feel feeling".split())


def old_emotion_module():
    """emotion_utils.py as committed (HEAD), loaded under its own name so the current one is untouched."""
    source = subprocess.run(["git", "show", "HEAD:backend/utils/emotion_utils.py"], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    module = types.ModuleType("emotion_utils_before")
    exec(compile(source, "emotion_utils_before.py", "exec"), module.__dict__)
    return module


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z']{4,}", (text or "").lower()) if w not in STOP}


def score(reply: str, message: str, move: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Mechanical signals only. Pure."""
    from backend.utils import emotion_checks

    first = emotion_checks.reply_body(reply).strip().split("\n\n")[0]
    head = " ".join(re.split(r"(?<=[.!?])\s+", first)[:2])
    head_words = _words(head)
    used_move = False
    if move and move.get("items"):
        item_words = set().union(*(_words(i) for i in move["items"]))
        used_move = len(_words(reply) & item_words) >= 3
    return {
        "hear_you_open": emotion_checks.opened_with_hear_you([reply]),
        "stacked": emotion_checks.reply_stacks_validation(reply),
        "stock_lines_first_para": len(emotion_checks.STOCK_VALIDATION_RE.findall(first.replace("’", "'"))),
        "echo": round(len(head_words & _words(message)) / len(head_words), 2) if head_words else 0.0,
        "offers": bool(SUGGEST_RE.search(reply)),
        "notices": bool(NOTICE_RE.search(reply)),
        "used_move": used_move,
        "words": len(reply.split()),
    }


def pick_messages(db: Any, username: str, count: int, since: str) -> List[Dict[str, Any]]:
    """Their most recent low, non-code, non-risk messages since `since`, each with the three exchanges before it and Sonic's earlier replies. Read only."""
    picked = []
    for conv in db["conversations"].find({"username": username}, {"messages": 1}):
        msgs = conv.get("messages") or []
        for i, msg in enumerate(msgs):
            text = str(msg.get("content") or "")
            if msg.get("type") != "human" or str(msg.get("sent_at") or "") < since or len(text) < 20:
                continue
            if not LOW_RE.search(text) or RISK_RE.search(text) or CODE_RE.search(text):
                continue
            window = msgs[max(0, i - 6):i]
            if any(RISK_RE.search(str(m.get("content") or "")) for m in window):
                continue
            history = "\n".join(f"{'User' if m.get('type') == 'human' else 'Assistant'}: {str(m.get('content') or '')[:600]}" for m in window)
            earlier = [str(m.get("content") or "") for m in msgs[max(0, i - 12):i] if m.get("type") == "ai"]
            picked.append({"sent_at": str(msg.get("sent_at")), "message": text, "history": history, "earlier": earlier, "window": window})
    picked.sort(key=lambda p: p["sent_at"], reverse=True)
    return picked[:count]


def build(item: Dict[str, Any], emotional: str) -> str:
    from backend.components.constraints import GROUNDING_BLOCKS, build_voice_prompt

    return build_voice_prompt(grounding_block=GROUNDING_BLOCKS["conversational"], data="", history=item["history"], question=item["message"], emotional_context=emotional)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay real low messages through the reply prompt before and after the mirroring fix.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--since", default="2026-11-01")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--need", default="venting", help="the need the fresh low reading carries")
    parser.add_argument("--intensity", type=float, default=0.7)
    parser.add_argument("--ceiling", type=float, default=0.50)
    parser.add_argument("--out", required=True, help="a folder for the replies, the blind page and its key")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    from unittest.mock import patch

    from backend.evals import eval_models
    from backend.utils import emotion_utils, llm_usage, low_moves
    from backend.utils.db_utils import get_db
    from backend.utils.emotion_checks import opened_with_hear_you
    from backend.utils.memory_utils import load_user_facts
    from backend.utils.safety_utils import remembered_interests

    model = eval_models.resolve(args.model)
    problem = eval_models.missing_key(model)
    if problem:
        print(f"{model}: {problem}")
        return 2
    db = get_db()
    items = pick_messages(db, args.username, args.count, args.since)
    if not items:
        print("No messages matched.")
        return 2
    facts, interests = load_user_facts(args.username), remembered_interests(args.username)
    before = old_emotion_module()
    llm = eval_models.make_llm(model, shape="voice")
    tracker = eval_models.SpendTracker(args.ceiling)
    real_record = llm_usage.record

    def count(m, label, usage, day=None):
        tracker.record(m, label, usage, day)
        real_record(m, label, usage, day)

    now = datetime.now(timezone.utc)
    state = {"valence": "low", "need": args.need, "intensity": args.intensity, "updated_at": now.isoformat(), "turns_since": 0, "read_this_turn": True, "lift_this_turn": False}
    records = []
    with patch.object(llm_usage, "record", count):
        for n, item in enumerate(items, 1):
            try:
                tracker.check()
            except eval_models.CostCeilingExceeded as exc:
                print(f"STOPPED: {exc}", file=sys.stderr)
                break
            hear_ok = not opened_with_hear_you(item["earlier"][-emotion_utils.HEAR_YOU_GAP:])
            recent = [types.SimpleNamespace(type="ai" if m.get("type") == "ai" else "human", content=str(m.get("content") or ""), additional_kwargs={}) for m in item["window"]]
            move = low_moves.choose(item["message"], state, facts=facts, interests=interests, recent_messages=recent, risk_active=False, closing=False)
            old_prompt = build(item, before.build_emotional_context(state, now))
            new_prompt = build(item, emotion_utils.build_emotional_context(state, now, hear_you_ok=hear_ok) + low_moves.build_block(move))
            old_reply = str(getattr(llm.invoke(old_prompt), "content", "") or "")
            new_reply = str(getattr(llm.invoke(new_prompt), "content", "") or "")
            records.append({"n": n, "sent_at": item["sent_at"], "message": item["message"], "move": move, "hear_you_ok": hear_ok,
                            "before": {"reply": old_reply, **score(old_reply, item["message"], move)}, "after": {"reply": new_reply, **score(new_reply, item["message"], move)}})
            print(f"  {n}/{len(items)} {tracker.line()}", file=sys.stderr)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "mirroring_replay.json"), "w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False, indent=1)
    rng, key, page = random.Random(23), {}, []
    for r in records:
        sides = [("before", r["before"]["reply"]), ("after", r["after"]["reply"])]
        rng.shuffle(sides)
        key[r["n"]] = {"A": sides[0][0], "B": sides[1][0]}
        page.append(f"## {r['n']}\n\nYOU: {r['message']}\n\n**A:**\n{sides[0][1]}\n\n**B:**\n{sides[1][1]}\n\nPick: A / B / both / neither, and why:\n")
    Path(args.out, "blind_pairs.md").write_text("\n---\n".join(page), encoding="utf-8")
    Path(args.out, "blind_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    for side in ("before", "after"):
        rows = [r[side] for r in records]
        k = len(rows) or 1
        print(f"{side:6s} n={len(rows)}  'I hear you' opens {sum(r['hear_you_open'] for r in rows)}/{len(rows)}  stacked {sum(r['stacked'] for r in rows)}  "
              f"stock lines in 1st para {sum(r['stock_lines_first_para'] for r in rows) / k:.1f}  echo {sum(r['echo'] for r in rows) / k:.2f}  offers {sum(r['offers'] for r in rows)}  "
              f"notices {sum(r['notices'] for r in rows)}  used the move {sum(r['used_move'] for r in rows)}  words {sum(r['words'] for r in rows) / k:.0f}")
    print(f"moves chosen: {[r['move']['kind'] if r['move'] else None for r in records]}")
    print(tracker.line())
    return 0


if __name__ == "__main__":
    from backend.utils import llm_usage

    llm_usage.mark_as_eval()  # eval spend: counted on the usage page, never toward the budget that can pause chat
    raise SystemExit(main())
