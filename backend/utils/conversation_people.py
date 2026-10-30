"""Who the conversation is about, so "her" is not guessed from whichever saved fact sounds closest.

THE FAILURE (2026-10-30): all evening they talked about Yashu. Then "I'm so afraid I won't see her again" (no name in it) was matched against the saved facts on its own words. The five
relationship facts it picked were about Suzie and Tina, two people not mentioned in weeks, because "rejection" and "loss" sound like "afraid I won't see her". None was about Yashu, and
Sonic asked whether this was about Suzie.

THE RULE: a saved person who is named in the recent conversation is the likely meaning of "she" or "he", so facts about them rank higher, and on a turn that names nobody the reply is
told who the conversation has been about (and not to reach for someone who has not come up). Pure code, no model call, no name in any log line.

WHICH WORDS COUNT AS A PERSON: a capitalised word in the middle of a sentence in one of their relationship facts. A word in many of their facts (their own name, the assistant's) is not
someone they talk about, and days and months are not people.
"""
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from backend.utils.name_aliases import _NOT_NAMES

WINDOW_MESSAGES = 80            # how far back the conversation is read (a whole evening is more than forty messages)
DECAY_PER_MESSAGE = 0.96        # a mention this many messages back counts for this much less each message
SATURATES_AT = 0.4              # a name mentioned about twice in the window counts fully; once, long ago, counts partly
MAX_NAMES = 4
COMMON_SHARE = 0.08             # a name in more than this share of all their facts is the person themselves, not someone they talk about
MIN_NAME_LETTERS = 3
BOOST = 0.5                     # a fact about a fully-present person ranks as if its score were this much higher

_CAP_WORD_RE = re.compile(r"\b[A-Z][a-z]{%d,}\b" % (MIN_NAME_LETTERS - 1))


def _mid_sentence_capitals(text: str) -> List[str]:
    """Capitalised words that are not the first word of a sentence."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        words = list(_CAP_WORD_RE.finditer(sentence))
        start = len(sentence) - len(sentence.lstrip())
        found.extend(m.group(0) for m in words if m.start() != start)
    return found


def people_vocabulary(facts: Sequence[Any]) -> List[str]:
    """The names of the people in their relationship facts, as written there. `facts` are UserFact-like (.fact, .category, .active)."""
    active = [f for f in facts if getattr(f, "active", True)]
    if not active:
        return []
    in_facts: Dict[str, int] = {}
    for fact in active:
        for word in {w.casefold() for w in _CAP_WORD_RE.findall(getattr(fact, "fact", "") or "")}:
            in_facts[word] = in_facts.get(word, 0) + 1
    common_at = max(COMMON_SHARE * len(active), 3)
    names: Dict[str, str] = {}
    for fact in active:
        if getattr(fact, "category", None) != "relationship":
            continue
        for word in _mid_sentence_capitals(getattr(fact, "fact", "") or ""):
            key = word.casefold()
            if key in _NOT_NAMES or in_facts.get(key, 0) > common_at:
                continue
            names.setdefault(key, word)
    return list(names.values())


def mention_weights(messages: Iterable[Any], names: Sequence[str]) -> Dict[str, float]:
    """name -> how present they are in the recent conversation: each message that names them counts DECAY_PER_MESSAGE ** (messages ago). Case does not matter (people type "tina")."""
    texts = [m.content for m in list(messages or [])[-WINDOW_MESSAGES:] if isinstance(getattr(m, "content", None), str)]
    patterns = {name: re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE) for name in names}
    weights: Dict[str, float] = {}
    for ago, text in enumerate(reversed(texts)):
        for name, pattern in patterns.items():
            if pattern.search(text):
                weights[name] = weights.get(name, 0.0) + DECAY_PER_MESSAGE ** ago
    return weights


def people_in_conversation(messages: Iterable[Any], facts: Sequence[Any]) -> List[Tuple[str, float]]:
    """[(name, presence 0..1)] for the saved people named in the recent conversation, most present first, at most MAX_NAMES."""
    names = people_vocabulary(facts)
    if not names:
        return []
    weights = mention_weights(messages, names)
    ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)[:MAX_NAMES]
    return [(name, min(1.0, weight / SATURATES_AT)) for name, weight in ranked]


def boost_for(fact_text: str, people: Sequence[Tuple[str, float]]) -> float:
    """The multiplier for a fact's ranking score: 1 plus BOOST times the presence of the most present person it names (1.0 when it names none)."""
    best = 0.0
    for name, presence in people:
        if presence > best and re.search(r"\b" + re.escape(name) + r"\b", fact_text or "", re.IGNORECASE):
            best = presence
    return 1.0 + BOOST * best


def names_anyone(question: str, people: Sequence[Tuple[str, float]]) -> bool:
    return any(re.search(r"\b" + re.escape(name) + r"\b", question or "", re.IGNORECASE) for name, _ in people)


def people_note(people: Sequence[Tuple[str, float]], question: str) -> str:
    """The line that tells the reply who the conversation has been about. Empty when no saved person is in it, and when this message already names one (then it is clear)."""
    if not people or names_anyone(question, people):
        return ""
    names = ", ".join(name for name, _ in people)
    return (
        f"PEOPLE THIS CONVERSATION HAS BEEN ABOUT: {names}.\n"
        "When they say she, he, her, him or they without a name, they mean one of these, whoever fits what they have been saying. Do not take it to be someone from the saved context above "
        "who has not come up in this conversation, and never guess a name: if it is not clear who they mean, ask.\n"
    )
