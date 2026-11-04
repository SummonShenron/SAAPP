"""A second reading of an applied change (2026-11-01). Both of the day's mistakes passed Sonic's own checks (a test built from an invented tool result; a backend change with nothing on the frontend to
receive it). Jack's constraint: even a cent a review is too much, so it is one small Haiku call, narrow (the project's own rules), only after an applied edit, with free checks in code. A review that finds
something BLOCKING hands steps back (the combo's counter-hit) so the turn can fix it; one that finds nothing blocking ends there; a failed one is no review."""
import asyncio
import json
import pathlib

import pytest
from langchain_core.messages import AIMessage

from backend.models import models as models_module
from backend.utils import combo as cb
from backend.utils import diff_review as dr

ROOT = pathlib.Path(__file__).resolve().parents[2]
APPLIED = "Proposed 1 edit(s) across 1 file(s): a.py. APPLIED: the files now contain the change."


def card(path, diff="+a\n+b\n+c", kind="edit", additions=3, deletions=0, **extra):
    return {"path": path, "kind": kind, "format": "text", "diff": diff, "additions": additions, "deletions": deletions, **extra}


# ---- is it worth a review ---------------------------------------------------------------------------------------------------------------------

def test_a_change_is_reviewed_only_when_it_is_more_than_prose_or_a_couple_of_lines():
    assert dr.trivial([card("README.md", additions=30)]) is True
    assert dr.trivial([card("notes.txt", additions=30), card("docs.md", additions=5)]) is True
    assert dr.trivial([card("a.py", additions=1, deletions=1)]) is True
    assert dr.trivial([card("a.py", kind="delete", additions=0, deletions=40)]) is True                # a removed file has nothing to read
    assert dr.trivial([card("Letter.docx", format="docx", additions=20)]) is True
    assert dr.trivial([card("a.py", additions=2, deletions=1)]) is False
    assert dr.trivial([card("README.md", additions=30), card("a.py", additions=3)]) is False
    assert dr.trivial([]) is True


# ---- what is sent -------------------------------------------------------------------------------------------------------------------------------

def test_the_prompt_asks_for_the_projects_own_rules_only_and_for_json_and_carries_the_request_and_the_diff():
    prompt = dr.build_prompt("add a Sonnet option to the dropdown", [card("a.py", diff="+x = 1")])
    for phrase in ("invented rather than the shape the real code returns", "crosses a boundary", "treats every line alike", "hard-codes today's date", "the request asked for that the diff does not do",
                   "If the diff does not let you tell, say nothing", "At most three problems", '{"findings": []}'):
        assert phrase in prompt, phrase
    assert "THE REQUEST:\nadd a Sonnet option to the dropdown" in prompt and "=== a.py (edit)\n+x = 1" in prompt


def test_the_request_and_the_diff_are_capped_and_a_deleted_file_sends_no_text(monkeypatch):
    monkeypatch.setattr(dr, "MAX_DIFF_CHARS", 300)
    text = dr.diff_text([card("a.py", diff="+" + "x" * 1000), card("b.py", diff="+y"), card("gone.py", kind="delete", diff="-secret body")])
    assert len(text) < 400 and "[cut]" in text and "more files not shown" in text and "secret body" not in text
    assert len(dr.build_prompt("r" * 5000, [card("a.py")])) < len(dr.REVIEW_PROMPT) + 1500 + 200


# ---- what comes back ---------------------------------------------------------------------------------------------------------------------------

def test_the_answer_is_read_from_json_even_in_a_fence_and_only_a_true_blocking_counts():
    answer = 'Here you go:\n```json\n{"findings": [{"blocking": true, "file": "a.py", "problem": "the test input is invented", "fix": "copy the real shape"}, {"blocking": "yes", "problem": "style"}]}\n```'
    found = dr.parse(answer)
    assert found[0] == {"blocking": True, "file": "a.py", "problem": "the test input is invented", "fix": "copy the real shape"}
    assert found[1]["blocking"] is False                                                           # only the boolean true blocks
    assert dr.blocking(found) == [found[0]]


@pytest.mark.parametrize("bad", ["", None, "no json here", "{broken", "[]", '{"findings": "none"}', '{"findings": [3, "x", {"problem": ""}, {"nope": 1}]}', '{"findings": []}'])
def test_anything_that_is_not_the_asked_for_json_is_no_findings(bad):
    assert dr.parse(bad) == []


def test_at_most_three_findings_and_each_field_is_flattened_and_capped():
    rows = [{"blocking": True, "file": "f", "problem": "p" * 500 + "\n  more", "fix": "q"} for _ in range(6)]
    found = dr.parse(json.dumps({"findings": rows}))
    assert len(found) == dr.MAX_FINDINGS and len(found[0]["problem"]) == 240 and "\n" not in found[0]["problem"]


