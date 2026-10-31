"""Sonic acts first (Jack, 2026-10-31): reads are targeted, actions do not use up the step budget, a change is made with propose_local_edits and never written out as code, and a turn that runs out of
reading steps makes the edit instead of ending in prose. Pure tests plus the loop driven by a scripted model."""
import pathlib

import pytest

from backend.utils import act_not_suggest as ans
from backend.utils import targeted_read as tr

ROOT = pathlib.Path(__file__).resolve().parents[2]


# --- reads: where the work is, never the top ---

@pytest.mark.parametrize("start, count, top", [
    (None, None, True), (0, None, True), (1, None, True), ("1", None, True), (1, 500, True), (None, 300, True),
    (1, 40, False), (None, 60, False), (1, 120, False), (200, None, False), (200, 60, False), ("abc", None, True),
])
def test_a_read_that_would_start_at_the_top_is_told_apart_from_a_window(start, count, top):
    assert tr.is_top_read(start, count) is top


def test_the_map_of_a_file_lists_top_level_declarations_with_line_numbers_in_any_language():
    lines = ["import os", "", "def alpha():", "    return 1", "", "class Beta:", "    def inner(self): ...", "", "export default function Chat() {", "  const x = 1;", "}", "// a comment", "const Gamma = () => {", "};"]
    where = tr.outline(lines)
    assert "line 3: def alpha" in where and "line 6: class Beta" in where and "line 9: export default function Chat() {" in where and "line 13: const Gamma = () => {" in where
    assert "inner" not in where and "a comment" not in where and "line 11" not in where            # nested, comments and closers are not declarations


def test_the_redirect_says_what_to_do_instead_and_never_contains_the_top_of_the_file():
    lines = [f"import thing{i}" for i in range(5)] + ["def target():", "    pass"]
    text = tr.redirect_text("backend/x.py", lines, "https://example/x.py")
    assert text.startswith("URL: https://example/x.py\nNOT READ FROM THE TOP: [truncated — this file has 7 lines total")
    assert "search_literal" in text and "start_line=<match line minus 15>, line_count=60" in text and "line 6: def target" in text
    assert "import thing3" not in text                                                  # the top of the file is not in the answer
    from backend.utils.agent_utils import _mentions_unresolved_truncation

    assert _mentions_unresolved_truncation(text)                                         # the older engine's rule against answering straight after a truncated read still fires


def test_a_long_outline_is_capped_by_entries_and_by_characters():
    where = tr.outline([f"def f{i}():" for i in range(400)])
    assert where.count("\n") <= tr.OUTLINE_MAX_ENTRIES and "more declarations" in where
    wide = tr.outline([f"export const Thing{i} = makeSomethingWithAVeryLongName({i}, 'argument', 'another argument', 'and one more');" for i in range(400)])
    assert len(wide) <= tr.OUTLINE_MAX_CHARS + 80 and "more declarations" in wide


def test_the_read_handler_refuses_the_top_of_a_big_file_and_keeps_small_files_and_small_windows():
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "if len(decoded) > _READ_FILE_CHAR_CAP and targeted_read.is_top_read(start_line, line_count):" in source
    assert source.index("targeted_read.is_top_read(start_line, line_count)") < source.index("if start_line:\n                lines = decoded.splitlines()")   # before any window is cut


# --- code in an answer instead of an edit ---

@pytest.mark.parametrize("text, wrote", [
    ("Change it like this:\n```python\ndef f():\n    x = 1\n    return x\n```", True),
    ("```tsx\nconst a = 1;\nconst b = 2;\nconst c = 3;\n```", True),
    ("```\nline one\nline two\nline three\n```", True),
    ("```diff\n- old\n+ new\n+ newer\n```", True),
    ("Run:\n```bash\nnpm test\nnpm run build\ngit status\n```", False),
    ("Output was:\n```text\na\nb\nc\n```", False),
    ("Use `foo()` here.", False),
    ("```python\nx = 1\n```", False),                      # one line quoted while explaining
    ("No code at all.", False),
    ("", False),
])
def test_a_fenced_block_of_real_code_in_an_answer_is_found_and_commands_output_and_one_liners_are_not(text, wrote):
    assert ans.wrote_code_instead_of_editing(text) is wrote


