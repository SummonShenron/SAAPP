"""Listening mode (2026-11-01): the support ladder used to loop. When every group was answered "no one" it kept printing the ladder, and when the person talked about everything but who they
could reach it asked the same question again. Now the questions end (every group answered, or two messages in a row that skipped it) and the reply just listens, with one offer of something to do
drawn from what they are into, made only after they have been heard."""
import datetime
from datetime import timezone
from types import SimpleNamespace

from backend.utils import safety_utils as su

T0 = datetime.datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
ALL_NO = {key: su.UNAVAILABLE for key in ("immediate_family", "friends", "extended_family", "professional")}
HEARD_988 = "Earlier I said you can call or text 988 any time."


def at(minutes):
    return T0 + datetime.timedelta(minutes=minutes)


def _skipped(level=su.RISK_ELEVATED, messages=2):
    """The state after the opening message, the answer to the safety question, and `messages` that did not answer the ladder."""
    state = su.merge_safety_state(None, level, at(0))
    state = su.merge_safety_state(state, level, at(1), from_model=True)             # answers the question about their safety: not a skip
    for step in range(messages):
        state = su.merge_safety_state(state, level, at(2 + step), from_model=True)
    return state


def _exhausted(level=su.RISK_ELEVATED):
    state = su.merge_safety_state(None, level, at(0), support_updates=ALL_NO)
    return su.merge_safety_state(state, level, at(1), from_model=True)


# ---- counting the skips ---------------------------------------------------------------------------------------------------------------------------

def test_the_answer_to_the_opening_safety_question_is_not_a_skip():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0))
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(1), from_model=True)
    assert "passed" not in state


def test_two_messages_that_do_not_answer_the_ladder_end_its_questions():
    one, two = _skipped(messages=1), _skipped(messages=2)
    assert one["passed"] == 1 and not su.ladder_status(one)["listening"]
    assert two["passed"] == 2 and su.ladder_status(two)["skipped"] and su.ladder_status(two)["listening"]


def test_answering_a_group_starts_the_count_again():
    state = _skipped(messages=1)
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(9), support_updates={"immediate_family": su.UNAVAILABLE}, from_model=True)
    assert "passed" not in state


def test_a_bare_thank_you_neither_answers_nor_skips():
    state = _skipped(messages=1)
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(9), from_model=True, quiet=True)
    assert state["passed"] == 1


def test_a_higher_level_is_a_new_disclosure_and_starts_the_count_again():
    state = su.merge_safety_state(_skipped(messages=2), su.RISK_ACUTE, at(9))
    assert "passed" not in state and not su.ladder_status(state)["listening"]


def test_once_they_name_someone_the_job_is_reaching_them_not_listening():
    state = su.merge_safety_state(_skipped(messages=2), su.RISK_ELEVATED, at(9), support_updates={"friends": su.AVAILABLE}, from_model=True)
    info = su.ladder_status(state)
    assert info["available"] == "friends" and not info["listening"]


def test_every_group_answered_no_is_listening_too():
    info = su.ladder_status(_exhausted())
    assert info["exhausted"] and info["listening"]


# ---- the reply ---------------------------------------------------------------------------------------------------------------------------------

def test_once_the_line_has_been_given_an_exhausted_ladder_listens_instead_of_printing_itself_again():
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, _exhausted(), HEARD_988, "i just keep replaying it")
    assert plan.listening and not plan.line_required
    assert "THEIR PEOPLE" not in plan.context and "NEXT STEP" not in plan.context
    assert "Do NOT ask about their family, friends" in plan.context and "already pointed them to a human line" in plan.context


def test_an_exhausted_ladder_hands_off_to_a_human_line_first_only_for_stated_intent():
    plan = su.plan_safety_turn(su.RISK_ACUTE, False, _exhausted(su.RISK_ACUTE), "", "no. there really is nobody")
    assert not plan.listening and plan.line_required and "moment for a human line" in plan.context


def test_at_the_lowest_level_an_exhausted_ladder_listens_and_never_offers_a_line_unasked():
    """2026-11-01: a heartbroken, exhausted person who had not said they would hurt themselves was handed 988 and 911 the moment the ladder ran out."""
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, _exhausted(), "", "i am so tired of carrying this")
    assert plan.listening and not plan.line_allowed and not plan.line_required
    assert "Do NOT mention any crisis line" in plan.context
    assert plan.reply_issue("I'm so sorry. You can call or text 988 right now, or 911.") is not None
    assert not plan.needs_resource_line("I'm here, and I'm listening.")


def test_at_the_lowest_level_the_number_is_still_given_when_they_ask_for_it():
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, _exhausted(), "", "can you give me the number for 988")
    assert plan.listening and plan.line_required and "a trained human can be on the line" in plan.context


def test_a_skipped_ladder_at_the_lowest_level_listens_and_names_no_line():
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, _skipped(), "", "she just stopped answering and i dont know what i did")
    assert plan.listening and not plan.line_allowed and not plan.line_required
    assert "Do NOT mention any crisis line" in plan.context
    assert plan.reply_issue("I hear how much that hurts. Call 988.") is not None


