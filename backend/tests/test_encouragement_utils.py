import pathlib
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import encouragement_utils as eu

OPEN = dict(source_type="conversational", risk_active=False, valence="frustrated", intensity=0.5, closing=False)


def fact(text, status="achieved", category="project", active=True, updated="2026-09-01"):
    return SimpleNamespace(fact=text, goal_status=status, category=category, active=active, updated_at=updated)


def pick(message, recent=(), facts=(), **overrides):
    return eu.select_encouragement(message, list(recent), lambda: list(facts), **{**OPEN, **overrides})


# ------------------------------------------------------------------- the trigger -----------------------------------

@pytest.mark.parametrize("message", [
    "i'm so bad at this", "i am terrible at regex", "i can't figure this out", "i can't get it to work",
    "nothing works", "nothing i try works", "i'll never get this", "i'll never understand pointers",
    "i'm not cut out for this", "i don't know what i'm doing", "i suck at css", "why am i so slow",
    "i'm in over my head", "i'm such a failure",
])
def test_self_doubt_is_recognized(message):
    assert eu.has_self_doubt(message), message


@pytest.mark.parametrize("message", [
    "how do i read a csv", "can you explain closures", "the test is bad at handling nulls", "i can't find the file",
    "this build keeps failing", "i'm lost on where the config lives", "my function can't do that yet", "",
])
def test_ordinary_messages_do_not_trigger_it(message):
    assert not eu.has_self_doubt(message), message


# ------------------------------------------------------------------- evidence --------------------------------------

def convo(*turns):
    out = []
    for kind, text in turns:
        out.append(HumanMessage(content=text) if kind == "u" else AIMessage(content=text))
    return out


def test_session_evidence_pairs_a_confirmed_fix_with_the_problem_raised_before_it():
    messages = convo(("u", "my login redirect loops forever"), ("a", "Check the cookie domain."), ("u", "that fixed it, thanks"),
                     ("a", "Great."), ("u", "now the signup form is broken and i'm so bad at this"))
    ev = eu.session_evidence(messages)
    assert ev == {"kind": "session", "what": "my login redirect loops forever"}


def test_session_evidence_ignores_the_current_message_and_needs_a_real_confirmation():
    assert eu.session_evidence(convo(("u", "it works now but i'm so bad at this"))) is None
    assert eu.session_evidence(convo(("u", "a problem"), ("a", "try x"), ("u", "still broken"))) is None
    assert eu.session_evidence([]) is None


def test_session_evidence_takes_the_most_recent_resolution():
    messages = convo(("u", "first problem"), ("a", "x"), ("u", "it works now"), ("a", "y"),
                     ("u", "second problem"), ("a", "z"), ("u", "got it working"), ("a", "ok"), ("u", "i'm so bad at this"))
    assert eu.session_evidence(messages)["what"] == "second problem"


def test_record_evidence_needs_an_achieved_goal_or_project_that_is_actually_related():
    facts = [fact("finished migrating the billing service to postgres"), fact("learning guitar", status="active"),
             fact("ran a marathon", category="goal")]
    ev = eu.record_evidence("i'm so bad at this postgres migration", facts)
    assert ev == {"kind": "record", "what": "finished migrating the billing service to postgres"}
    assert eu.record_evidence("i'm so bad at this css layout", facts) is None  # an unrelated achievement is not evidence


def test_record_evidence_skips_inactive_unachieved_and_wrong_category_facts():
    facts = [fact("shipped the deploy pipeline", active=False), fact("shipped the deploy script", status="active"),
             fact("shipped the deploy tooling", category="preference")]
    assert eu.record_evidence("i can't get this deploy script working", facts) is None


def test_the_better_overlap_wins_then_the_newer_fact():
    facts = [fact("deploy", updated="2026-01-01"), fact("deploy script pipeline", updated="2026-01-01"),
             fact("deploy script pipeline refactor", updated="2026-06-01")]
    assert "refactor" in eu.record_evidence("my deploy script pipeline keeps failing", facts)["what"]


# ------------------------------------------------------------------- selection and gates ---------------------------

def test_self_doubt_with_evidence_offers_it_and_without_any_offers_only_the_plain_route():
    with_record = pick("i'm so bad at this postgres migration", facts=[fact("migrated billing to postgres")])
    assert with_record["evidence"][0]["kind"] == "record"
    nothing = pick("i'm so bad at this")
    assert nothing == {"evidence": []}


def test_it_is_silent_when_there_is_no_self_doubt():
    assert pick("how do i read a csv") is None


@pytest.mark.parametrize("overrides", [
    dict(risk_active=True), dict(closing=True), dict(source_type="kb_strict"), dict(source_type="kb_open"),
    dict(valence="distressed", intensity=0.8),
])
def test_it_is_silent_under_a_safety_context_a_closing_message_grounded_answers_and_acute_distress(overrides):
    assert pick("i'm so bad at this", **overrides) is None


def test_mild_distress_and_tool_routes_are_allowed():
    assert pick("i'm so bad at this", valence="distressed", intensity=0.3) is not None
    assert pick("i'm so bad at this", source_type="tool_output") is not None


def test_it_is_rate_limited_to_once_per_twelve_messages():
    offered = AIMessage(content="x", additional_kwargs={eu.ENCOURAGEMENT_MARK: True})
    filler = [HumanMessage(content="a"), AIMessage(content="b")]
    assert pick("i'm so bad at this", [offered] + filler * 2) is None
    assert pick("i'm so bad at this", [offered] + filler * 6) is not None
    assert eu.recently_offered([offered]) and not eu.recently_offered(filler)