# --- the loop, with a scripted model ---

def _drive(script, tools, results, max_steps=3, required=True, plan_budget=None):
    import asyncio

    from langchain_core.messages import AIMessage

    from backend.services import native_loop as nl
    from backend.utils import llm_turns

    seen = {"offered": [], "messages": []}

    class LLM:
        async def ainvoke_tools(self, messages, offered):
            seen["offered"].append([t["name"] for t in offered])
            seen["messages"].append([str(getattr(m, "content", "")) for m in messages])
            if not script:
                return AIMessage(content="out of script")
            item = script.pop(0)
            return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

    async def act(decision):
        return results[decision["tool_action"]]

    required_action = {"tool_action": "propose_local_edits", "final_notice": "You must propose the edit.", "nudge": "Edit now.", "max_final_rejections": 2} if required else None
    turn = llm_turns.begin("someone")
    try:
        out = asyncio.run(nl.run(llm=LLM(), tools=tools, act=act, question="fix it", max_steps=max_steps, required_action=required_action, plan_budget=plan_budget))
    finally:
        llm_turns._current.set(None)
    return out, seen, turn


def _call(name, **args):
    return {"name": name, "args": args, "id": f"c-{name}-{len(args)}-{id(args)}", "type": "tool_call"}


READ = {"name": "read_repo_file", "description": "r"}
SEARCH = {"name": "search_literal", "description": "s"}
EDIT = {"name": "propose_local_edits", "description": "e"}
CHECK = {"name": "run_local_command", "description": "c"}
APPLIED = "Proposed 1 edit(s) across 1 file(s): a.py. APPLIED: the files now contain the change."


def test_edits_and_checks_do_not_use_up_the_step_budget_but_reads_still_do():
    # a budget of 3 steps: the old loop would be forced to answer after the third call; here each edit and check is handed back
    script = [_call("read_repo_file", path="a.py", start_line=10, line_count=60), _call("propose_local_edits", n=1), _call("run_local_command", n=2), _call("propose_local_edits", n=3),
              _call("run_local_command", n=4), "All four actions ran, and the change is applied."]
    out, seen, turn = _drive(script, [READ, EDIT, CHECK], {"read_repo_file": "x", "propose_local_edits": APPLIED, "run_local_command": "LOCAL COMMAND `npm test` FINISHED with exit code 0 (3 s)."}, max_steps=3)
    assert out["final_answer"] == "All four actions ran, and the change is applied."
    assert [a["purpose"] for a in out["attempts"]].count("Run propose_local_edits") == 2 and len(out["attempts"]) == 5 and out["show_work"] is False


def test_reads_alone_still_hit_the_limit_so_a_turn_cannot_read_forever():
    script = [_call("read_repo_file", path=f"a{i}.py", start_line=10) for i in range(8)]
    out, seen, turn = _drive(script, [READ, SEARCH], {"read_repo_file": "x"}, max_steps=3, required=False)
    assert out["show_work"] is True and len(out["attempts"]) == 3                      # three reads, then forced to answer


def test_the_refund_has_a_cap_per_turn(monkeypatch):
    from backend.services import native_loop as nl

    monkeypatch.setattr(nl, "ACTION_REFUND_CAP", 2)
    script = [_call("propose_local_edits", n=i) for i in range(6)]
    out, seen, turn = _drive(script, [READ, EDIT], {"propose_local_edits": APPLIED}, max_steps=1, required=False)
    assert len(out["attempts"]) == 3                                                    # one real step plus two refunded, then the limit


def test_an_answer_that_writes_the_code_out_is_sent_back_and_the_edit_is_made():
    script = [_call("read_repo_file", path="a.py", start_line=1, line_count=40), "Change it like this:\n```python\ndef f():\n    x = 1\n    return x\n```", _call("propose_local_edits", n=1), "Done: the edit is applied."]
    out, seen, turn = _drive(script, [READ, EDIT], {"read_repo_file": "x", "propose_local_edits": APPLIED}, max_steps=6, required=False, plan_budget=_plan())
    assert out["final_answer"] == "Done: the edit is applied."
    assert any("ACT, DO NOT DESCRIBE" in " ".join(m) for m in seen["messages"][2:])


