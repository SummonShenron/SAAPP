"""The three answer checks ported to the native loop from the old one (backend/utils/answer_checks.py; docs/react-sunset-plan.md stage C)."""
import asyncio

import pytest

from backend.services import agent_workflow as aw
from backend.services import native_loop as nl
from backend.tests.test_native_loop import Acts, ToolLLM, answers, asks, call
from backend.utils import answer_checks as ac
from backend.utils import self_counters as sc

WATCHLIST = aw.TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST
GROUNDED_FILE_PATH = "backend/app.py"
GROUNDED_FILE_CONTENT = "def foo():\n    return 1\n"
GROUNDED_INITIAL_ATTEMPTS = [{
    "purpose": "Read the real file",
    "action_desc": f"read_repo_file(path={GROUNDED_FILE_PATH})",
    "observation": f"URL: https://example/{GROUNDED_FILE_PATH}\n{GROUNDED_FILE_CONTENT}",
}]
# A diff claiming lines (a CONSTANTS dict) that the one real read of the file never showed, and one whose pre-existing lines are really there.
UNGROUNDED_DIFF_ANSWER = (
    "Here's the fix:\n```diff\n"
    f"diff --git a/{GROUNDED_FILE_PATH} b/{GROUNDED_FILE_PATH}\n"
    f"--- a/{GROUNDED_FILE_PATH}\n"
    f"+++ b/{GROUNDED_FILE_PATH}\n"
    "@@ -1,3 +1,3 @@\n"
    " CONSTANTS = {\n"
    '-    "a": 1,\n'
    '+    "a": 2,\n'
    " }\n"
    "```"
)
GROUNDED_DIFF_ANSWER = (
    "Here's the fix:\n```diff\n"
    f"diff --git a/{GROUNDED_FILE_PATH} b/{GROUNDED_FILE_PATH}\n"
    f"--- a/{GROUNDED_FILE_PATH}\n"
    f"+++ b/{GROUNDED_FILE_PATH}\n"
    "@@ -1,2 +1,2 @@\n"
    " def foo():\n"
    "-    return 1\n"
    "+    return 2\n"
    "```"
)
READ_TOOLS = [{"name": "read_repo_file", "description": "read"}, {"name": "search_code", "description": "search"}]
EDIT_TOOLS = READ_TOOLS + [{"name": "propose_local_edits", "description": "propose"}]
DENIAL = "I am only able to read and search your files, I cannot write the changes. Here is what to change in prose."
CODE_ANSWER = "Add this:\n```python\ndef helper():\n    return 1\n```"
FILE_RESULT = f"URL: https://example/{GROUNDED_FILE_PATH}\n{GROUNDED_FILE_CONTENT}"


def run(coro):
    return asyncio.run(coro)


def go(llm, tools, acts=None, **kwargs):
    return run(nl.run(llm=llm, tools=tools, act=acts or Acts({"read_repo_file": FILE_RESULT, "search_code": "backend/app.py: def foo()"}), question="change it", capability_denial_watchlist=WATCHLIST, **kwargs))


def notices(llm):
    """The text of every plain note the loop added to the conversation after the first call."""
    seen = []
    for messages, _tools in llm.calls:
        for message in messages:
            content = getattr(message, "content", "")
            if getattr(message, "type", "") == "human" and isinstance(content, str) and content not in seen:
                seen.append(content)
    return seen


# ---- the pure checks --------------------------------------------------------------------------------------------------------------------

def test_a_denied_capability_is_found_only_when_the_tool_is_offered():
    assert ac.denied_capability(DENIAL, {"propose_local_edits", "read_repo_file"}, WATCHLIST) == "propose_local_edits"
    assert ac.denied_capability(DENIAL, {"read_repo_file"}, WATCHLIST) is None                      # not offered: the denial is true
    assert ac.denied_capability("I changed the file and it works.", {"propose_local_edits"}, WATCHLIST) is None      # no denial wording
    assert ac.denied_capability(DENIAL, {"propose_local_edits"}, None) is None                        # nothing to watch
    assert ac.denied_capability("I can't open a browser here, sorry.", {"browser_navigate"}, WATCHLIST) == "browser_navigate"


