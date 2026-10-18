"""Links between facts: notes about the same real thing in a person's life are bound together, so that recalling one brings the others along.

Sonic saves what a person tells it as separate short facts ("Works at Principal.", "Is working on the PAG UI redesign at work."), each chosen for a
reply on its own similarity to the question. The second fact never says "Principal", so asking about the redesign could surface one without the other
and the reply lost context the person had already given. A link says "these two belong together": when either is chosen for a reply, the facts it is
linked to are brought along as background.

How a link is made. Embedding similarity cannot decide it: measured on real facts, a pair that belongs together (an employer and a project there)
scored 0.65 and pairs that do not (a dog and a sister, a trip and a marathon) scored 0.65 to 0.67. So similarity only picks a short list of
candidates, and a small model says which of them are about the SAME thing (same employer, project, person, trip, health issue). It is told to answer
with none when in doubt, because a wrong link puts unrelated context into a reply, which is worse than a missing one.

How a link is used. Only facts chosen because they matched the question bring their links along (never the always-on identity facts, which would then
drag the same extras into every reply), a few extras at most, and a link whose fact has gone is simply ignored. Links are symmetric and capped.
Nothing here ever raises: with no model, no embedding or a bad answer the result is no link.
"""
import json
import logging
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("SASS Logger")

CANDIDATES = int(os.getenv("FACT_LINK_CANDIDATES", "6"))
# Shortlist floor on cosine similarity. Short fact pairs rarely score below 0.55 even when unrelated, so this only drops the clearly distant.
MIN_SIMILARITY = float(os.getenv("FACT_LINK_MIN_SIMILARITY", "0.5"))
MAX_LINKS_PER_FACT = int(os.getenv("FACT_LINK_MAX_PER_FACT", "6"))
MAX_NEW_LINKS = 3
MAX_EXTRAS = int(os.getenv("FACT_LINK_MAX_EXTRAS", "3"))


def link_enabled() -> bool:
    return os.getenv("FACT_LINKS", "true").strip().lower() not in ("0", "false", "no", "off")


FACT_LINK_PROMPT = """You decide which saved notes about a person belong together with a NEW note, so that when one of them comes up in conversation the assistant also has the others as background.

NEW NOTE: {new_fact}

SAVED NOTES (id: text):
{candidates}

Two notes belong together only when they are about the SAME real thing in the person's life: the same employer or job, the same project, the same person, the same trip, the same event, the same health issue, the same goal. One would be useful background when the other is discussed.

They do NOT belong together just because they share a topic, a category, or a word: two unrelated projects, two different people, a hobby and a job. A personal preference or taste (dark mode, a food, music) never belongs with a job, project or person just because a word overlaps, such as a preference about "UI" and a project about "UI". A general fact about the person (a name, being an engineer in the abstract) does not belong with anything by default.

A note about work belongs with the employer note when it clearly happens at that employer ("at work", "at the office", a team or product there). Two notes about the same person (a sister, a coworker, a partner) belong together.

If you are not sure, leave it out. Answering with none is correct most of the time.

For every note you pick, name in a few words the ONE real thing in the person's life that it and the new note both belong to (for example "the job at Principal", "the trip to Denver", "the sister Priya"). If you cannot name such a thing, do not pick the note.

Return ONLY a JSON object with no preamble or markdown: {{"links": [{{"n": 1, "thing": "the job at Principal"}}]}}, where n is the number of the saved note. Use an empty list when none belong. At most {limit}."""


def _active(facts: Iterable[Any]) -> List[Any]:
    return [f for f in facts if getattr(f, "active", True)]


def candidate_facts(facts: Sequence[Any], new_fact: Any, similarity: Callable[[Sequence[float], Sequence[float]], float]) -> List[Any]:
    """The few saved facts most like the new one (by embedding), the only ones the model is asked about. Never the fact itself, never an inactive one."""
    embedding = getattr(new_fact, "embedding", None)
    if not embedding:
        return []
    scored = []
    for fact in _active(facts):
        if fact.id == new_fact.id or not getattr(fact, "embedding", None):
            continue
        if getattr(fact, "category", "") == "identity":
            continue          # shown in every reply already, so a link to it adds nothing
        value = similarity(embedding, fact.embedding)
        if value >= MIN_SIMILARITY:
            scored.append((value, fact))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [fact for _, fact in scored[:CANDIDATES]]


def build_link_prompt(new_text: str, candidates: Sequence[Any]) -> str:
    lines = "\n".join(f"{index}: {fact.fact}" for index, fact in enumerate(candidates, start=1))
    return FACT_LINK_PROMPT.format(new_fact=new_text, candidates=lines, limit=MAX_NEW_LINKS)


def parse_links(raw: Any, count: int) -> List[Tuple[int, str]]:
    """The 1-based positions the model named, as ints within 1..count, in order and without repeats, each with the real thing it named ("" in an older plain list); []
    for anything unreadable. The prompt numbers the candidates 1..n instead of showing their ids, so a long id can never be mistyped into a link to the wrong fact."""
    if isinstance(raw, list):
        raw = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in raw)
    text = str(raw or "").replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
        related = parsed.get("links", parsed.get("related"))
    except Exception:
        return []
    if not isinstance(related, list):
        return []
    picked: List[Tuple[int, str]] = []
    for item in related:
        thing = ""
        if isinstance(item, dict):
            thing = " ".join(str(item.get("thing") or "").split())
            if not thing:
                continue              # a link with no real thing named is not made
            item = item.get("n")
        try:
            position = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= position <= count and position not in [p for p, _ in picked]:
            picked.append((position, thing))
    return picked[:MAX_NEW_LINKS]


