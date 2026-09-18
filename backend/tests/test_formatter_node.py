from unittest.mock import Mock

from backend.services import agent_workflow as aw


def _state(**overrides):
    base = {"messages": [], "documents": []}
    base.update(overrides)
    return base


def test_formatter_node_never_calls_an_llm(monkeypatch):
    # The core regression this refactor locks in: formatter_node used to make a real, wasted
    # LLM call on almost every request. It must never do that again.
    mock_get_chat_llm = Mock()
    monkeypatch.setattr(aw, "get_chat_llm", mock_get_chat_llm)

    aw.formatter_node(_state(relevance_grade="conversational"))

    mock_get_chat_llm.assert_not_called()


def test_source_type_mapping_web_search():
    state = aw.formatter_node(_state(relevance_grade="web_search", content_to_format=None))
    assert state["voice_payload"]["source_type"] == "web"


def test_source_type_mapping_tool_output_grades():
    for grade in ("code_interpreter", "github_search", "pr_summary"):
        state = aw.formatter_node(_state(relevance_grade=grade, content_to_format="some tool output"))
        assert state["voice_payload"]["source_type"] == "tool_output", grade
        assert state["voice_payload"]["data"] == "some tool output"


def test_source_type_mapping_conversational_grade():
    state = aw.formatter_node(_state(relevance_grade="conversational"))
    assert state["voice_payload"]["source_type"] == "conversational"


def test_source_type_mapping_insight_answer_overrides_to_conversational():
    # memory_save_node sets relevance_grade="conversational" AND insight_answer together; other
    # callers (e.g. cancel_action) may set insight_answer with a different/absent grade.
    state = aw.formatter_node(_state(relevance_grade="yes", insight_answer="Saved a memory fact."))
    assert state["voice_payload"]["source_type"] == "conversational"
    assert state["voice_payload"]["insight"] == "Saved a memory fact."


def test_source_type_mapping_rag_fallthrough_strict():
    state = aw.formatter_node(_state(relevance_grade="yes", rag_mode="strict"))
    assert state["voice_payload"]["source_type"] == "kb_strict"


def test_source_type_mapping_rag_fallthrough_open():
    state = aw.formatter_node(_state(relevance_grade="yes", rag_mode="open"))
    assert state["voice_payload"]["source_type"] == "kb_open"


def test_source_type_mapping_no_grade_defaults_to_kb_strict():
    state = aw.formatter_node(_state(relevance_grade="no"))
    assert state["voice_payload"]["source_type"] == "kb_strict"


def test_source_type_mapping_hitl_grades_default_to_kb_strict():
    # These grades short-circuit before formatter_node even runs in app.py's real flow, but
    # confirm the mapping doesn't crash / silently misclassify if it were ever reached.
    for grade in ("hitl_approval_required", "action_complete"):
        state = aw.formatter_node(_state(relevance_grade=grade))
        assert state["voice_payload"]["source_type"] == "kb_strict", grade


def test_voice_payload_carries_relevance_grade_through():
    state = aw.formatter_node(_state(relevance_grade="pr_summary", content_to_format="review body"))
    assert state["voice_payload"]["relevance_grade"] == "pr_summary"
