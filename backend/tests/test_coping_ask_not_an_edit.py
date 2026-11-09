"""A request for help with a feeling that happens to say "some code" is not a change request (backend/utils/agent_utils.py _COPING_ASK_RE).

Why (2026-11-08, a real thread; the wording below is made up): right after "it just breaks my heart", "can you help me add some code to cope?" has an edit verb and the bare word "code", so it was read as an edit request:
the turn searched the repo for four steps, asked a stronger model, and answered "nothing has changed". The rule is narrow: a personal coping ask AND nothing code-shaped but a bare word. Real change requests must keep working."""
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import agent_utils as au


def edit(text, *earlier):
    messages = []
    for human, ai in earlier:
        messages += [HumanMessage(content=human), AIMessage(content=ai)]
    return au.looks_like_edit_request(messages + [HumanMessage(content=text)])


HURT = ("it just hurts, i feel like i tried my best and it didn't matter", "I hear you. That is a hard thing to carry.")


def test_a_coping_ask_that_only_says_code_is_not_an_edit_request_after_a_hurt_message():
    for text in (
        "can you help me add some code to cope?",
        "can you write some code to help me cope",
        "add some code so I can feel better",
        "make a script to help me get through tonight",
        "can you add some code to help me deal with this?",
        "create an app so i can calm down",
    ):
        assert edit(text, HURT) is False, text
        assert edit(text) is False, text                                   # the wording alone is enough: it never depended on the earlier message


def test_real_change_requests_still_are_edit_requests_including_ones_that_say_cope():
    for text in (
        "add a retry to the upload code so it can cope with timeouts",
        "add some code to cope with network errors",
        "change the header text in Chat.tsx",
        "add a journal page so I can cope",                               # a named target: a page
        "add a calm-down button to the sidebar so i can cope",
        "add some code to the login function",
        "fix the code in app.py, i can't cope with this bug",              # a file name: a real change even in a bad mood
    ):
        assert edit(text) is True, text


def test_a_bare_code_request_with_no_coping_in_it_is_still_an_edit_request():
    assert edit("add some code to log the request time") is True
    assert edit("write some code to parse the dates") is True


def test_a_coping_ask_does_not_count_as_the_open_change_request_a_later_short_message_continues():
    ask = "can you help me add some code to cope?"
    reply = "Nothing has changed yet, because my search ran out of steps. Name the file."
    assert au.continues_unfinished_edit("just find the files", reply, [HumanMessage(content=ask), HumanMessage(content="just find the files")]) is False
    assert au.continues_unfinished_edit("just find the files", reply, [HumanMessage(content="add a dark mode toggle to the settings page"), HumanMessage(content="just find the files")]) is True


def test_the_bare_word_check_only_passes_when_nothing_is_named():
    assert au._only_bare_code_target("add some code to cope") is True
    assert au._only_bare_code_target("add some code to the sidebar") is False
    assert au._only_bare_code_target("add some code to app.py") is False
    assert au._only_bare_code_target("feel better") is False                # nothing code-shaped at all is not "only bare code"
