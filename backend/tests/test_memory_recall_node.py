"""What the explicit-recall node tells the reply: only notes that matched, and plainly when none did."""
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw
from backend.state.graph_state import GraphState  # noqa: F401  (the node's state type)


class Fact:
    def __init__(self, text):
        self.category, self.fact, self.id, self.embedding = "preference", text, "1", None
        self.source = "explicit"
        self.confidence = 1.0
        self.created_at = self.updated_at = "2026-01-01T00:00:00+00:00"
        self.active = True
        self.goal_status = None
        self.last_nudged_at = None
        self.username = "u"

    def dict(self):
        return {k: v for k, v in self.__dict__.items()}


def run_node(monkeypatch, facts, notes, question="Do you remember my car?"):
    monkeypatch.setattr(aw, "load_user_facts", lambda username: facts)
    monkeypatch.setattr(aw, "fact_for_state", lambda f: {"category": f.category, "fact": f.fact})
    monkeypatch.setattr(aw, "recall_explicit", lambda *a, **k: notes)
    state = aw.memory_recall_node({"messages": [HumanMessage(content=question)], "username": "u"})
    return state, state["documents"][-1].page_content


def test_matching_notes_are_listed_as_relevant_past_context(monkeypatch):
    state, report = run_node(monkeypatch, [Fact("Likes tea")], [Document(page_content="Adopted a greyhound named Biscuit.")])
    assert "Relevant past context:\n- Adopted a greyhound named Biscuit." in report and "none matched" not in report
    assert state["memory_hits"] == ["Adopted a greyhound named Biscuit."]


def test_when_no_note_matched_the_report_says_so_instead_of_offering_unrelated_ones(monkeypatch):
    state, report = run_node(monkeypatch, [Fact("Likes tea")], [])
    assert "Notes from past conversations: none matched this question." in report and "Relevant past context" not in report
    assert "say plainly that you do not have it" in report and "Do not guess" in report
    assert "Saved facts/preferences:\n- [preference] Likes tea" in report
    assert state["memory_hits"] == []


def test_with_neither_facts_nor_notes_the_report_says_neither_exists(monkeypatch):
    _, report = run_node(monkeypatch, [], [])
    assert "No saved facts or preferences" in report and "no notes from past conversations matched" in report
