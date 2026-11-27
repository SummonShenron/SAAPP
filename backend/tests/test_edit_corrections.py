"""Corrections become edit turns, and a coding turn does not stop with the request half done (2026-11-27, Jack's loader session).

WHY. Turning the loader's bounce into a double helix took 17 messages and was never finished. Replaying Sonic's own detector on that session: 9 of the 17 messages were not edit turns, mostly corrections right after a
coding reply ("ugh no the logo should not bounce...", "shoot no, the animations dont transition, they just pop in..."). A turn that is not an edit turn is never required to propose and never sent back, so it read,
described a plan and asked. On the edit turns, after the first file of a two-file change was applied, three things told it to stop: the APPLIED result ("or finish with a short answer"), the checkpoint ("Prefer
answering", and an answer there skipped every check), and a hand-back rule that sent an "it still isn't wired" answer back only once. The last reply then said "this reply didn't write any files" about an edit applied
in that very turn. The messages below are shaped like the real ones; no model, no database."""
import asyncio
import pathlib

from langchain_core.messages import AIMessage, HumanMessage

from backend.tests.test_native_loop import EDIT_TOOLS, REQUIRED, Acts, ToolLLM, answers, asks, call, go
from backend.utils import agent_utils as au
from backend.utils import claim_check, handback_check as hb, self_counters
from backend.utils.code_outcomes import CODE_MARK

ROOT = pathlib.Path(__file__).resolve().parents[2]

REQUEST = HumanMessage(content="replace the bounce in the loader animation with a double helix, and update loader-preview.html so I can check it first")


def coding_reply(text="The helix is in HelixLogo.tsx and the preview shows it. Both checks passed.", marked=True):
    return AIMessage(content=text, additional_kwargs={CODE_MARK: True} if marked else {})


def edit_turn(text, *history):
    return au.looks_like_edit_request([*history, HumanMessage(content=text)])


# ---- corrections after coding work are edit requests --------------------------------------------------------------------------------------------

CORRECTIONS = (
    "ugh no the logo should not bounce it should just show the comet pulse and then switch to the helix",
    "shoot no, the animations dont transition, they just pop in, it needs to morph into the helix",
    "okay so close but there should only be 6 nodes in total just like the logo has, and the same color as the other animations",
    "okay perfect now lets implement it for real please.",
    "no that didnt work at all... it shouldnt fade into the next animation, it should reconfigure itself like every other step",
)


def test_a_correction_right_after_a_coding_reply_is_an_edit_request():
    for text in CORRECTIONS:
        assert edit_turn(text, REQUEST, coding_reply()) is True, text


def test_before_this_those_corrections_were_plain_messages():
    """The same messages without the correction rule: what the session really got."""
    original = au.corrects_recent_change
    try:
        au.corrects_recent_change = lambda *a, **k: False
        missed = [text for text in CORRECTIONS if not edit_turn(text, REQUEST, coding_reply())]
    finally:
        au.corrects_recent_change = original
    assert len(missed) >= 4


def test_a_long_correction_is_not_cut_off_by_the_forty_word_limit():
    long = ("okay so close but there should only be a total of 6 nodes in total just like the logo has and 2 comets should spawn, one for each helix and they should pulse at the same time, "
            "it also needs to be the same color as the other animations so the transition is smooth")
    assert len(long.split()) > 40 and edit_turn(long, REQUEST, coding_reply()) is True


def test_the_work_stays_the_topic_across_one_reply_that_is_not_about_it():
    stopped = AIMessage(content="_[Stopped by user before responding]_")
    assert edit_turn("shoot no, the animations dont transition, they just pop in", REQUEST, coding_reply(), HumanMessage(content="lets see"), stopped) is True
    chat = AIMessage(content="Fingers crossed! Let me know how it looks.")
    assert edit_turn("no that didnt work at all, it shouldnt fade into the next animation", REQUEST, coding_reply(), HumanMessage(content="hopefully it works this time!"), chat) is True
    old = [coding_reply(), HumanMessage(content="cool"), AIMessage(content="Nice."), HumanMessage(content="anyway"), AIMessage(content="Sure.")]
    assert edit_turn("it should not bounce", REQUEST, *old) is False                                    # two replies with nothing to do with the work: it is no longer the topic


def test_an_unmarked_transcript_counts_a_reply_that_names_a_file_and_what_was_done_to_it():
    plain = AIMessage(content="The edit to local/src/components/ThinkingLogo.tsx was applied, and tsc exits 0.")
    assert edit_turn("ugh no the logo should not bounce", REQUEST, plain) is True
    assert edit_turn("ugh no the logo should not bounce", REQUEST, AIMessage(content="The logo is a nice touch, I like it.")) is False     # no file, nothing done
    assert edit_turn("ugh no the logo should not bounce", HumanMessage(content="how are you"), plain) is False                              # and no change was asked for


