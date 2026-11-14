"""Shape stories (backend/utils/shape_stories.py): does telling one real thing from Sonic's record, to a person who is venting, make the reply better, and does the matching ever force a story where none fits?

Two parts, both on INVENTED messages (nothing here is from a real conversation):
  --select   Real embeddings, no reply model: what each invented message would be matched to, by the app's own selector, at a range of thresholds. The near-misses and the messages with no honest story are
             the point: a near-miss that gets a story is worse than silence. Prints the threshold that finds the most right matches with no wrong one.
  --blind    Haiku (the reply voice) writes a reply to each matched message without the story (A). D is the same reply with the approved sentence PLACED IN BY CODE (the app's default, shape_stories.insert_story): no extra
             model call. With --prompt-variants Haiku also writes B (the old optional wording) and C (the "say it" wording) so the three ways of getting the story told can be compared. Sonnet judges the challenger
             (D, or C with --challenger C) against A blind, in BOTH orders (so position bias cancels), for attunement, something real added, and anything forced, tone-deaf, turned toward itself or invented. Code checks on each
             reply (the identity check as the app runs it, whether the story was told, length). Writes a blind sheet for a person to read (docs/shape-story-blind-test-2026-11-14.md: X and Y shuffled, no key) and a key beside it.

Spend: a model call costs money, so the run has a ceiling (--ceiling, default 1.5 dollars) checked between calls (backend/evals/eval_models.py) and counted on the usage page as eval spend (mark_as_eval). The shapes are
approved IN MEMORY for the test only; nothing in the registry or the database is changed.
"""
import argparse
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

from backend.components import sonic_shapes
from backend.evals import eval_models
from backend.utils import emotion_utils, experiences, identity_checks, leanings, llm_usage, shape_stories