def _plan():
    from backend.utils import code_plan as cp

    plan = {"state": cp.APPROVED, "goal": "g", "steps": [{"id": "s1", "text": "Edit a.py", "files": ["a.py"], "status": "todo"}], "files": [{"path": "a.py", "why": "x"}], "tests": [], "unknowns": []}
    return cp.PlanBudget(plan)


def test_a_turn_that_runs_out_of_reading_steps_makes_the_edit_instead_of_writing_prose():
    script = [_call("search_literal", term="a"), _call("read_repo_file", path="a.py", start_line=5), _call("read_repo_file", path="b.py", start_line=5),
              _call("propose_local_edits", n=1), "I made the edit."]
    out, seen, turn = _drive(script, [READ, SEARCH, EDIT], {"search_literal": "a.py:5: x", "read_repo_file": "x", "propose_local_edits": APPLIED}, max_steps=3)
    # the step after the last read offered ONLY the edit tool, and its notice told the model to act, not to write the code out
    assert ["propose_local_edits"] in seen["offered"]
    assert any("Make it NOW" in " ".join(m) for m in seen["messages"])
    assert any(a["purpose"] == "Run propose_local_edits" for a in out["attempts"])
    assert out["final_answer"] == "I made the edit."


def test_the_old_ending_that_asked_for_the_code_in_a_fenced_block_is_not_used_when_an_edit_was_due():
    script = [_call("read_repo_file", path=f"a{i}.py", start_line=5) for i in range(3)] + ["prose without a tool call"]
    out, seen, turn = _drive(script, [READ, EDIT], {"read_repo_file": "x"}, max_steps=3)
    assert not any("The step limit has been reached" in " ".join(m) for m in seen["messages"])          # the notice that asked for the code in a fenced block
    assert out["final_answer"] == "prose without a tool call"


def test_the_prompts_and_plan_rules_say_act_read_targeted_and_that_acting_is_free():
    from backend.services import native_loop as nl
    from backend.utils import code_plan as cp

    assert "Never read a file from line 1" in nl.SYSTEM_PROMPT and "search_literal for a word from the task" in nl.SYSTEM_PROMPT
    assert "ACT, DO NOT DESCRIBE" in nl.EDIT_PROMPT and "do NOT use up your tool steps" in nl.EDIT_PROMPT
    assert "ACT, DO NOT DESCRIBE" in cp.EXECUTE_RULES and "never a file from line 1" in cp.EXECUTE_RULES


def test_a_change_turn_that_is_stopped_for_wandering_ends_by_saying_what_is_left_not_by_asking_for_code():
    # off-plan reads past the plan's allowance end the loop (that is a stop for wandering, not a spent budget): no forced edit, and the ending does not ask for a fenced code block
    wandering = [_call("search_literal", term=f"q{i}") for i in range(6)]
    out, seen, turn = _drive(wandering, [READ, SEARCH, EDIT], {"search_literal": "x"}, max_steps=6, required=False, plan_budget=_plan_with_small_allowance())
    ending = [m for m in seen["messages"] if any("The step limit has been reached" in part for part in m)]
    assert ending and all("fenced code block" not in part for part in ending[-1][-1:])
    assert ["propose_local_edits"] not in seen["offered"]                                   # not forced to edit: it was told to say what is left


def _plan_with_small_allowance():
    from backend.utils import code_plan as cp

    plan = {"state": cp.APPROVED, "goal": "g", "steps": [{"id": "s1", "text": "Edit a.py", "files": ["a.py"], "status": "todo"}], "files": [{"path": "a.py", "why": "x"}], "tests": [], "unknowns": []}
    return cp.PlanBudget(plan, deviation_steps=2, hard_cap=30)


def test_the_edit_turn_ending_and_the_general_one_are_different_texts():
    from backend.services import native_loop as nl

    assert "fenced code block" in nl.STEP_LIMIT_NOTICE and "fenced code block" not in nl.EDIT_TURN_LIMIT_NOTICE
    assert "Do not write the code, pseudocode or a list of changes" in nl.EDIT_TURN_LIMIT_NOTICE