def test_the_message_for_the_loop_says_what_to_do_and_that_a_wrong_finding_may_be_argued():
    blocking_note = dr.note([{"blocking": True, "file": "a.py", "problem": "invented input", "fix": "use the real shape"}])
    assert blocking_note.startswith("REVIEW OF THE CHANGE YOU JUST APPLIED") and "Fix what is blocking now, then run the check again" in blocking_note
    assert "- a.py: invented input Fix: use the real shape" in blocking_note and "say why in your answer instead of changing the code" in blocking_note
    soft = dr.note([{"blocking": False, "file": "", "problem": "naming", "fix": ""}], ["a.py: a fixed date"])
    assert "Nothing here blocks" in soft and "- naming (not blocking)" in soft and "- a.py: a fixed date" in soft


# ---- the free check ---------------------------------------------------------------------------------------------------------------------------

def test_a_test_that_gains_a_fixed_date_is_noted_without_a_model():
    cards = [
        card("backend/tests/test_x.py", diff="+NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)"),
        card("local/src/a.test.mjs", diff="+const d = new Date('2026-10-25T15:00:00Z');"),
        card("backend/utils/x.py", diff="+START = datetime(2026, 1, 1)"),                          # not a test file
        card("backend/tests/test_y.py", diff="-NOW = datetime(2026, 10, 25, 15, 0)\n+NOW = datetime.now()"),   # the date was REMOVED
        card("backend/tests/test_z.py", kind="delete", diff="+datetime(2026, 1, 1"),
    ]
    notes = dr.fixed_date_notes(cards)
    assert [n.split(":")[0] for n in notes] == ["backend/tests/test_x.py", "local/src/a.test.mjs"] and all("hard-codes a calendar date" in n for n in notes)


# ---- asking the model -------------------------------------------------------------------------------------------------------------------------

class FakeLLM:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.prompts = answer, error, []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return AIMessage(content=self.answer)


def run(coro):
    return asyncio.run(coro)


def test_a_review_returns_the_findings_and_a_failed_or_missing_one_is_none_not_an_empty_list():
    ok = FakeLLM('{"findings": [{"blocking": true, "file": "a.py", "problem": "bad", "fix": "good"}]}')
    assert run(dr.review(ok, "do it", [card("a.py")]))[0]["problem"] == "bad" and len(ok.prompts) == 1
    assert run(dr.review(FakeLLM('{"findings": []}'), "do it", [card("a.py")])) == []              # reviewed, nothing found
    assert run(dr.review(FakeLLM(error=RuntimeError("down")), "do it", [card("a.py")])) is None      # no review at all
    assert run(dr.review(None, "do it", [card("a.py")])) is None and run(dr.review(ok, "do it", [])) is None


def test_a_model_that_answers_in_content_blocks_is_read_too():
    class Blocks:
        async def ainvoke(self, prompt):
            return AIMessage(content=[{"type": "text", "text": '{"findings": [{"blocking": false, "problem": "p"}]}'}])

    assert run(dr.review(Blocks(), "r", [card("a.py")]))[0]["problem"] == "p"


# ---- the refund --------------------------------------------------------------------------------------------------------------------------------

def test_a_blocking_review_hands_steps_back_like_a_failed_check_and_a_clean_one_hands_back_nothing():
    combo = cb.Combo({"propose_local_edits"}, cap=30)
    assert combo.after_review(False) == 0
    assert [combo.after_review(True), combo.after_review(True), combo.after_review(True)] == [4, 2, 1]        # full, half, one: the same scaling as a repeated result
    assert combo.reviews == 4 and combo.blocked_reviews == 3 and combo.refunded == 7
    counts = combo.counts()
    assert counts["review_runs"] == 4 and counts["review_blocked"] == 3


def test_the_refund_stays_under_the_turns_ceiling():
    combo = cb.Combo({"propose_local_edits"}, cap=5)
    assert combo.after_review(True) == 4 and combo.after_review(True) == 1 and combo.after_review(True) == 0 and combo.ceiling_hit


# ---- the loop ------------------------------------------------------------------------------------------------------------------------------------

def _drive(script, max_steps, reviewer):
    from backend.services import native_loop as nl
    from backend.utils import llm_turns

    seen = []

    class LLM:
        async def ainvoke_tools(self, messages, offered):
            seen.append([str(getattr(m, "content", "")) for m in messages])
            if not script:
                return AIMessage(content="out of script")
            item = script.pop(0)
            return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

    async def act(decision):
        return APPLIED if decision["tool_action"] == "propose_local_edits" else "text"

    tools = [{"name": n, "description": n} for n in ("read_repo_file", "propose_local_edits")]
    turn = llm_turns.begin("someone")
    try:
        out = asyncio.run(nl.run(llm=LLM(), tools=tools, act=act, question="fix it", max_steps=max_steps, required_action=None, reviewer=reviewer))
    finally:
        llm_turns._current.set(None)
    return out, seen


