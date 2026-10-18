"""One-off: bind the facts that were saved before fact links existed (backend/utils/fact_links.py).

New facts are linked when they are saved, but a store built earlier has none (a real one had 2 links across 400 facts), so recalling a fact brought nothing along.
This proposes the links over the existing store, in two steps so that nothing is written until the proposals have been read:

    python -m backend.utils.fact_links_backfill --user someone@example.com            # asks the small model, writes the proposals to a file, changes nothing
    python -m backend.utils.fact_links_backfill --user someone@example.com --verify   # a strict second look at each proposed pair: keeps the ones that pass, lists the rest
    python -m backend.utils.fact_links_backfill --user someone@example.com --apply    # applies exactly the proposals in that file
    python -m backend.utils.fact_links_backfill --user someone@example.com --apply --skip 4,17   # ... except proposals number 4 and 17

It does what saving a fact does: each fact, oldest first, is shown the few earlier facts most like it and the model says which are about the SAME real thing (and
names the thing), the same prompt, the same caps. Run on a person's own store; the proposals file lists their facts in full, so keep it out of anything shared.
The model calls are small and real spend (about 400 for a full store), which is why the default is a dry run. Safe to re-run: pairs already linked are not proposed
again, and applying twice does nothing the second time.
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.utils import fact_links

PROPOSALS_JSON = os.path.join("saapp_data", "fact_link_proposals.json")
PROPOSALS_MD = os.path.join("saapp_data", "fact_link_proposals.md")
WORKERS = 4


def eligible(facts: Sequence[Any]) -> List[Any]:
    """Active facts with an embedding, oldest first. Identity facts are in a reply anyway, so they are never linked (as when saving)."""
    return sorted(
        (f for f in facts if getattr(f, "active", True) and getattr(f, "embedding", None) and f.category != "identity"),
        key=lambda f: (f.created_at, f.id),
    )


def plan_links(
    facts: Sequence[Any],
    similarity: Callable[[Sequence[float], Sequence[float]], float],
    invoke: Callable[[str], Any],
    limit: Optional[int] = None,
    workers: int = WORKERS,
) -> List[Dict[str, Any]]:
    """Proposed links as [{"new": id, "other": id, "thing": str}], numbered by the caller. Each fact is judged against the facts older than it, like a fact being saved.
    `limit` stops after that many facts have been judged (a sample). Changes nothing."""
    ordered = eligible(facts)
    already = {frozenset((f.id, other)) for f in facts for other in (getattr(f, "related", None) or [])}

    def judge(index: int) -> List[Dict[str, Any]]:
        fact = ordered[index]
        candidates = fact_links.candidate_facts(ordered[:index], fact, similarity)
        if not candidates:
            return []
        try:
            response = invoke(fact_links.build_link_prompt(fact.fact, candidates))
            raw = response.content if hasattr(response, "content") else response
            picks = fact_links.parse_links(raw, len(candidates))
        except Exception:
            return []
        return [{"new": fact.id, "other": candidates[position - 1].id, "thing": thing} for position, thing in picks]

    indexes = list(range(len(ordered) if limit is None else min(limit, len(ordered))))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        judged = list(pool.map(judge, indexes))
    proposals: List[Dict[str, Any]] = []
    held: Dict[str, int] = {f.id: len(getattr(f, "related", None) or []) for f in facts}
    for group in judged:
        for item in group:
            pair = frozenset((item["new"], item["other"]))
            if pair in already or held.get(item["new"], 0) >= fact_links.MAX_LINKS_PER_FACT or held.get(item["other"], 0) >= fact_links.MAX_LINKS_PER_FACT:
                continue
            already.add(pair)
            held[item["new"]] = held.get(item["new"], 0) + 1
            held[item["other"]] = held.get(item["other"], 0) + 1
            proposals.append(item)
    return proposals


def verify_links(
    facts: Sequence[Any],
    proposals: Sequence[Dict[str, Any]],
    invoke: Callable[[str], Any],
    workers: int = WORKERS,
) -> Dict[str, List[Dict[str, Any]]]:
    """A second, stricter look at each proposed pair on its own: {"kept": [...], "dropped": [...]}. Anything but a clear yes drops the pair, including a model that fails."""
    by_id = {f.id: f for f in facts}

    def check(item: Dict[str, Any]) -> bool:
        new, other = by_id.get(item["new"]), by_id.get(item["other"])
        return new is not None and other is not None and fact_links.verify_pair(new.fact, other.fact, item.get("thing") or "", invoke)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        verdicts = list(pool.map(check, proposals))
    return {"kept": [p for p, ok in zip(proposals, verdicts) if ok], "dropped": [p for p, ok in zip(proposals, verdicts) if not ok]}


def apply_links(facts: Sequence[Any], proposals: Sequence[Dict[str, Any]], skip: Sequence[int] = ()) -> int:
    """Makes the proposed links in place (both ways, within the caps), leaving out the 1-based numbers in `skip` and any proposal whose facts have since gone or
    changed state. Returns how many links were made."""
    by_id = {f.id: f for f in facts if getattr(f, "active", True)}
    skipped = set(skip)
    made = 0
    for number, item in enumerate(proposals, start=1):
        if number in skipped:
            continue
        new, other = by_id.get(item.get("new")), by_id.get(item.get("other"))
        if new is None or other is None:
            continue
        made += fact_links.add_links(new, [other])
    return made


def report(facts: Sequence[Any], proposals: Sequence[Dict[str, Any]], dropped: Sequence[Dict[str, Any]] = ()) -> str:
    by_id = {f.id: f for f in facts}
    lines = [f"# Proposed fact links ({len(proposals)})", "", "Read each pair: do these two notes belong together (the same job, project, person, trip)? Re-run with `--apply --skip N,N` to leave any out.", ""]
    for number, item in enumerate(proposals, start=1):
        new, other = by_id.get(item["new"]), by_id.get(item["other"])
        if new and other:
            lines += [f"{number}. **{item.get('thing') or '(no name)'}**", f"   - {new.fact}", f"   - {other.fact}", ""]
    if dropped:
        lines += [f"# Dropped by the second look ({len(dropped)})", "", "These were proposed and then refused. They are not applied.", ""]
        for item in dropped:
            new, other = by_id.get(item["new"]), by_id.get(item["other"])
            if new and other:
                lines += [f"- **{item.get('thing') or '(no name)'}**", f"   - {new.fact}", f"   - {other.fact}", ""]
    return "\n".join(lines)


def _parse_skip(argv: List[str]) -> List[int]:
    if "--skip" not in argv or argv.index("--skip") + 1 >= len(argv):
        return []
    return [int(part) for part in argv[argv.index("--skip") + 1].split(",") if part.strip().isdigit()]


def main(argv: List[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if "--user" not in argv or argv.index("--user") + 1 >= len(argv):
        print("Name the person whose store to read: --user someone@example.com")
        return 2
    user = argv[argv.index("--user") + 1]
    from backend.utils import memory_utils as mu

    facts = mu.load_user_facts(user)
    if "--verify" in argv:
        if not os.path.exists(PROPOSALS_JSON):
            print(f"No proposals file at {PROPOSALS_JSON}. Run without --verify first.")
            return 1
        with open(PROPOSALS_JSON, encoding="utf-8") as handle:
            saved = json.load(handle)
        if saved.get("user") != user:
            print("That proposals file was made for a different person.")
            return 1
        from backend.models.models import lite_llm

        result = verify_links(facts, saved["proposals"], lite_llm.invoke)
        with open(PROPOSALS_JSON, "w", encoding="utf-8") as handle:
            json.dump({"user": user, "proposals": result["kept"], "verified": True}, handle)
        with open(PROPOSALS_MD, "w", encoding="utf-8") as handle:
            handle.write(report(facts, result["kept"], result["dropped"]))
        print(f"{len(result['kept'])} of {len(saved['proposals'])} links passed the second look; {len(result['dropped'])} were dropped. Read {PROPOSALS_MD}.")
        return 0
    if "--apply" in argv:
        if not os.path.exists(PROPOSALS_JSON):
            print(f"No proposals file at {PROPOSALS_JSON}. Run without --apply first.")
            return 1
        with open(PROPOSALS_JSON, encoding="utf-8") as handle:
            saved = json.load(handle)
        if saved.get("user") != user:
            print("That proposals file was made for a different person.")
            return 1
        made = apply_links(facts, saved["proposals"], _parse_skip(argv))
        fact_links.drop_dangling_links(facts)
        if made:
            mu.save_user_facts(user, facts)
            mu.clear_facts_cache()
        print(f"{made} links made.")
        return 0
    from backend.models.models import lite_llm
    from backend.utils.embedding_utils import cosine_similarity

    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv and argv.index("--limit") + 1 < len(argv) else None
    proposals = plan_links(facts, cosine_similarity, lite_llm.invoke, limit=limit)
    os.makedirs("saapp_data", exist_ok=True)
    with open(PROPOSALS_JSON, "w", encoding="utf-8") as handle:
        json.dump({"user": user, "proposals": proposals}, handle)
    with open(PROPOSALS_MD, "w", encoding="utf-8") as handle:
        handle.write(report(facts, proposals))
    print(f"{len(proposals)} links proposed across {len(eligible(facts))} facts. Read {PROPOSALS_MD}; nothing was written to the database.")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    sys.exit(main(sys.argv[1:]))
