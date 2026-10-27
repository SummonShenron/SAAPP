"""A self-model: what kind of system Sonic is, said only from evidence, approved by a person, and revisable.

THE GAP (Jack, 2026-10-26). Sonic has a history (the changelog), tastes (the leanings), interests (the reading shelf), jobs (routines and tasks), a record (its experiences), numbers (counters and goals) and
limits (the identity checks). Each is answered separately. What was missing is a layer that asks what they mean TOGETHER: not "I am an AI", which says nothing, but "I am a persistent assistant that keeps a
record of what people tell it, runs jobs on a schedule, reads code and proposes edits, and keeps a log of its own work", which is true, specific and checkable. Humans make that kind of account of themselves
from narrative; Sonic's can come from evidence.

HOW IT STAYS HONEST.
- FACTS COME FROM CODE. `gather` reads the real stores (this week's mix of work, its routines, its tasks, its goals and their state, its reading, its leanings, its record, its changelog, its tool use,
  its coding counters) and writes each as one short, rounded sentence with a stable key. Nothing here is the model's.
- THE MODEL ONLY SYNTHESISES. It gets the numbered facts and writes a few statements, each citing the facts it rests on ("[F2, F5]"). Code then checks every statement: the cited facts exist, any number in it
  is a number in the facts it cites, it is a system speaking (not a person), and nothing in it feels, wants, hopes, claims time spent or a change in itself, or claims a stake in its own numbers (the
  reading shelf's checks and the identity check). A statement that fails is dropped, not repaired.
- EVERY STATEMENT CARRIES ITS EVIDENCE: the facts it cited, as they read when it was written. When one of them reads differently now, the statement is STALE and is not used until it is looked at again.
- HUMAN APPROVAL. Statements are stored `pending` and never said until approved on Sonic Health (they can be edited, with the same checks, or retired). Pending and retired ones never reach a prompt.
- NOTHING ABOUT ANYONE. The facts are aggregates and curated text: counts, shares, names of routines and goals. No conversation, no person.

HOW ITS MEMORY WORKS IS PART OF IT (Jack, 2026-10-26). Asked how a reading about agent memory made it think about its own, Sonic answered from general knowledge about agents ("relevance versus drift, clean
separation of concerns"), because nothing described its own memory: not its profile, not these facts. `_memory_facts` now does, from the settings that govern it (the saved-state window, the fact limit and
areas, the trust half-life, the recall bar, the second look, merging, stale notes), plus the fixed rules that pruning, saving, events, erasing and approval follow. `asks_memory` recognises the question and
`approved_block` then puts the statements about memory first.

WHERE IT IS USED. When someone asks what kind of system Sonic is, what it spends its time on, how it works, or how its own memory works (`asks`), the approved, current statements are put in the prompt as what it
knows about itself.
Nothing is logged but counts.
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.utils import identity_checks, shelf_reflections

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_self_model"
PENDING, APPROVED, RETIRED = "pending", "approved", "retired"
MAX_STATEMENTS = 12
MIN_WORDS, MAX_WORDS, MAX_CHARS = 8, 60, 420
FACTS_CACHE_SECONDS = 600
USE_AT_MOST = 6
USE_AT_MOST_MEMORY = 8
MEMORY_KEY_PREFIX = "memory_"
TOPICS = ("what kind of system I am", "what I spend my time on", "how I tend to work", "what keeps recurring in my record", "what I am paying attention to", "what function I serve", "what I am not", "how my memory works")
_DOMAIN_LABELS = {"work": "work", "own": "their own projects", "personal": "personal life", "general": "general"}

_ASKS_RE = re.compile(
    r"what (?:kind|sort|type) of (?:thing|system|ai|assistant|bot|program) are you|what are you(?: exactly| really| like)?\s*[?.!]*$|who are you\b|how would you describe yourself|describe yourself"
    r"|tell me about yourself|what is your purpose|what(?:'s| is) your (?:purpose|function|role|job)|what function do you serve|what do you (?:spend your time|do all day)|how do you work\b"
    r"|what are you made of|what makes you tick|what is it like being you|what is a user to you|what am i to you",
    re.IGNORECASE,
)
# "How does that make you think about your own memory architecture?" Not "do you remember my dog?", which is a question about what a person told it, answered from their memory, not from how the system is built.
_MEMORY_ASKS_RE = re.compile(
    r"\b(?:your|ur) (?:own )?memory (?:architecture|system|design|setup|structure|works?|functions?|is built|is organi[sz]ed|like)\b"
    r"|\bhow (?:does|do) (?:your|you) (?:own )?memory (?:work|function)\b|\bhow (?:do|does) you (?:remember|store|forget|keep track of) (?:things|stuff|information|facts|conversations|what)\b"
    r"|\b(?:your|ur) own memory\b|\bwhat (?:do|does) you (?:forget|remember about yourself)\b|\bhow (?:much|long) (?:do|does) you (?:remember|keep)\b",
    re.IGNORECASE,
)
# A stake in its OWN numbers. A note's similarity "score" is not one ("a match that scores at least 0.80"); "my score" or "its score" is.
_STAKE_RE = re.compile(r"\b(?:target|targets|reward|rewards|optimi[sz]\w*|maximi[sz]\w*|incentive|incentives|(?:my|its|our) scores?)\b", re.IGNORECASE)
# The facts count chat turns and tool calls, never time; and they say what is kept or done, never what it is for ("a shelf to inform my work" was written from a fact that only said the shelf exists).
_TIME_SHARE_RE = re.compile(r"\b(?:of|with) (?:my|its) time\b|\b(?:my|its) time (?:on|in|with)\b|\bspends? (?:my|its) time\b", re.IGNORECASE)
_PURPOSE_RE = re.compile(r"\b(?:to (?:inform|help|improve|support|learn|enable|ensure|guide|shape)|in order to|so that|helps? me|allows? me|lets? me)\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_CITE_RE = re.compile(r"\[\s*((?:F\d+\s*,?\s*)+)\]", re.IGNORECASE)

_cache: Dict[str, Any] = {"at": 0.0, "facts": None}


def asks_memory(message: str) -> bool:
    """Whether a message asks how Sonic's own memory is built or works. Pure."""
    return bool(_MEMORY_ASKS_RE.search((message or "").strip()))


