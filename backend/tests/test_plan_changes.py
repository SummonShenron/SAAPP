"""A saved plan follows the person when they say it moved or was cancelled (backend/utils/plan_changes.py).
The real case: "a date with Yashu on Friday (2026-10-30)" stayed saved after they said the date moved to Sunday."""
import asyncio
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.utils import memory_utils
from backend.utils import plan_changes as pc

NOW = datetime(2026, 10, 30, 17, 0, tzinfo=timezone.utc)      # a Friday
TODAY = date(2026, 10, 30)
DATE_FACT = "The user has a date with Yashu on Friday (2026-10-30)."
MESSAGE = "no.. the date got canceled remember? it got moved to sunay?"
counted: list = []


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: [1.0, 0.0] if "Yashu" in text else [0.0, 1.0])
    monkeypatch.setattr(memory_utils.lite_llm, "invoke", lambda *a, **k: SimpleNamespace(content=json.dumps({"action": "distinct"})))
    counted.clear()
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts: counted.append(dict(counts)))
    yield


def _msgs(*texts):
    return [SimpleNamespace(content=t, type="human") for t in texts]


def _save(text, category="relationship"):
    return memory_utils.save_user_fact("jack", text, category=category)


def _run(message, invoke, messages=None):
    return asyncio.run(pc.apply_plan_changes("jack", message, messages or _msgs("did yashu call?", message), "UTC", invoke=invoke, now=NOW))


def _says(*changes):
    async def invoke(prompt):
        return SimpleNamespace(content=json.dumps({"changes": list(changes)}))
    return invoke


def test_the_gate_wants_a_move_or_a_cancellation():
    for text in ("it got moved to sunay", "she cancelled", "we pushed it back", "rescheduled for monday", "the date fell through", "it's now on saturday"):
        assert pc.looks_like_change(text), text
    for text in ("i moved to a new apartment", "what time is it", "i have a date on sunday"):
        assert not pc.looks_like_change(text), text


def test_candidates_are_dated_facts_about_someone_in_the_conversation():
    dated = _save(DATE_FACT)
    _save("Jack likes hiking and trails.", "preference")
    _save("The user had a job interview at Proofpoint (2026-10-06).", "career")      # dated, but a month ago and about nobody here
    for i in range(6):
        _save(f"Jack likes hobby number {i}.", "trait")
    found = pc.candidate_facts(memory_utils.load_user_facts("jack"), MESSAGE, _msgs("did yashu call?", MESSAGE), TODAY)
    assert [f.id for f in found] == [dated.id]


def test_a_move_rewrites_the_fact_with_the_real_date_from_code():
    fact = _save(DATE_FACT)
    result = _run(MESSAGE, _says({"id": fact.id, "action": "update", "text": "The user has a date with Yashu on Sunday."}))
    assert result["updated"] == 1
    saved = memory_utils.load_user_facts("jack")[0]
    assert saved.fact == "The user has a date with Yashu on Sunday (2026-11-01)."
    assert saved.source == memory_utils.USER_EDIT_SOURCE and saved.confidence == 1.0
    assert result["noted"] == ["Noted: updated what I had saved, now: The user has a date with Yashu on Sunday (2026-11-01)."]
    assert counted == [{"plan_change_updated": 1, "plan_change_cancelled": 0, "plan_change_unchanged": 0}]       # counts only


def test_a_model_that_writes_its_own_numeric_date_or_keeps_the_old_one_is_not_applied():
    fact = _save(DATE_FACT)
    for text in ("The user has a date with Yashu on Sunday (2026-11-01).",              # the model's own date
                 "The user has a date with Yashu on Sunday, moved from Friday (2026-10-30)."):   # two days, the old date still beside it
        assert _run(MESSAGE, _says({"id": fact.id, "action": "update", "text": text}))["updated"] == 0
    assert memory_utils.load_user_facts("jack")[0].fact == DATE_FACT


def test_an_update_may_not_add_a_day_time_or_name_the_person_did_not_give():
    fact = _save(DATE_FACT)
    for text in ("The user has a date with Yashu on Saturday.",                         # a day they never said
                 "The user has a date with Yashu on Sunday at 7pm.",                    # a time they never said
                 "The user has a date with Yashu and Priya on Sunday."):                # a name they never said
        assert _run(MESSAGE, _says({"id": fact.id, "action": "update", "text": text}))["updated"] == 0
    assert memory_utils.load_user_facts("jack")[0].fact == DATE_FACT


def test_a_cancellation_removes_the_fact_only_when_the_person_said_it_is_off():
    fact = _save(DATE_FACT)
    result = _run("the date with yashu got cancelled", _says({"id": fact.id, "action": "cancel", "text": ""}))
    assert result["cancelled"] == 1 and memory_utils.load_user_facts("jack") == []

    fact = _save(DATE_FACT)
    assert _run("it got moved to sunday", _says({"id": fact.id, "action": "cancel", "text": ""}))["cancelled"] == 0      # no "off" in their words: the model's cancel is ignored
    assert len(memory_utils.load_user_facts("jack")) == 1


def test_keep_and_unknown_ids_change_nothing():
    fact = _save(DATE_FACT)
    result = _run(MESSAGE, _says({"id": fact.id, "action": "keep", "text": ""}, {"id": "not-a-fact", "action": "update", "text": "x"}))
    assert result["updated"] == result["cancelled"] == 0 and result["unchanged"] == 1
    assert memory_utils.load_user_facts("jack")[0].fact == DATE_FACT


def test_no_model_call_without_the_gate_a_candidate_or_when_the_topic_is_sensitive():
    calls = []

    async def invoke(prompt):
        calls.append(prompt)
        return SimpleNamespace(content='{"changes": []}')

    _save(DATE_FACT)
    _run("what a nice day", invoke)                                              # no move or cancellation
    _run("my therapy appointment got moved to sunday", invoke)                    # sensitive
    _run("the meeting about the roof got moved to monday", invoke, _msgs("hello"))   # nothing dated about it
    assert calls == []


def test_a_model_failure_changes_nothing_and_does_not_raise():
    _save(DATE_FACT)

    async def broken(prompt):
        raise RuntimeError("model down")

    assert _run(MESSAGE, broken)["updated"] == 0
    assert memory_utils.load_user_facts("jack")[0].fact == DATE_FACT


def test_malformed_model_output_is_ignored():
    assert pc.parse("not json") == [] and pc.parse('{"changes": "x"}') == []
    assert pc.parse('```json\n{"changes": [{"id": "a", "action": "update", "text": "t"}, {"id": 3}]}\n```') == [{"id": "a", "action": "update", "text": "t"}]


def test_the_prompt_is_filled_and_the_module_logs_no_text():
    fact = _save(DATE_FACT)
    prompt = pc.build_prompt(MESSAGE, _msgs("did yashu call?", MESSAGE), [fact], TODAY)
    assert fact.id in prompt and "TODAY: 2026-10-30 (Friday)" in prompt and MESSAGE in prompt
    assert not any(f"{{{name}}}" in prompt for name in ("today", "weekday", "facts", "recent", "message"))
    source = Path(pc.__file__).read_text(encoding="utf-8")
    log_lines = re.findall(r"logger\.\w+\(([^\n]*)", source)
    assert len(log_lines) == 1 and "exc_info=True" in log_lines[0] and "%s" not in log_lines[0]      # one line, no value in it