def test_a_diff_that_no_real_read_backs_is_found_by_its_path():
    assert ac.ungrounded_diff_path(UNGROUNDED_DIFF_ANSWER, GROUNDED_INITIAL_ATTEMPTS) == GROUNDED_FILE_PATH
    assert ac.ungrounded_diff_path(GROUNDED_DIFF_ANSWER, GROUNDED_INITIAL_ATTEMPTS) is None
    assert ac.ungrounded_diff_path("No diff here, just words.", GROUNDED_INITIAL_ATTEMPTS) is None


def test_code_with_nothing_read_is_found_unless_it_is_quoted_from_the_conversation():
    assert ac.code_without_reading(CODE_ANSWER, [], "") is True
    assert ac.code_without_reading(CODE_ANSWER, GROUNDED_INITIAL_ATTEMPTS, "") is False             # a real read of the repo came back
    assert ac.code_without_reading("Just words, no code block.", [], "") is False
    assert ac.code_without_reading(CODE_ANSWER, [], "the user pasted:\ndef helper():\n    return 1\n") is False      # the code is theirs


# ---- in the loop ------------------------------------------------------------------------------------------------------------------------

def test_a_denial_of_an_offered_tool_is_sent_back_once_then_the_next_answer_stands():
    llm = ToolLLM(answers(DENIAL), answers("I will use the edit tool: here is the plan."))
    result = go(llm, EDIT_TOOLS)
    assert len(llm.calls) == 2 and result["final_answer"].startswith("I will use the edit tool")
    assert any("'propose_local_edits' is among your tools" in text for text in notices(llm))


def test_the_same_denial_stands_when_the_tool_was_not_offered():
    llm = ToolLLM(answers(DENIAL))
    result = go(llm, READ_TOOLS)
    assert len(llm.calls) == 1 and result["final_answer"] == DENIAL


def test_a_check_sends_an_answer_back_only_once_even_if_the_model_repeats_it():
    llm = ToolLLM(answers(DENIAL), answers(DENIAL))
    result = go(llm, EDIT_TOOLS)
    assert len(llm.calls) == 2 and result["final_answer"] == DENIAL


def test_an_ungrounded_diff_is_sent_back_once_with_its_path_then_the_corrected_one_stands():
    llm = ToolLLM(asks(call("read_repo_file", {"path": GROUNDED_FILE_PATH})), answers(UNGROUNDED_DIFF_ANSWER), answers(GROUNDED_DIFF_ANSWER))
    result = go(llm, READ_TOOLS)
    assert len(llm.calls) == 3 and result["final_answer"] == GROUNDED_DIFF_ANSWER
    assert any(GROUNDED_FILE_PATH in text and "none of the lines it claims" in text for text in notices(llm))


def test_code_with_nothing_read_is_sent_back_then_accepted_once_something_real_was_read():
    llm = ToolLLM(answers(CODE_ANSWER), asks(call("search_code", {"query": "helper"})), answers(CODE_ANSWER))
    result = go(llm, READ_TOOLS)
    assert len(llm.calls) == 3 and result["final_answer"] == CODE_ANSWER
    assert any("you have not looked at any real code" in text for text in notices(llm))


def test_the_code_and_diff_checks_stand_aside_when_the_edit_tool_is_offered():
    llm = ToolLLM(answers(CODE_ANSWER))
    result = go(llm, EDIT_TOOLS)                                            # a change goes through propose_local_edits, which is checked against the real folder
    assert len(llm.calls) == 1 and result["final_answer"] == CODE_ANSWER


