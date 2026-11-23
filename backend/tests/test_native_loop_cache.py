"""The prompt cache in the tool loop: one tool list for every call of a turn (native_loop STABLE_CONTINUE_TOOL), and the system prompt marked for the cache so a later turn reads it (ClaudeLLM).

Measured 2026-11-22 before this: Sonnet's checkpoint calls read 0% from the cache (they offered [continue_working] instead of the turn's list, and the tool list is the first thing in the cached
prefix), and each turn's first call paid the ~10K-token tool list + instructions in full. These tests pin the request shapes that fix both; no model is called.
"""
import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.models import claude_llm
from backend.services import native_loop as nl
from backend.utils import act_not_suggest, self_counters


def run(coro):
    return asyncio.run(coro)


def call(name, args=None, id=None):
    return {"name": name, "args": args or {}, "id": id or f"id_{name}", "type": "tool_call"}


def asks(*calls, text=""):
    return AIMessage(content=text, tool_calls=list(calls))


def answers(text):
    return AIMessage(content=text)


def search(n):
    return asks(call("search_code", {"query": f"q{n}"}, id=f"s{n}"))


TOOLS = [{"name": "search_code", "description": "find things"}, {"name": "read_repo_file", "description": "read a file"}]
TURN_LIST = ("search_code", "read_repo_file", "save_draft", "continue_working")      # the repo tools bring save_draft; a caching run adds continue_working
EDIT_TOOLS = TOOLS + [{"name": "propose_local_edits", "description": "make a change"}]
REQUIRED = {
    "tool_action": "propose_local_edits", "nudge_after_step": 3, "max_final_rejections": 2, "nudge": "NUDGE: propose it now.",
    "final_notice": "FINAL NOTICE: you can make it, call propose_local_edits.", "steps_notice": "Your next tool step must be propose_local_edits.",
}


