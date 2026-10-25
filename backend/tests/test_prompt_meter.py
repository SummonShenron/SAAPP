"""The prompt meter: sizes of a tool turn's prompt, and of everything its calls re-send (backend/utils/prompt_meter.py)."""
import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.services import native_loop as nl
from backend.utils import prompt_meter as pm
from backend.utils import self_counters as sc


def test_a_call_counts_everything_it_sends_including_the_tool_list():
    meter = pm.PromptMeter()
    tools = [{"name": "read_repo_file", "description": "x" * 100}]
    messages = [SystemMessage(content="a" * 50), HumanMessage(content="b" * 30)]
    meter.note_call(messages, tools)
    meter.note_call(messages + [AIMessage(content="c" * 20)], tools)
    meter.note_call(messages, [])                      # the forced final answer sends no tools
    assert meter.calls == 3
    listed = len(__import__("json").dumps(tools))
    assert meter.sent == (80 + listed) + (100 + listed) + 80


def test_parts_are_sized_by_name_and_unknown_names_are_ignored():
    meter = pm.PromptMeter()
    meter.part("memory", "m" * 40)
    meter.part("request", ["ab", {"text": "cd"}])
    meter.part("secrets", "never counted")
    meter.tools([{"name": "t"}])
    counts = meter.counts(results_chars=500)
    assert counts["native_chars_memory"] == 40 and counts["native_chars_request"] == 4 and counts["native_chars_tools"] > 0
    assert counts["native_chars_results"] == 500 and counts["native_runs"] == 1 and "native_chars_secrets" not in counts


def test_every_count_key_is_in_the_closed_counter_list_so_none_is_dropped():
    assert set(pm.COUNT_KEYS) <= sc.KEYS


def test_averages_are_per_run_and_empty_when_nothing_was_measured():
    assert pm.averages({}) is None
    totals = {"native_runs": 2, "native_calls": 7, "native_chars_sent": 38000, "native_chars_results": 6000, "native_chars_tools": 10000, "native_chars_request": 200}
    out = pm.averages(totals)
    assert out["runs"] == 2 and out["calls"] == 3.5 and out["sent_chars"] == 19000 and out["sent_tokens"] == 5000 and out["parts"]["tools"] == 5000 and out["parts"]["request"] == 100


def test_the_loop_records_sizes_without_any_text(monkeypatch):
    recorded = []
    monkeypatch.setattr(sc, "record_turn_counts", lambda counts, now=None: recorded.append(dict(counts)))

    class LLM:
        def __init__(self):
            self.n = 0

        async def ainvoke_tools(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return AIMessage(content="", tool_calls=[{"name": "read_repo_file", "args": {"path": "a.py"}, "id": "1", "type": "tool_call"}])
            return AIMessage(content="done")

    async def act(decision):
        return "file text " * 20

    secret = "THE PERSONS OWN WORDS"
    asyncio.run(nl.run(llm=LLM(), tools=[{"name": "read_repo_file", "description": "r"}], act=act, question=secret, user_memory="remembered " * 10, history="earlier " * 5))
    assert len(recorded) == 1
    counts = recorded[0]
    assert counts["native_runs"] == 1 and counts["native_calls"] == 2 and counts["native_chars_results"] == 200
    assert counts["native_chars_request"] == len(secret) and counts["native_chars_memory"] == len("remembered " * 10) and counts["native_chars_history"] == len("earlier " * 5)
    assert counts["native_chars_sent"] > counts["native_chars_instructions"] * 2          # two calls each re-send the instructions
    assert all(isinstance(key, str) and isinstance(value, int) for key, value in counts.items()) and secret not in str(counts)
