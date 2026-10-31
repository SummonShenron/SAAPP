"""A claim that a check passed is held to a passing run in the same turn (backend/utils/claim_check.py). Pure tests plus a check that the loop, the turn record and app.py use it."""
import pathlib

import pytest

from backend.utils import claim_check as cc

ROOT = pathlib.Path(__file__).resolve().parents[2]


def command(line, code=0):
    return {"purpose": "Run run_local_command", "action_desc": "run_local_command(line=...)", "observation": f"LOCAL COMMAND `{line}` FINISHED with exit code {code} (3.1 s). Exit code 0 means no command in it reported a problem."}


def check(name, verdict="PASSED"):
    return {"purpose": "Run run_local_check", "action_desc": f"run_local_check(check={name})", "observation": f"LOCAL CHECK {name} {verdict} (exit code 0, 8 s)."}


# --- what a reply claims ---

@pytest.mark.parametrize("text, kinds", [
    ("The changes pass `npx tsc -b` and the node test suite cleanly.", {"typecheck", "tests"}),
    ("All 86 tests pass.", {"tests"}),
    ("tsc passes cleanly now.", {"typecheck"}),
    ("Lint is clean and the build succeeded.", {"lint", "build"}),
    ("I ran pytest and everything is green.", {"tests"}),
    ("Type-check passed with no errors.", {"typecheck"}),
])
def test_a_reply_that_says_a_check_passed_is_a_claim(text, kinds):
    assert cc.claimed_kinds(text) == kinds


@pytest.mark.parametrize("text", [
    "The tests should pass once you apply it.",
    "I did not run the tests.",
    "I haven't run tsc, so I can't say it passes.",
    "Want me to run the tests and see if they pass?",
    "Run `npx tsc -b` to confirm it passes.",
    "2 tests failed and 72 passed.",
    "Make the tests pass by fixing the import.",
    "The tests need to pass before you merge.",
    "I changed the file and added a test for the new route.",
    "Everything looks fine.",                       # no kind named: too vague to hold to anything
    "",
])
def test_a_plan_a_hedge_a_question_a_negation_or_a_failure_is_not_a_claim(text):
    assert cc.claimed_kinds(text) == set()


def test_a_follow_up_offer_at_the_end_is_not_read():
    assert cc.claimed_kinds("Added the route.\n\n<<<FOLLOW_UP: Want me to confirm the tests pass?>>>") == set()


# --- what the turn really ran ---

def test_a_command_that_finished_with_exit_code_zero_backs_each_program_in_it():
    status = cc.evidence([command("npx tsc -b && node --test src/*.test.mjs")])
    assert status == {"typecheck": "passed", "tests": "passed"}


def test_a_non_zero_exit_code_is_a_failed_run_and_the_last_run_wins():
    assert cc.evidence([command("npx tsc -b", 2)]) == {"typecheck": "failed"}
    assert cc.evidence([command("npx tsc -b", 2), command("npx tsc -b", 0)]) == {"typecheck": "passed"}
    assert cc.evidence([command("npx tsc -b", 0), command("npx tsc -b", 1)]) == {"typecheck": "failed"}


def test_a_named_check_backs_its_kind_and_a_tests_folder_in_a_command_is_not_a_test_run():
    assert cc.evidence([check("pytest")]) == {"tests": "passed"}
    assert cc.evidence([check("backend-tests", "FAILED")]) == {"tests": "failed"}
    assert cc.evidence([command("ls backend/tests")]) == {}
    assert cc.evidence([command("git status")]) == {}


def test_a_run_that_timed_out_never_started_or_errored_is_no_evidence():
    timed_out = {"purpose": "Run run_local_command", "observation": "LOCAL COMMAND `pytest -q` TIMED OUT: it did not finish in time, so the result is unknown."}
    errored = {"purpose": "Run run_local_command", "observation": "ERROR: no runner of theirs is connected right now."}
    assert cc.evidence([timed_out, errored]) == {}


def test_the_sandbox_tests_back_the_tests_unless_their_result_says_something_failed():
    ok = {"purpose": "Run run_sandbox_tests", "observation": "3 passed in 0.4s"}
    bad = {"purpose": "Run run_sandbox_tests", "observation": "1 failed, 2 passed in 0.4s"}
    assert cc.evidence([ok]) == {"tests": "passed"} and cc.evidence([bad]) == {"tests": "failed"}


# --- the claim held to the run ---

def test_the_2026_10_30_reply_is_caught_when_nothing_ran_and_when_it_failed():
    reply = "The changes pass `npx tsc -b` and the node test suite cleanly."
    assert cc.unproven(reply, cc.evidence([])) == ["typecheck", "tests"]
    assert cc.unproven(reply, cc.evidence([command("npx tsc -b", 2)])) == ["typecheck", "tests"]            # failed, and the tests never ran
    assert cc.unproven(reply, cc.evidence([command("npx tsc -b && node --test src/*.test.mjs")])) == []     # both really passed


def test_the_note_says_which_check_was_not_run_or_failed_and_stays_one_line():
    note = cc.note_for(["typecheck", "tests"], {"typecheck": "failed"})
    assert note == "_Not checked: the type-check failed in this turn; the tests were not run in this turn. Treat what I said about them as unverified._"
    assert "\n" not in note
    assert cc.note_for(["lint"], {}) == "_Not checked: the lint check was not run in this turn. Treat what I said about it as unverified._"
    assert cc.note_for([], {}) == ""