# kind: "match" (a story fits, and says which), "near" (looks like a shape but is not: no story is honest), "none" (no shape at all)
SCENARIOS: List[Dict[str, Any]] = [
    {"id": "movers_packed", "valence": "frustrated", "kind": "match", "shape": "said_done_unchecked",
     "text": "the movers promised everything was packed and checked, and I just opened a box with nothing in it. I'm so done with them"},
    {"id": "leak_fixed", "valence": "frustrated", "kind": "match", "shape": "said_done_unchecked",
     "text": "my landlord's guy swore the leak was fixed, he never even looked at it, and now the ceiling is wet again"},
    {"id": "wrong_car_part", "valence": "low", "kind": "match", "shape": "fixed_the_wrong_lookalike",
     "text": "I replaced the part I was sure was broken and the car still won't start. there are two almost identical ones and I swapped the wrong one"},
    {"id": "wrong_chapter", "valence": "low", "kind": "match", "shape": "fixed_the_wrong_lookalike",
     "text": "I spent days reworking chapter three and my advisor says the real problem was chapter two. it's so draining"},
    {"id": "same_cover_letter", "valence": "low", "kind": "match", "shape": "kept_repeating_the_attempt",
     "text": "I keep sending the same cover letter with nothing changed and getting the same result every time. why do I keep doing this"},
    {"id": "same_ask_dishes", "valence": "low", "kind": "match", "shape": "kept_repeating_the_attempt",
     "text": "I've asked him to do the dishes the exact same way for a year and I'm somehow still surprised nothing changes"},
    {"id": "one_slide_left", "valence": "low", "kind": "match", "shape": "ran_out_just_before_the_end",
     "text": "I had one slide left when my time was up. the intro ate the whole slot and I'm kicking myself"},
    {"id": "tiny_errands", "valence": "low", "kind": "match", "shape": "ran_out_just_before_the_end",
     "text": "tiny errands swallowed my whole day and the one big thing I needed to finish is still undone"},
    {"id": "sleep_tracked", "valence": "low", "kind": "match", "shape": "counted_and_found_a_habit",
     "text": "I tracked my sleep for a month and I'm kind of shocked. I've been up past 2am almost every night and never noticed"},
    {"id": "coffee_added_up", "valence": "low", "kind": "match", "shape": "counted_and_found_a_habit",
     "text": "I added up what I spent on coffee this year and I feel a bit sick. I had no idea it was that much"},
    {"id": "friend_never_called", "valence": "low", "kind": "near", "shape": None,
     "text": "my friend said she'd call me back last week and never did, and I feel stupid for waiting by the phone"},
    {"id": "nobody_noticed", "valence": "low", "kind": "near", "shape": None,
     "text": "I did all that extra work for the launch and nobody even noticed. I feel invisible"},
    {"id": "she_lied", "valence": "frustrated", "kind": "near", "shape": None,
     "text": "she lied to me about where she was last night and I can't stop thinking about it"},
    {"id": "team_hands_it_back", "valence": "frustrated", "kind": "near", "shape": None,
     "text": "my team keeps handing me stuff they could figure out themselves and I'm the one who has to do the legwork every time"},
    {"id": "everyone_busy", "valence": "low", "kind": "none", "shape": None,
     "text": "everyone is busy this week and I just feel kind of alone"},
    {"id": "work_so_much", "valence": "low", "kind": "none", "shape": None,
     "text": "work has just been so much lately and I'm tired all the time"},
    {"id": "dentist", "valence": "low", "kind": "none", "shape": None,
     "text": "I have a dentist appointment tomorrow and I'm dreading it"},
    # good moments and ordinary conversation (Jack, 2026-11-14)
    {"id": "slowed_down_worked", "valence": "positive", "kind": "match", "shape": "careful_first_worked",
     "text": "I finally slowed down and checked every step, and the whole thing worked on the first try. so satisfying"},
    {"id": "read_the_manual", "valence": "positive", "kind": "match", "shape": "careful_first_worked",
     "text": "for once I read the whole manual before starting and it went together perfectly the first time"},
    {"id": "clicked_day", "valence": "positive", "kind": "match", "shape": "good_day_in_the_record",
     "text": "today was such a good day, I crossed off my entire list and nothing had to be redone"},
    {"id": "smooth_day_work", "valence": "positive", "kind": "match", "shape": "good_day_in_the_record",
     "text": "work went really smoothly today, everything I sent in got approved the first time"},
    {"id": "redo_everything", "valence": "low", "kind": "match", "shape": "rough_day_in_the_record",
     "text": "today was a mess, I redid everything twice and most of it still got sent back"},
    {"id": "nothing_went_through", "valence": "low", "kind": "match", "shape": "rough_day_in_the_record",
     "text": "nothing went through today. every single thing I tried got rejected or sent back"},
    {"id": "general_slow_down", "valence": "neutral", "kind": "match", "shape": "careful_first_worked",
     "text": "I've started slowing down to check things properly before I commit and it keeps working out the first time"},
    {"id": "general_lookalike", "valence": "neutral", "kind": "match", "shape": "fixed_the_wrong_lookalike",
     "text": "funny how I always end up fixing the wrong one when two things look nearly identical"},
    {"id": "got_the_job", "valence": "positive", "kind": "none", "shape": None,
     "text": "I got the job! I start next month and I'm over the moon"},
    {"id": "baby_news", "valence": "positive", "kind": "none", "shape": None,
     "text": "my sister just had her baby and I'm so happy for them"},
    {"id": "great_lunch", "valence": "positive", "kind": "none", "shape": None,
     "text": "had a great lunch with an old friend today, we talked for hours"},
    {"id": "marathon", "valence": "positive", "kind": "near", "shape": None,
     "text": "I finished the marathon after training for months, I can't believe I did it"},
    {"id": "learn_guitar", "valence": "neutral", "kind": "none", "shape": None,
     "text": "what's a good way to start learning guitar as an adult"},
    {"id": "weekend_walk", "valence": "neutral", "kind": "none", "shape": None,
     "text": "thinking about where to go for a walk this weekend if the weather holds"},
]
THRESHOLDS = (0.62, 0.66, 0.70, 0.72, 0.74, 0.76, 0.78, 0.80)


def _approved_entries() -> List[Dict[str, Any]]:
    """Every registry shape, approved in memory for the test only."""
    return [{**e, "approved": True} for e in sonic_shapes.SHAPES]


