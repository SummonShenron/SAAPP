"""Sorting the facts already stored into the part of life each is about (backend/utils/memory_utils.py VALID_DOMAINS), once, and reversibly.

WHY. Sonic merged the person's job work with their own project ("the parser nightmares we deal with") because facts reached the reply as one flat list, and many of them
were filed in the wrong bucket or already mixed the two. New facts are now written with a domain; this tags the ones from before.

HOW IT STAYS SAFE.
- A dry run by default (memory_maintenance classify-domains); nothing changes without --apply.
- A tag changes nothing but the new `domain` field. A REWRITE (a fact that wrongly joins the job and an own project) replaces the fact's text, so it is held to the grounding
  rule: it may only use details already in the original (backend/utils/memory_grounding.py), and a fact the person wrote themselves is never reworded.
- Before anything is written, each changed fact's original is copied into BACKUP_COLLECTION; `restore` puts it back (and removes any fact the rewrite added).
- Facts of a protected kind (a remembered support contact) are left alone.
- The model sees fact text only, in batches, and nothing about the person beyond the two short descriptions given on the command line.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.utils import memory_grounding
from backend.utils.memory_utils import PROTECTED_SOURCES, USER_EDIT_SOURCE, VALID_CATEGORIES, VALID_DOMAINS, UserFact, fact_for_state, normalize_domain

logger = logging.getLogger("SASS Logger")

BACKUP_COLLECTION = "user_memory_domain_backups"
BATCH_SIZE = 25
MAX_REWRITE_PARTS = 3
MAX_FACT_CHARS = 240


def _response_text(response: Any) -> str:
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def parse_answer(text: str) -> Dict[str, Dict[str, Any]]:
    """{fact id: {"domain": ..., "rewrite": [...] | None}} from the model's JSON (fences and chatter tolerated). An entry with a bad domain keeps none. Pure."""
    raw = (text or "").replace("```json", "").replace("```", "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        items = json.loads(raw[start:end + 1]).get("items") or []
    except (ValueError, AttributeError):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        rewrite = item.get("rewrite")
        parts = []
        if isinstance(rewrite, list):
            for part in rewrite[:MAX_REWRITE_PARTS]:
                if isinstance(part, dict) and isinstance(part.get("fact"), str) and part["fact"].strip():
                    parts.append({"fact": " ".join(part["fact"].split()), "domain": normalize_domain(part.get("domain")) or "general"})
        out[item["id"]] = {"domain": normalize_domain(item.get("domain")), "rewrite": parts or None}
    return out


def classify(facts: Sequence[UserFact], invoke: Callable[[str], Any], employer: str, own: str, batch_size: int = BATCH_SIZE) -> Dict[str, Dict[str, Any]]:
    """The model's answer for every fact, a batch at a time. A batch that fails (or answers nothing usable) leaves its facts out: they stay as they were."""
    from backend.components.constraints import DOMAIN_CLASSIFY_PROMPT

    answers: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(facts), batch_size):
        chunk = facts[start:start + batch_size]
        listing = "\n".join(f"{f.id}: {f.fact}" for f in chunk)
        try:
            prompt = DOMAIN_CLASSIFY_PROMPT.format(employer=employer or "(not given)", own=own or "(not given)", facts=listing)
            answers.update({k: v for k, v in parse_answer(_response_text(invoke(prompt))).items() if k in {f.id for f in chunk}})
        except Exception:
            logger.warning("[memory domains] a batch of %d facts could not be sorted.", len(chunk), exc_info=True)
    return answers


def _valid_rewrite(original: UserFact, parts: Optional[List[Dict[str, str]]]) -> bool:
    """A rewrite is only accepted when every part is short, has a real domain, and adds nothing the original did not hold (the grounding rule, applied to the fact's own text)."""
    if not parts or original.source == USER_EDIT_SOURCE:
        return False
    for part in parts:
        text = part["fact"]
        if not (8 <= len(text) <= MAX_FACT_CHARS) or part.get("domain") not in VALID_DOMAINS:
            return False
        if memory_grounding.ungrounded_kinds(text, user_text=original.fact):
            return False
    return True


def plan_changes(facts: Sequence[UserFact], answers: Dict[str, Dict[str, Any]], redo: bool = False, allow_rewrites: bool = False) -> Dict[str, Any]:
    """What would change: {"tags": [{id, domain}], "rewrites": [{id, parts}], "rejected_rewrites": [id], "skipped": counts}. Facts already sorted are left alone unless `redo`,
    protected facts always. REWRITES ARE OFF BY DEFAULT: a tag changes only the new domain field, a rewrite replaces the fact's wording, and on the person's real facts the
    model proposed six rewrites that were all wrong (it split observations that legitimately span their life), so a fact it wants rewritten is only tagged unless the caller opts in. Pure."""
    tags: List[Dict[str, Any]] = []
    rewrites: List[Dict[str, Any]] = []
    rejected: List[str] = []
    skipped = {"already_sorted": 0, "protected": 0, "no_answer": 0, "rewrites_ignored": 0}
    for fact in facts:
        if fact.source in PROTECTED_SOURCES:
            skipped["protected"] += 1
            continue
        if fact.domain_locked:
            skipped["protected"] += 1                  # the person chose this domain themselves, even a redo leaves it
            continue
        if fact.domain is not None and not redo:
            skipped["already_sorted"] += 1
            continue
        answer = answers.get(fact.id)
        if not answer or not answer.get("domain"):
            skipped["no_answer"] += 1
            continue
        parts = answer.get("rewrite")
        if parts and not allow_rewrites:
            skipped["rewrites_ignored"] += 1
        elif parts:
            if _valid_rewrite(fact, parts):
                rewrites.append({"id": fact.id, "parts": parts})
                continue
            rejected.append(fact.id)
        tags.append({"id": fact.id, "domain": answer["domain"]})
    return {"tags": tags, "rewrites": rewrites, "rejected_rewrites": rejected, "skipped": skipped}


