"""What a "Source: Memory" citation shows when it is clicked.

When someone asks Sonic what it remembers ("when did I last have ice cream?"), the memory recall step puts the person's saved facts and the notes that matched into the prompt as one
document whose source is `user_memory`, and the answer cites it. That citation used to link to a download that does not exist ("Failed to load document"). It now opens the memory the
answer drew on: the notes from past conversations that matched (with how long ago), and the saved facts that relate to the question (each one editable on the Memory page).

- BUILT AT RECALL TIME, SMALL. `build` keeps at most MAX_NOTES notes and MAX_FACTS facts (of hundreds), trimmed, and stores them on the recall document's metadata, so nothing is looked up
  again when the chip is clicked and the checkpoint stays small. A fact is shown when it shares a content word with the question; a note is shown because it matched the question.
- KEPT WITH THE REPLY. app.py sends them to the browser as a `memory_sources` event and saves them on the reply, so the chip still works after the page is reloaded. A reply saved
  before this existed has none, and the chip says so and points to the Memory page.
- IT IS THE PERSON'S OWN DATA, shown only to them (the chat stream is theirs). Guests have no memory and get nothing.
- Nothing here is logged.
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

SOURCE_NAME = "user_memory"
MAX_NOTES = 4
MAX_FACTS = 8
MAX_TEXT_CHARS = 600
CITATION = "[Source: Memory](#memory-source)"      # what the model is told to copy; the browser turns this link into the viewer

_STOP = {
    "the", "and", "for", "you", "your", "that", "this", "with", "have", "had", "has", "was", "were", "are", "did", "does", "when", "what", "who", "how", "why", "where", "which",
    "last", "time", "about", "from", "they", "them", "then", "than", "been", "will", "would", "could", "should", "can", "any", "ever", "just", "like", "remember", "recall",
    "tell", "know", "mine", "myself", "some", "much", "many", "into", "out", "over", "our", "his", "her", "its", "not", "but", "all", "got", "get", "going", "went",
}


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _words(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z][a-z0-9']{2,}", (text or "").lower()) if w not in _STOP}


def _clip(text: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned if len(cleaned) <= MAX_TEXT_CHARS else cleaned[: MAX_TEXT_CHARS - 1].rstrip() + "…"


def _when(doc: Any, now: datetime) -> Optional[str]:
    """"about 4 days ago" for a note with a usable time, else None (an unknown time is never guessed)."""
    from backend.services.memory_search import _parse_time
    from backend.utils.time_utils import describe_gap

    created = _parse_time((getattr(doc, "metadata", None) or {}).get("created_at"))
    if created is None:
        return None
    seconds = (now - created).total_seconds()
    return f"{describe_gap(seconds)} ago" if seconds >= 0 else None


def note_entries(docs: Iterable[Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    out = []
    for doc in list(docs or [])[:MAX_NOTES]:
        text = _clip(getattr(doc, "page_content", ""))
        if text:
            out.append({"text": text, "when": _when(doc, now)})
    return out


def fact_entries(question: str, facts: Iterable[Any]) -> List[Dict[str, Any]]:
    """The saved facts that share at least one content word with the question, the closest first. `facts` are UserFact objects or the dicts the graph state holds."""
    wanted = _words(question)
    if not wanted:
        return []
    scored = []
    for fact in facts or []:
        get = (lambda key, default=None: fact.get(key, default)) if isinstance(fact, dict) else (lambda key, default=None: getattr(fact, key, default))
        text = str(get("fact") or "")
        overlap = len(wanted & _words(text))
        if overlap:
            scored.append((overlap, len(text), {"id": str(get("id") or ""), "text": _clip(text), "category": str(get("category") or "")}))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [entry for _, _, entry in scored[:MAX_FACTS]]


def build(question: str, note_docs: Iterable[Any], facts: Iterable[Any], now: Optional[datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
    return {"notes": note_entries(note_docs, now), "facts": fact_entries(question, facts)}


def from_documents(documents: Iterable[Any]) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """The viewer's content from this turn's documents, or None when the turn did not use the memory report (or it held nothing to show)."""
    for doc in documents or []:
        metadata = getattr(doc, "metadata", None) or {}
        if metadata.get("source") == SOURCE_NAME:
            sources = metadata.get("memory_sources")
            if isinstance(sources, dict) and (sources.get("notes") or sources.get("facts")):
                return {"notes": list(sources.get("notes") or []), "facts": list(sources.get("facts") or [])}
            return None
    return None