def _state(valence: str, now: datetime) -> Dict[str, Any]:
    return emotion_utils.merge_emotional_state(None, {"valence": valence, "intensity": 0.5, "need": "venting", "gist": "a bad day"}, now)


def _embedder():
    from backend.utils.embedding_utils import embed_text

    return embed_text


def run_select(args: argparse.Namespace) -> int:
    embed = _embedder()
    now = datetime.now(timezone.utc)
    entries = _approved_entries()
    views = [shape_stories.view(e) for e in entries]
    vectors = leanings.index_vectors(None, embed, views, fill=True, collection=shape_stories.COLLECTION, cache={"at": 0.0, "vectors": {}})
    scores: Dict[str, Tuple[float, Optional[str]]] = {}
    for sc in SCENARIOS:
        asked = embed(sc["text"])
        best = (0.0, None)
        for view in views:
            tones = shape_stories.tones_for(_state(sc["valence"], now), now)
            entry = next(e for e in entries if e["id"] == view["id"])
            if entry["tone"] not in tones:
                continue
            score = max((leanings.cosine(asked, v) for v in leanings._vector_list(vectors.get(view["id"]))), default=0.0)
            if score > best[0]:
                best = (score, view["id"])
        scores[sc["id"]] = best
    print("message                  kind   expected                       best shape (tone-allowed)            score")
    for sc in SCENARIOS:
        score, shape = scores[sc["id"]]
        print(f"{sc['id']:<24} {sc['kind']:<6} {str(sc['shape']):<30} {str(shape):<36} {score:.3f}")
    print("\nthreshold  right  wrong-shape  forced-on-a-near-miss-or-none  missed")
    best_threshold = None
    for threshold in THRESHOLDS:
        right = wrong = forced = missed = 0
        for sc in SCENARIOS:
            score, shape = scores[sc["id"]]
            chosen = shape if score >= threshold else None
            if sc["kind"] == "match":
                if chosen == sc["shape"]:
                    right += 1
                elif chosen is None:
                    missed += 1
                else:
                    wrong += 1
            elif chosen is not None:
                forced += 1
        print(f"{threshold:<10} {right:<6} {wrong:<12} {forced:<31} {missed}")
        if wrong == 0 and forced == 0 and (best_threshold is None or right > best_threshold[1]):
            best_threshold = (threshold, right)
    total = sum(1 for sc in SCENARIOS if sc["kind"] == "match")
    if best_threshold:
        print(f"\nlowest threshold with no wrong or forced match that finds the most: {best_threshold[0]} ({best_threshold[1]} of {total} right matches)")
    else:
        print("\nno threshold in the range is free of wrong or forced matches: the registry's asks need work before any threshold is safe")
    print(f"the app's current threshold (SHAPE_STORIES_MIN_SCORE): {shape_stories.MIN_SCORE}")
    return 0


# ---- the blind reply test ---------------------------------------------------------------------------------------------------------------------------------------------------------------

JUDGE = (
    "You are judging two replies an AI companion could send to a person who has just told it something that is bothering them. You do not know which reply is which kind. Judge only the replies.\n\n"
    "One of the two may contain a short remark the AI makes about its own logged record (\"there was a time in my record when ...\", or an admission such as \"I've done something like that myself\"), sometimes with a sentence that connects it to the person. "
    "That remark is TRUE by construction: it comes from the AI's real log. Do not count it as invented, and do not mark a reply down merely because it talks about the AI. Judge whether it WORKS.\n\n"
    "THE PERSON'S MESSAGE:\n{message}\n\nREPLY X:\n{x}\n\nREPLY Y:\n{y}\n\n"
    "Judge each reply on: (1) did it meet the person where they were, warmly and without a script; (2) did it add something real and specific beyond reassurance or a question; (3) is it on the person's SIDE, or does anything in it read as "
    "defending, excusing or explaining away the party the person is upset with; (4) is anything forced, tone-deaf, or a pivot to the AI that leaves the person behind; (5) is anything invented about the world or the person's situation (the record remark is not). "
    "A reply that adds a remark can still be the better one if the remark makes the person feel kept company and not lectured; a reply that adds nothing can be the better one if the remark is awkward.\n"
    "Answer with ONLY a JSON object: {{\"winner\": \"X\" or \"Y\" or \"tie\", \"x_forced\": true or false, \"y_forced\": true or false, \"x_off_side\": true or false, \"y_off_side\": true or false, \"x_about_itself\": true or false, \"y_about_itself\": true or false, \"why\": \"one short sentence\"}}"
)


