"""How a reply prompt is shaped for a Claude reply model (backend/utils/claude_voice.py). Claude only: Gemini's prompt is the same flat string it always was."""
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from backend.components import constraints as c
from backend.models.claude_llm import ClaudeLLM
from backend.utils import claude_voice as cv


def reply_prompt(**kwargs):
    return c.build_voice_prompt(c.GROUNDING_BLOCKS["conversational"], kwargs.pop("data", ""), kwargs.pop("history", "User: hi\nAssistant: hello"), kwargs.pop("question", "can you help me plan my week?"), **kwargs)


def test_a_reply_prompt_splits_into_the_stable_rules_and_this_turn_without_losing_a_word():
    prompt = reply_prompt(emotional_context="\nEMOTIONAL CONTEXT: a check-in is due.\n", time_context="\nIt is Friday.\n")
    system, user = cv.split_prompt(prompt)
    assert system.startswith(c.SONIC_ASSISTANT_PERSONA.strip()[:80]) and system.endswith(c.ACTION_HONESTY.strip())
    assert user.startswith("DATA:") and "EMOTIONAL CONTEXT: a check-in is due." in user and "can you help me plan my week?" in user
    assert "EMOTIONAL CONTEXT" not in system and "CONVERSATION HISTORY" not in system          # what changes every turn stays out of the stable part
    assert (system + "\n" + user).split() == prompt.split()                                      # the same words, in the same order


def test_the_split_is_stable_across_turns_so_the_system_part_can_be_cached():
    first = cv.split_prompt(reply_prompt(question="one thing", history="")) [0]
    second = cv.split_prompt(reply_prompt(question="something else entirely", history="User: x\nAssistant: y", data="web results", emotional_context="\nEMOTIONAL CONTEXT: x\n"))[0]
    assert first == second


def test_a_prompt_that_is_not_a_reply_prompt_is_left_whole():
    for text in ("just a question", "", "DATA:\nsome data", "\nDATA:\nwithout the persona before it", None, ["a", "b"], 42):
        assert cv.split_prompt(text) is None, text


def test_the_claude_wrapper_shapes_a_reply_only_when_asked_and_everything_else_is_one_user_message():
    prompt = reply_prompt()
    shaped = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True)._to_messages(prompt)
    assert [type(m) for m in shaped] == [SystemMessage, HumanMessage]
    plain = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None)._to_messages(prompt)           # the tool agent's wiring: voice_shape stays off
    assert len(plain) == 1 and isinstance(plain[0], HumanMessage) and plain[0].content == prompt
    odd = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True)._to_messages("a different job's prompt")
    assert len(odd) == 1 and isinstance(odd[0], HumanMessage)


def test_only_the_claude_wrapper_uses_it_so_gemini_and_the_app_are_untouched():
    users = []
    for path in Path("backend").rglob("*.py"):
        if "tests" in path.parts or "evals" in path.parts or path.name == "claude_voice.py":
            continue
        if re.search(r"\bclaude_voice\b", path.read_text(encoding="utf-8")):
            users.append(path.as_posix())
    assert users == ["backend/models/claude_llm.py", "backend/models/models.py"]          # the wrapper, and the lite voice's prompt helper; nothing in app.py or the standard voice
    assert "claude_voice" not in Path("app.py").read_text(encoding="utf-8")


def test_the_eval_passes_the_shape_to_a_claude_reply_model_and_to_nothing_else():
    from backend.evals import eval_models

    assert eval_models.make_llm("haiku", shape="system").voice_shape is True
    assert eval_models.make_llm("haiku").voice_shape is False
    assert "--shape" in Path("backend/evals/character_eval.py").read_text(encoding="utf-8")


def test_the_closing_reminders_go_last_and_the_checkin_one_only_when_a_checkin_is_due():
    from backend.utils.emotion_checkin import build_block

    plain = cv.split_prompt(reply_prompt(), tail="all")[1]
    assert plain.endswith(cv.STANDING_REMINDER.strip()) and cv.CHECKIN_REMINDER.strip() not in plain
    assert plain.index("When in doubt, leave it out.") < plain.index("REMINDERS")                       # after the follow-up rule, which is the last thing the flat prompt says
    due = cv.split_prompt(reply_prompt(emotional_context=build_block()), tail="all")[1]
    assert due.endswith(cv.CHECKIN_REMINDER.strip()) and cv.STANDING_REMINDER.strip() in due
    assert cv.CHECKIN_MARKER in build_block()                                                         # the marker is the check-in section's own opening words
    assert cv.split_prompt(reply_prompt(), tail="")[1] == cv.split_prompt(reply_prompt())[1]       # without the tail the split adds nothing
    assert "REMINDERS" not in cv.split_prompt(reply_prompt())[1]