def test_what_is_not_a_correction_stays_a_plain_message():
    history = (REQUEST, coding_reply())
    for text in (
        "hopefully it works this time!",                                     # directs nothing
        "why does it pop in like that?",                                     # a question
        "ugh i should go to bed",                                            # about their life, not the work
        "ugh well she canceled so not seeing her today",                     # a person, a feeling
        "im overwhelmed i need to go grocery shopping",
        "no thats okay, we dont want to overhaul it",                         # turning it down
        "no dont look in this repo silly its private",
        "its not working but im gonna have claude fix it instead",           # handing it to someone else
        "this is just a hypothetical, we should build it some day",
        "YES you crushed it with the new animations, you should be so proud", # praise
        "PERFECT, those ui changes will go a long way in making you look professional",
        "thanks",
    ):
        assert edit_turn(text, *history) is False, text


def test_each_guard_on_its_own_keeps_a_message_out():
    """Each of these passes every other test, so only the one guard named stops it (the mutation check found the list above did not show that)."""
    history = (REQUEST, coding_reply("The helix animation in HelixLogo.tsx is applied and the preview shows it."))
    assert edit_turn("ugh im so stressed, the helix should be done before my interview", *history) is False       # about their life (_PERSONAL_RE)
    assert edit_turn("perfect, the helix animation looks great, you should be happy", *history) is False          # praise with nothing left to do
    assert edit_turn("the helix should morph, why does it pop in?", *history) is False                           # a question
    assert edit_turn("ugh i should eat something first", *history) is False                                      # nothing about the work
    assert edit_turn("the helix should morph instead of popping in", *history) is True                             # and the same shape, about the work, is one


def test_an_unmarked_reply_needs_both_a_file_and_what_was_done_to_it_and_an_earlier_request():
    done = AIMessage(content="The edit to local/src/components/ThinkingLogo.tsx was applied: the logo no longer jumps.")
    assert edit_turn("ugh no the logo should not bounce", REQUEST, done) is True
    assert edit_turn("ugh no the logo should not bounce", REQUEST, AIMessage(content="ThinkingLogo.tsx draws the logo, which is a nice touch.")) is False    # a file, nothing done to it
    assert edit_turn("ugh no the logo should not bounce", HumanMessage(content="what draws the logo"), done) is False                                     # no change was asked for


def test_praise_with_something_still_to_do_is_a_request():
    assert edit_turn("yay its working. we need to address the new node sizes though, they dont align with the lines", REQUEST, coding_reply()) is True


def test_a_go_ahead_in_words_answers_an_unfinished_reply_that_asked_about_the_change():
    asked = AIMessage(content="I haven't made that edit yet. My next step is to propose a change to the stage-1 motion in thinkingLogoMotion.ts. Should the logo stay fully still?")
    for go_ahead in ("okay lets see if it works", "go for it", "try it", "lets do it"):
        assert edit_turn(go_ahead, REQUEST, asked) is True, go_ahead


def test_the_correction_is_counted_where_the_edit_turn_is_decided():
    assert {"edit_turn_correction", "claim_denial_dropped"} <= set(self_counters.KEYS)
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    start = source.index("        if edit_turn:\n            logger.info(\"[tool_agent_node] edit request")
    assert "is_correction(state.get(\"messages\", []))" in source[start:start + 600] and '"edit_turn_correction": 1' in source[start:start + 900]
    assert au.is_correction([REQUEST, coding_reply(), HumanMessage(content=CORRECTIONS[0])]) is True
    assert au.is_correction([REQUEST, coding_reply(), HumanMessage(content="hopefully it works this time!")]) is False


# ---- an answer that says the work is not finished is sent back, more than once -----------------------------------------------------------------

REAL_ENDING = ("The three edits to thinkingLogoMotion.ts were applied. The morph still isn't wired into ThinkingLogo.tsx, so the dots never move into a helix. "
               "The next change is to route stage 2 through the shape morph. I can't make that edit from this reply, so it needs a new request.")


def test_the_real_ending_reads_as_unfinished():
    assert hb.kind(REAL_ENDING, coding_turn=True, edited=True, offered={"propose_local_edits"}, steps_left=13) == "unfinished"
    assert hb.says_unfinished("It needs a new request.") and hb.says_unfinished("My next step is to propose the change.")
    assert not hb.says_unfinished("The fix is in thinkingLogoMotion.ts and tsc passes. In this turn I changed one file.")      # a finished answer is not caught