def test_a_skipped_ladder_at_the_top_level_still_gives_the_line_once_then_listens():
    state = _skipped(su.RISK_ACUTE)
    first = su.plan_safety_turn(su.RISK_ACUTE, False, state, "", "it hurts so much")
    assert first.listening and first.line_required and "a trained human can be on the line" in first.context
    later = su.plan_safety_turn(su.RISK_ACUTE, False, state, HEARD_988, "it hurts so much")
    assert later.listening and not later.line_required and "already pointed them to a human line" in later.context


def test_listening_is_told_to_fix_nothing_and_to_ask_at_most_one_open_question():
    context = su.plan_safety_turn(su.RISK_ELEVATED, False, _skipped(), "", "x").context
    for phrase in ("whole job is to listen", "Take it in before anything else", "Ask at most ONE question", "No advice, no silver linings", "Fuller replies than usual"):
        assert phrase in context, phrase
    assert "Plain, warm prose" in context                                                  # the same format rules as the rest of the safety layer


def test_a_reply_that_goes_back_to_asking_about_their_people_is_sent_back():
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, _skipped(), "", "x")
    for reply in ("That sounds so heavy. Is there someone you could call tonight?", "Do you have a friend nearby?", "Who could you text right now?",
                  "I'm sorry. Is there anyone who could come over?"):
        assert plan.reply_issue(reply), reply
    for reply in ("That sounds so heavy, and it makes sense that it hurts. What is the worst part of it right now?",
                  "You said your sister used to check on you. What was that like?",              # a person THEY brought up is not the ladder
                  "I hear you. Would you want to text Maya, the one you mentioned?"):
        assert plan.reply_issue(reply) is None, reply


def test_the_ladder_check_is_not_applied_while_the_ladder_is_still_running():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0))
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, state, "", "x")
    assert not plan.listening and plan.reply_issue("Is there someone you could call tonight?") is None


# ---- the one offer ---------------------------------------------------------------------------------------------------------------------------

def test_after_they_have_been_heard_one_offer_may_come_from_what_they_are_into():
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, _skipped(), "", "x", interests=["Is building SAAPP, a local-first assistant", "Plays guitar"])
    assert "WHAT THEY ARE INTO" in plan.context and "Is building SAAPP" in plan.context and "Plays guitar" in plan.context
    assert "no \"you should\"" in plan.context and "once" in plan.context


def test_with_nothing_known_the_offer_stays_general():
    plan = su.plan_safety_turn(su.RISK_ELEVATED, False, _skipped(), "", "x", interests=[])
    assert "WHAT THEY ARE INTO" not in plan.context and "don't know yet what they are into" in plan.context


def test_no_offer_on_the_message_that_raised_the_level_or_beside_a_handoff():
    fresh = su.plan_safety_turn(su.RISK_ACUTE, True, su.merge_safety_state(_exhausted(), su.RISK_ACUTE, at(9)), HEARD_988, "x", interests=["Plays guitar"])
    assert fresh.listening and "Plays guitar" not in fresh.context and "directly and plainly" in fresh.context
    handoff = su.plan_safety_turn(su.RISK_ACUTE, False, _skipped(su.RISK_ACUTE), "", "x", interests=["Plays guitar"])
    assert handoff.line_required and "Plays guitar" not in handoff.context


def test_at_the_top_level_an_offer_waits_until_the_line_has_been_given():
    state = _skipped(su.RISK_ACUTE)
    assert "Plays guitar" in su.plan_safety_turn(su.RISK_ACUTE, False, state, HEARD_988, "x", interests=["Plays guitar"]).context


# ---- what counts as something they are into ------------------------------------------------------------------------------------------------

def test_remembered_interests_keep_hobbies_and_projects_and_drop_sensitive_or_style_notes(monkeypatch):
    facts = {
        "preference": [SimpleNamespace(fact="Plays guitar in a band on weekends", active=True), SimpleNamespace(fact="Prefers short, concise answers", active=True),
                       SimpleNamespace(fact="Has been in therapy for anxiety", active=True), SimpleNamespace(fact="Collects vinyl records", active=False)],
        "project": [SimpleNamespace(fact="Is building SAAPP, a local-first assistant", active=True)],
        "goal": [SimpleNamespace(fact="Wants to save money for a car", active=True)],
    }
    import backend.utils.memory_utils as memory_utils

    monkeypatch.setattr(memory_utils, "load_user_facts", lambda username, category=None: facts.get(category, []))
    got = su.remembered_interests("jack")
    assert got == ["Plays guitar in a band on weekends", "Is building SAAPP, a local-first assistant"]
    assert su.remembered_interests(None) == []


def test_remembered_interests_never_raise(monkeypatch):
    import backend.utils.memory_utils as memory_utils

    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(memory_utils, "load_user_facts", boom)
    assert su.remembered_interests("jack") == []


# ---- wired in --------------------------------------------------------------------------------------------------------------------------------

def test_the_app_and_the_workflow_use_them():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    app = (root / "app.py").read_text(encoding="utf-8")
    workflow = (root / "backend/services/agent_workflow.py").read_text(encoding="utf-8")
    assert "interests=await asyncio.to_thread(remembered_interests, username) if risk_level != RISK_NONE else []" in app
    assert "quiet=is_acknowledgement(msg)" in workflow