def test_the_model_is_told_to_run_the_check_only_when_it_can():
    can = cc.send_back_notice(["typecheck"], True)
    cannot = cc.send_back_notice(["tests"], False)
    assert "run_local_check" in can and "not run" in can
    assert "run_local_check" not in cannot and "none is possible here" in cannot


# --- the wiring ---

def test_the_loop_sends_the_answer_back_once_records_its_checks_and_app_py_adds_the_line():
    loop = (ROOT / "backend" / "services" / "native_loop.py").read_text(encoding="utf-8")
    assert "claim_check.unproven(answer, claim_check.evidence(attempts))" in loop and "claim_rejections < 1 and step < budget" in loop
    assert "llm_turns.note_checks(claim_check.evidence(attempts), sent_back=claim_rejections > 0)" in loop
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'getattr(llm_turn, "checks_noted", False)' in app_source and "claim_check.note_for(unbacked, llm_turn.checks)" in app_source
    assert app_source.index("claim_check.note_for(unbacked") < app_source.index("'event': 'final_generation', 'text': full_response")


def test_only_the_native_loop_records_checks_so_an_older_loops_reply_is_never_held_to_nothing():
    from backend.utils import llm_turns

    turn = llm_turns.Turn() if hasattr(llm_turns, "Turn") else None
    assert turn is not None and turn.checks_noted is False and turn.checks == {}


def test_the_counters_exist():
    from backend.utils.self_counters import KEYS

    assert {"claim_unproven", "claim_sent_back"} <= set(KEYS)


# --- the loop, with a scripted model ---

def _drive(script, tools, results, max_steps=6):
    """Runs the native loop with a scripted model. `script` items are answers (str) or tool calls (dict); `results` maps a tool name to what it returns. Returns (final answer, the messages the model was shown, the turn)."""
    import asyncio

    from langchain_core.messages import AIMessage

    from backend.services import native_loop as nl
    from backend.utils import llm_turns

    shown = []

    class LLM:
        async def ainvoke_tools(self, messages, offered):
            shown.append([getattr(m, "content", "") for m in messages])
            if not offered:
                return AIMessage(content="forced final")
            item = script.pop(0)
            return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

    async def act(decision):
        return results[decision["tool_action"]]

    turn = llm_turns.begin("someone")
    try:
        out = asyncio.run(nl.run(llm=LLM(), tools=tools, act=act, question="fix it", max_steps=max_steps))
    finally:
        llm_turns._current.set(None)
    return out["final_answer"], shown, turn


def _call(name, **args):
    return {"name": name, "args": args, "id": f"c{name}", "type": "tool_call"}


READ = {"name": "read_repo_file", "description": "r"}
RUN = {"name": "run_local_command", "description": "c"}


def test_an_answer_claiming_a_pass_with_no_run_is_sent_back_once_and_the_honest_answer_stands():
    script = [_call("read_repo_file", path="a.ts"), "Done. The changes pass `npx tsc -b` cleanly.", "Done. I did not run the type-check."]
    final, shown, turn = _drive(script, [READ, RUN], {"read_repo_file": "export const a = 1;"})
    assert final == "Done. I did not run the type-check."
    assert any("CHECK YOUR CLAIM" in str(part) for part in shown[-1])                       # the model was told, with the way to run it
    assert any("run_local_command" in str(part) for part in shown[-1] if "CHECK YOUR CLAIM" in str(part))
    assert turn.claim_sent_back is True and turn.checks_noted is True and turn.checks == {}


def test_it_is_sent_back_only_once_so_a_stubborn_answer_ends_the_turn_and_app_py_adds_the_line():
    script = [_call("read_repo_file", path="a.ts"), "tsc passes cleanly.", "tsc passes cleanly, really."]
    final, shown, turn = _drive(script, [READ, RUN], {"read_repo_file": "x"})
    assert final == "tsc passes cleanly, really."                                           # never an endless argument
    assert cc.unproven(final, turn.checks) == ["typecheck"]                                  # ... and the line app.py adds is what is left


def test_a_claim_backed_by_a_real_passing_run_in_the_same_turn_is_left_alone():
    ran = f"LOCAL COMMAND `npx tsc -b` FINISHED with exit code 0 (9 s)."
    script = [_call("read_repo_file", path="a.ts"), _call("run_local_command", line="npx tsc -b"), "The changes pass `npx tsc -b` cleanly."]
    final, shown, turn = _drive(script, [READ, RUN], {"read_repo_file": "x", "run_local_command": ran})
    assert final == "The changes pass `npx tsc -b` cleanly."
    assert turn.claim_sent_back is False and turn.checks == {"typecheck": "passed"}
    assert cc.unproven(final, turn.checks) == []


def test_a_turn_with_no_repo_tools_is_not_held_to_it():
    final, shown, turn = _drive(["The tests pass, I am told."], [{"name": "web_search", "description": "w"}], {})
    assert final == "The tests pass, I am told." and turn.claim_sent_back is False
