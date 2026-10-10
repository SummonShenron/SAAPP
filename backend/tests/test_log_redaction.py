import logging

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import log_hygiene as lh

SECRET = "my therapist said I should stop seeing him, and my card number is 4111111111111111"


def state():
    return {
        "workflowName": "sonic_assistant",
        "requestId": "abc123",
        "username": "jack@example.com",
        "messages": [HumanMessage(content=SECRET), AIMessage(content="That sounds hard. " + SECRET)],
        "documents": [Document(page_content=SECRET, metadata={"source": "taxes.pdf", "page": 3})],
        "memory_facts": [{"fact": SECRET, "category": "relationship"}],
        "emotional_state": {"valence": "distressed", "intensity": 0.8, "need": "support", "risk": "elevated",
                            "gist": SECRET, "contact": "his sister Dana"},
        "relevance_grade": "yes",
        "loop_count": 1,
        "reasoner_flags": {"needs_retrieval": True, "needs_conversation": False},
        "raw_generation": SECRET,
        "original_question": SECRET,
        "pending_action": {"action_type": "send_email", "details": {"to": "a@b.c", "subject": "hi", "body": SECRET}},
    }


# ---- the summary -----------------------------------------------------------------------------------------------------

def test_a_summary_of_a_state_contains_no_content_at_all():
    summary = lh.redact_state(state())
    assert SECRET not in repr(summary) and "therapist" not in repr(summary) and "Dana" not in repr(summary)
    assert "4111" not in repr(summary) and "a@b.c" not in repr(summary)


def test_the_summary_keeps_what_debugging_needs_shapes_counts_sources_and_flags():
    summary = lh.redact_state(state())
    assert summary["workflowName"] == "sonic_assistant" and summary["requestId"] == "abc123"
    assert summary["relevance_grade"] == "yes" and summary["loop_count"] == 1
    assert summary["reasoner_flags"] == {"needs_retrieval": True, "needs_conversation": False}
    assert summary["messages"]["messages"] == 2 and summary["messages"]["roles"] == ["human", "ai"]
    assert summary["messages"]["chars"] > 0
    assert summary["documents"] == {"documents": 1, "sources": ["taxes.pdf"]}
    assert summary["raw_generation"] == {"str": len(SECRET)}


def test_a_key_it_has_never_heard_of_is_summarized_not_passed_through():
    summary = lh.redact_state({"brand_new_field": "something the user typed", "another": ["a", "b"], "nested": {"deep": "text"}})
    assert "something the user typed" not in repr(summary) and "text" not in repr(summary).replace("str", "")
    assert summary["brand_new_field"] == {"str": len("something the user typed")}
    assert summary["another"] == {"list": 2}


def test_an_identifier_in_a_kept_key_survives_but_a_long_value_in_that_key_does_not():
    assert lh.redact_state({"requestId": "r-1"})["requestId"] == "r-1"
    assert lh.redact_state({"requestId": "x" * 500})["requestId"] == {"str": 500}


def test_odd_inputs_never_raise():
    assert lh.redact_state(None) is None
    assert lh.redact_state("just a string") == {"str": 13}
    assert lh.redact_state(object())["type"] == "object"
    assert lh.redact_state([1, 2]) == {"list": 2}


def test_the_emotion_summary_drops_the_gist_and_the_contact_and_keeps_how_strong_and_what_kind():
    out = lh.emotion_summary(state()["emotional_state"])
    assert out == {"valence": "distressed", "intensity": 0.8, "need": "support", "risk": "elevated"}
    assert lh.emotion_summary(None) == {} and lh.emotion_summary("x") == {}


def test_a_tool_result_is_logged_as_a_size_unless_the_tool_itself_reported_an_error():
    result = "Meeting with Dana about the divorce at 3pm"
    assert lh.describe_observation(result) == f"{len(result)} chars" and "Dana" not in lh.describe_observation(result)
    assert lh.describe_observation("ERROR: file not found: src/app.py") == "ERROR: file not found: src/app.py"
    assert lh.describe_observation("") == "0 chars" and lh.describe_observation(None) == "0 chars"
    assert len(lh.describe_observation("ERROR " + "x" * 500)) == 200