def apply_plan(
    username: str, facts: List[UserFact], plan: Dict[str, Any], *, save_facts: Callable[[str, List[UserFact]], None], save_backup: Callable[[Dict[str, Any]], None],
    embed: Callable[[str], Optional[List[float]]], now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Writes the plan. The backup is saved FIRST; a failure there stops everything. A rewrite keeps the original fact's id, links, category, source and dates and gives it the first
    part's text; any further part becomes a new fact linked to it. Returns {"run_id", "tagged", "rewritten", "added"}."""
    moment = (now or datetime.now(timezone.utc)).isoformat()
    by_id = {f.id: f for f in facts}
    run_id = uuid.uuid4().hex[:12]
    touched = [by_id[t["id"]] for t in plan["tags"] if t["id"] in by_id] + [by_id[r["id"]] for r in plan["rewrites"] if r["id"] in by_id]
    backup = {"run_id": run_id, "username": username, "at": moment, "changes": []}
    for fact in touched:
        backup["changes"].append({"id": fact.id, "before": fact_for_state(fact), "new_ids": []})
    save_backup(backup)
    changes = {c["id"]: c for c in backup["changes"]}

    for tag in plan["tags"]:
        fact = by_id.get(tag["id"])
        if fact is not None:
            fact.domain = tag["domain"]
    added: List[UserFact] = []
    for rewrite in plan["rewrites"]:
        fact = by_id.get(rewrite["id"])
        if fact is None:
            continue
        first, rest = rewrite["parts"][0], rewrite["parts"][1:]
        fact.fact, fact.domain = first["fact"], first["domain"]
        if rewrite.get("category") in VALID_CATEGORIES:       # only a deliberate, hand-made fix sets this (the command's own plans never do): a fact filed under the wrong kind
            fact.category = rewrite["category"]
        fact.embedding = embed(first["fact"]) or fact.embedding
        for part in rest:
            new = fact.model_copy(update={"id": str(uuid.uuid4()), "fact": part["fact"], "domain": part["domain"], "embedding": embed(part["fact"]), "related": [fact.id]})
            fact.related = list(fact.related) + [new.id]
            added.append(new)
            changes[fact.id]["new_ids"].append(new.id)
    if added:
        save_backup(backup)           # the same run, now naming the facts it created, so a restore can remove them
    save_facts(username, list(facts) + added)
    return {"run_id": run_id, "tagged": len(plan["tags"]), "rewritten": len(plan["rewrites"]), "added": len(added)}


def restore_run(username: str, backup: Dict[str, Any], facts: List[UserFact], *, save_facts: Callable[[str, List[UserFact]], None]) -> Dict[str, int]:
    """Puts back what a run changed: each fact's original text, domain (including "never sorted") and links; facts the run added are removed. Facts changed since by
    something else keep those later changes only where the run did not touch them."""
    by_id = {f.id: f for f in facts}
    restored = removed = 0
    drop = set()
    for change in backup.get("changes", []):
        fact = by_id.get(change["id"])
        before = change.get("before") or {}
        if fact is not None:
            for key in ("fact", "domain", "category", "related", "updated_at"):
                if key in before:
                    setattr(fact, key, before[key])
            restored += 1
        drop.update(change.get("new_ids") or [])
    kept = [f for f in facts if f.id not in drop]
    removed = len(facts) - len(kept)
    for fact in kept:
        fact.related = [rid for rid in fact.related if rid not in drop]
    save_facts(username, kept)
    return {"restored": restored, "removed": removed}


def summarize(plan: Dict[str, Any], facts: Sequence[UserFact]) -> Dict[str, Any]:
    """Counts only, for the command's output: how many facts each domain would get, and which categories the job and own-project facts sit in."""
    by_id = {f.id: f for f in facts}
    domains: Dict[str, int] = {}
    categories: Dict[str, Dict[str, int]] = {"work": {}, "own": {}}
    for tag in plan["tags"]:
        domains[tag["domain"]] = domains.get(tag["domain"], 0) + 1
        if tag["domain"] in categories and tag["id"] in by_id:
            cat = by_id[tag["id"]].category
            categories[tag["domain"]][cat] = categories[tag["domain"]].get(cat, 0) + 1
    return {"domains": domains, "categories": categories, "rewrites": len(plan["rewrites"]), "rejected_rewrites": len(plan["rejected_rewrites"]), "skipped": plan["skipped"]}
