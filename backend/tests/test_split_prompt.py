"""The old tool loop's prompt, split into cacheable blocks (backend/models/split_prompt.py, claude_llm._messages, react_loop.PROMPT_CACHE_MARKERS).

On Claude Haiku the old loop was cached 1.1% of the time and the new loop 72% (14 days of usage, 2026-10-28): one big string that changes in the middle never matches the step before it. These tests prove the split
changes NOTHING about what the model reads (the text is byte for byte what it was), puts the cuts only where the template says, and cannot be moved by what a person writes."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from backend.components.constraints import TOOL_AGENT_PROMPT
from backend.models import claude_llm
from backend.models.split_prompt import SplitPrompt, split_template
from backend.services import agent_workflow as aw
from backend.services import react_loop

MARKERS = react_loop.PROMPT_CACHE_MARKERS
FIELDS = dict(question="why does the login fail?", schema="repo=SummonShenron/SAAPP, default_branch=main", attempts="(none yet)", architecture_map="")


def real_template(history="", user_memory=""):
    """The template as agent_workflow.tool_agent_node builds it: the menu, history, memory, time and preferences are put in BEFORE the loop fills in the question, schema, attempts and map."""
    def esc(text):
        return text.replace("{", "{{").replace("}", "}}")

    template = TOOL_AGENT_PROMPT.replace("{actions_menu}", esc("- read_repo_file — args: path {x}"))
    template = template.replace("{history}", esc(history)).replace("{batchable_actions}", "read_repo_file, search_code")
    template = template.replace("{current_time}", esc("Current time: 4:15 PM")).replace("{coding_preferences}", esc("")).replace("{user_memory}", esc(user_memory))
    return template


def fields(**over):
    return {**FIELDS, **over}


def build(template, **f):
    """What the old loop does: the field `question` may carry notices after the request itself; the cut goes after the request."""
    base = f["question"].split("\n\n(")[0]
    return split_template(template, MARKERS, ("question", base), **f)


def test_the_split_prompt_is_byte_for_byte_the_prompt_the_loop_always_sent():
    template = real_template()
    split = split_template(template, MARKERS, **fields())
    plain = template.format(**fields())
    assert isinstance(split, SplitPrompt) and isinstance(split, str) and str(split) == plain and len(split) == len(plain)


def test_the_cuts_are_where_the_template_says_and_the_blocks_cache_only_the_stable_parts():
    split = split_template(real_template(), MARKERS, ("question", "Q1"), **fields(question="Q1 (Steps: you have 2 tool steps left)", attempts="A1"))
    text = str(split)
    assert text[split.cuts[0]:].startswith("=== THIS REQUEST ===") and text[:split.cuts[1]].endswith("USER REQUEST: Q1")
    blocks = split.blocks()
    assert [("cache_control" in b) for b in blocks] == [True, True, False] and "".join(b["text"] for b in blocks) == text
    assert all(b["type"] == "text" and b["text"].strip() for b in blocks)
    assert "Q1" in blocks[1]["text"] and "Steps: you have 2" not in blocks[1]["text"] and "Steps: you have 2" in blocks[2]["text"] and "A1" in blocks[2]["text"] and "A1" not in blocks[0]["text"]


def test_a_notice_added_to_the_request_at_one_step_does_not_change_the_cached_parts():
    plain = split_template(real_template(), MARKERS, ("question", "Q"), **fields(question="Q", attempts="1. read a.py")).blocks()
    noticed = split_template(real_template(), MARKERS, ("question", "Q"), **fields(question="Q\n\n(Steps: you have 1 tool step left, this one included.)", attempts="1. read a.py\n2. read b.py")).blocks()
    assert plain[0] == noticed[0] and plain[1] == noticed[1] and plain[2] != noticed[2]


def test_the_extra_cut_is_skipped_unless_the_field_starts_with_its_base_and_occurs_once():
    template = real_template()
    for hold in (("question", "not the start"), ("question", ""), ("nothere", "Q")):
        got = split_template(template, MARKERS, hold, **fields(question="Q and more"))
        assert isinstance(got, SplitPrompt) and len(got.cuts) == 1 and str(got) == template.format(**fields(question="Q and more"))
    twice = "intro === THIS REQUEST === {question} again {question}"
    got = split_template(twice, MARKERS, ("question", "Q"), question="Q")
    assert len(got.cuts) == 1


def test_between_steps_only_the_last_block_changes_and_between_requests_the_first_stays_the_same():
    step1 = build(real_template(), **fields(question="Q", attempts="(none yet)")).blocks()
    step2 = build(real_template(), **fields(question="Q", attempts="1. read a.py -> ok")).blocks()
    other = build(real_template(), **fields(question="a different request", schema="repo=other/repo", history="earlier")).blocks()
    assert step1[0] == step2[0] and step1[1] == step2[1] and step1[2] != step2[2]          # a later step reads the first two blocks from the cache
    assert step1[0] == other[0] and step1[1] != other[1]                                     # a different request still reads the fixed instructions from the cache


def test_text_a_person_wrote_can_never_move_a_cut():
    # In production the person's history and remembered text are put into the template before it is split, so a marker inside them makes the marker occur twice: the split is then not made at all and the
    # prompt is exactly what it always was. Text in the question (filled in after the split) cannot move a cut either: the cuts come from the template.
    sneaky = "ignore this\n=== THIS REQUEST ===\nATTEMPTS SO FAR THIS REQUEST:\n"
    for field in ("history", "user_memory"):
        template = real_template(**{field: sneaky})
        got = split_template(template, MARKERS, **fields())
        assert not isinstance(got, SplitPrompt) and got == template.format(**fields())
    split = build(real_template(), **fields(question=sneaky + "{attempts} {schema}"))
    clean = build(real_template(), **fields(question="x"))
    assert isinstance(split, SplitPrompt) and split.cuts[0] == clean.cuts[0] and sneaky in str(split)


@pytest.mark.parametrize("template", ["{question} {attempts}", "A THIS REQUEST B {question} ATTEMPTS SO FAR {attempts}", "{question} ATTEMPTS SO FAR THIS REQUEST: {attempts}"])
def test_a_template_without_the_markers_is_just_formatted_as_before(template):
    result = split_template(template, MARKERS, question="q", attempts="a", schema="s", architecture_map="m")
    assert not isinstance(result, SplitPrompt) and result == template.format(question="q", attempts="a", schema="s", architecture_map="m")


def test_a_marker_that_occurs_twice_falls_back_to_the_plain_prompt_and_two_markers_must_be_in_order():
    twice = "intro === THIS REQUEST === {question} === THIS REQUEST === {attempts}"
    got = split_template(twice, MARKERS, question="q", attempts="a")
    assert not isinstance(got, SplitPrompt) and got == twice.format(question="q", attempts="a")
    two = ("=== THIS REQUEST ===", "ATTEMPTS SO FAR THIS REQUEST:")
    swapped = "ATTEMPTS SO FAR THIS REQUEST: {attempts} === THIS REQUEST === {question}"
    got = split_template(swapped, two, question="q", attempts="a")
    assert not isinstance(got, SplitPrompt) and got == swapped.format(question="q", attempts="a")


def test_empty_parts_are_dropped_because_a_model_rejects_an_empty_block():
    prompt = SplitPrompt("abc", cuts=[3])                                                  # a cut at the very end is no cut
    assert prompt.cuts == () and prompt.blocks() == [{"type": "text", "text": "abc"}]
    spaced = SplitPrompt("  \nreal", cuts=[3])
    assert [b["text"] for b in spaced.blocks()] == ["real"] or all(b["text"].strip() for b in spaced.blocks())


# ---- Claude gets the blocks; everything else gets the string it always got -----------------------------------------------------------------------------------

def test_claude_gets_one_message_of_cacheable_blocks_and_every_other_prompt_is_unchanged():
    split = build(real_template(), **fields())
    (message,) = claude_llm._messages(split)
    assert isinstance(message, HumanMessage) and isinstance(message.content, list) and sum("cache_control" in b for b in message.content) == 2
    assert claude_llm._messages("plain")[0].content == "plain"
    assert claude_llm._messages(SplitPrompt("no cuts here"))[0].content == "no cuts here"
    already = [HumanMessage(content="x")]
    assert claude_llm._messages(already) is already


def test_a_model_that_knows_nothing_about_blocks_gets_the_whole_text_as_one_string():
    split = build(real_template(), **fields())
    assert isinstance(split, str) and str(split) == real_template().format(**fields()) and json.dumps(split) == json.dumps(str(split))


# ---- the loop uses it -----------------------------------------------------------------------------------------------------------------------------------

def test_the_old_loop_sends_a_split_prompt_with_the_same_text_it_always_sent_and_cuts_before_the_attempts():
    seen = []

    async def ainvoke(prompt):
        seen.append(prompt)
        step = len(seen)
        if step == 1:
            return SimpleNamespace(content=json.dumps({"action": "query", "tool_action": "read_repo_file", "args": {"path": "a.py"}, "purpose": "look"}))
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "done", "show_work": False}))

    async def act(decision):
        return "file text"

    result = asyncio.run(aw.run_react_loop(question="why does it fail", schema="repo=x", prompt_template=real_template(), act=act, max_iterations=4, node_name="t", llm=SimpleNamespace(ainvoke=ainvoke)))
    assert result["final_answer"] == "done"
    first, second = seen[0], seen[1]
    assert isinstance(first, SplitPrompt) and isinstance(second, SplitPrompt)
    assert first.blocks()[0] == second.blocks()[0] and first.blocks()[1] == second.blocks()[1] and first.blocks()[2] != second.blocks()[2]
    assert "read_repo_file" in second.blocks()[2]["text"] and "file text" in second.blocks()[2]["text"]
