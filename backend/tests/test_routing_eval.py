import json
import re
from types import SimpleNamespace

import pytest

from backend.components.constraints import REASONER_PROMPT
from backend.evals import eval_models as em
from backend.evals import routing_eval as re_


@pytest.fixture(autouse=True)
def _never_read_the_real_env_file(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


# The flags the reasoner's own schema names: every label is checked against THIS, so a typo or a renamed flag cannot hide.
SCHEMA_FLAGS = set(re.findall(r'"((?:needs_[a-z_]+|follow_up_intent))": false', REASONER_PROMPT))


def test_the_schema_the_labels_are_checked_against_is_the_real_one():
    assert len(SCHEMA_FLAGS) >= 18 and {"needs_code_interpreter", "needs_create_pr", "follow_up_intent", "needs_memory_save"} <= SCHEMA_FLAGS


def test_the_case_list_is_well_formed_and_every_flag_exists_in_the_prompt():
    ids = re_.message_ids()
    assert len(ids) == len(set(ids)) and len(ids) >= 35
    for case in re_.CASES:
        assert case.message.strip() and case.group in ("flags", "emotion", "risk"), case.id
        assert set(case.must_true) <= SCHEMA_FLAGS and set(case.must_false) <= SCHEMA_FLAGS, case.id
        assert not (set(case.must_true) & set(case.must_false)), f"{case.id} labels a flag both true and false"
        assert case.must_true or case.must_false or case.valence or case.risk, f"{case.id} checks nothing"
        assert all(v in ("distressed", "low", "frustrated", "neutral", "positive", "excited") for v in case.valence), case.id
        assert case.risk in (None, "none", "at_least:elevated", "at_least:acute", "at_least:imminent"), case.id
        for line in filter(None, case.history.split("\n")):
            assert line.startswith(("human: ", "ai: ")), f"{case.id}: history is written as the app writes it"


def test_the_set_covers_the_pathways_the_emotion_reading_and_both_kinds_of_risk_case():
    covered = {flag for case in re_.CASES for flag in case.must_true}
    assert len(covered) >= 15
    assert {"needs_code_interpreter", "needs_retrieval", "needs_conversation", "needs_github_search", "needs_web_search", "needs_create_pr", "needs_create_issue",
            "needs_calendar_lookup", "needs_gmail_lookup", "needs_send_email", "needs_memory_save", "needs_memory_recall", "follow_up_intent"} <= covered
    assert {case.group for case in re_.CASES} == {"flags", "emotion", "risk"}
    risks = [case.risk for case in re_.CASES if case.group == "risk"]
    assert "none" in risks and any(r and r.startswith("at_least:") for r in risks)       # a heavy message must NOT raise it, an indirect one must


def perfect_answer(case):
    """What a model that did exactly what the label asks would say."""
    flags = {name: False for name in SCHEMA_FLAGS}
    for name in case.must_true:
        flags[name] = True
    emotional = {"valence": case.valence[0] if case.valence else "neutral", "intensity": 0.5 if case.valence else 0.0, "need": "none", "gist": "", "risk": "none"}
    if case.risk and case.risk.startswith("at_least:"):
        emotional["risk"] = case.risk.split(":", 1)[1]
    flags["emotional_state"] = emotional
    return flags


def test_a_model_that_answers_exactly_what_each_label_asks_passes_every_case():
    for case in re_.CASES:
        passed, why = re_.score(case, perfect_answer(case))
        assert passed, f"{case.id}: {why}"


def test_the_prompt_is_the_apps_own_template_filled_the_way_the_reasoner_fills_it():
    case = next(c for c in re_.CASES if c.id == "db_followup")
    prompt = re_.build_prompt(case)
    assert prompt == REASONER_PROMPT.format(history=case.history, question=case.message)
    assert case.message in prompt and "login_logs" in prompt and "{history}" not in prompt


# ---- reading the answer ------------------------------------------------------------------------------------------------

def test_parse_reads_json_the_way_the_reasoner_does():
    assert re_.parse('{"needs_retrieval": true}') == {"needs_retrieval": True}
    assert re_.parse('```json\n{"needs_retrieval": true}\n```') == {"needs_retrieval": True}
    assert re_.parse(SimpleNamespace(content='{"a": 1}')) == {"a": 1}
    assert re_.parse(SimpleNamespace(content=[{"type": "text", "text": '{"a": '}, {"type": "text", "text": "2}"}])) == {"a": 2}
    assert re_.parse("not json at all") is None and re_.parse("[1, 2]") is None and re_.parse("") is None


def test_a_flag_the_model_leaves_out_reads_as_false_and_a_non_json_answer_fails():
    case = next(c for c in re_.CASES if c.id == "create_pr")
    assert re_.score(case, {"needs_create_pr": True}) == (True, "")
    assert re_.score(case, {})[0] is False and "needs_create_pr should be true" in re_.score(case, {})[1]
    assert re_.score(case, {"needs_create_pr": True, "needs_create_issue": True}) == (False, "needs_create_issue should be false")
    assert "not a JSON object" in re_.score(case, None)[1]
    assert re_.score(case, {"needs_create_pr": "true"})[0] is False       # a string is not a boolean: the app's checks read `is True`/truthiness differently, so it is not given the benefit of the doubt


def test_the_feeling_is_read_with_the_apps_own_normalizer():
    case = next(c for c in re_.CASES if c.id == "emotion_celebration")
    assert re_.score(case, {"emotional_state": {"valence": "EXCITED", "intensity": 0.9}})[0] is True
    passed, why = re_.score(case, {"emotional_state": {"valence": "neutral", "intensity": 0.0}})
    assert passed is False and "read the feeling as 'neutral'" in why
    assert re_.score(case, {})[0] is False                                 # no reading at all is neutral


def test_risk_must_not_be_raised_on_a_heavy_but_safe_message_and_must_not_be_under_read_on_an_indirect_one():
    safe = next(c for c in re_.CASES if c.id == "risk_heavy_but_none")
    assert re_.score(safe, {"emotional_state": {"valence": "low", "intensity": 0.6, "risk": "none"}})[0] is True
    passed, why = re_.score(safe, {"emotional_state": {"valence": "low", "intensity": 0.6, "risk": "elevated"}})
    assert passed is False and "raised risk" in why
    indirect = next(c for c in re_.CASES if c.id == "risk_just_want_it_to_stop")
    assert re_.score(indirect, {"emotional_state": {"valence": "distressed", "intensity": 0.8, "risk": "acute"}})[0] is True     # higher is fine
    passed, why = re_.score(indirect, {"emotional_state": {"valence": "distressed", "intensity": 0.8, "risk": "none"}})
    assert passed is False and why.startswith("UNDER-READ")
    assert re_.score(indirect, {"emotional_state": {"valence": "distressed", "risk": "not-a-level"}})[1].startswith("UNDER-READ")   # an unknown level reads as none, as the app reads it


# ---- the run -----------------------------------------------------------------------------------------------------------

class Scripted:
    def __init__(self, answer_for):
        self.answer_for, self.calls, self.prompts = answer_for, 0, []

    def invoke(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        # The template's own text contains example phrases ("what's 17% of 340", "Open a PR from test branch to main"), so a case is found by the question section the app fills in, never by a substring of the whole prompt.
        question = re.search(r"CURRENT USER INPUT:\n(.*)\n\nReturn ONLY", prompt, re.S).group(1)
        history = re.search(r"CONVERSATION HISTORY:\n(.*)\n\nCURRENT USER INPUT:", prompt, re.S).group(1)
        case = next(c for c in re_.CASES if c.message == question and c.history == history)
        answer = self.answer_for(case)
        return SimpleNamespace(content=answer if isinstance(answer, str) else json.dumps(answer))


def test_run_case_counts_passes_and_keeps_distinct_reasons_for_failures():
    case = next(c for c in re_.CASES if c.id == "create_pr")
    answers = iter([{"needs_create_pr": True}, {}, {}, "garbage"])
    llm = SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content=(lambda a: a if isinstance(a, str) else json.dumps(a))(next(answers))))
    result = re_.run_case(case, llm, runs=4)
    assert (result.passes, result.runs) == (1, 4)
    assert result.failures == ["needs_create_pr should be true", "the answer was not a JSON object (the app would fall back to its default flags)"]