# ---- the filter, on a real logger with a real handler ------------------------------------------------------------------

class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def logger_with_capture():
    logger = logging.getLogger("test-redaction-logger")
    logger.handlers.clear()
    logger.filters.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addFilter(lh.StateRedactionFilter())
    handler = Capture()
    logger.addHandler(handler)
    yield logger, handler
    logger.handlers.clear()
    logger.filters.clear()


def test_a_handler_never_sees_the_nodes_conversation_state_only_its_summary(logger_with_capture, monkeypatch):
    monkeypatch.delenv(lh.CAPTURE_ENV, raising=False)
    logger, handler = logger_with_capture
    node_input = state()
    logger.info("node executed", extra={"service": "SAAPP", "erragent_context": {"input": node_input, "output": state()}})
    seen = handler.records[0].erragent_context
    assert SECRET not in repr(seen) and "Dana" not in repr(seen)
    assert seen["input"]["messages"]["messages"] == 2 and seen["output"]["documents"]["sources"] == ["taxes.pdf"]


def test_other_context_keys_pass_through_unchanged(logger_with_capture, monkeypatch):
    monkeypatch.delenv(lh.CAPTURE_ENV, raising=False)
    logger, handler = logger_with_capture
    logger.error("Request returned server error", extra={"erragent_context": {"method": "POST", "path": "/api/chat", "statusCode": 500}})
    assert handler.records[0].erragent_context == {"method": "POST", "path": "/api/chat", "statusCode": 500}


def test_the_callers_own_dictionary_is_not_mutated_and_the_record_is_never_dropped(logger_with_capture, monkeypatch):
    monkeypatch.delenv(lh.CAPTURE_ENV, raising=False)
    logger, handler = logger_with_capture
    context = {"input": state(), "output": state()}
    logger.info("node executed", extra={"erragent_context": context})
    assert len(handler.records) == 1
    assert isinstance(context["input"]["messages"][0], HumanMessage)  # the node's own state is untouched


def test_full_state_is_captured_only_when_deliberately_asked_for(logger_with_capture, monkeypatch):
    logger, handler = logger_with_capture
    monkeypatch.setenv(lh.CAPTURE_ENV, "true")
    logger.info("node executed", extra={"erragent_context": {"input": {"raw_generation": SECRET}}})
    assert handler.records[0].erragent_context["input"]["raw_generation"] == SECRET


def test_a_record_with_no_context_is_untouched(logger_with_capture):
    logger, handler = logger_with_capture
    logger.info("plain line")
    assert handler.records[0].getMessage() == "plain line" and not hasattr(handler.records[0], "erragent_context")


def test_if_redaction_itself_fails_the_context_becomes_a_marker_never_the_raw_state(logger_with_capture, monkeypatch):
    monkeypatch.delenv(lh.CAPTURE_ENV, raising=False)
    logger, handler = logger_with_capture

    def boom(_):
        raise RuntimeError("redactor bug")

    monkeypatch.setattr(lh, "redact_state", boom)
    logger.info("node executed", extra={"erragent_context": {"input": {"raw_generation": SECRET}}})
    assert handler.records[0].erragent_context == {"redaction": "failed"}


def test_the_apps_logger_setup_installs_the_filter_once_and_before_any_handler_is_added():
    from backend.logging.sass_logger import setup_logging

    logger = setup_logging()
    setup_logging()
    assert sum(isinstance(f, lh.StateRedactionFilter) for f in logger.filters) == 1

    # And on the real logger every node uses, a handler attached later (as erragent's is) still sees only the summary.
    handler = Capture()
    logger.addHandler(handler)
    try:
        logger.info("node executed", extra={"service": "SAAPP", "erragent_context": {"input": state(), "output": state()}})
    finally:
        logger.removeHandler(handler)
    assert SECRET not in repr(handler.records[0].erragent_context)
    assert handler.records[0].erragent_context["input"]["messages"]["messages"] == 2