def parse_related(raw: Any, count: int) -> List[int]:
    """Just the positions from parse_links."""
    return [position for position, _ in parse_links(raw, count)]


def judge_links_with_things(new_text: str, candidates: Sequence[Any], invoke: Callable[[str], Any]) -> List[Tuple[Any, str]]:
    """The candidate facts the model says belong with the new one, each with the real thing it named. [] on any failure."""
    if not candidates:
        return []
    try:
        response = invoke(build_link_prompt(new_text, candidates))
        raw = response.content if hasattr(response, "content") else response
        return [(candidates[position - 1], thing) for position, thing in parse_links(raw, len(candidates))]
    except Exception:
        logger.exception("[FactLinks] The link judgment failed; no link was made.")
        return []


def judge_links(new_text: str, candidates: Sequence[Any], invoke: Callable[[str], Any]) -> List[Any]:
    """The candidate facts the model says belong with the new one. [] on any failure."""
    return [fact for fact, _ in judge_links_with_things(new_text, candidates, invoke)]


# A second, stricter look at a pair the first look picked. Measured on a real store of 400 facts: the first look alone proposed 562 links, and about 60% of them were
# themes ("the Dark Souls series", "personal software projects") or a feeling pulled in beside something unrelated ("coping", beside a coding project). Each pair is
# asked again on its own, and anything but a clear yes is dropped.
VERIFY_PROMPT = """Two saved notes about a person. Decide whether they are about the SAME specific thing in the person's life, so that when one comes up in conversation the other is useful background.

NOTE A: {first}
NOTE B: {second}
(The first model that looked at these said they share: {thing})

Answer yes ONLY when both notes are about the same specific job or employer, the same named project, the same particular person, the same trip or event, or the same single goal, so that each one describes the same thing from a different side.

Answer no when:
- they share a broad theme, interest or field (gaming, coding, fitness, music, a game series), or one is a general preference, trait or method that merely applies to the other;
- they are two different aspects of the person's inner life or habits (feelings, coping, recovery, relationships) or one is a general description of how the person works;
- one note is about their feelings, health, recovery, addiction or a relationship and the other is about something else (work, a hobby, software): what is said about the person's feelings must never be pulled into a discussion of something unrelated;
- they only mention the same word.

If you are not sure, answer no. Return ONLY a JSON object with no preamble or markdown: {{"same": true}} or {{"same": false}}."""


def verify_enabled() -> bool:
    return os.getenv("FACT_LINK_VERIFY", "true").strip().lower() not in ("0", "false", "no", "off")


def verify_pair(first: str, second: str, thing: str, invoke: Callable[[str], Any]) -> bool:
    """True only on a clear yes. A model that fails, or an answer that is not exactly {"same": true}, is a no."""
    try:
        response = invoke(VERIFY_PROMPT.format(first=first, second=second, thing=thing or "(no name)"))
        raw = response.content if hasattr(response, "content") else response
        if isinstance(raw, list):
            raw = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in raw)
        text = str(raw or "").replace("```json", "").replace("```", "").strip()
        return json.loads(text).get("same") is True
    except Exception:
        return False


def add_links(new_fact: Any, others: Iterable[Any]) -> int:
    """Links the new fact and each of `others` to one another, both ways, within the per-fact cap. Returns how many links were made."""
    made = 0
    for other in others:
        if other.id == new_fact.id:
            continue
        if other.id not in new_fact.related and len(new_fact.related) < MAX_LINKS_PER_FACT and len(other.related) < MAX_LINKS_PER_FACT:
            new_fact.related.append(other.id)
            other.related.append(new_fact.id)
            made += 1
    return made


def link_new_fact(facts: Sequence[Any], new_fact: Any, similarity: Callable[[Sequence[float], Sequence[float]], float], invoke: Callable[[str], Any]) -> int:
    """Finds and makes the links for a fact that was just added to `facts`. Returns how many it made. Never raises."""
    if not link_enabled():
        return 0
    try:
        candidates = candidate_facts(facts, new_fact, similarity)
        picks = judge_links_with_things(new_fact.fact, candidates, invoke)
        if verify_enabled():
            picks = [(other, thing) for other, thing in picks if verify_pair(new_fact.fact, other.fact, thing, invoke)]
        return add_links(new_fact, [other for other, _ in picks])
    except Exception:
        logger.exception("[FactLinks] Could not link a new fact.")
        return 0


def drop_dangling_links(facts: Sequence[Any]) -> None:
    """Removes links to facts that no longer exist (deleted, pruned), and repeats, in place."""
    alive = {fact.id for fact in facts}
    for fact in facts:
        fact.related = [linked for linked in dict.fromkeys(fact.related) if linked in alive and linked != fact.id]


def linked_extras(selected: Sequence[Any], facts: Sequence[Any], limit: Optional[int] = None) -> List[Any]:
    """The facts linked to the selected ones that are not already selected, best-supported first, at most `limit`. `selected` are the facts chosen
    because they matched the question; the caller leaves out the always-on identity facts. Order follows the selection, so the closest match's
    links come first."""
    cap = MAX_EXTRAS if limit is None else limit
    if cap <= 0 or not selected:
        return []
    by_id: Dict[str, Any] = {fact.id: fact for fact in _active(facts)}
    chosen = {fact.id for fact in selected}
    extras: List[Any] = []
    for fact in selected:
        for linked_id in getattr(fact, "related", []) or []:
            other = by_id.get(linked_id)
            if other is None or other.id in chosen:
                continue
            chosen.add(other.id)
            extras.append(other)
            if len(extras) >= cap:
                return extras
    return extras