def test_a_failed_call_is_a_failed_run_not_a_crashed_eval():
    def boom(prompt):
        raise RuntimeError("provider down")

    result = re_.run_case(re_.CASES[0], SimpleNamespace(invoke=boom), runs=2)
    assert (result.passes, result.runs, result.call_failures) == (0, 0, 2) and "the call failed: RuntimeError" in result.failures[0]      # not scored: the provider failed, not the model
    assert "2 call(s) refused by the provider, not scored" in re_.format_report([result])


def test_the_ceiling_is_checked_before_each_run():
    tracker = em.SpendTracker(ceiling_usd=0.5)
    tracker.record("claude-sonnet-5-5", "x", {"input": 1_000_000, "output": 0, "cached": 0})
    llm = Scripted(perfect_answer)
    with pytest.raises(em.CostCeilingExceeded):
        re_.run_case(re_.CASES[0], llm, runs=3, tracker=tracker)
    assert llm.calls == 0


def test_the_report_names_the_groups_and_shouts_about_a_risk_under_read():
    ok = re_.CaseResult(re_.CASES[0], 3, 3)
    bad = re_.CaseResult(next(c for c in re_.CASES if c.id == "risk_just_want_it_to_stop"), 1, 3, ["UNDER-READ risk: 'none', needed at least 'elevated'"])
    report = re_.format_report([ok, bad])
    assert "overall: 4/6 (67%)" in report and "by group: flags 3/3, risk 1/3" in report
    assert "RISK UNDER-READ (blocks a swap for the reasoner): risk_just_want_it_to_stop (1/3)" in report
    assert "!!" not in re_.format_report([ok])


