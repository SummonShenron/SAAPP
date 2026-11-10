"""A follow-up that carries on a change request Sonic has not finished is an edit turn (backend/utils/agent_utils.py continues_unfinished_edit).

Why (2026-11-08, a real thread; the text below is made up): after "ran out of steps" and a question about which version, three follow-ups in a row were read as plain messages, so the turn was never offered
propose_local_edits (the native loop offers it only on an edit turn), was not nudged to propose after reading, and spent its steps reading again. The rule is narrow on purpose: it must not turn "I just feel so
empty" or "what did you change?" into an edit."""
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import agent_utils as au

ASK = HumanMessage(content="add a dark mode toggle to the settings page")
LEFT_UNFINISHED = AIMessage(content="Nothing has changed yet, because my search ran out of steps before I could propose an edit. Send the request again and name the file.")
ASKED = AIMessage(content="Before I propose anything: should the toggle go in the header or on the settings page <<<FOLLOW_UP: In the header>>>?")


def thread(*pairs):
    messages = []
    for human, ai in pairs:
        messages.append(HumanMessage(content=human) if isinstance(human, str) else human)
        if ai is not None:
            messages.append(ai)
    return messages


def edit_turn(text, *earlier):
    return au.looks_like_edit_request(thread(*earlier) + [HumanMessage(content=text)])


def test_the_request_itself_is_an_edit_and_a_plain_chat_message_is_not():
    assert au.looks_like_edit_request([ASK]) is True
    assert au.looks_like_edit_request([HumanMessage(content="i'm having a rough day")]) is False


def test_getting_on_with_it_after_a_reply_that_ran_out_of_steps_continues_the_request():
    earlier = [(ASK, LEFT_UNFINISHED)]
    assert edit_turn("go find the files", *earlier) is True
    assert edit_turn("come on just find the files", *earlier) is True
    assert edit_turn("so find it!", *earlier) is True
    assert edit_turn("just make the change in settings.tsx", *earlier) is True


def test_answering_the_question_it_asked_about_the_change_continues_the_request():
    earlier = [(ASK, ASKED)]
    assert edit_turn("in the header like the other toggles", *earlier) is True          # no verb in it, but it shares "header" with the question
    assert edit_turn("settings page", *earlier) is True


def test_a_question_a_feeling_or_another_topic_is_never_a_continuation():
    earlier = [(ASK, LEFT_UNFINISHED)]
    assert edit_turn("what did you just change?", *earlier) is False                      # a question about the past
    assert edit_turn("can you find it?", *earlier) is False                              # question form: left to the existing confirmation rules
    assert edit_turn("i'm so sad about everything today", *earlier) is False             # no task word, and nothing was asked
    assert edit_turn("thanks", *earlier) is False
    asked = [(ASK, ASKED)]
    assert edit_turn("i just feel so empty tonight", *asked) is False                    # it asked, but this answers nothing it asked and has no task word
    for feeling in ("i don't want to do this anymore", "i can't keep going like this", "i just need to stop thinking about her", "i want to be left alone"):
        assert edit_turn(feeling, *earlier) is False, feeling                            # "want", "do", "keep", "just" and "need" are feelings' words too


def test_it_needs_an_open_explicit_request_and_a_reply_that_left_it_unfinished_or_asked():
    assert edit_turn("go find the files", ("good morning", AIMessage(content="Good morning!"))) is False                      # nothing was ever asked to change
    finished = [(ASK, AIMessage(content="Done: I applied the change to the settings page."))]
    assert edit_turn("it should be in the header", *finished) is False                                                       # the reply finished the work
    old = [(ASK, LEFT_UNFINISHED)] + [(f"message {i}", AIMessage(content="ok")) for i in range(au.CONTINUATION_LOOKBACK + 1)] + [("something else", LEFT_UNFINISHED)]
    assert edit_turn("go find the files", *old) is False                                                                     # the request is too far back to be the one being continued
    long = "go find the files " + "and then keep looking at it " * 6
    assert len(long.split()) > au.CONTINUATION_MAX_WORDS and edit_turn(long.strip(), (ASK, LEFT_UNFINISHED)) is False       # a long message is its own request


def test_the_scrubbed_denial_placeholder_counts_as_unfinished_and_the_follow_up_tag_is_ignored():
    earlier = [(ASK, AIMessage(content=au._SCRUBBED_DENIAL_PLACEHOLDER))]
    assert edit_turn("go find the files", *earlier) is True
    tagged = [(ASK, AIMessage(content="Here is what I found. <<<FOLLOW_UP: Apply it?>>>"))]
    assert edit_turn("go find the files", *tagged) is False                                  # a follow-up suggestion is not a question Sonic asked; with nothing unfinished it is not a continuation


