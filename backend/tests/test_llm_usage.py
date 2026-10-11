import asyncio
import json
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from backend.models import models as models_module
from backend.utils import llm_usage as lu

ROOT = pathlib.Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    lu.reset()
    monkeypatch.setattr(lu, "flush_in_background", lambda: None)  # the real one starts a thread; tests flush by hand
    yield
    lu.reset()


def reply(input_tokens=10, output_tokens=5, reasoning=None, cache_read=None, model=None):
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}
    if cache_read is not None:
        usage["input_token_details"] = {"cache_read": cache_read}
    if reasoning is not None:
        usage["output_token_details"] = {"reasoning": reasoning}
    meta = {"model_name": model} if model else {}
    return AIMessage(content="ok", usage_metadata=usage, response_metadata=meta)


# ---- reading usage out of a response ----------------------------------------------------------------------------------------

def test_a_response_reports_input_output_thinking_and_cached_tokens():
    assert lu.usage_of(reply(100, 40, reasoning=25, cache_read=60)) == {"input": 100, "output": 40, "reasoning": 25, "cached": 60}
    assert lu.usage_of(reply(7, 1)) == {"input": 7, "output": 1, "reasoning": 0, "cached": 0}


@pytest.mark.parametrize("message", [None, "text", AIMessage(content="no usage"), SimpleNamespace(usage_metadata="nope"),
                                     SimpleNamespace(usage_metadata={"input_tokens": "x", "output_tokens": -4}),
                                     SimpleNamespace(usage_metadata={"input_token_details": "bad", "output_token_details": None})])
def test_anything_that_reports_nothing_usable_counts_as_zero(message):
    assert lu.usage_of(message) == {"input": 0, "output": 0, "reasoning": 0, "cached": 0}