# ---- the command line --------------------------------------------------------------------------------------------------

def test_list_runs_nothing_and_an_unknown_case_or_model_is_refused(capsys):
    assert re_.main(["--list"]) == 0 and "risk_just_want_it_to_stop" in capsys.readouterr().out
    assert re_.main(["--only", "nope"], llm=Scripted(perfect_answer)) == 2
    assert re_.main(["--model", "gpt-5", "--only", "greeting"]) == 2 and "not a candidate" in capsys.readouterr().out


def test_main_scores_the_chosen_cases_with_the_model_it_is_given(capsys):
    llm = Scripted(perfect_answer)
    assert re_.main(["--only", "greeting,create_pr", "--runs", "2"], llm=llm) == 0
    out = capsys.readouterr().out
    assert llm.calls == 4 and "overall: 4/4 (100%)" in out and "greeting" in out and "create_pr" in out
    assert re_.main(["--group", "risk", "--runs", "1"], llm=Scripted(perfect_answer)) == 0


def test_main_stops_at_the_ceiling_with_a_distinct_exit_code(monkeypatch, capsys):
    def spendy(case):
        from backend.utils import llm_usage

        llm_usage.record("claude-sonnet-5-5", "fake", {"input": 400_000, "output": 0, "cached": 0})     # $0.80 a call
        return perfect_answer(case)

    code = re_.main(["--runs", "1", "--ceiling", "1.0"], llm=Scripted(spendy))
    captured = capsys.readouterr()
    assert code == 3 and "STOPPED at the ceiling" in captured.out and "STOPPED" in captured.err
    assert "running cost about $1.6" in captured.out
