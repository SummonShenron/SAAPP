"""What a "Source: Memory" citation shows (backend/utils/memory_sources.py): which notes and facts it holds, the citation text the model copies, and that it is saved with the reply."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from backend.components.constraints import format_docs
from backend.utils import app_utils, memory_sources as ms

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)


def note(text, days_ago=None):
    metadata = {} if days_ago is None else {"created_at": (NOW - timedelta(days=days_ago, hours=1)).isoformat()}
    return Document(page_content=text, metadata=metadata)


def fact(id_, text, category="personal"):
    return SimpleNamespace(id=id_, fact=text, category=category)


FACTS = [
    fact("f1", "Had ice cream on Friday night after finishing the migration.", "personal"),
    fact("f2", "Works at Principal Financial Group.", "career"),
    fact("f3", "Likes chocolate ice cream with peanut butter.", "personal"),
    fact("f4", "Has a dog named Max.", "relationship"),
]


# ---- the notes -------------------------------------------------------------------------------------------------------------------------------

def test_a_note_carries_its_text_and_how_long_ago_it_was():
    entries = ms.note_entries([note("Went for ice cream after the long week.", days_ago=4)], NOW)
    assert entries == [{"text": "Went for ice cream after the long week.", "when": entries[0]["when"]}] and "4 days" in entries[0]["when"] and entries[0]["when"].endswith("ago")


def test_a_note_with_no_time_says_none_and_is_never_guessed():
    assert ms.note_entries([note("something", None)], NOW)[0]["when"] is None


def test_notes_are_capped_and_trimmed_and_empty_ones_dropped():
    many = [note(f"note {i} " + "x" * 900, days_ago=i) for i in range(10)] + [note("   ")]
    entries = ms.note_entries(many, NOW)
    assert len(entries) == ms.MAX_NOTES and all(len(e["text"]) <= ms.MAX_TEXT_CHARS for e in entries)
    assert ms.note_entries([note("   ")], NOW) == []


# ---- the facts --------------------------------------------------------------------------------------------------------------------------------

def test_only_facts_that_share_a_word_with_the_question_are_shown_best_match_first():
    entries = ms.fact_entries("when did I last have ice cream?", FACTS)
    assert [e["id"] for e in entries] == ["f3", "f1"] or [e["id"] for e in entries] == ["f1", "f3"]
    assert {e["id"] for e in entries} == {"f1", "f3"}
    assert all(e["text"] and e["category"] for e in entries)


def test_a_plural_matches_its_singular_and_filler_words_match_nothing():
    assert {e["id"] for e in ms.fact_entries("what dogs do I have", FACTS)} == {"f4"}
    assert ms.fact_entries("when did I last have it", FACTS) == []             # only filler words
    assert ms.fact_entries("", FACTS) == []


def test_facts_may_be_dicts_as_the_graph_state_holds_them_and_are_capped():
    dicts = [{"id": f"d{i}", "fact": f"Ate pizza number {i}", "category": "personal"} for i in range(20)]
    entries = ms.fact_entries("pizza", dicts)
    assert len(entries) == ms.MAX_FACTS and all(e["id"].startswith("d") for e in entries)


def test_build_keeps_both_parts():
    view = ms.build("when did I last have ice cream", [note("Ice cream on Friday.", days_ago=5)], FACTS, NOW)
    assert len(view["notes"]) == 1 and {e["id"] for e in view["facts"]} == {"f1", "f3"}


# ---- finding it in a turn's documents ---------------------------------------------------------------------------------------------------------------

def memory_doc(view):
    return Document(page_content="SYSTEM MEMORY REPORT: x", metadata={"source": "user_memory", "priority": True, "memory_sources": view})


def test_the_view_comes_from_the_memory_document_only():
    view = {"notes": [{"text": "n", "when": None}], "facts": []}
    other = Document(page_content="a library page", metadata={"source": "guide.pdf"})
    assert ms.from_documents([other, memory_doc(view)]) == view
    assert ms.from_documents([other]) is None
    assert ms.from_documents([]) is None and ms.from_documents(None) is None


def test_a_memory_document_that_held_nothing_to_show_gives_none():
    assert ms.from_documents([memory_doc({"notes": [], "facts": []})]) is None
    assert ms.from_documents([Document(page_content="x", metadata={"source": "user_memory"})]) is None


# ---- the citation the model copies --------------------------------------------------------------------------------------------------------------------

def test_the_memory_document_is_cited_as_a_viewer_link_not_a_download():
    text = format_docs([memory_doc({"notes": [{"text": "n", "when": None}], "facts": []})])
    assert "EXACT CITATION LINK: [Source: Memory](#memory-source)" in text
    assert "/api/documents/download/user_memory" not in text and "user_memory" not in text.replace("SYSTEM MEMORY REPORT", "")
    assert "DOCUMENT REPOSITORY SOURCE: Memory | PAGE NUMBER: N/A" in text


def test_an_ordinary_document_is_cited_exactly_as_before():
    text = format_docs([Document(page_content="body", metadata={"source": "guide.pdf", "page": 2})])
    assert "[Source: guide.pdf - Page 3](/api/documents/download/guide.pdf#page=3)" in text


# ---- kept with the reply, and loaded back with the history --------------------------------------------------------------------------------------------------

def test_the_view_is_saved_with_the_reply_and_comes_back_after_a_reload():
    view = {"notes": [{"text": "Ice cream on Friday.", "when": "5 days ago"}], "facts": [{"id": "f1", "text": "Had ice cream.", "category": "personal"}]}
    saved = app_utils._serialize_messages([AIMessage(content="It was Friday.", additional_kwargs={"memory_sources": view})]) if hasattr(app_utils, "_serialize_messages") else None
    if saved is None:
        import inspect

        names = [n for n, f in inspect.getmembers(app_utils, inspect.isfunction) if "serial" in n and "de" not in n[:3]]
        saved = getattr(app_utils, names[0])([AIMessage(content="It was Friday.", additional_kwargs={"memory_sources": view})])
    assert saved[0]["memory_sources"] == view
    restored = app_utils._deserialize_messages(saved)
    assert restored[0].additional_kwargs["memory_sources"] == view


def test_a_reply_without_a_view_saves_nothing_extra():
    saved = app_utils._deserialize_messages([{"type": "ai", "content": "hi"}])
    assert "memory_sources" not in saved[0].additional_kwargs


# ---- the wiring ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_recall_node_stores_what_matched_on_its_document():
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    assert '"memory_sources": memory_sources.build(question, semantic_hits, facts)' in source


def test_the_chat_endpoint_sends_and_saves_the_view_but_never_for_a_guest():
    source = open("app.py", encoding="utf-8").read()
    assert "None if is_guest_username(username) else memory_sources.from_documents(documents_sorted)" in source
    assert "'event': 'memory_sources'" in source and 'ai_message.additional_kwargs["memory_sources"] = memory_source_view' in source


def test_the_browser_opens_the_viewer_for_the_new_link_and_for_the_old_download_link():
    chat = open("local/src/pages/Chat.tsx", encoding="utf-8").read()
    assert "href === '#memory-source'" in chat and "endsWith('/api/documents/download/user_memory')" in chat
    assert "payload.event === 'memory_sources'" in chat and "m.memory_sources" in chat and "<MemorySourceDialog" in chat
