import os
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel
from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "saapp_data", "memory")
os.makedirs(DATA_DIR, exist_ok=True)

VALID_CATEGORIES = {"preference", "identity", "setting", "trait"}


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


def save_user_fact(
    username: str,
    fact: str,
    category: str = "preference",
    source: str = "explicit",
    confidence: float = 1.0,
) -> UserFact:
    """Saves (or updates, if a similar fact already exists) a durable fact for a user."""
    if category not in VALID_CATEGORIES:
        category = "preference"

    now = datetime.now(timezone.utc).isoformat()
    all_facts = load_user_facts(username)

    existing = find_similar_fact(all_facts, category, fact)
    if existing:
        existing.fact = fact.strip()
        existing.source = source
        existing.confidence = confidence
        existing.updated_at = now
        result = existing
    else:
        result = UserFact(
            id=str(uuid.uuid4()),
            username=username,
            category=category,
            fact=fact.strip(),
            source=source,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            active=True,
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
    Builds a short "known about this user" context block for passive injection
    into every generation prompt, mirroring app_utils.fetch_relevant_corrections.
    """
    try:
        facts = load_user_facts(username)
        if not facts:
            return ""

        category_order = {"identity": 0, "preference": 1, "trait": 2, "setting": 3}
        ranked = sorted(
            facts,
            key=lambda f: (category_order.get(f.category, 9), f.updated_at),
            reverse=False,
        )
        top_facts = ranked[:limit]
        if not top_facts:
            return ""

        lines = "\n".join(f"- {f.fact}" for f in top_facts)
        return f"\n\nKNOWN USER CONTEXT (use naturally, do not restate unless relevant):\n{lines}\n"
    except Exception:
        logger.exception("[MEMORY WARNING] Could not fetch user facts for %s", username)
        return ""