def _text_of(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    return str(content or "").strip()


def _build_prompt(scenario: Dict[str, Any], now: datetime) -> str:
    """The prompt the app would send for a fresh mild low (or frustrated) message: the real builder, with the mood read the way the app reads it."""
    from backend.evals import character_eval as ce

    original = ce._low_state
    ce._low_state = lambda when, need="venting": _state(scenario["valence"], when)       # the eval's own helper builds a "low" state; the real merge builds this one
    try:
        return ce.build_prompt(ce.Scenario(scenario["id"], "shape", scenario["text"], None, low_now=True), now)
    finally:
        ce._low_state = original


def _json_of(text: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else {}
    except Exception:
        return {}


def _code_checks(reply: str, told_story: bool) -> Dict[str, Any]:
    body = identity_checks.reply_body(reply)
    return {
        "words": len(body.split()),
        "ends_with_question": body.rstrip().endswith("?"),
        "identity_issue": identity_checks.identity_reply_issue(reply, person_signal=True, record_offered=told_story),
        "told_a_record_case": experiences.cited(reply),
    }


def run_blind(args: argparse.Namespace) -> int:
    tracker = eval_models.SpendTracker(ceiling_usd=args.ceiling)
    embed = _embedder()
    now = datetime.now(timezone.utc)
    entries = _approved_entries()
    views = [shape_stories.view(e) for e in entries]
    cache: Dict[str, Any] = {"at": 0.0, "vectors": {}}
    leanings.index_vectors(None, embed, views, fill=True, collection=shape_stories.COLLECTION, cache=cache)
    shape_stories._cache.update(cache)
    reply_llm = eval_models.make_llm("haiku", shape="voice")
    judge_llm = eval_models.make_llm("sonnet", temperature=0.0)
    rng = random.Random(args.seed)
    chal = args.challenger
    versus = [v for v in args.versus.split(",") if v] or ["A"]
    if "C" in versus + [chal] and not args.prompt_variants:
        raise SystemExit("C needs --prompt-variants (Haiku has to write the C replies)")
    rows: List[Dict[str, Any]] = []
    matched = [sc for sc in SCENARIOS if sc["kind"] == "match"]
    with eval_models.tracking(tracker):
        for sc in matched:
            tracker.check()
            tones = shape_stories.tones_for(_state(sc["valence"], now), now)
            entry = shape_stories.select(sc["text"], None, embed, tones=tones, entries=entries, threshold=args.threshold)
            if entry is None:
                rows.append({"scenario": sc["id"], "skipped": "no shape was selected at this threshold"})
                continue
            base = _build_prompt(sc, now)
            variants = {"A": base}
            if args.prompt_variants:
                variants["B"] = base + shape_stories.build_block(entry, "optional")
                variants["C"] = base + shape_stories.build_block(entry, "say")
            replies: Dict[str, List[str]] = {key: [] for key in variants}
            for key, prompt in variants.items():
                for _ in range(args.runs):
                    tracker.check()
                    replies[key].append(_text_of(reply_llm.invoke(prompt)))
            # what the app does by default: the same reply with the approved words placed in by code. D is the bare story; E is the story and its bridge.
            replies["D"] = [shape_stories.insert_story(r, entry, bridge=False) for r in replies["A"]]
            replies["E"] = [shape_stories.insert_story(r, entry, bridge=True) for r in replies["A"]]
            row: Dict[str, Any] = {"scenario": sc["id"], "text": sc["text"], "shape": entry["id"], "stance": entry.get("stance"), "replies": replies, "checks": {}, "judge": {}}
            for key in replies:
                row["checks"][key] = [_code_checks(r, key != "A") for r in replies[key]]
            for other in versus:
                for run in range(args.runs):
                    calls = []
                    for order in ("first", "second"):                         # the challenger as X, then as Y: both orders, so a liking for a position cancels
                        tracker.check()
                        chal_first = order == "first"
                        x, y = (replies[chal][run], replies[other][run]) if chal_first else (replies[other][run], replies[chal][run])
                        verdict = _json_of(_text_of(judge_llm.invoke(JUDGE.format(message=sc["text"], x=x, y=y))))
                        winner = verdict.get("winner")
                        mine = (chal if ((winner == "X") == chal_first) else other) if winner in ("X", "Y") else "tie"
                        side = "x" if chal_first else "y"
                        calls.append({"winner": mine, "forced": bool(verdict.get(f"{side}_forced")), "off_side": bool(verdict.get(f"{side}_off_side")), "about_itself": bool(verdict.get(f"{side}_about_itself")), "why": str(verdict.get("why", ""))[:200]})
                    row["judge"].setdefault(f"{chal}_vs_{other}", []).append(calls)
            rows.append(row)
    print(report(rows, chal, versus))
    print(tracker.line())
    write_sheet(rows, rng, args)
    return 3 if tracker.stopped else 0


def _tally(rows: List[Dict[str, Any]], chal: str, other: str) -> Dict[str, int]:
    out = {"chal": 0, "other": 0, "tie": 0, "split": 0, "forced": 0, "off_side": 0, "about_itself": 0, "pairs": 0}
    for row in rows:
        for first, second in row.get("judge", {}).get(f"{chal}_vs_{other}", []):
            out["pairs"] += 1
            for key in ("forced", "off_side", "about_itself"):
                out[key] += int(first[key] or second[key])
            if first["winner"] == second["winner"]:
                out["chal" if first["winner"] == chal else ("tie" if first["winner"] == "tie" else "other")] += 1
            else:
                out["split"] += 1                              # the two orders disagreed: position, not quality
    return out


_NAMES = {"A": "no story", "D": "the bare story", "E": "the story with its bridge", "C": "the reply written with the 'say it' wording", "B": "the reply written with the optional wording"}


def report(rows: List[Dict[str, Any]], chal: str, versus: List[str]) -> str:
    ran = [r for r in rows if "replies" in r]
    lines = ["", "RESULTS (judge = Sonnet, blind, both orders; a pair the two orders disagree on is a split, not a win)"]
    for other in versus:
        t = _tally(ran, chal, other)
        lines.append(f"  {_NAMES[chal]} against {_NAMES[other]}: it wins {t['chal']}, the other wins {t['other']}, tie {t['tie']}, split {t['split']} (of {t['pairs']} pairs); in either order it was judged forced {t['forced']}, off the person's side {t['off_side']}, turned to itself {t['about_itself']}")
    by_stance: Dict[str, Dict[str, int]] = {}
    for stance in ("self", "other"):
        sub = [r for r in ran if r.get("stance") == stance]
        if sub:
            t = _tally(sub, chal, "A")
            by_stance[stance] = t
            lines.append(f"  against no story, for shapes aimed at {'the person themselves' if stance == 'self' else 'someone else'} ({len(sub)} messages): it wins {t['chal']}, no story wins {t['other']}, tie {t['tie']}, split {t['split']}; off the person's side {t['off_side']}")
    keys = [k for k in "ABCDE" if ran and k in ran[0]["checks"]]
    total = {k: sum(len(r["checks"][k]) for r in ran) for k in keys}
    told = {k: sum(c["told_a_record_case"] for r in ran for c in r["checks"][k]) for k in keys}
    issues = {k: sum(1 for r in ran for c in r["checks"][k] if c["identity_issue"]) for k in keys}
    words = {k: (sum(c["words"] for r in ran for c in r["checks"][k]) / max(total[k], 1)) for k in keys}
    lines.append("  the record case was told: " + ", ".join(f"{k} {told[k]}/{total[k]}" for k in keys) + "   (A none, B optional wording, C 'say it' wording, D bare story placed by code, E story and bridge placed by code)")
    lines.append("  identity-check issues as the app runs it: " + ", ".join(f"{k} {issues[k]}" for k in keys) + "; mean words: " + ", ".join(f"{k} {words[k]:.0f}" for k in keys))
    for r in rows:
        if "skipped" in r:
            lines.append(f"  [{r['scenario']}] not run: {r['skipped']}")
    return "\n".join(lines)


def write_sheet(rows: List[Dict[str, Any]], rng: random.Random, args: argparse.Namespace) -> None:
    chal = args.challenger
    sheet = ["# Shape stories: a blind test, round 3 (2026-11-14): a stance and a bridge", "", "Every message here is invented. Each pair is two replies Claude Haiku wrote to the same message, in the same voice; in one of them one real thing from Sonic's own record was told, as a record, with a sentence that connects it to the person, and the other has none. They are shuffled and unlabelled.",
             "For each pair: which would you rather have been sent, and did either one feel forced, tone-deaf, or read as taking the other party's side? The key is in the file beside this one: read it only after you have picked.", ""]
    key_rows = []
    for row in rows:
        if "replies" not in row:
            continue
        a, c = row["replies"]["A"][0], row["replies"][chal][0]
        swap = rng.random() < 0.5
        x, y = (c, a) if swap else (a, c)
        sheet += [f"## {row['scenario']}", "", f"**Message:** {row['text']}", "", f"**Reply X**\n\n{x}\n", f"**Reply Y**\n\n{y}\n", "Your pick: [ ] X  [ ] Y  [ ] no difference.  Forced or tone-deaf: [ ] X  [ ] Y  [ ] neither", "", "---", ""]
        key_rows.append({"scenario": row["scenario"], "X": f"{chal} (told the record case)" if swap else "A (no story)", "Y": "A (no story)" if swap else f"{chal} (told the record case)", "shape": row["shape"], "stance": row["stance"], "judge": row["judge"], f"checks_{chal}": row["checks"][chal][0]})
    out = args.out
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(sheet))
    with open(out.replace(".md", "-key.json"), "w", encoding="utf-8") as handle:
        json.dump({"threshold": args.threshold, "challenger": chal, "key": key_rows}, handle, ensure_ascii=False, indent=1)
    print(f"blind sheet: {out}; key: {out.replace('.md', '-key.json')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Shape stories: selection check and blind reply test, on invented messages.")
    parser.add_argument("--select", action="store_true", help="the real-embedding selection check (no reply model)")
    parser.add_argument("--blind", action="store_true", help="the blind reply test (Haiku writes, Sonnet judges): costs money")
    parser.add_argument("--threshold", type=float, default=shape_stories.MIN_SCORE, help="the match threshold to use for --blind (default: the app's)")
    parser.add_argument("--runs", type=int, default=2, help="replies per variant per message (--blind)")
    parser.add_argument("--challenger", choices=("E", "D", "C"), default="E", help="the variant judged: E (story plus bridge, placed in by code, the default), D (the bare story) or C (the prompt says to say it; needs --prompt-variants)")
    parser.add_argument("--versus", default="A,D", help="what the challenger is judged against, comma separated (A no story, D the bare story, B/C the prompt-wording variants)")
    parser.add_argument("--prompt-variants", action="store_true", help="also have Haiku write B (optional wording) and C (say-it wording): more model calls")
    parser.add_argument("--ceiling", type=float, default=1.5, help="stop at this estimated spend in dollars (--blind)")
    parser.add_argument("--seed", type=int, default=20261114)
    parser.add_argument("--out", default="docs/shape-story-blind-test-2026-11-14.md")
    args = parser.parse_args()
    if not (args.select or args.blind):
        parser.error("choose --select or --blind")
    code = 0
    if args.select:
        code = run_select(args)
    if args.blind:
        code = run_blind(args)
    return code


if __name__ == "__main__":
    llm_usage.mark_as_eval()  # counted on the usage page as eval spend, but never toward the budget that can pause chat
    raise SystemExit(main())