def test_the_model_that_answered_is_the_one_named_in_the_response_and_the_default_otherwise():
    assert lu.served_by(reply(model="gemini-3.5-flash"), "gemini-3.1-flash-lite") == "gemini-3.5-flash"
    assert lu.served_by(reply(), "gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"
    assert lu.served_by(None, "x") == "x"


def test_a_streams_pieces_add_up():
    total = lu.empty_usage()
    for chunk in (AIMessageChunk(content="a", usage_metadata={"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}),
                  AIMessageChunk(content="b", usage_metadata={"input_tokens": 0, "output_tokens": 3, "total_tokens": 3}),
                  AIMessageChunk(content="c")):
        lu.add_into(total, lu.usage_of(chunk))
    assert (total["input"], total["output"]) == (9, 5)


# ---- who asked ----------------------------------------------------------------------------------------------------------------

def asks_the_model():
    return lu.caller_label()


def test_the_label_is_the_nearest_frame_that_is_not_plumbing():
    assert lu.caller_label().endswith("test_the_label_is_the_nearest_frame_that_is_not_plumbing")
    assert asks_the_model().endswith(".asks_the_model") and "." in asks_the_model()


def test_a_stack_of_nothing_but_plumbing_is_unknown_and_never_raises(monkeypatch):
    monkeypatch.setattr(lu, "_PLUMBING_PREFIXES", ("",))  # everything counts as plumbing
    assert lu.caller_label() == "unknown"


# ---- adding up and saving -----------------------------------------------------------------------------------------------------

class _Col:
    def __init__(self):
        self.docs = []
        self.fail = False

    def update_one(self, flt, update, upsert=False):
        if self.fail:
            raise OSError("db down")
        doc = next((d for d in self.docs if all(d.get(k) == v for k, v in flt.items())), None)
        if doc is None:
            doc = dict(flt)
            self.docs.append(doc)
        for key, by in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + by

    def find(self, flt, projection=None):
        floor = flt["day"]["$gte"]
        return [dict(d) for d in self.docs if d["day"] >= floor]


class _DB:
    def __init__(self):
        self.col = _Col()

    def __getitem__(self, name):
        assert name == lu.COLLECTION
        return self.col


def test_calls_are_counted_per_day_model_and_caller():
    lu.record("m1", "node.a", {"input": 100, "output": 10, "reasoning": 4, "cached": 60}, day="2026-10-10")
    lu.record("m1", "node.a", {"input": 50, "output": 5, "reasoning": 0, "cached": 0}, day="2026-10-10")
    lu.record("m1", "node.b", {"input": 1, "output": 1, "reasoning": 0, "cached": 0}, day="2026-10-10")
    lu.record("m2", "node.a", {"input": 7, "output": 7, "reasoning": 0, "cached": 0}, day="2026-10-11")
    snap = lu.snapshot()
    assert snap[("2026-10-10", "m1", "node.a")] == {"calls": 2, "input": 150, "output": 15, "reasoning": 4, "cached": 60}
    assert len(snap) == 3


def test_flush_writes_one_increment_per_row_and_a_second_flush_adds_to_it():
    db = _DB()
    lu.record("m1", "node.a", {"input": 10, "output": 2, "reasoning": 0, "cached": 0}, day="2026-10-10")
    assert lu.flush(db) == 1 and lu.snapshot() == {}
    lu.record("m1", "node.a", {"input": 5, "output": 1, "reasoning": 0, "cached": 0}, day="2026-10-10")
    assert lu.flush(db) == 1
    assert db.col.docs == [{"day": "2026-10-10", "model": "m1", "label": "node.a", "calls": 2, "input": 15, "output": 3, "reasoning": 0, "cached": 0}]
    assert lu.flush(db) == 0  # nothing new, nothing written


def test_a_failed_write_keeps_the_counts_for_the_next_flush():
    db = _DB()
    db.col.fail = True
    lu.record("m1", "node.a", {"input": 10, "output": 2, "reasoning": 0, "cached": 0}, day="2026-10-10")
    assert lu.flush(db) == 0
    assert lu.snapshot()[("2026-10-10", "m1", "node.a")]["input"] == 10
    db.col.fail = False
    assert lu.flush(db) == 1 and db.col.docs[0]["input"] == 10


def test_with_no_database_nothing_is_lost_or_raised(monkeypatch):
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: None)
    lu.record("m1", "node.a", {"input": 3, "output": 1, "reasoning": 0, "cached": 0}, day="2026-10-10")
    assert lu.flush() == 0 and lu.snapshot()[("2026-10-10", "m1", "node.a")]["input"] == 3


def test_enough_calls_or_enough_time_starts_a_background_flush(monkeypatch):
    started = []
    monkeypatch.setattr(lu, "flush_in_background", lambda: started.append(1))
    monkeypatch.setattr(lu, "FLUSH_AFTER_CALLS", 3)
    for _ in range(2):
        lu.record("m", "x", {"input": 1, "output": 1, "reasoning": 0, "cached": 0})
    assert started == []
    lu.record("m", "x", {"input": 1, "output": 1, "reasoning": 0, "cached": 0})
    assert started == [1]
    lu.reset()
    monkeypatch.setattr(lu, "FLUSH_AFTER_CALLS", 1000)
    monkeypatch.setattr(lu, "_last_flush", lu.time.monotonic() - lu.FLUSH_AFTER_SECONDS - 1)
    lu.record("m", "x", {"input": 1, "output": 1, "reasoning": 0, "cached": 0})
    assert started == [1, 1]


def test_recording_garbage_never_raises():
    lu.record(None, None, None)
    lu.record(object(), 5, {"input": "x"})
    assert lu.fetch_usage_docs(SimpleNamespace(), 7) == []


def test_the_background_flush_runs_once_at_a_time(monkeypatch):
    monkeypatch.undo()  # the real function, not the no-op the fixture installed
    calls = []
    monkeypatch.setattr(lu, "flush", lambda *a: calls.append(1) or 0)
    lu.flush_in_background()
    for thread in __import__("threading").enumerate():
        if thread.name == "llm-usage-flush":
            thread.join(2)
    assert calls == [1] and lu._flushing is False


# ---- reading it back ----------------------------------------------------------------------------------------------------------

def test_the_window_covers_today_and_the_days_before_it():
    db = _DB()
    for day in ("2026-10-03", "2026-10-04", "2026-10-10"):
        db.col.docs.append({"day": day, "model": "m", "label": "x", "calls": 1, "input": 1, "output": 1, "reasoning": 0, "cached": 0})
    now = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
    assert [d["day"] for d in lu.fetch_usage_docs(db, 7, now)] == ["2026-10-04", "2026-10-10"]
    assert [d["day"] for d in lu.fetch_usage_docs(db, 1, now)] == ["2026-10-10"]


DOCS = [
    {"day": "d1", "model": "big", "label": "agent_workflow.tool_agent_node", "calls": 6, "input": 60_000, "output": 6_000, "reasoning": 2_000, "cached": 30_000},
    {"day": "d2", "model": "big", "label": "agent_workflow.tool_agent_node", "calls": 4, "input": 40_000, "output": 4_000, "reasoning": 1_000, "cached": 10_000},
    {"day": "d1", "model": "small", "label": "agent_workflow.reasoner_node", "calls": 10, "input": 30_000, "output": 1_000, "reasoning": 0, "cached": 0},
]


def test_the_summary_totals_ranks_and_gives_shares_and_rates():
    summary = lu.summarize_usage(DOCS)
    assert summary["total"]["calls"] == 20 and summary["total"]["tokens"] == 141_000
    assert [r["model"] for r in summary["by_model"]] == ["big", "small"]
    top = summary["by_label"][0]
    assert top["label"] == "agent_workflow.tool_agent_node" and top["calls"] == 10 and top["tokens"] == 110_000
    assert top["share"] == round(110_000 / 141_000, 4) and top["cache_rate"] == 0.4 and top["thinking_rate"] == 0.3
    assert summary["priced"] is False and summary["estimated_cost"] is None and summary["unpriced_models"] == []


def test_prices_turn_it_into_an_estimate_and_models_without_one_are_listed_not_guessed():
    prices = lu.load_prices(json.dumps({"big": {"input": 1.0, "output": 10.0, "cached_input": 0.1}}))
    summary = lu.summarize_usage(DOCS, prices)
    expected = ((100_000 - 40_000) * 1.0 + 40_000 * 0.1 + 10_000 * 10.0) / 1_000_000
    assert summary["estimated_cost"] == round(expected, 4)
    assert summary["unpriced_models"] == ["small"] and summary["priced"] is True
    big = next(r for r in summary["by_model"] if r["model"] == "big")
    small = next(r for r in summary["by_model"] if r["model"] == "small")
    assert big["cost"] == pytest.approx(expected) and small["cost"] is None


@pytest.mark.parametrize("raw,expected", [
    ("", {}), ("not json", {}), ("[1,2]", {}), ('{"m": "x"}', {}), ('{"m": {"input": 1}}', {}), ('{"m": {"input": "a", "output": 2}}', {}),
    ('{"m": {"input": 1, "output": 2}}', {"m": {"input": 1.0, "output": 2.0, "cached_input": 1.0}}),
])
def test_a_malformed_price_table_is_ignored_and_a_missing_cached_price_means_full_price(raw, expected):
    assert lu.load_prices(raw) == expected


def test_an_empty_summary_has_no_rates_to_divide():
    summary = lu.summarize_usage([])
    assert summary["total"]["tokens"] == 0 and summary["total"]["share"] is None and summary["total"]["cache_rate"] is None
    assert summary["by_model"] == [] and summary["by_label"] == []


# ---- LazyLLM reports every kind of call ---------------------------------------------------------------------------------------

class FakeClient:
    def __init__(self, response=None, chunks=(), error=None):
        self.response, self.chunks, self.error = response, list(chunks), error

    def invoke(self, *a, **k):
        if self.error:
            raise self.error
        return self.response

    async def ainvoke(self, *a, **k):
        if self.error:
            raise self.error
        return self.response

    async def astream(self, *a, **k):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error


def llm_with(client):
    llm = models_module.LazyLLM(model_name="primary-model")
    llm._real_llm = client
    return llm


def only_row():
    snap = lu.snapshot()
    assert len(snap) == 1
    (day, model, label), counts = next(iter(snap.items()))
    return model, label, counts


def invoke_from_here(llm):
    return llm.invoke("hello")


async def ainvoke_from_here(llm):
    return await llm.ainvoke("hello")


async def stream_from_here(llm, stop_after=None):
    seen = 0
    async for _ in llm.astream("hello"):
        seen += 1
        if stop_after and seen >= stop_after:
            break
    return seen


def test_invoke_and_ainvoke_are_counted_under_the_calling_function():
    llm = llm_with(FakeClient(reply(100, 20, reasoning=5, cache_read=50)))
    invoke_from_here(llm)
    model, label, counts = only_row()
    assert model == "primary-model" and label.endswith(".invoke_from_here")
    assert counts == {"calls": 1, "input": 100, "output": 20, "reasoning": 5, "cached": 50}
    run(ainvoke_from_here(llm))
    assert lu.snapshot()[next(k for k in lu.snapshot() if k[2].endswith(".ainvoke_from_here"))]["input"] == 100


def test_a_call_answered_by_the_fallback_is_counted_against_the_fallback():
    llm = llm_with(FakeClient(reply(10, 2, model="fallback-model")))
    invoke_from_here(llm)
    assert only_row()[0] == "fallback-model"


def test_a_stream_is_summed_and_counted_once():
    chunks = [AIMessageChunk(content="a", usage_metadata={"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}),
              AIMessageChunk(content="b", usage_metadata={"input_tokens": 0, "output_tokens": 3, "total_tokens": 3}),
              AIMessageChunk(content="c")]
    llm = llm_with(FakeClient(chunks=chunks))
    assert run(stream_from_here(llm)) == 3
    model, label, counts = only_row()
    assert label.endswith(".stream_from_here") and (counts["calls"], counts["input"], counts["output"]) == (1, 9, 5)


def test_a_stream_that_is_stopped_early_is_still_counted_for_what_it_reported():
    chunks = [AIMessageChunk(content="a", usage_metadata={"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}),
              AIMessageChunk(content="b", usage_metadata={"input_tokens": 0, "output_tokens": 3, "total_tokens": 3})]
    llm = llm_with(FakeClient(chunks=chunks))
    assert run(stream_from_here(llm, stop_after=1)) == 1
    assert only_row()[2]["input"] == 9


def test_a_call_that_reports_no_usage_still_counts_as_a_call_and_a_failed_one_is_not_counted_at_all():
    invoke_from_here(llm_with(FakeClient(AIMessage(content="no usage"))))
    assert only_row()[2] == {"calls": 1, "input": 0, "output": 0, "reasoning": 0, "cached": 0}  # visible as calls with no tokens
    lu.reset()
    busy = invoke_from_here(llm_with(FakeClient(error=TimeoutError("slow"))))
    assert "high traffic" in busy.content and lu.snapshot() == {}
    with pytest.raises(ValueError):
        invoke_from_here(llm_with(FakeClient(error=ValueError("a real bug"))))
    assert lu.snapshot() == {}


def test_a_metering_failure_never_costs_the_reply(monkeypatch):
    monkeypatch.setattr(lu, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("meter broke")))
    response = reply(10, 2)
    assert invoke_from_here(llm_with(FakeClient(response))) is response
    assert run(ainvoke_from_here(llm_with(FakeClient(response)))) is response


def test_dev_mode_calls_are_not_counted():
    llm = models_module.LazyLLM()
    llm.dev_mode = True
    assert "DEV_MODE" in llm.invoke("x").content and lu.snapshot() == {}


# ---- wiring ---------------------------------------------------------------------------------------------------------------------

def test_the_chat_turn_and_shutdown_flush_the_counts_and_the_health_page_reports_them():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert app_source.count("await asyncio.to_thread(llm_usage.flush)") >= 3  # both ends of a turn, and shutdown
    health = (ROOT / "backend" / "utils" / "sonic_health.py").read_text(encoding="utf-8")
    assert '"usage"' in health and "**summarize_usage(fetch_usage_docs(db, days, now), load_prices())" in health


def test_only_counts_a_model_name_and_a_caller_are_ever_recorded():
    import inspect

    source = inspect.getsource(lu.record) + inspect.getsource(models_module.LazyLLM._metered)
    assert "prompt" not in source and "content" not in source


def test_code_run_as_a_script_is_labelled_by_what_was_run_and_not_as_main(monkeypatch):
    import sys
    import types

    fake_main = types.SimpleNamespace(__spec__=types.SimpleNamespace(name="backend.evals.behavior_eval"))
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    frame = types.SimpleNamespace(f_globals={"__file__": "/x/y/my_script.py"})
    assert lu._script_name(frame) == "backend.evals.behavior_eval"
    monkeypatch.setitem(sys.modules, "__main__", types.SimpleNamespace(__spec__=None))
    assert lu._script_name(frame) == "my_script"
    assert lu._script_name(types.SimpleNamespace(f_globals={})) == "script"


# ---- which model answered versus which was asked for -------------------------------------------------------------------------------

def test_only_calls_answered_by_a_different_model_are_noted_and_they_are_told_apart():
    lu.note_route("m1", "m1", "fb")                        # answered by the model asked for: nothing to say
    lu.note_route("old-name", "new-name", "fb", day="2026-10-10")
    lu.note_route("old-name", "new-name", "fb", day="2026-10-10")
    lu.note_route("primary", "fb", "fb", day="2026-10-10")
    lu.note_route("", "x", None)
    lu.note_route(None, None, None)
    assert lu._routes == {("2026-10-10", "old-name", "new-name", "rerouted"): 2, ("2026-10-10", "primary", "fb", "fallback"): 1}


def test_routes_are_saved_with_the_usage_and_come_back_summarised_most_first(monkeypatch):
    db = _DB()
    db.cols = {}

    class Routes(_Col):
        def update_one(self, flt, update, upsert=False):
            doc = next((d for d in self.docs if all(d.get(k) == v for k, v in flt.items())), None)
            if doc is None:
                doc = dict(flt)
                self.docs.append(doc)
            for key, by in update.get("$inc", {}).items():
                doc[key] = doc.get(key, 0) + by

    usage_col, routes_col = _Col(), Routes()

    class Both:
        def __getitem__(self, name):
            return {lu.COLLECTION: usage_col, lu.ROUTES_COLLECTION: routes_col}[name]

    lu.note_route("old-name", "new-name", "fb", day="2026-10-10")
    lu.note_route("old-name", "new-name", "fb", day="2026-10-10")
    lu.note_route("primary", "fb", "fb", day="2026-10-10")
    assert lu.flush(Both()) == 0 and lu._routes == {}                       # routes only: no usage rows were written
    assert sorted(d["calls"] for d in routes_col.docs) == [1, 2]
    summary = lu.summarize_routes(routes_col.docs + [{"requested": "old-name", "served": "new-name", "kind": "rerouted", "calls": 3}])
    assert summary[0] == {"requested": "old-name", "served": "new-name", "kind": "rerouted", "calls": 5}
    assert summary[1] == {"requested": "primary", "served": "fb", "kind": "fallback", "calls": 1}
    assert lu.summarize_routes([]) == []


def test_a_failed_save_keeps_the_route_notes_too():
    class Down:
        def __getitem__(self, name):
            raise OSError("db down")

    lu.note_route("a", "b", None, day="2026-10-10")
    assert lu.flush(Down()) == 0
    assert lu._routes == {("2026-10-10", "a", "b", "rerouted"): 1}


def test_lazy_llm_notes_a_rerouted_answer_and_a_fallback_and_stays_quiet_when_the_asked_for_model_answered():
    llm = llm_with(FakeClient(reply(10, 2, model="newer-model")))
    invoke_from_here(llm)
    assert list(lu._routes) == [(lu._today(), "primary-model", "newer-model", "rerouted")]
    lu.reset()
    fallback_llm = models_module.LazyLLM(model_name="primary-model", fallback_model="backup-model")
    fallback_llm._real_llm = FakeClient(reply(10, 2, model="backup-model"))
    invoke_from_here(fallback_llm)
    assert list(lu._routes) == [(lu._today(), "primary-model", "backup-model", "fallback")]
    lu.reset()
    invoke_from_here(llm_with(FakeClient(reply(10, 2, model="primary-model"))))
    assert lu._routes == {}


def test_the_code_names_the_model_google_actually_serves_and_the_health_page_reports_routes():
    source = (ROOT / "backend" / "models" / "models.py").read_text(encoding="utf-8")
    assert source.count('"gemini-3.6-flash"') >= 4 and 'model_name="gemini-3.5-flash"' not in source
    health = (ROOT / "backend" / "utils" / "sonic_health.py").read_text(encoding="utf-8")
    assert '"routes": summarize_routes(fetch_route_docs(db, days, now))' in health