def test_each_send_back_is_counted_by_kind(monkeypatch):
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    go(ToolLLM(answers(DENIAL), answers("ok")), EDIT_TOOLS)
    assert recorded[-1].get("answer_sent_back_denial") == 1 and not recorded[-1].get("answer_sent_back_diff")
    go(ToolLLM(answers(CODE_ANSWER), asks(call("search_code", {"query": "x"})), answers(CODE_ANSWER)), READ_TOOLS)
    assert recorded[-1].get("answer_sent_back_no_read") == 1
    go(ToolLLM(asks(call("read_repo_file", {"path": GROUNDED_FILE_PATH})), answers(UNGROUNDED_DIFF_ANSWER), answers(GROUNDED_DIFF_ANSWER)), READ_TOOLS)
    assert recorded[-1].get("answer_sent_back_diff") == 1


def test_the_counter_keys_are_registered_and_the_turn_hands_the_loop_what_it_needs():
    assert set(sc.ANSWER_CHECK_KEYS) == {"answer_sent_back_denial", "answer_sent_back_diff", "answer_sent_back_no_read", "answer_sent_back_handback_offer", "answer_sent_back_handback_verify", "answer_sent_back_handback_failed", "answer_sent_back_handback_unfinished", "answer_sent_back_handback_choice"} and set(sc.ANSWER_CHECK_KEYS) <= sc.KEYS      # the last two: backend/utils/handback_check.py
    source = open(aw.__file__, encoding="utf-8").read()
    assert 'capability_denial_watchlist=kwargs.get("capability_denial_watchlist"), conversation_text=kwargs.get("conversation_text") or ""' in source


# ---- "I can't open your files" on a turn that had the read tools (2026-11-08: twice in one thread, the wording below is paraphrased) -----------------------------------------------------------------

LOOK_DENIALS = (
    "I can't open your connected folder in this reply, so I haven't looked at anything yet.",
    "Sorry, I can't open these files inside this reply. Paste the code around line 640.",
    "I cannot read the repository from here.",
    "I'm not able to look at your code right now.",
    "I don't have access to your files in this chat.",
    "I can't actually search the codebase this turn.",
)
NOT_DENIALS = (
    DENIAL,                                                              # "only able to read and search your files, I cannot write": it affirms reading
    "I can't read what she wrote unless you share it with me.",          # not a file
    "I can't see output from a run in this reply, so I won't report a result.",
    "I looked in the folder and found three files; I read backend/app.py.",
    "I can edit files here, and I can read them too.",
)


def test_a_denial_of_looking_at_the_code_is_found_when_a_read_tool_is_offered_and_only_then():
    for text in LOOK_DENIALS:
        assert ac.denied_capability(text, {"read_repo_file", "search_literal"}, WATCHLIST) == "read_repo_file", text
        assert ac.denied_capability(text, {"search_literal"}, WATCHLIST) is None, text                     # no read tool offered: the denial may be true
    for text in NOT_DENIALS:
        assert ac.denied_capability(text, {"read_repo_file", "search_literal"}, WATCHLIST) in (None, "propose_local_edits"), text
        assert ac.denied_capability(text, {"read_repo_file"}, WATCHLIST) is None, text


def test_such_a_denial_in_the_loop_is_sent_back_once_naming_the_tool_then_the_next_answer_stands():
    llm = ToolLLM(answers(LOOK_DENIALS[0]), answers("Here is what I found after reading the file."))
    result = go(llm, READ_TOOLS)
    assert len(llm.calls) == 2 and result["final_answer"].startswith("Here is what I found")
    assert any("'read_repo_file' is among your tools" in text for text in notices(llm))
    again = ToolLLM(answers(LOOK_DENIALS[0]), answers(LOOK_DENIALS[0]))
    assert len(again.calls) == 0 and go(again, READ_TOOLS)["final_answer"] == LOOK_DENIALS[0] and len(again.calls) == 2      # once only, then it stands