def _tool(name, **args):
    return {"name": name, "args": args, "id": f"c-{name}-{sorted(args.items())}", "type": "tool_call"}


def _script(reads):
    return [_tool("propose_local_edits", n=1)] + [_tool("read_repo_file", path=f"f{i}.py", start_line=10, line_count=60) for i in range(reads)] + ["All done."]


def test_a_blocking_finding_reaches_the_model_and_gives_it_room_to_fix_what_a_clean_review_would_not():
    """One step of budget. The applied edit earns 4 back; seven reads need more than that. Only the blocking review's refund lets the turn finish."""
    async def blocking():
        return [{"blocking": True, "file": "a.py", "problem": "the test input is invented", "fix": "copy the real shape"}], []

    async def clean():
        return [], []

    out, seen = _drive(_script(7), 1, blocking)
    assert out["final_answer"] == "All done." and out["show_work"] is False
    assert any("REVIEW OF THE CHANGE YOU JUST APPLIED" in " ".join(messages) and "the test input is invented" in " ".join(messages) for messages in seen)
    out, seen = _drive(_script(7), 1, clean)
    assert out["show_work"] is True                                                           # not enough steps without the refund
    assert not any("REVIEW OF THE CHANGE" in " ".join(messages) for messages in seen)       # a clean review says nothing


def test_a_failed_review_is_silent_and_gives_nothing_and_an_error_in_it_never_breaks_the_turn():
    async def failed():
        return None

    async def broken():
        raise RuntimeError("model down")

    for reviewer in (failed, broken):
        out, seen = _drive(_script(7), 1, reviewer)
        assert out["show_work"] is True and not any("REVIEW OF THE CHANGE" in " ".join(m) for m in seen)


def test_at_most_two_reviews_run_in_a_turn_and_none_without_an_applied_edit():
    calls = []

    async def counting():
        calls.append(1)
        return [], []

    script = [_tool("propose_local_edits", n=i) for i in range(4)] + ["done"]
    _drive(script, 6, counting)
    assert len(calls) == dr.MAX_ROUNDS == 2
    calls.clear()
    _drive([_tool("read_repo_file", path="a.py", start_line=10, line_count=60), "done"], 6, counting)
    assert calls == []


# ---- wired in ------------------------------------------------------------------------------------------------------------------------------------

def test_reviews_are_off_in_the_test_suite_unless_a_test_turns_them_on():
    assert dr.ENABLED is False


def test_the_review_model_is_haiku_at_low_effort_with_thinking_off_no_fallback_and_none_without_a_key(monkeypatch):
    monkeypatch.setattr(models_module, "_review_llm", {})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    assert models_module.review_llm() is None
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    llm = models_module.review_llm()
    assert llm.model_name == "claude-haiku-5-5" and llm.effort == "low" and llm.thinking_off is True and llm.fallback is None and llm.max_tokens == 600
    assert models_module.review_llm() is llm


def test_the_tool_agent_collects_applied_cards_reviews_them_and_hands_the_loop_the_reviewer():
    source = (ROOT / "backend/services/agent_workflow.py").read_text(encoding="utf-8")
    assert "_applied_cards.extend(result.get(\"files\") or [])" in source and "async def _review_applied():" in source
    assert "kind, reader = diff_review.reader_for(plan_phase == code_plan.EXECUTE_PHASE, claude_may_read, plan_review_llm(), review_llm())" in source
    assert "findings = await diff_review.review(reader, latest_message_content, files)" in source
    assert "findings = await diff_review.review(reader, diff_review.plan_request(plan) or latest_message_content, files, plan=True)" in source
    assert "claude_may_read = state.get(\"tool_model\") in code_plan.ANTHROPIC_CHOICES or tool_model_choice in code_plan.ANTHROPIC_CHOICES" in source
    assert "reviewer=_review_applied if local_workspace_handle is not None else None," in source
    loop = (ROOT / "backend/services/native_loop.py").read_text(encoding="utf-8")
    assert "if reviewer is not None and applied_now and not more_plan_steps and review_rounds < diff_review.MAX_ROUNDS:" in loop and "combo.after_review(blocked)" in loop


# ---- the plan-end reading by Sonnet (2026-11-05) --------------------------------------------------------------------------------------------

PLAN = {"goal": "Count browser calls", "steps": [{"id": "s1", "text": "Add the counter keys", "files": ["backend/utils/self_counters.py"]}, {"id": "s2", "text": "Record them in the loop", "files": []}],
        "tests": ["a turn with two page calls counts two"]}