def test_the_memory_is_only_read_after_every_cheap_gate_passes():
    calls = []

    def loader():
        calls.append(1)
        return []
    eu.select_encouragement("how do i read a csv", [], loader, **OPEN)
    eu.select_encouragement("i'm so bad at this", [], loader, **{**OPEN, "risk_active": True})
    assert calls == []
    eu.select_encouragement("i'm so bad at this", [], loader, **OPEN)
    assert calls == [1]


def test_a_memory_failure_means_no_record_never_an_error():
    def boom():
        raise RuntimeError("db down")
    assert eu.select_encouragement("i'm so bad at this", [], boom, **OPEN) == {"evidence": []}


# ------------------------------------------------------------------- the prompt block ------------------------------

def test_the_block_names_each_piece_of_evidence_and_the_rules():
    entry = {"evidence": [{"kind": "session", "what": "my login redirect loops"}, {"kind": "record", "what": "migrated billing to postgres"}]}
    block = eu.build_encouragement_block(entry)
    assert "Earlier in this conversation they got through: \"my login redirect loops\"" in block
    assert "they achieved: \"migrated billing to postgres\"" in block
    for rule in ("ONE calm, specific sentence", "no exclamation marks", "never invent any", "believe in them", "skip it"):
        assert rule in block


def test_with_no_evidence_the_block_forbids_any_claim_about_their_past():
    block = eu.build_encouragement_block({"evidence": []})
    assert "NO record to point to" in block and "say nothing about their past" in block
    assert eu.build_encouragement_block(None) == ""


# ------------------------------------------------------------------- how encouragement goes wrong ------------------

WITH_EVIDENCE = {"evidence": [{"kind": "record", "what": "x"}]}
NO_EVIDENCE = {"evidence": []}


@pytest.mark.parametrize("reply,issue", [
    ("You've got this!", "cheers"), ("It's really easy once you see it.", "minimizes"),
    ("You've handled much harder things than this.", "minimizes"), ("I believe in you.", "promises"),
    ("You'll definitely get this working.", "promises"), ("It will all work out.", "promises"),
    ("I'm proud of you.", "promises"), ("Everyone struggles with this.", "minimizes"),
    ("You can definitely handle this one.", "promises"), ("You're absolutely capable of this.", "promises"),
    ("This kind of roadblock happens to everyone who writes deployment code.", "minimizes"),
])
def test_cheering_minimizing_and_promising_are_flagged_with_or_without_evidence(reply, issue):
    for entry in (WITH_EVIDENCE, NO_EVIDENCE):
        assert issue in (eu.encouragement_reply_issue(reply, entry) or ""), (reply, entry)


def test_an_invented_record_is_flagged_only_when_there_was_no_evidence_to_point_at():
    invented = "You've solved problems like this before, and your track record shows it."
    assert "record the person doesn't have" in eu.encouragement_reply_issue(invented, NO_EVIDENCE)
    assert eu.encouragement_reply_issue("You've solved things like this before.", NO_EVIDENCE) is not None
    assert eu.encouragement_reply_issue("You've solved problems like this before.", NO_EVIDENCE) is not None
    assert eu.encouragement_reply_issue("You've fixed bugs like that before.", NO_EVIDENCE) is not None
    assert eu.encouragement_reply_issue("You've solved problems like this before.", WITH_EVIDENCE) is None  # allowed when there is a record
    assert eu.encouragement_reply_issue("Last time, you figured out the redirect loop, so the same method applies.", WITH_EVIDENCE) is None


@pytest.mark.parametrize("reply", [
    "That sounds frustrating after hours on it. You migrated the billing service to postgres last month, so you know how to work through a stubborn one.",
    "Intermittent deploy failures are common with scripts like this. Start by checking the exit code of the first failing step.",
    "Let's go step by step. What does the log say at the first error?",
    "You tracked down that redirect loop earlier, so the same approach of isolating the cookie domain may apply here.",
    "Deploy scripts like this are a common source of frustration, and they tend to fail at permissions or environment variables.",
])
def test_calm_specific_encouragement_passes(reply):
    assert eu.encouragement_reply_issue(reply, WITH_EVIDENCE) is None


def test_the_follow_up_tag_is_ignored():
    assert eu.encouragement_reply_issue("Let's look at the log.\n<<<FOLLOW_UP: you've got this!>>>", WITH_EVIDENCE) is None


def test_the_revision_prompt_keeps_the_original_and_never_mentions_itself():
    prompt, reason = eu.build_encouragement_revision_prompt("ORIGINAL", "You've got this!", "cheers them on instead of staying calm")
    assert prompt.startswith("ORIGINAL") and "You've got this!" in prompt and "never mention this note" in prompt
    assert "cheers them on" in reason and "no 'I believe in you'" in reason


# ------------------------------------------------------------------- the app ---------------------------------------

def test_the_app_selects_offers_marks_checks_and_counts_encouragement():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    for needle in (
        "encouragement_entry = await asyncio.to_thread(",
        "select_encouragement,",
        "lambda: load_user_facts(username)",
        "risk_active=risk_level != RISK_NONE",
        "encouragement_context=build_encouragement_block(encouragement_entry)",
        "ai_message.additional_kwargs[ENCOURAGEMENT_MARK] = True",
        'tally(turn_counts, "encouragement_offered")',
        'revision_tag = "encouragement"',
        "if encouragement_entry and not safety_plan.active else None",
    ):
        assert needle in source, needle


def test_the_counters_know_the_new_kind_and_key():
    from backend.utils import self_counters as sc
    assert "encouragement_offered" in sc.KEYS and "revised_encouragement" in sc.KEYS
    assert sc.revision_kind("encouragement") == "encouragement"
