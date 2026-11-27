"""Putting Sonic's own approved words into a finished reply by code, word for word.

WHY. Material that is only OFFERED to the model almost never reaches a reply, and material that code PLACES always does (30 days to 2026-11-22: record cases offered 99, cited 1; shape stories
and jokes, both placed by code, 4 of 4 each). Shape stories were the first to be placed (backend/utils/shape_stories.py insert_story, 2026-11-14, after asking the model got the sentence said in
1 reply of 20). This is the same placement for the other kinds of Sonic's own material: a case from its record (experiences.spoken) and a habit it really has (a RELATABLE line).

RULES. At most one placed item per reply (the caller picks, in order: shape story, record case, relatable line). The words are placed as their own paragraph after the reply's first paragraph (at
the end of a one-paragraph reply), before any <<<FOLLOW_UP>>> tag, and never when they are already in the reply. Nothing here decides WHETHER to place: every gate (mood, safety, rate limits,
the slot) is the caller's, as before. Pure.
"""
import re

_FOLLOW_UP_TAG_RE = re.compile(r"\s*<<<FOLLOW_UP:.*?>>>\s*$", re.DOTALL)


def after_first_paragraph(reply: str, words: str) -> str:
    """The reply with `words` as a paragraph of its own after its first paragraph. Unchanged when there is nothing to place, no reply, or the words are already in it. Pure."""
    text = reply or ""
    speech = (words or "").strip()
    if not speech or not text.strip() or speech in text:
        return text
    tag = _FOLLOW_UP_TAG_RE.search(text)
    body, tail = (text[: tag.start()], text[tag.start():].lstrip()) if tag else (text, "")
    paragraphs = body.rstrip().split("\n\n", 1)
    placed = f"{paragraphs[0]}\n\n{speech}" + (f"\n\n{paragraphs[1]}" if len(paragraphs) == 2 else "")
    return f"{placed}\n\n{tail}" if tail else placed


def at_end(reply: str, words: str) -> str:
    """The reply with `words` as its last paragraph (before any <<<FOLLOW_UP>>> tag). Unchanged when there is nothing to place, no reply, or the words are already in it. For something that
    stands apart from the answer (backend/utils/volunteer.py), where the middle of the reply would interrupt it. Pure."""
    text = reply or ""
    speech = (words or "").strip()
    if not speech or not text.strip() or speech in text:
        return text
    tag = _FOLLOW_UP_TAG_RE.search(text)
    body, tail = (text[: tag.start()], text[tag.start():].lstrip()) if tag else (text, "")
    placed = f"{body.rstrip()}\n\n{speech}"
    return f"{placed}\n\n{tail}" if tail else placed
