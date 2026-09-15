import os
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel

from backend.utils.db_utils import get_db
from backend.utils.embedding_utils import embed_text, cosine_similarity
from backend.components.constraints import FACT_CONFLICT_PROMPT
from backend.models.models import lite_llm

logger = logging.getLogger("SASS Logger")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "saapp_data", "memory")
os.makedirs(DATA_DIR, exist_ok=True)

VALID_CATEGORIES = {"preference", "identity", "setting", "trait"}
FACT_SIMILARITY_THRESHOLD = float(os.getenv("FACT_SIMILARITY_THRESHOLD", "0.80"))


class UserFact(BaseModel):
    id: str
    username: str
    category: str
    fact: str
    source: str = "explicit"
    confidence: float = 1.0
    created_at: str
    updated_at: str
    active: bool = True
    embedding: Optional[List[float]] = None


def _get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}.json")


def load_user_facts(username: str, category: Optional[str] = None) -> List[UserFact]:
    """Loads persistent facts from MongoDB, falling back to a local JSON file."""
    facts: List[UserFact] = []
    db = get_db()

    if db is not None:
        doc = db["user_memory_facts"].find_one({"username": username})
        if doc and "facts" in doc:
            facts = [UserFact(**item) for item in doc["facts"]]
    else:
        path = _get_user_file(username)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    facts = [UserFact(**item) for item in json.load(f)]
            except Exception:
                logger.exception("Failed to load local memory file for %s", username)
                facts = []

    facts = [f for f in facts if f.active]
    if category:
        facts = [f for f in facts if f.category == category]
    return facts


def save_user_facts(username: str, facts: List[UserFact]) -> None:
    """Saves the full fact list to MongoDB and mirrors it to the local JSON fallback."""
    facts_dicts = [f.dict() for f in facts]

    db = get_db()
    if db is not None:
        db["user_memory_facts"].update_one(
            {"username": username},
            {"$set": {"facts": facts_dicts}},
            upsert=True,
        )

    path = _get_user_file(username)
    with open(path, "w") as f:
        json.dump(facts_dicts, f, indent=2)


def find_similar_fact(facts: List[UserFact], category: str, fact_text: str) -> Optional[UserFact]:
    """Naive same-category text-overlap match, used to update an existing fact instead of duplicating it."""
    candidate = fact_text.strip().lower()
    for existing in facts:
        if existing.category != category or not existing.active:
            continue
        existing_text = existing.fact.strip().lower()
        if existing_text == candidate or existing_text in candidate or candidate in existing_text:
            return existing
    return None


def _find_best_embedding_match(facts: List[UserFact], category: str, embedding: List[float]) -> tuple:
    """Returns (best_matching_fact, similarity) among same-category facts that have an embedding."""
    best_fact, best_sim = None, 0.0
    for existing in facts:
        if existing.category != category or not existing.active or not existing.embedding:
            continue
        sim = cosine_similarity(embedding, existing.embedding)
        if sim > best_sim:
            best_fact, best_sim = existing, sim
    return best_fact, best_sim


def _judge_fact_relationship(existing_fact: str, new_fact: str) -> str:
    """Asks the LLM whether a new statement duplicates, supersedes, or is distinct from an
    existing fact. Defaults to 'distinct' (the safe, non-destructive choice) on any failure."""
    try:
        response = lite_llm.invoke(FACT_CONFLICT_PROMPT.format(existing_fact=existing_fact, new_fact=new_fact))
        raw_content = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw_content, list):
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
        else:
            raw_text = str(raw_content)
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        action = json.loads(clean_json).get("action")
        return action if action in ("duplicate", "supersede", "distinct") else "distinct"
    except Exception:
        logger.exception("[MemoryUtils] Fact conflict judgment failed; treating as distinct.")
        return "distinct"


def save_user_fact(
    username: str,
    fact: str,
    category: str = "preference",
    source: str = "explicit",
    confidence: float = 1.0,
) -> UserFact:
    """Saves a durable fact for a user, using embedding similarity + an LLM judgment call to
    detect near-duplicates and contradictions (superseding the old fact) instead of a naive
    text-overlap check. Falls back to that naive check if embedding generation fails."""
    if category not in VALID_CATEGORIES:
        category = "preference"

    now = datetime.now(timezone.utc).isoformat()
    all_facts = load_user_facts(username)
    fact_text = fact.strip()
    new_embedding = embed_text(fact_text)

    existing = None
    if new_embedding is not None:
        best_match, best_sim = _find_best_embedding_match(all_facts, category, new_embedding)
        if best_match is not None and best_sim >= FACT_SIMILARITY_THRESHOLD:
            action = _judge_fact_relationship(best_match.fact, fact_text)
            if action in ("duplicate", "supersede"):
                existing = best_match
    else:
        # Embeddings unavailable for some reason — fall back to the old naive substring check
        # rather than always creating a new fact.
        existing = find_similar_fact(all_facts, category, fact_text)

    if existing:
        existing.fact = fact_text
        existing.embedding = new_embedding if new_embedding is not None else existing.embedding
        existing.source = source
        existing.confidence = confidence
        existing.updated_at = now
        result = existing
    else:
        result = UserFact(
            id=str(uuid.uuid4()),
            username=username,
            category=category,
            fact=fact_text,
            source=source,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            active=True,
            embedding=new_embedding,
        )
        all_facts.append(result)

    save_user_facts(username, all_facts)
    logger.info("Saved memory fact for %s: [%s] %s", username, category, result.fact)
    return result


def delete_user_fact(username: str, fact_id: str) -> bool:
    all_facts = load_user_facts(username)
    remaining = [f for f in all_facts if f.id != fact_id]
    if len(remaining) == len(all_facts):
        return False
    save_user_facts(username, remaining)
    return True


def delete_all_user_facts(username: str) -> None:
    save_user_facts(username, [])


def fetch_relevant_user_facts(username: str, question: str, limit: int = 5) -> str:
    """
    Builds a short "known about this user" context block for passive injection into every
    generation prompt, mirroring app_utils.fetch_relevant_corrections. Identity facts are
    always included (foundational context shouldn't disappear just because the current
    question isn't about it); remaining slots go to whichever facts are most relevant to the
    current question by embedding similarity, falling back to recency if that's unavailable.
    """
    try:
        facts = load_user_facts(username)
        if not facts:
            return ""

        identity_facts = [f for f in facts if f.category == "identity"]
        other_facts = [f for f in facts if f.category != "identity"]
        remaining_slots = max(limit - len(identity_facts), 0)

        question_embedding = embed_text(question) if question else None
        if question_embedding is not None:
            scored = [
                (cosine_similarity(question_embedding, f.embedding) if f.embedding else -1.0, f)
                for f in other_facts
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            ranked_other = [f for _, f in scored]
        else:
            # Reverse before the stable sort so that facts with an identical updated_at
            # timestamp (e.g. two saved within the same tick) still break ties in favor of
            # whichever was appended most recently, rather than falling back to insertion
            # (oldest-first) order.
            ranked_other = sorted(reversed(other_facts), key=lambda f: f.updated_at, reverse=True)

        top_facts = identity_facts + ranked_other[:remaining_slots]
        if not top_facts:
            return ""

        lines = "\n".join(f"- {f.fact}" for f in top_facts)
        return f"\n\nKNOWN USER CONTEXT (use naturally, do not restate unless relevant):\n{lines}\n"
    except Exception:
        logger.exception("[MEMORY WARNING] Could not fetch user facts for %s", username)
        return ""