def test_the_plan_is_shown_to_the_reviewer_as_its_goal_its_steps_with_files_and_its_tests():
    text = dr.plan_request(PLAN)
    assert text.splitlines()[0] == "Goal: Count browser calls"
    assert "- Add the counter keys [backend/utils/self_counters.py]" in text and "- Record them in the loop" in text
    assert "Tests the plan wanted:" in text and "- a turn with two page calls counts two" in text
    assert dr.plan_request(None) == "" and dr.plan_request({}) == ""


def test_the_plan_reading_uses_the_broader_prompt_the_plan_and_room_for_a_whole_plans_diff():
    big = card("a.py", diff="+" + "x" * 30000)
    plan_prompt = dr.build_prompt(dr.plan_request(PLAN), [big], plan=True)
    for phrase in ("A step of the plan", "would still pass if the change were taken out", "nothing in the diff ever sets or increments", "share one page, session, connection or list", "nothing in the diff defines or imports"):
        assert phrase in plan_prompt, phrase
    assert "THE APPROVED PLAN:\nGoal: Count browser calls" in plan_prompt and "[cut]" not in plan_prompt          # 30,000 characters fit the plan reading's 40,000
    ordinary = dr.build_prompt("add a thing", [big])
    assert dr.REVIEW_PROMPT in ordinary and dr.PLAN_REVIEW_PROMPT not in ordinary and "[cut]" in ordinary          # and not the ordinary reading's 12,000


def test_the_plan_reading_may_report_five_findings_and_the_ordinary_one_three():
    rows = json.dumps({"findings": [{"blocking": True, "file": f"f{i}.py", "problem": f"problem {i}", "fix": "do it"} for i in range(7)]})
    assert len(dr.parse(rows)) == 3
    assert len(dr.parse(rows, dr.PLAN_MAX_FINDINGS)) == 5

    class Fake:
        async def ainvoke(self, prompt):
            return AIMessage(content=rows)

    assert len(asyncio.run(dr.review(Fake(), "x", [card("a.py")]))) == 3
    assert len(asyncio.run(dr.review(Fake(), "x", [card("a.py")], plan=True))) == 5


def test_who_reads_a_change_depends_on_whether_the_person_chose_claude_and_whether_a_plan_is_being_carried_out():
    strong, ordinary = object(), object()
    assert dr.reader_for(True, True, strong, ordinary) == ("plan", strong)
    assert dr.reader_for(False, True, strong, ordinary) == ("ordinary", ordinary)
    assert dr.reader_for(True, True, None, ordinary) == ("ordinary", ordinary)          # no Sonnet price: the Haiku reading, as before
    assert dr.reader_for(True, True, None, None) == ("ordinary", None)                   # no Claude key at all: no review
    # a person who chose Gemini or GPT for the code work: their code goes to Claude for no reading at all
    assert dr.reader_for(True, False, strong, ordinary) == ("none", None)
    assert dr.reader_for(False, False, strong, ordinary) == ("none", None)


def test_the_plan_reading_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(dr, "PLAN_REVIEW_ENABLED", False)
    strong, ordinary = object(), object()
    assert dr.reader_for(True, True, strong, ordinary) == ("ordinary", ordinary)


def test_a_plan_reading_is_counted_as_a_run_and_as_blocked_only_when_something_blocks_and_a_failed_one_is_not_counted():
    from backend.utils import self_counters

    assert dr.plan_counts([]) == {"plan_review_runs": 1}
    assert dr.plan_counts([{"blocking": False, "problem": "x"}]) == {"plan_review_runs": 1}
    assert dr.plan_counts([{"blocking": True, "problem": "x"}]) == {"plan_review_runs": 1, "plan_review_blocked": 1}
    assert dr.plan_counts(None) == {}
    assert set(dr.plan_counts([{"blocking": True, "problem": "x"}])) <= set(self_counters.COMBO_KEYS)          # the keys the day's tally accepts


def test_the_plan_review_model_is_sonnet_at_low_effort_with_thinking_off_no_fallback_and_none_without_a_price(monkeypatch):
    monkeypatch.setattr(models_module, "_plan_review_llm", {})
    monkeypatch.setattr(models_module, "sonnet_available", lambda: False)
    assert models_module.plan_review_llm() is None
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(models_module, "sonnet_available", lambda: True)
    llm = models_module.plan_review_llm()
    assert llm.model_name == "claude-sonnet-5-5" and llm.effort == "low" and llm.thinking_off is True and llm.fallback is None and llm.max_tokens == 1500
    assert models_module.plan_review_llm() is llm