class Scripted:
    """A tool-calling model that answers from a script, one reply per call, and remembers what it was sent (messages and tool names)."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), [t["name"] for t in tools]))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class Acts:
    def __init__(self, results=None):
        self.results, self.decisions = results or {}, []

    async def __call__(self, decision):
        self.decisions.append(decision)
        return self.results.get(decision["tool_action"], f"result of {decision['tool_action']}")


def go(llm, act=None, cached=True, **kwargs):
    """`cached`: the caller says the model caches the conversation (agent_workflow passes shorten_old=False for a ClaudeLLM whose caches_prompt is on)."""
    return run(nl.run(llm=llm, tools=kwargs.pop("tools", TOOLS), act=act or Acts(), question="where is X?", shorten_old=not cached, **kwargs))


def tool_lists(llm):
    return {tuple(names) for _, names in llm.calls}


def counted(monkeypatch):
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    return recorded


# ---- one tool list for the turn -------------------------------------------------------------------------------------------------------------------

def test_a_model_that_caches_is_offered_the_same_tool_list_on_every_call_including_the_checkpoint():
    llm = Scripted(*[search(i) for i in range(4)], answers("Answer at the checkpoint."))
    out = go(llm, max_steps=12)
    assert out["final_answer"] == "Answer at the checkpoint."
    assert any("Decide now" in str(m.content) for m in llm.calls[-1][0])                  # the last call WAS the checkpoint
    assert tool_lists(llm) == {TURN_LIST}                                                     # and it offered the turn's list, so it reads the cache


def test_without_caching_the_checkpoint_still_offers_only_continue_working():
    llm = Scripted(*[search(i) for i in range(4)], answers("Answer at the checkpoint."))
    go(llm, cached=False, max_steps=12)
    assert tool_lists(llm) == {("continue_working",), ("search_code", "read_repo_file", "save_draft")}


def test_a_real_tool_call_at_the_checkpoint_is_that_step_and_the_conversation_only_ever_grows():
    llm = Scripted(*[search(i) for i in range(4)], search(4), answers("Final, checked answer."))
    act = Acts()
    out = go(llm, act, max_steps=12)
    assert out["final_answer"] == "Final, checked answer."
    assert [d["args"]["query"] for d in act.decisions] == ["q0", "q1", "q2", "q3", "q4"]        # the call made at the checkpoint ran as step five
    assert len(llm.calls) == 6                                                                  # four steps, the checkpoint (which was step five), the answer: no call repeated
    for (before, _), (after, _) in zip(llm.calls, llm.calls[1:]):
        assert len(before) <= len(after) and all(a is b for a, b in zip(before, after))       # append-only: each call's conversation starts with the previous one, so it is read from the cache


def test_a_reply_made_at_the_checkpoint_follows_the_question_it_answered_and_a_due_reading_notice_waits_for_the_next_step():
    def read(n):
        return asks(call("read_repo_file", {"path": f"f{n}.py"}, id=f"r{n}"))

    proposal = asks(call("propose_local_edits", {"summary": "s", "edits": []}, id="p1"))
    llm = Scripted(proposal, read(2), read(3), read(4), read(5), read(6), answers("Done."))       # an applied edit, three reads (a streak is due), the checkpoint's own read, one more
    go(llm, Acts({"propose_local_edits": "Proposed 1 edit(s) across 1 file(s): a.py. APPLIED: the person applied the edits."}), tools=EDIT_TOOLS, required_action=REQUIRED, max_steps=12)
    later = llm.calls[-1][0]
    asked = next(i for i, m in enumerate(later) if isinstance(m, HumanMessage) and "Decide now" in str(m.content))
    assert isinstance(later[asked + 1], AIMessage) and later[asked + 1].tool_calls[0]["id"] == "r5"         # the reply sits right after the question it was made for
    assert any("READING STREAK" in str(m.content) for m in later[asked + 1:])                              # the due notice is shown on the step after


def test_continue_working_at_the_checkpoint_still_keeps_going_the_old_way():
    best = "Draft answer so far: the retry lives in client.py, in send(), and it uses a fixed delay."
    llm = Scripted(*[search(i) for i in range(4)], asks(call("continue_working", {"reason": "check the second file"}), text=best), search(5), answers("Final."))
    out = go(llm, max_steps=12)
    assert out["final_answer"] == "Final."
    text = " ".join(str(m.content) for m in llm.calls[5][0])
    assert "CHECKPOINT: you chose to keep working on: check the second file" in text and "client.py" in text
    assert len(tool_lists(llm)) == 1


def test_continue_working_outside_a_checkpoint_is_answered_without_running_anything():
    llm = Scripted(asks(call("continue_working", {"reason": "more"}, id="c1")), answers("ok"))
    act = Acts()
    out = go(llm, act, max_steps=6)
    assert act.decisions == [] and out["final_answer"] == "ok"
    results = [m for m in llm.calls[-1][0] if isinstance(m, ToolMessage)]
    assert [m.content for m in results] == [nl.CONTINUE_OUTSIDE_CHECKPOINT]
    assert not any("continue_working" in a["action_desc"] for a in out["attempts"])             # not a step in the receipt


def test_the_turn_list_adds_continue_working_once_and_the_meter_counts_it():
    llm = Scripted(answers("done"))
    go(llm, max_steps=6)
    assert llm.calls[0][1] == list(TURN_LIST)
    assert nl.STABLE_CONTINUE_TOOL["parameters"] == nl.CONTINUE_TOOL["parameters"] and "CHECKPOINT" in nl.STABLE_CONTINUE_TOOL["description"]


# ---- forced endings ----------------------------------------------------------------------------------------------------------------------------

class ReadsUntilTold:
    """Searches on every call that offers tools, and answers in words only when offered none: the shape that makes a forced ending fall back."""

    def __init__(self):
        self.calls = []

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), [t["name"] for t in tools]))
        return search(len(self.calls)) if tools else answers("Here is what I found.")


def test_a_forced_ending_that_makes_a_call_anyway_is_asked_again_with_no_tools_and_counted(monkeypatch):
    recorded = counted(monkeypatch)
    llm = ReadsUntilTold()
    out = go(llm, max_steps=3)
    assert out["final_answer"] == "Here is what I found."
    assert llm.calls[-1][1] == [] and llm.calls[-2][1] == list(TURN_LIST)     # first the whole list, then the old narrow ask
    assert not any(m is llm.calls[-2] for m in llm.calls[-1][0])
    assert not any(isinstance(m, AIMessage) and m.tool_calls and m.tool_calls[0]["args"]["query"] == f"q{len(llm.calls) - 1}" for m in llm.calls[-1][0])   # the stray reply was dropped
    assert recorded[-1]["native_stable_tools"] == 1 and recorded[-1]["native_narrow_fallback"] == 1


class AnswersWhenTold:
    """Searches until the step-limit notice, then answers in words even though tools are offered."""

    def __init__(self):
        self.calls = []

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), [t["name"] for t in tools]))
        if "step limit has been reached" in str(messages[-1].content):
            return answers("Answer from what I read.")
        return search(len(self.calls))


def test_a_forced_ending_that_answers_reads_the_cache_and_needs_no_second_call(monkeypatch):
    recorded = counted(monkeypatch)
    llm = AnswersWhenTold()
    out = go(llm, max_steps=3)
    assert out["final_answer"] == "Answer from what I read."
    assert len(tool_lists(llm)) == 1 and len(llm.calls) == 4                     # three steps and the ending, all on the one list
    assert recorded[-1]["native_stable_tools"] == 1 and recorded[-1].get("native_narrow_fallback", 0) == 0


class ProposesWhenTold:
    """Reads files until told to act now, then proposes; says one line when asked what happened."""

    def __init__(self):
        self.calls = []

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), [t["name"] for t in tools]))
        last = str(messages[-1].content)
        if last == act_not_suggest.ACT_NOW_NOTICE:
            return asks(call("propose_local_edits", {"summary": "s", "edits": []}, id="p1"))
        if last.startswith("Say in one or two sentences"):
            return answers("Proposed the change.")
        return asks(call("read_repo_file", {"path": f"f{len(self.calls)}.py"}, id=f"r{len(self.calls)}"))


def test_the_forced_edit_ending_offers_the_turn_list_and_still_makes_the_proposal(monkeypatch):
    counted(monkeypatch)
    llm = ProposesWhenTold()
    act = Acts({"propose_local_edits": "Proposed 1 edit(s) across 1 file(s)"})
    out = go(llm, act, tools=EDIT_TOOLS, required_action=REQUIRED, max_steps=3)
    assert "propose_local_edits" in [d["tool_action"] for d in act.decisions] and out["final_answer"] == "Proposed the change."
    assert tool_lists(llm) == {("search_code", "read_repo_file", "propose_local_edits", "continue_working")}


def test_without_caching_the_forced_endings_keep_their_narrow_lists():
    llm = ProposesWhenTold()
    go(llm, Acts({"propose_local_edits": "Proposed 1 edit(s) across 1 file(s)"}), cached=False, tools=EDIT_TOOLS, required_action=REQUIRED, max_steps=3)
    assert ("propose_local_edits",) in tool_lists(llm) and () in tool_lists(llm)


def test_the_cache_counters_are_known_keys_and_only_counted_for_a_caching_run():
    assert {"native_stable_tools", "native_narrow_fallback"} <= self_counters.KEYS
    assert nl.cache_counts(True, 2) == {"native_stable_tools": 1, "native_narrow_fallback": 2}
    assert nl.cache_counts(False, 2) == {}


# ---- the system prompt is marked for the cache ---------------------------------------------------------------------------------------------------

class FakeChat:
    def __init__(self, error=None):
        self.error, self.seen = error, []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, *args, **kwargs):
        self.seen.append(messages)
        if self.error:
            raise self.error
        return AIMessage(content="done", response_metadata={"model_name": "claude-haiku-5-5"})


class FakeFallback:
    def __init__(self):
        self.seen = []

    async def ainvoke_tools(self, messages, tools):
        self.seen.append(messages)
        return AIMessage(content="from the fallback")


def claude(chat, cache=True, fallback=None, monkeypatch=None):
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr("backend.utils.llm_usage.record", lambda *a, **k: None)
    llm = claude_llm.ClaudeLLM(cache=cache, fallback=fallback)
    llm._chat = chat
    return llm


def test_a_tool_call_sends_the_system_prompt_as_one_block_marked_for_an_hour_and_leaves_the_callers_list_alone(monkeypatch):
    monkeypatch.setattr(claude_llm, "PREFIX_TTL", "1h")
    chat = FakeChat()
    messages = [SystemMessage(content="the rules"), HumanMessage(content="the question")]
    run(claude(chat, monkeypatch=monkeypatch).ainvoke_tools(messages, TOOLS))
    sent = chat.seen[0]
    assert sent[0].content == [{"type": "text", "text": "the rules", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    assert sent[1] is messages[1]
    assert messages[0].content == "the rules"


def test_the_prefix_mark_can_be_the_default_five_minutes(monkeypatch):
    monkeypatch.setattr(claude_llm, "PREFIX_TTL", "")
    chat = FakeChat()
    run(claude(chat, monkeypatch=monkeypatch).ainvoke_tools([SystemMessage(content="r"), HumanMessage(content="q")], TOOLS))
    assert chat.seen[0][0].content[0]["cache_control"] == {"type": "ephemeral"}


def test_nothing_is_marked_with_caching_off_or_without_a_plain_system_prompt(monkeypatch):
    chat = FakeChat()
    run(claude(chat, cache=False, monkeypatch=monkeypatch).ainvoke_tools([SystemMessage(content="r"), HumanMessage(content="q")], TOOLS))
    assert chat.seen[0][0].content == "r"
    chat = FakeChat()
    run(claude(chat, monkeypatch=monkeypatch).ainvoke_tools([HumanMessage(content="q")], TOOLS))
    assert chat.seen[0][0].content == "q"


def test_the_fallback_model_gets_the_conversation_unmarked(monkeypatch):
    fallback = FakeFallback()
    messages = [SystemMessage(content="the rules"), HumanMessage(content="q")]
    out = run(claude(FakeChat(error=RuntimeError("down")), fallback=fallback, monkeypatch=monkeypatch).ainvoke_tools(messages, TOOLS))
    assert out.content == "from the fallback" and fallback.seen[0] is messages


def test_the_prefix_ttl_reads_its_setting():
    assert claude_llm.prefix_ttl_from(None) == "1h" and claude_llm.prefix_ttl_from(" 1H ") == "1h"
    assert claude_llm.prefix_ttl_from("5m") == "" and claude_llm.prefix_ttl_from("") == ""


def test_the_reply_and_reasoner_marks_use_the_hour_long_lifetime(monkeypatch):
    monkeypatch.setattr(claude_llm, "PREFIX_TTL", "1h")
    assert claude_llm.prefix_mark() == {"type": "ephemeral", "ttl": "1h"}
    reasoner = claude_llm.ClaudeLLM(stable_prefix="the long stable instructions " * 5, cache=True)
    blocks = reasoner._to_messages("the long stable instructions " * 5 + "this turn's message")[0].content
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} and "cache_control" not in blocks[1]
    monkeypatch.setattr(claude_llm, "PREFIX_TTL", "")
    assert claude_llm.prefix_mark() == {"type": "ephemeral"}