def test_unfinished_and_offer_may_repeat_up_to_the_limit_and_the_others_stay_at_one():
    assert hb.MAX_REPEATS == 3
    assert hb.may_send_back({}, "unfinished") and hb.may_send_back({"handback_unfinished": 2}, "unfinished")
    assert not hb.may_send_back({"handback_unfinished": 2, "handback_offer": 1}, "offer")
    assert hb.may_send_back({}, "verify") and not hb.may_send_back({"handback_unfinished": 1}, "verify")
    assert hb.repeat_note(0) == "" and "send-back number 3" in hb.repeat_note(2) and "no need for a new request" in hb.repeat_note(1)


APPLIED = {"propose_local_edits": "Proposed 1 edit(s) across 1 file(s). APPLIED: the person applied the edits, so those files now contain them."}


def test_after_an_applied_edit_the_checkpoint_does_not_say_prefer_answering_and_an_unfinished_answer_there_is_sent_back():
    reads = [asks(call("read_repo_file", {"path": f"f{n}.ts"}, id=f"r{n}")) for n in range(3)]
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "helix shape", "edits": []}, id="p1")), *reads, answers(REAL_ENDING),
                  asks(call("propose_local_edits", {"summary": "wire the morph", "edits": []}, id="p2")), answers("Both files are changed: the dots morph into the helix and then the circle."))
    act = Acts(APPLIED)
    result = go(llm, act, tools=EDIT_TOOLS, required_action=REQUIRED, max_steps=12, question="make the logo morph into the helix")
    assert result["final_answer"].startswith("Both files are changed")
    assert [d["tool_action"] for d in act.decisions].count("propose_local_edits") == 2
    sent = " ".join(str(m.content) for c in llm.calls for m in c[0] if getattr(m, "type", "") == "human")
    assert "a change has been applied" in sent and "no need for a new request" in sent
    checkpoint_question = [str(m.content) for c in llm.calls for m in c[0] if str(getattr(m, "content", "")).startswith("CHECKPOINT: you have used")]
    assert checkpoint_question and all("Prefer answering" not in q for q in checkpoint_question)


def test_a_turn_with_nothing_left_still_answers_at_the_checkpoint():
    reads = [asks(call("read_repo_file", {"path": f"f{n}.ts"}, id=f"r{n}")) for n in range(3)]
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []}, id="p1")), *reads, answers("Done: the helix replaces the bounce, and tsc passes."))
    result = go(llm, Acts(APPLIED), tools=EDIT_TOOLS, required_action=REQUIRED, max_steps=12)
    assert result["final_answer"] == "Done: the helix replaces the bounce, and tsc passes."


def test_the_applied_result_no_longer_invites_a_short_answer_with_work_left():
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "\"change, or finish with a short answer on what changed.\"" not in source
    assert "you can make more edits in this same turn" in source and "Answer only when the whole request is done" in source


def test_the_folder_note_says_trace_symbol_works_on_the_folder():
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "trace_symbol are not offered" not in source and "trace_symbol works on the" in source


# ---- a reply that denies writing on a turn where an edit was applied -----------------------------------------------------------------------------

REAL_FINAL = ("The edit is the one described in the change summary: the six dots move onto the double helix, then reconfigure into the circle. But this reply didn't write any files. "
              "My tools only read and search, so I can't confirm the change is on disk.\n\nThe tests and `npx tsc -b` haven't been run.")


def test_the_false_denial_is_taken_out_and_the_rest_is_kept():
    text, dropped = claim_check.drop_write_denials(REAL_FINAL)
    assert dropped == 2 and "didn't write any files" not in text and "only read and search" not in text
    assert text.startswith("The edit is the one described") and text.endswith("haven't been run.")
    assert claim_check.drop_write_denials("I changed ThinkingLogo.tsx. I haven't run the tests.") == ("I changed ThinkingLogo.tsx. I haven't run the tests.", 0)
    assert claim_check.drop_write_denials("It needs a new request to finish.")[1] == 1


def test_app_takes_it_out_only_when_an_edit_was_applied_this_turn():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    at = source.index("claim_check.drop_write_denials(full_response)")
    assert 'int((getattr(llm_turn, "rhythm", None) or {}).get("applies", 0) or 0) > 0' in source[at - 400:at]
    assert 'tally(turn_counts, "claim_denial_dropped")' in source[at:at + 300]
    assert source.find("'event': 'final_generation'", at) > at                                 # before the final text is sent, so the screen shows it too
