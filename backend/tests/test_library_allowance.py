"""Extra tool steps for reading a long library (backend/utils/library_allowance.py): the arithmetic, both loops end to end, and the note when files are still unread."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import agent_workflow as aw
from backend.services import library_search
from backend.services import native_loop as nl
from backend.utils import library_allowance as la
from backend.utils import llm_turns

GROUNDING_CHECK_MARKER = "REAL TOOL OBSERVATIONS GATHERED THIS TURN"


def names(n):
    return [f"week-{i:02d}.md" for i in range(1, n + 1)]


def listing(n):
    """A real list_for_agent result for n files."""
    files = [{"source": name, "library": "mine", "passages": 3} for name in names(n)]
    original = library_search.list_files
    library_search.list_files = lambda scope: files
    original_scope = library_search.usable_scope
    library_search.usable_scope = lambda scope: ["mine"]
    try:
        return library_search.list_for_agent(["mine"])
    finally:
        library_search.list_files = original
        library_search.usable_scope = original_scope


@pytest.fixture(autouse=True)
def _clean():
    llm_turns.reset()
    yield
    llm_turns.reset()


# ---- reading the listing ----------------------------------------------------------------------------------------------------------------------

def test_the_file_names_come_back_out_of_the_real_listing_format():
    assert la.listed_files(listing(3)) == names(3)
    assert la.listed_files("ERROR: there are no files in the person's libraries.") == []
    assert la.listed_files("nothing like a listing") == []


def test_a_name_with_brackets_and_spaces_survives():
    text = "2 file(s) in the person's libraries (pass a name exactly as written to read_library_file):\n- Notes (draft) 1.md (library mine, 1 passage)\n- b.md (library mine, 4 passages)"
    assert la.listed_files(text) == ["Notes (draft) 1.md", "b.md"]


# ---- the grant --------------------------------------------------------------------------------------------------------------------------------

def grant(files, reads_left, **kw):
    allowance = la.LibraryAllowance(**kw)
    return allowance.after_listing(la.LIST_TOOL, listing(files), reads_left), allowance


def test_a_library_that_already_fits_gets_nothing():
    assert grant(7, reads_left=2)[0] == 0           # the case that fit exactly: list + 2 read steps + answer, in a budget of 4
    assert grant(10, reads_left=2)[0] == 0
    assert grant(3, reads_left=2)[0] == 0


def test_a_longer_library_gets_exactly_the_shortfall():
    assert grant(11, reads_left=2)[0] == 1          # 3 read steps needed, 2 left
    assert grant(12, reads_left=2)[0] == 1
    assert grant(16, reads_left=2)[0] == 2
    assert grant(12, reads_left=0)[0] == 3


def test_the_grant_is_capped():
    assert grant(100, reads_left=2)[0] == la.MAX_EXTRA_STEPS == 4
    assert grant(100, reads_left=0)[0] == 4


def test_a_second_listing_in_the_same_turn_adds_nothing():
    allowance = la.LibraryAllowance()
    assert allowance.after_listing(la.LIST_TOOL, listing(30), 0) == 4
    assert allowance.after_listing(la.LIST_TOOL, listing(30), 0) == 0


def test_low_power_mode_never_grants_but_still_remembers_the_listing():
    amount, allowance = grant(30, reads_left=0, enabled=False)
    assert amount == 0 and allowance.listed == names(30)


def test_only_a_successful_listing_counts():
    allowance = la.LibraryAllowance()
    assert allowance.after_listing("read_library_file", listing(30), 0) == 0
    assert allowance.after_listing(la.LIST_TOOL, "ERROR: the library could not be read right now.", 0) == 0
    assert allowance.listed == [] and allowance.granted == 0
    assert allowance.after_listing(la.LIST_TOOL, listing(30), 0) == 4          # a failed listing did not use up the turn's one decision


def test_the_batch_size_matches_the_loops_own_limit():
    assert la.READS_PER_STEP == aw._MAX_BATCH_SIZE


def test_the_note_tells_the_model_to_batch():
    allowance = la.LibraryAllowance()
    allowance.after_listing(la.LIST_TOOL, listing(12), 2)
    note = allowance.note(1)
    assert "12 files" in note and "3 read steps" in note and "1 more tool step was added" in note and "up to 5 read_library_file calls together" in note


# ---- what was not read ------------------------------------------------------------------------------------------------------------------------

def read_attempt(name, ok=True):
    return {"action_desc": f"read_library_file(name={name})", "observation": "text" if ok else "ERROR: no file"}


def test_unread_files_are_the_listed_ones_no_successful_read_named():
    allowance = la.LibraryAllowance()
    allowance.after_listing(la.LIST_TOOL, listing(4), 2)
    attempts = [read_attempt("week-01.md"), read_attempt("week-02.md", ok=False), {"action_desc": "search_library(query=week-03.md)", "observation": "x"}]
    assert allowance.unread(attempts) == ["week-02.md", "week-03.md", "week-04.md"]


def test_the_unread_note_names_what_was_missed_and_is_empty_when_all_were_read():
    allowance = la.LibraryAllowance()
    allowance.after_listing(la.LIST_TOOL, listing(3), 2)
    note = allowance.unread_note([read_attempt("week-01.md")])
    assert note.startswith("NOTE (authoritative)") and "week-02.md, week-03.md" in note and "2 of them NOT read" in note and "do not describe the unread ones as covered" in note
    assert allowance.unread_note([read_attempt(n) for n in names(3)]) == ""
    assert la.LibraryAllowance().unread_note([]) == ""


def test_a_long_unread_list_is_shortened():
    allowance = la.LibraryAllowance()
    allowance.after_listing(la.LIST_TOOL, listing(40), 0)
    note = allowance.unread_note([])
    assert "week-10.md" in note and "week-11.md" not in note and "(and 30 more)" in note


# ---- the native loop, end to end --------------------------------------------------------------------------------------------------------------

def call(name, args=None, id=None):
    return {"name": name, "args": args or {}, "id": id or f"id_{name}_{json.dumps(args, sort_keys=True)}", "type": "tool_call"}


class ToolLLM:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), list(tools)))
        if not tools:
            return self.responses[-1]
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


LIB_TOOLS = [{"name": "list_library_files", "description": "list"}, {"name": "read_library_file", "description": "read"}]


def native_script(file_count):
    """The model lists, then reads everything in batches of five, then answers."""
    all_names = names(file_count)
    batches = [all_names[i:i + 5] for i in range(0, len(all_names), 5)]
    return [AIMessage(content="", tool_calls=[call("list_library_files")])] + [
        AIMessage(content="", tool_calls=[call("read_library_file", {"name": n}) for n in batch]) for batch in batches
    ] + [AIMessage(content="Here is the review of every week.")]


class LibraryActs:
    def __init__(self, file_count):
        self.file_count, self.reads = file_count, []

    async def __call__(self, decision):
        if decision["tool_action"] == "list_library_files":
            return listing(self.file_count)
        self.reads.append(decision["args"]["name"])
        return f"text of {decision['args']['name']}"


def run_native(file_count, allowance, max_steps=4):
    llm, act = ToolLLM(*native_script(file_count)), LibraryActs(file_count)
    result = asyncio.run(nl.run(llm=llm, tools=LIB_TOOLS, act=act, question="review every week", max_steps=max_steps, library_allowance=allowance))
    return result, llm, act


def test_native_a_sixteen_file_review_reads_every_file_with_the_allowance():
    allowance = la.LibraryAllowance()
    result, llm, act = run_native(16, allowance)
    assert sorted(act.reads) == names(16) and result["final_answer"] == "Here is the review of every week." and allowance.granted == 1
    assert any(isinstance(m, HumanMessage) and "1 more tool step was added" in str(m.content) for m in llm.calls[-1][0])
    assert allowance.unread(result["attempts"]) == []


def test_native_without_the_allowance_the_same_review_is_cut_off():
    result, llm, act = run_native(16, None)
    assert len(act.reads) == 15                                           # the list and three batches of five, then the forced answer: the last week never read
    allowance = la.LibraryAllowance()
    allowance.after_listing(la.LIST_TOOL, listing(16), 0)
    assert "week-16.md" in allowance.unread_note(result["attempts"])


def test_native_up_to_fifteen_files_use_no_extra_steps():
    for count in (7, 15):
        allowance = la.LibraryAllowance()
        result, llm, act = run_native(count, allowance)
        assert sorted(act.reads) == names(count) and allowance.granted == 0
        assert not any("were added" in str(m.content) or "was added" in str(m.content) for m in llm.calls[-1][0] if isinstance(m, HumanMessage))


def test_native_a_huge_library_is_capped_and_the_unread_files_are_known():
    allowance = la.LibraryAllowance()
    result, llm, act = run_native(60, allowance)
    assert allowance.granted == 4 and len(act.reads) == 35                # the list and seven batches of five
    missing = allowance.unread(result["attempts"])
    assert len(missing) == 25 and "25 of them NOT read" in allowance.unread_note(result["attempts"])


# ---- the react loop, end to end ---------------------------------------------------------------------------------------------------------------

def react_script(file_count):
    all_names = names(file_count)
    batches = [all_names[i:i + 5] for i in range(0, len(all_names), 5)]
    steps = [SimpleNamespace(content=json.dumps({"action": "query", "purpose": "list", "tool_action": "list_library_files", "args": {}}))]
    for batch in batches:
        steps.append(SimpleNamespace(content=json.dumps({"action": "query", "purpose": "read", "queries": [
            {"tool_action": "read_library_file", "args": {"name": n}, "purpose": f"read {n}"} for n in batch]})))
    steps.append(SimpleNamespace(content=json.dumps({"action": "final", "answer": "Here is the review of every week."})))
    return steps


async def _run_react(file_count, allowance, max_iterations=4):
    responses, prompts = react_script(file_count), []
    reads = []

    async def fake(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return SimpleNamespace(content=json.dumps({"grounded": True, "unsupported_claims": []}))
        prompts.append(prompt)
        if "You have used all your steps" in prompt:                          # told the steps are gone, a real model answers
            return SimpleNamespace(content=json.dumps({"action": "final", "answer": "Here is what I could cover."}))
        return responses.pop(0) if len(responses) > 1 else responses[0]

    async def act(decision):
        if decision["tool_action"] == "list_library_files":
            return listing(file_count)
        reads.append(decision["args"]["name"])
        return f"text of {decision['args']['name']}"

    original = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake
    try:
        result = await aw.run_react_loop(
            question="review every week", schema="x", prompt_template="{question} | {schema} | {attempts}", act=act, max_iterations=max_iterations,
            node_name="t", batchable_actions=aw.TOOL_AGENT_BATCHABLE_ACTIONS, library_allowance=allowance,
        )
    finally:
        aw.lite_llm.ainvoke = original
    return result, prompts, reads


def test_react_a_twelve_file_review_reads_every_file_with_the_allowance():
    allowance = la.LibraryAllowance()
    result, prompts, reads = asyncio.run(_run_react(12, allowance))
    assert sorted(reads) == names(12) and allowance.granted == 1 and allowance.unread(result["attempts"]) == []
    assert any("1 more tool step was added" in p for p in prompts)


def test_react_without_the_allowance_the_same_review_is_cut_off():
    result, prompts, reads = asyncio.run(_run_react(12, None))
    assert len(reads) == 10


def test_react_seven_files_use_no_extra_steps():
    allowance = la.LibraryAllowance()
    result, prompts, reads = asyncio.run(_run_react(7, allowance))
    assert sorted(reads) == names(7) and allowance.granted == 0


def test_react_a_huge_library_is_capped_and_the_unread_files_are_known():
    allowance = la.LibraryAllowance()
    result, prompts, reads = asyncio.run(_run_react(60, allowance))
    assert allowance.granted == 4 and len(reads) == 5 * 6                  # the list, then six batches of five, then the answer
    assert len(allowance.unread(result["attempts"])) == 30 and "NOT read" in allowance.unread_note(result["attempts"])


def test_react_a_review_that_fit_leaves_no_unread_note():
    allowance = la.LibraryAllowance()
    result, prompts, reads = asyncio.run(_run_react(12, allowance))
    assert allowance.unread_note(result["attempts"]) == ""


# ---- the wiring ---------------------------------------------------------------------------------------------------------------------------------

def test_the_tool_agent_makes_one_allowance_per_turn_only_for_people_with_a_library_and_adds_the_unread_note_on_a_cap_hit():
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    assert "LibraryAllowance(enabled=not budget_guard.low_power(budget_state)) if library_scope else None" in source
    assert "library_allowance=library_allowance," in source and 'library_allowance=kwargs.get("library_allowance"),' in source
    assert 'getattr(llm_turns.current(), "cap_hit", False)' in source and "library_allowance.unread_note(attempts)" in source