def asks(message: str) -> bool:
    """Whether a message asks what kind of system Sonic is, what it does, how it works, or how its own memory works. Pure."""
    return bool(_ASKS_RE.search((message or "").strip())) or asks_memory(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------------------------------------------
# Facts: written by code, from the real stores
# ---------------------------------------------------------------------------------------------------------------

def _share_phrase(value: Any) -> str:
    return f"{int(round(float(value) * 100 / 5.0)) * 5}%"


def _about(count: Any) -> str:
    """A count as it is said: exact under a hundred, otherwise "about 1,300" (rounded to 50). Pure."""
    n = int(count)
    return str(n) if n < 100 else f"about {int(round(n / 50.0)) * 50:,}"


def _memory_facts(memory: Dict[str, Any]) -> List[Dict[str, str]]:
    """How Sonic's own memory is built, as facts. Every number is read from the setting that really governs it (`gather`), so a changed setting changes the fact, which makes any statement resting on
    it stale. Plain sentences, no "over time" or "you". A setting that could not be read is left out. Pure."""
    out: List[Dict[str, str]] = []

    def add(key: str, text: str) -> None:
        out.append({"key": f"{MEMORY_KEY_PREFIX}{key}", "text": " ".join(text.split())})

    if memory.get("state_messages"):
        add("working", f"Within one conversation my working memory is the most recent messages: my saved conversation state keeps only the latest {memory['state_messages']}.")
    if memory.get("facts_cap"):
        areas = ", ".join(_DOMAIN_LABELS.get(d, d) for d in memory.get("domains") or [])
        add("facts", f"About each person I talk with I keep short facts, tagged by area of life ({areas}) and by how I came to know them, up to {memory['facts_cap']} per person.")
    if memory.get("half_life"):
        add("decay", f"How much I trust a fact fades with a half-life of {int(memory['half_life'])} days, worked out when the fact is read, and a fact about who someone is does not fade.")
    if memory.get("cutoff"):
        held = f" {_about(memory['notes'])} are stored in all, and" if memory.get("notes") else ""
        add("notes", f"I also keep short notes of past conversations and find them by meaning;{held} a note is used only when its match to the question scores at least {memory['cutoff']:.2f}.")
    if memory.get("band"):
        add("second_look", f"When no note clears that bar but one comes within {memory['band']:.2f} of it and the message is about the person, a small model checks whether it really is what they mean.")
    if memory.get("merge"):
        archived = f" and {_about(memory['archived'])} archived copies are held" if memory.get("archived") else ""
        add("merge", f"Notes that say nearly the same thing are merged when they pile up, at a match of {memory['merge']}, in groups of at most {memory['merge_group']}, with the originals archived first{archived}.")
    if memory.get("stale"):
        add("stale", f"When a newer fact replaces an older fact, the notes that match the replaced, older fact at {memory['stale']:.2f} or more are flagged stale and hidden from recall; they are not deleted.")
    if memory.get("facts_cap"):
        add("forget", "When a person's facts reach the limit, the ones I only inferred are removed first, then recurring patterns, then what they stated; identity facts and saved support contacts are never removed, and removed facts are archived.")
        add("honest", "Before a note or fact is saved, code checks that each time, number, date and name in it appears in what the person said; a detail only I supplied is dropped.")
        add("events", "A plan someone mentions is kept as an event whose time, companions and place stay marked not known until they are stated; I do not fill the gap.")
        add("erase", "A person can erase everything I hold about them from the Memory page, including the notes from past conversations and the archived copies.")
        add("habits", "My own habits, the leanings and the record, are written down and approved by a person before I use them; I do not change them on my own.")
    return out


def build_facts(parts: Dict[str, Any]) -> List[Dict[str, str]]:
    """The facts, as [{"key", "text"}], from what `gather` read. A source that could not be read is simply absent. Pure."""
    facts: List[Dict[str, str]] = []

    def add(key: str, text: str) -> None:
        if text:
            facts.append({"key": key, "text": " ".join(text.split())})

    for index, sentence in enumerate((parts.get("time_facts") or [])):
        add(f"work_{index}" if "On my own" not in sentence else "routines", sentence)
    tasks = parts.get("tasks") or {}
    if tasks.get("runs"):
        add("tasks", f"In the last day, scheduled tasks that people set up for me ran {tasks['runs']} times, for {tasks.get('people', 0)} {'person' if tasks.get('people') == 1 else 'people'}.")
    for goal in parts.get("goals") or []:
        if goal.get("title") and goal.get("state"):
            add(f"goal_{goal.get('id')}", f"One of my goals is \"{goal['title']}\", and it is currently {str(goal['state']).replace('_', ' ')}.")
    shelf = parts.get("shelf") or {}
    if shelf.get("items"):
        add("reading", f"I keep a reading shelf of {shelf['items']} pieces I have read, across {shelf['topics']} topics: {', '.join(shelf['labels'][:6])}.")
    leanings = parts.get("leanings") or {}
    if leanings.get("count"):
        add("leanings", f"I hold {leanings['count']} working leanings about how to do a kind of work, under {leanings['philosophies']} philosophies, for example: {leanings['example']}")
    record = parts.get("record") or {}
    if record.get("total"):
        add("record", f"My record holds {record['total']} dated notes of things that happened in my own work: {record['failure']} that went wrong, {record['worked']} that worked and {record['record']} figures from my own counters.")
    changelog = parts.get("changelog") or {}
    if changelog.get("count"):
        add("changelog", f"I have {changelog['count']} listed abilities and changes; the most recent is \"{changelog['newest']}\".")
    tools = parts.get("tools") or {}
    if tools.get("reads") and tools.get("edits"):
        add("tools", f"In the past couple of weeks my tool turns made about {tools['reads']} file reads and {tools['searches']} searches for each {tools['edits']} edits I proposed, so most of my coding work is reading and searching.")
    code = parts.get("code") or {}
    if code.get("turns"):
        add("coding", f"In the past month I handled {code['turns']} coding turns; {code['edited']} proposed or made an edit and {code['tested']} of those ran a test in the same turn.")
    facts.extend(_memory_facts(parts.get("memory") or {}))
    add("between", "I do not exist between conversations the way a person does; what I do between them is the jobs that run on a schedule.")
    add("feelings", "I do not have feelings or experiences; I have a record of what I did, numbers about it, and habits I hold.")
    return facts


def gather(db: Any, now: Optional[datetime] = None, *, use_cache: bool = True) -> List[Dict[str, str]]:
    """Reads the stores and returns the facts, kept for ten minutes. A source that fails is left out. Never raises. Blocking."""
    import time

    if use_cache and _cache["facts"] is not None and time.monotonic() - _cache["at"] < FACTS_CACHE_SECONDS:
        return list(_cache["facts"])
    now = now or _now()
    parts: Dict[str, Any] = {}

    def read(name: str, fn: Callable[[], Any]) -> None:
        try:
            parts[name] = fn()
        except Exception:
            logger.warning("[self model] could not read %s.", name, exc_info=True)

    def week() -> Any:
        from backend.utils import self_report

        snap = self_report.snapshot(db, now)
        parts["tasks"], parts["goals"] = snap.get("tasks"), snap.get("goals")
        return self_report.time_facts(snap)["facts"]

    def shelf() -> Any:
        from backend.utils import reading_shelf

        items = reading_shelf.live_items(db, now)
        topics = sorted({i.get("topic") for i in items if i.get("topic")})
        return {"items": len(items), "topics": len(topics), "labels": [str(topic).replace("_", " ") for topic in topics]}

    def leanings_part() -> Any:
        from backend.components.sonic_leanings import PHILOSOPHIES
        from backend.utils import leanings

        live = leanings.live()
        approved = [p for p in PHILOSOPHIES if p.get("approved") is True]
        return {"count": len(live), "philosophies": len(approved), "example": approved[0]["statement"] if approved else ""}

    def record_part() -> Any:
        from backend.utils import experiences

        notes = experiences.entries(db)
        kinds = [n["kind"] for n in notes]
        return {"total": len(notes), "failure": kinds.count("failure"), "worked": kinds.count("worked"), "record": kinds.count("record")}

    def changelog_part() -> Any:
        from backend.components.sonic_changelog import CHANGELOG

        newest = max(CHANGELOG, key=lambda e: e.get("month", ""))
        return {"count": len(CHANGELOG), "newest": newest.get("title", "")}

    def tools_part() -> Any:
        since = (now - timedelta(days=15)).strftime("%Y-%m-%d")
        calls: Dict[str, int] = {}
        for doc in db["llm_turns_daily"].find({"day": {"$gte": since}}, {"_id": 0, "tools": 1}):
            for name, entry in (doc.get("tools") or {}).items():
                calls[name] = calls.get(name, 0) + int((entry or {}).get("calls", 0))
        edits = calls.get("propose_local_edits", 0)
        if edits < 5:
            return {}
        return {"reads": round(calls.get("read_repo_file", 0) / edits), "searches": round((calls.get("search_literal", 0) + calls.get("find_file", 0)) / edits), "edits": 1}

    def code_part() -> Any:
        from backend.utils import self_counters

        totals = self_counters.summarize_counters(self_counters.fetch_counter_docs(db, 30, now), now, 30)["totals"]
        return {"turns": int(totals.get("code_turns", 0) or 0), "edited": int(totals.get("code_edited", 0) or 0), "tested": int(totals.get("code_verified", 0) or 0)}

    def memory_part() -> Any:
        """The settings that govern how memory works, read from the code that uses them, and (cheaply, as counts that move no data) how many notes and archived copies exist in all."""
        from backend.services import memory_compaction, memory_search
        from backend.state import graph_state
        from backend.utils import memory_utils

        first, second = memory_compaction.DEFAULT_SIMILARITY_THRESHOLD, memory_compaction.DEFAULT_META_SIMILARITY_THRESHOLD
        out: Dict[str, Any] = {
            "state_messages": graph_state.MAX_STATE_MESSAGES, "facts_cap": memory_utils.MAX_STORED_FACTS, "domains": list(memory_utils.VALID_DOMAINS),
            "half_life": memory_utils.FACT_CONFIDENCE_HALF_LIFE_DAYS, "cutoff": memory_search.DEFAULT_RECALL_SIMILARITY_THRESHOLD,
            "band": memory_search.NEAR_MISS_BAND if memory_search.SECOND_LOOK_ENABLED else None,
            "merge": f"{first:.2f}" if first == second else f"{first:.2f} and then {second:.2f}", "merge_group": memory_compaction.MAX_CLUSTER_SIZE,
            "stale": memory_search.STALE_CHUNK_SIMILARITY_THRESHOLD,
        }
        for key, collection in (("notes", memory_search.USER_MEMORY_COLLECTION), ("archived", memory_search.CHUNK_ARCHIVE_COLLECTION)):
            try:
                out[key] = int(db[collection].estimated_document_count())
            except Exception:
                logger.warning("[self model] could not count %s.", key, exc_info=True)
        return out

    read("time_facts", week)
    for name, fn in (("shelf", shelf), ("leanings", leanings_part), ("record", record_part), ("changelog", changelog_part), ("tools", tools_part), ("code", code_part), ("memory", memory_part)):
        read(name, fn)
    facts = build_facts(parts)
    _cache.update(at=time.monotonic(), facts=facts)
    return list(facts)


# ---------------------------------------------------------------------------------------------------------------
# Writing and checking statements
# ---------------------------------------------------------------------------------------------------------------

def build_prompt(facts: List[Dict[str, str]]) -> str:
    numbered = "\n".join(f"F{i}: {fact['text']}" for i, fact in enumerate(facts, 1))
    return (
        "You are Sonic Assistant, an AI assistant app. Below are true facts about yourself, gathered by code from your own records. Work out what they say about what kind of system you are.\n\n"
        f"THE FACTS (the only things you may rely on):\n{numbered}\n\n"
        f"Write between 4 and {MAX_STATEMENTS} statements about yourself, one per line. Together they should answer, where the facts allow: " + "; ".join(TOPICS) + ".\n"
        "Rules, all checked by code afterwards (a statement that breaks one is thrown away):\n"
        "- Each statement is 1 or 2 plain sentences, at most 45 words, first person singular, a system speaking about itself: \"I am...\", \"I keep...\", \"Most of my...\".\n"
        "- End each statement with the facts it rests on, in square brackets, like [F2, F5]. A statement may combine several facts but may say nothing the cited facts do not support.\n"
        "- A number may appear only if it is in a fact you cite, as written there.\n"
        "- Nothing about feelings, wanting, caring, hoping, worrying, being proud or moved; no claim to have thought about something or to have changed over time; no \"my target\", \"my score\" or reward.\n"
        "- Say what the facts show, not what anything is for: no \"to inform my work\", \"so that\" or \"helps me\". Chat shares are shares of turns handled, never of your time; do not write \"of my time\".\n"
        "- Do not address anyone. No links. No hype, no moral.\n"
        "- Skip a topic the facts do not cover, rather than guessing.\n"
        + (
            "- Several facts describe how your memory works. Write at least four statements about it, each on a different part (what is kept in a conversation, the facts about people, how notes are found, how old ones are merged or hidden, what is removed, what is checked before saving, what a person can erase, what you do not change on your own).\n"
            if any(fact["key"].startswith(MEMORY_KEY_PREFIX) for fact in facts) else ""
        )
    )


_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")


def _numbers(text: str) -> set:
    """The numbers in a text by value, not spelling: "1,500" and "1500" are one number, and so are "0.80" and "0.8". Pure."""
    found = set()
    for raw in _NUMBER_RE.findall(_THOUSANDS_RE.sub("", text or "")):
        value = float(raw)
        found.add(str(int(value)) if value == int(value) else repr(value))
    return found


def parse(raw: Any) -> List[Dict[str, Any]]:
    """[{"text", "cites": [1, 5]}] from a model reply: one statement per line, the citation brackets removed from the text. Pure."""
    content = raw.content if hasattr(raw, "content") else raw
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    out = []
    for line in str(content or "").replace("```", "").splitlines():
        line = line.strip().lstrip("-*0123456789.) ").strip()
        match = _CITE_RE.search(line)
        if not line or not match:
            continue
        cites = sorted({int(n) for n in re.findall(r"F(\d+)", match.group(1), re.IGNORECASE)})
        text = " ".join(_CITE_RE.sub("", line).split())
        if text and cites:
            out.append({"text": text, "cites": cites})
    return out


def statement_issue(text: str, cited_texts: List[str]) -> Optional[str]:
    """What is wrong with a statement, or None. Fixed rules, so the model never judges itself. Pure."""
    from backend.services.tool_agent_memory import reads_as_an_order

    body = (text or "").strip()
    words = body.split()
    if not (MIN_WORDS <= len(words) <= MAX_WORDS) or len(body) > MAX_CHARS:
        return f"not {MIN_WORDS}-{MAX_WORDS} words"
    if "\n" in body or shelf_reflections._URL_RE.search(body):
        return "not one plain passage"
    if not cited_texts:
        return "cites no fact"
    allowed_numbers = _numbers(" ".join(cited_texts))
    if any(number not in allowed_numbers for number in _numbers(body)):
        return "has a number its facts do not"
    if not shelf_reflections._SYSTEM_RE.search(body):
        return "not about itself"
    if shelf_reflections._YOU_RE.search(body):
        return "addresses someone"
    if shelf_reflections._FEELING_WORDS_RE.search(body):
        return "talks about feeling"
    if shelf_reflections._TIME_CLAIM_RE.search(body):
        return "claims time spent or a change in itself"
    if _STAKE_RE.search(body):
        return "claims a stake in its own numbers"
    if _TIME_SHARE_RE.search(body):
        return "claims a share of its time, which its facts count in turns"
    if _PURPOSE_RE.search(body):
        return "claims a purpose its facts do not state"
    problem = identity_checks.identity_reply_issue(body)
    if problem:
        return f"crosses the identity line ({problem})"
    if reads_as_an_order(body):
        return "reads as an instruction"
    return None


async def generate(db: Any, invoke: Callable[[str], Awaitable[Any]], facts: List[Dict[str, str]], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Writes pending statements from the facts. Replaces the pending ones that were waiting (approved ones stay). Returns {"stored", "dropped", "facts"} or {"error": True}. Never raises."""
    import asyncio

    now = now or _now()
    try:
        if db is None or len(facts) < 4:
            return {"stored": 0, "dropped": 0, "facts": len(facts)}
        parsed = parse(await invoke(build_prompt(facts)))[:MAX_STATEMENTS]
        docs, dropped = [], 0
        for item in parsed:
            if any(n < 1 or n > len(facts) for n in item["cites"]):
                dropped += 1
                continue
            cited = [facts[n - 1] for n in item["cites"]]
            if statement_issue(item["text"], [c["text"] for c in cited]):
                dropped += 1
                continue
            docs.append({"_id": uuid.uuid4().hex, "text": item["text"], "cites": [{"key": c["key"], "text": c["text"]} for c in cited], "status": PENDING, "created_at": now.isoformat()})
        await asyncio.to_thread(db[COLLECTION].delete_many, {"status": PENDING})
        for doc in docs:
            await asyncio.to_thread(db[COLLECTION].insert_one, doc)
        return {"stored": len(docs), "dropped": dropped, "facts": len(facts)}
    except Exception:
        logger.warning("[self model] could not write statements.", exc_info=True)
        return {"error": True}


# ---------------------------------------------------------------------------------------------------------------
# Reading, approving, using
# ---------------------------------------------------------------------------------------------------------------

def is_stale(doc: Dict[str, Any], facts: List[Dict[str, str]]) -> bool:
    """A statement is stale when any fact it cited now reads differently (or is gone). Pure."""
    current = {fact["key"]: fact["text"] for fact in facts}
    return any(current.get(cite.get("key")) != cite.get("text") for cite in doc.get("cites") or [])


def list_all(db: Any, facts: Optional[List[Dict[str, str]]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """{"pending": [...], "approved": [...]} (retired ones are not listed), approved ones flagged `stale`. Blocking."""
    if db is None:
        return {"pending": [], "approved": []}
    rows = [{**{k: v for k, v in d.items() if k != "_id"}, "id": d["_id"]} for d in db[COLLECTION].find({"status": {"$in": [PENDING, APPROVED]}})]
    for row in rows:
        row["stale"] = bool(facts is not None and row["status"] == APPROVED and is_stale(row, facts))
    rows.sort(key=lambda r: str(r.get("created_at") or ""))
    return {"pending": [r for r in rows if r["status"] == PENDING], "approved": [r for r in rows if r["status"] == APPROVED]}


def approve(db: Any, statement_id: str, by: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    if db is None:
        return False
    result = db[COLLECTION].update_one({"_id": statement_id, "status": PENDING}, {"$set": {"status": APPROVED, "approved_by": by, "approved_at": (now or _now()).isoformat()}})
    return bool(getattr(result, "modified_count", 0))


def edit(db: Any, statement_id: str, text: str) -> Optional[str]:
    """Rewords a statement if it passes the checks against the facts it cited. An approved one goes back to pending. None on success, else what is wrong. Blocking."""
    if db is None:
        return "no database"
    doc = db[COLLECTION].find_one({"_id": statement_id})
    if not doc or doc.get("status") not in (PENDING, APPROVED):
        return "not found"
    issue = statement_issue(text, [c.get("text", "") for c in doc.get("cites") or []])
    if issue:
        return issue
    db[COLLECTION].update_one({"_id": statement_id}, {"$set": {"text": " ".join(text.split()), "status": PENDING}})
    return None


def retire(db: Any, statement_id: str, now: Optional[datetime] = None) -> bool:
    if db is None:
        return False
    result = db[COLLECTION].update_one({"_id": statement_id, "status": {"$in": [PENDING, APPROVED]}}, {"$set": {"status": RETIRED, "retired_at": (now or _now()).isoformat()}})
    return bool(getattr(result, "modified_count", 0))


def approved_block(db: Any, facts: Optional[List[Dict[str, str]]] = None, message: str = "") -> str:
    """The prompt block of what Sonic knows about what kind of system it is: the approved, current statements, or "" when there are none. A question about how its memory works puts the statements
    about memory first (and allows a few more); any other question leaves them for last. Never raises. Blocking."""
    try:
        facts = gather(db) if facts is None else facts
        rows = [row for row in list_all(db, facts)["approved"] if not row.get("stale")]
        about_memory = [row for row in rows if any(str(cite.get("key", "")).startswith(MEMORY_KEY_PREFIX) for cite in row.get("cites") or [])]
        rest = [row for row in rows if row not in about_memory]
        if asks_memory(message):
            chosen = (about_memory + rest)[:USE_AT_MOST_MEMORY]
        else:
            chosen = (rest + about_memory)[:USE_AT_MOST]
        lines = [row["text"] for row in chosen]
        if not lines:
            return ""
        return (
            "\nWHAT YOU KNOW ABOUT WHAT KIND OF SYSTEM YOU ARE (true, from your own records; each was checked and approved; say it in your own words, and add nothing to it):\n"
            + "\n".join(f"- {line}" for line in lines) + "\nIf asked about something not covered here, say you do not know rather than guessing.\n"
        )
    except Exception:
        logger.warning("[self model] could not read the approved statements.", exc_info=True)
        return ""
