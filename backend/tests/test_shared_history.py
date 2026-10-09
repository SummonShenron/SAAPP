import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from backend.services import memory_search as ms
from backend.utils import shared_history as sh

OPEN = dict(source_type="conversational", risk_active=False, energy_tier="open", closing=False)


def ai(marked=False):
    return SimpleNamespace(additional_kwargs={sh.CALLBACK_MARK: True} if marked else {})


def allowed(recalled=True, recent=(), **overrides):
    return sh.callback_allowed(recalled, recent, **{**OPEN, **overrides})


def test_a_callback_is_invited_only_when_something_was_actually_recalled():
    assert allowed()
    assert not allowed(recalled=False)


@pytest.mark.parametrize("overrides", [
    {"risk_active": True}, {"closing": True}, {"energy_tier": "subdued"}, {"energy_tier": "easing"},
    {"proactive_taken": True}, {"source_type": "kb_strict"}, {"source_type": "web"}, {"source_type": "tool_output"},
])
def test_it_stays_quiet_under_safety_a_held_down_mood_a_closing_another_proactive_item_or_a_non_chat_route(overrides):
    assert not allowed(**overrides)


def test_it_is_rate_limited_by_the_marker_on_earlier_replies():
    assert not allowed(recent=[ai(marked=True)] + [ai() for _ in range(4)])
    assert allowed(recent=[ai(marked=True)] + [ai() for _ in range(sh.CALLBACK_MIN_GAP)])


def test_the_block_allows_one_light_connection_and_forbids_reciting_naming_memory_or_claiming_it_was_thought_about():
    block = sh.build_callback_block()
    assert "CALLBACK (optional)" in block and "once, in a sentence or less" in block
    assert "genuinely connects" in block and "skip it entirely if the link is a stretch" in block
    for rule in ("Never list or recite", "never mention memory, records or notes", "never say you were thinking about it"):
        assert rule in block


# ---- recalled lines carry how long ago, honestly --------------------------------------------------------------------

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def doc(text="Fixed a flaky retry bug in billing.", created=None):
    metadata = {} if created is None else {"created_at": created.isoformat()}
    return Document(page_content=text, metadata=metadata)


def test_a_recalled_chunk_is_dated_roughly_from_its_own_timestamp():
    assert ms._age_prefix(doc(created=NOW - timedelta(days=21)), NOW) == "(about 3 weeks ago) "
    assert ms._age_prefix(doc(created=NOW - timedelta(days=3)), NOW) == "(about 3 days ago) "


def test_a_chunk_without_a_usable_timestamp_gets_no_date_so_nothing_is_guessed():
    assert ms._age_prefix(doc(), NOW) == ""
    assert ms._age_prefix(Document(page_content="x", metadata={"created_at": "garbage"}), NOW) == ""
    assert ms._age_prefix(doc(created=NOW + timedelta(days=2)), NOW) == ""  # a timestamp in the future is not trusted


def test_the_recall_block_carries_the_age_in_front_of_each_line():
    class Store:
        def similarity_search_with_score(self, *a, **k):
            return [(doc(created=datetime.now(timezone.utc) - timedelta(days=14)), 0.9)]

    out = ms.retrieve_relevant_memory_context(Store(), "u", "my retry is flaky again")
    assert "- (about 2 weeks ago) Fixed a flaky retry bug in billing." in out


def test_the_app_invites_a_callback_only_with_recalled_context_marks_it_and_counts_it():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    for needle in (
        "if callback_allowed(",
        "prompt = prompt + build_callback_block()",
        "callback_offered = True",
        'tally(turn_counts, "callback_offered")',
        "ai_message.additional_kwargs[CALLBACK_MARK] = True",
    ):
        assert needle in source, needle
    recall_at = source.index("if semantic_memory_context:")
    assert source.index("if callback_allowed(") > recall_at  # inside the branch where something was recalled