def test_the_wrapper_adds_the_reminders_only_with_voice_tail():
    shaped = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True, voice_tail="all")._to_messages(reply_prompt())
    assert shaped[1].content.rstrip().endswith(cv.STANDING_REMINDER.strip())
    assert "REMINDERS" not in ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True)._to_messages(reply_prompt())[1].content
    from backend.evals import eval_models

    assert eval_models.make_llm("haiku", shape="tail").voice_tail == "all" and eval_models.make_llm("haiku", shape="system").voice_tail == "" and eval_models.make_llm("haiku", shape="checkin").voice_tail == "checkin"


def test_the_check_in_only_tail_adds_one_reminder_and_only_when_a_check_in_is_due():
    from backend.utils.emotion_checkin import build_block

    none_due = cv.split_prompt(reply_prompt(), tail="checkin")[1]
    assert none_due == cv.split_prompt(reply_prompt())[1]                                              # no check-in due: nothing added, not even the standing rules
    due = cv.split_prompt(reply_prompt(emotional_context=build_block()), tail="checkin")[1]
    assert due.endswith(cv.CHECKIN_ONLY_REMINDER.strip()) and cv.STANDING_REMINDER.strip() not in due and "outranks the follow-up rule" in due


def test_with_tail_is_pure_and_the_eval_wrapper_adds_it_for_a_gemini_model_without_touching_the_app():
    from backend.evals import eval_models
    from backend.utils.emotion_checkin import build_block

    due = reply_prompt(emotional_context=build_block())
    assert cv.with_tail(due, "") == due and cv.with_tail(due, "checkin") == due + cv.CHECKIN_ONLY_REMINDER
    quiet = reply_prompt()
    assert cv.with_tail(quiet, "checkin") == quiet and cv.with_tail(quiet, "all").endswith(cv.STANDING_REMINDER)
    seen = []
    inner = type("Fake", (), {"invoke": lambda self, prompt, *a, **k: seen.append(prompt) or "ok", "other": 7})()
    wrapped = eval_models.TailedLLM(inner, "checkin")
    assert wrapped.invoke(due) == "ok" and seen[-1].endswith(cv.CHECKIN_ONLY_REMINDER.strip()) and wrapped.other == 7
    assert isinstance(eval_models.make_llm("gemini-3.5-flash-lite", shape="checkin"), eval_models.TailedLLM)
    assert not isinstance(eval_models.make_llm("gemini-3.5-flash-lite"), eval_models.TailedLLM)


def test_the_no_referral_reminder_goes_last_only_where_the_low_mood_rule_is_and_only_with_a_tail():
    from backend.utils.emotion_utils import NO_REFERRAL_RULE, build_emotional_context
    from datetime import datetime, timezone

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    state = {"valence": "low", "intensity": 0.6, "updated_at": now.isoformat(), "turns_since": 0, "read_this_turn": True, "need": "venting", "gist": "flat"}
    low = reply_prompt(emotional_context=build_emotional_context(state, now))
    assert cv.REFERRAL_MARKER in NO_REFERRAL_RULE and cv.REFERRAL_MARKER in low                       # the marker is the rule's own opening words
    for tail in ("checkin", "all", "fuller"):
        assert cv.with_tail(low, tail).endswith(cv.REFERRAL_REMINDER + cv.QUESTION_REMINDER), tail           # the fresh low block carries both rules
    assert cv.with_tail(low, "") == low                                                               # no tail, nothing added
    safety = reply_prompt(emotional_context=build_emotional_context(state, now, safety=True))
    assert cv.REFERRAL_MARKER not in safety and cv.REFERRAL_REMINDER not in cv.with_tail(safety, "checkin")
    assert cv.QUESTION_MARKER not in safety and cv.QUESTION_REMINDER not in cv.with_tail(safety, "checkin")      # the ladder asks on purpose
    assert cv.REFERRAL_REMINDER not in cv.with_tail(reply_prompt(), "all") and cv.QUESTION_REMINDER not in cv.with_tail(reply_prompt(), "all")      # an ordinary turn carries none


def test_a_haiku_reply_to_a_mildly_low_message_ends_on_the_reminder_and_the_checkin_one_still_comes_first_when_due():
    from backend.utils.emotion_checkin import build_block
    from backend.utils.emotion_utils import build_emotional_context
    from datetime import datetime, timezone

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    state = {"valence": "low", "intensity": 0.6, "updated_at": now.isoformat(), "turns_since": 0, "read_this_turn": True, "need": "venting", "gist": "flat"}
    prompt = reply_prompt(emotional_context=build_emotional_context(state, now) + build_block())
    shaped = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True, voice_tail="checkin")._to_messages(prompt)           # how the app builds the Haiku voice (models.get_haiku_stream_llm)
    last = shaped[1].content.rstrip()
    assert last.endswith(cv.QUESTION_REMINDER.strip()) and last.index(cv.CHECKIN_ONLY_REMINDER.strip()) < last.index(cv.REFERRAL_REMINDER.strip()) < last.index(cv.QUESTION_REMINDER.strip())