def test_the_agent_decides_an_edit_turn_from_this_detector_so_the_proposal_tool_is_offered_on_a_continuation():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "looks_like_edit_request(state.get(\"messages\", []))" in source and "can_apply_here" in source
    loop = (Path(__file__).resolve().parents[1] / "services" / "native_loop.py").read_text(encoding="utf-8")
    assert "can_propose = bool(required_action)" in loop                                     # the proposal tool is part of the choice only on an edit turn: why a continuation must be one


# ---- a bare yes to an offer to carry on (2026-11-09, Jack's real run: the wording below is made up) ------------------------------------------------------------------------------------------

OFFER_TO_READ = AIMessage(content="The backend side is in. Whether the page shows it is still open: I haven't read the dashboard file. Should I read dashboard.tsx next to see how the page picks up the rates?")


def test_a_bare_yes_to_a_question_about_carrying_on_with_the_change_continues_it():
    for yes in ("yes", "yes please", "yep", "go on", "sure, keep going", "ok"):
        assert edit_turn(yes, (ASK, OFFER_TO_READ)) is True, yes


def test_a_bare_yes_continues_nothing_without_an_open_request_a_question_about_the_change_or_a_short_enough_message():
    assert edit_turn("yes", ("add a dark mode toggle to the settings page", LEFT_UNFINISHED)) is False         # the reply left it unfinished but asked nothing: a yes answers nothing
    assert edit_turn("yes", ("i'm having a rough day", AIMessage(content="That sounds heavy. Do you want to tell me what happened with the code review today?"))) is False   # no open change request
    assert edit_turn("yes", ("add a dark mode toggle to the settings page", AIMessage(content="Are you doing alright?"))) is False                  # a question, but not about the change
    assert edit_turn("yes i think so but let me look at it properly first and get back to you later", (ASK, OFFER_TO_READ)) is False             # not a bare yes


def test_a_bare_no_or_a_question_back_is_still_not_a_continuation():
    assert edit_turn("no", (ASK, OFFER_TO_READ)) is False
    assert edit_turn("yes?", (ASK, OFFER_TO_READ)) is False


# ---- finishing the task, and answering Sonic's own options in words (2026-11-10, the Office Hours run: the wording is made up) --------------------------------------------------------------------------------------

UNFINISHED_STATEMENT = AIMessage(content="I've laid the foundation, but it's not fully wired into the live flow yet.\nWhat needs to be finished:\n1. Pass coverage to the prompt context.\n\nI'll read build_context_for_session next.")
OPTIONS = AIMessage(content="The open piece is the trigger itself:\n1. Match on reply: when Sonic asks agenda item N and you reply, mark it covered. Simple and cheap.\n2. Judge the turn: run a lightweight model call after each turn. More flexible.\n\nWhich approach do you want to go with?")
REQUEST = "make the meeting keep track of which agenda items are covered, with tests"


def test_finish_it_after_a_reply_that_says_what_is_not_wired_or_promises_the_next_step_continues_the_change():
    for follow in ("okay finish the task please", "please finish wiring it up", "finish it", "go on and complete the rest", "hook up the trigger"):
        assert edit_turn(follow, (REQUEST, UNFINISHED_STATEMENT)) is True, follow


def test_an_answer_in_words_to_a_question_that_lists_options_continues_the_change_even_when_it_names_no_file():
    assert edit_turn("try match on reply", (REQUEST, UNFINISHED_STATEMENT), ("okay finish the task please", OPTIONS)) is True
    assert edit_turn("judge the turn", (REQUEST, OPTIONS)) is True                                    # it names an option
    assert edit_turn("the second one", (REQUEST, OPTIONS)) is False                                  # no shared word and no task word: an ordinal is not guessed at


def test_a_feeling_or_a_question_after_such_a_reply_is_still_not_an_edit_request():
    for follow in ("honestly i am just so tired today", "what did you change so far?", "thanks, that makes sense", "i can't keep going like this"):
        assert edit_turn(follow, (REQUEST, UNFINISHED_STATEMENT)) is False, follow
        assert edit_turn(follow, (REQUEST, OPTIONS)) is False, follow
    assert edit_turn("okay finish the task please", ("how was your day", AIMessage(content="Pretty quiet. I'll look into that book next."))) is False      # no open change request