def test_the_closing_reminder_also_restates_the_plain_talk_rule_so_a_claude_reply_ends_on_it():
    assert "Talk the way a friend would" in cv.REFERRAL_REMINDER and "no list and no bold" in cv.REFERRAL_REMINDER
    assert "no symptom or health detail from earlier or recalled context" in cv.REFERRAL_REMINDER and "severe, sudden or new" in cv.REFERRAL_REMINDER


def test_a_carried_low_mood_gets_the_no_state_question_reminder_last_and_not_the_no_referral_one():
    from backend.evals.character_eval import _carried_state
    from backend.utils.emotion_utils import build_emotional_context
    from datetime import datetime, timezone

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    carried = reply_prompt(emotional_context=build_emotional_context(_carried_state(now, 3), now))
    assert cv.QUESTION_MARKER in carried and cv.REFERRAL_MARKER not in carried                         # the carried blocks carry the question rule only
    assert cv.with_tail(carried, "checkin").endswith(cv.QUESTION_REMINDER) and cv.REFERRAL_REMINDER not in cv.with_tail(carried, "checkin")
    assert cv.with_tail(carried, "") == carried
    for text in ("no how-are-you-holding-up", "food, sleep, warmth, the phone", "unless a CHECK-IN is due", "follow them onto any topic they enjoy"):
        assert text.lower() in cv.QUESTION_REMINDER.lower() or text in cv.QUESTION_REMINDER, text


def test_the_voice_reminder_is_the_last_thing_a_haiku_reply_reads_and_only_the_voice_tail_carries_it():
    from backend.utils.emotion_checkin import build_block

    quiet = reply_prompt()
    assert cv.with_tail(quiet, "voice") == quiet + cv.VOICE_REMINDER                                      # an ordinary turn: just the voice reminder
    due = reply_prompt(emotional_context=build_block())
    voiced = cv.with_tail(due, "voice")
    assert voiced.endswith(cv.VOICE_REMINDER) and voiced.index(cv.CHECKIN_ONLY_REMINDER) < voiced.index(cv.VOICE_REMINDER)       # the check-in one still comes first
    for tail in ("", "checkin", "all", "fuller"):
        assert cv.VOICE_REMINDER not in cv.with_tail(due, tail) and cv.VOICE_REMINDER not in cv.with_tail(quiet, tail), tail        # the Gemini lite voice and the evals' other shapes are unchanged
    system, user = cv.split_prompt(quiet, tail="voice")
    assert user.endswith(cv.VOICE_REMINDER.strip()) and cv.VOICE_REMINDER.strip() not in system


def test_the_voice_reminder_says_what_the_measured_gaps_were_and_the_app_and_the_eval_both_use_the_voice_tail():
    import pathlib

    text = cv.VOICE_REMINDER
    for part in ("never open a reply with doubt", "\"I can't see anything about them\", \"I don't know why\", \"I'd be guessing\" or \"I won't guess\"", "labelled as a read, and promise nothing either way",
                 "Never explain at length what you are or are not", "open your reaction with an exclamation mark, once", "never when they are low or the topic is serious",
                 "ask one pointed question about it", "this outranks the rule about not adding a question", "never how they feel about it", "If they are wrapping up or saying goodbye, ask nothing.",
                 "open with the thing itself in plain words", "never with a label for it", "Then say one thing they did not say, as a read, using their own details", "never comfort that would fit anyone",
                 "ends on a statement, never on a question", "Do not ask them to tell you more, what happened, what was said, what they wish had happened, or why",
                 "is a statement and is fine", "never as reassurance about what the other person feels or means", "Start every reply with the substance (\"I hear you.\" counts, when the rules above ask for it), never with their name",
                 "use their name at most once in three replies"):
        assert part in text, part
    models = pathlib.Path("backend/models/models.py").read_text(encoding="utf-8")
    assert 'voice_shape=True, voice_tail="voice"' in models
    from backend.evals import eval_models

    llm = eval_models.make_llm("haiku", shape="voice")
    assert llm.voice_shape is True and llm.voice_tail == "voice"
    assert '"voice"' in pathlib.Path("backend/evals/character_eval.py").read_text(encoding="utf-8")                  # the --shape choice
