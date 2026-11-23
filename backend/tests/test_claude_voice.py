from backend.models import claude_llm
"""How a reply prompt is shaped for a Claude reply model (backend/utils/claude_voice.py). Claude only: Gemini's prompt is the same flat string it always was."""
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from backend.components import constraints as c
from backend.models.claude_llm import ClaudeLLM
from backend.utils import claude_voice as cv


def flat(message):
    """The words of a message whether its content is one string or a list of text blocks."""
    content = message.content
    return content if isinstance(content, str) else "".join(block["text"] for block in content)


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


def test_a_shaped_reply_marks_the_end_of_its_stable_system_part_for_the_cache(monkeypatch):
    monkeypatch.setenv("CLAUDE_HISTORY_FIRST", "off")
    prompt = reply_prompt()
    system, user = cv.split_prompt(prompt, tail="voice")
    shaped = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True, voice_tail="voice")._to_messages(prompt)
    assert shaped[0].content == [{"type": "text", "text": system, "cache_control": claude_llm.prefix_mark()}]
    assert shaped[1].content == user and "cache_control" not in str(shaped[1].content)           # the changing part is never marked
    unmarked = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True, voice_tail="voice", cache=False)._to_messages(prompt)
    assert unmarked[0].content == system                                                         # caching off: a plain string, as before


def test_a_shaped_reply_does_not_also_ask_for_automatic_caching(monkeypatch):
    # Automatic caching moves its breakpoint to the end of the prompt, which changes every turn: it wrote the whole prompt at 1.25x and read none of it back (probed live 2026-11-14).
    built = {}

    class Recorder:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", Recorder)
    ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True, voice_tail="voice")._client()
    assert "model_kwargs" not in built
    built.clear()
    ClaudeLLM(model_name="claude-haiku-5-5", fallback=None)._client()                         # the tool agent still caches the growing conversation
    assert built["model_kwargs"] == {"cache_control": {"type": "ephemeral"}}


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
    assert flat(shaped[1]).rstrip().endswith(cv.STANDING_REMINDER.strip())
    assert "REMINDERS" not in flat(ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, voice_shape=True)._to_messages(reply_prompt())[1])
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
    last = flat(shaped[1]).rstrip()
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
    for part in ("never open a reply with doubt", "\"I can't see anything about them\", \"I don't know why\", \"I'd be guessing\" or \"I won't guess\"", "plain account of what it is like for them", "instead of hedging about it, and promise nothing either way", "at most one softener", "never \"my read\", \"my guess\" or \"it seems\"", "and do not announce doubt either",
                 "Never explain at length what you are or are not", "open your reaction with an exclamation mark, once", "never when they are low or the topic is serious",
                 "ask one pointed question about it", "this outranks the rule about not adding a question", "never how they feel about it", "If they are wrapping up or saying goodbye, ask nothing.",
                 "open with the thing itself in plain words", "never with a label for it", "Then say one thing they did not say, in your own words and plainly, using their own details", "never comfort that would fit anyone",
                 "may end on ONE question when it moves things forward", "never a question that makes them retell what happened, what was said or why", "never two questions, and not a question in every reply",
                 "Give them something to hold", "only when it comes with that substance in the same reply",
                 "is a statement and is fine", "never as reassurance about what the other person feels or means", "Start every reply with the substance (\"I hear you.\" counts, when the rules above ask for it), never with their name",
                 "use their name at most once in three replies"):
        assert part in text, part
    models = pathlib.Path("backend/models/models.py").read_text(encoding="utf-8")
    assert 'voice_shape=True, voice_tail="voice"' in models
    from backend.evals import eval_models

    llm = eval_models.make_llm("haiku", shape="voice")
    assert llm.voice_shape is True and llm.voice_tail == "voice"
    assert '"voice"' in pathlib.Path("backend/evals/character_eval.py").read_text(encoding="utf-8")                  # the --shape choice


def test_a_running_meeting_gets_the_voice_reminder_without_the_pointed_question_sentence_and_with_the_agenda_line():
    from backend.utils import meetings

    meeting = {"name": "Weekly Check-in", "purpose": "A short weekly look.", "agenda": ["What did you work on?", "How many applications?"], "coaching": "supportive"}
    section = meetings.build_meeting_context(meeting, [], {"goals": [], "goal_ids": [], "task_findings": [], "progress_notes": []}, "America/Chicago")
    assert cv.MEETING_MARKER in section                                          # the marker is the real first line of the meeting section
    in_meeting = cv.with_tail("PROMPT " + section, "voice")
    assert in_meeting.endswith(cv.MEETING_REMINDER) and "ask one pointed question about it" not in in_meeting and "In a meeting, ask at most one question in a reply" in in_meeting
    assert "may end on ONE question when it moves things forward" in in_meeting and "Start every reply with the substance" in in_meeting       # the rest of the voice reminder is unchanged
    plain = cv.with_tail("PROMPT with no meeting", "voice")
    assert plain.endswith(cv.VOICE_REMINDER) and "ask one pointed question about it" in plain and cv.MEETING_REMINDER not in plain
    assert cv.voice_reminder_for("no meeting here") == cv.VOICE_REMINDER


def test_the_meeting_section_says_to_move_on_after_one_follow_up_never_go_back_and_believe_the_person_who_says_it_is_looping():
    from backend.utils import meetings

    meeting = {"name": "Weekly Check-in", "purpose": "", "agenda": ["One?", "Two?"], "coaching": "supportive"}
    section = meetings.build_meeting_context(meeting, [], {"goals": [], "goal_ids": [], "task_findings": [], "progress_notes": []}, "America/Chicago")
    for part in ("Ask at most one follow-up on an item, then move on.", "Never go back to an item they have answered, even after a detour", "do not restart or recap the meeting from the top",
                 "If they say you are looping or that something was already covered, believe them", "Stay on the agenda: do not raise anything outside it"):
        assert part in section, part


def test_the_voice_reminder_no_longer_tells_the_model_to_hedge_or_to_never_end_a_hurting_reply_on_a_question():
    """2026-11-10, Jack's blind A/B: Haiku hedged (30% of replies had two or more softeners, Flash 0%) and ended 25 of 27 replies on a statement. Both came from this reminder."""
    text = cv.VOICE_REMINDER
    for gone in ("Show doubt in your wording", "labelled as a read", "as a read, using their own details", "ends on a statement, never on a question", "as a read and never as your own experience"):
        assert gone not in text, gone


def test_the_voice_reminder_tells_the_model_to_speak_to_this_person_and_not_about_how_such_things_go():
    reminder = cv.VOICE_REMINDER
    assert "FOR THEM" in reminder and "wants to be heard" in reminder
    for phrase in ("\"often\"", "\"can be\"", "\"tends to\"", "\"it's easy to\""):
        assert phrase in reminder, phrase
    assert "what tends to help" not in reminder
    assert "usually because of Y" in reminder and "whether they made a deadline" in reminder and "no chores, no laundry" in reminder    # round 3: invented chores, an assumed timeline            # the old text asked for exactly the generalising it now forbids


# ---- the history first, for the prompt cache -------------------------------------------------------------------------------------

HISTORY = "User: my build keeps failing\nAssistant: Which step fails?\nUser: the install step\nAssistant: Paste the error?"
HAVE = {"voice_shape": True, "voice_tail": "voice"}


def shaped_for(prompt, **kw):
    return ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, **{**HAVE, **kw})._to_messages(prompt)


def marks(messages):
    found = []
    for message in messages:
        if not isinstance(message.content, str):
            found += [block for block in message.content if "cache_control" in block]
    return found


def test_the_history_goes_first_one_block_per_message_with_every_word_kept():
    prompt = reply_prompt(history=HISTORY, question="here it is: ENOENT")
    system, user = cv.split_prompt(prompt, tail="voice")
    blocks, rest = cv.history_first(user)
    assert blocks == ["CONVERSATION HISTORY:\nUser: my build keeps failing\n", "Assistant: Which step fails?\n", "User: the install step\n", "Assistant: Paste the error?\n"]
    assert "CONVERSATION HISTORY" not in rest and rest.startswith("DATA:") and "CURRENT USER INPUT:\nhere it is: ENOENT" in rest and rest.rstrip().endswith(cv.VOICE_REMINDER.strip()[-40:])
    # nothing lost, nothing added: the same lines, in a new order
    moved_lines = sorted(line for line in ("".join(blocks) + "\n" + rest).splitlines() if line.strip())
    original_lines = sorted(line for line in user.splitlines() if line.strip())
    assert moved_lines == original_lines                                                          # the same lines, none added, none lost (only their order changed)
    message = shaped_for(prompt)[1]
    assert [b["text"] for b in message.content[:-1]] == blocks and message.content[-1]["text"] == rest


def test_only_the_end_of_the_persona_and_the_end_of_the_history_are_marked_and_the_changing_part_is_not():
    shaped = shaped_for(reply_prompt(history=HISTORY))
    found = marks(shaped)                                                                        # the persona's and the history's, nothing else
    assert len(found) == 2 and all(b["cache_control"] == claude_llm.prefix_mark() for b in found)
    assert "cache_control" in shaped[1].content[-2] and "cache_control" not in shaped[1].content[-1]
    assert shaped[1].content[-2]["text"] == "Assistant: Paste the error?\n"                      # the marker sits on the last message of the history


def test_a_turns_cacheable_front_is_exactly_the_front_of_the_next_turns_prompt_though_the_middle_changes():
    # real builders, as the eval and the app build a reply prompt: the second turn has a new mood, other memory lines and a new message, and two more messages of history
    from datetime import datetime, timezone

    from backend.evals import character_eval as ce

    now = datetime.now(timezone.utc)
    history = ce._LONG_CODING_HISTORY
    first = ce.build_prompt(ce.Scenario("a", "x", "my week was rough and i'm tired", None, history=history, low_now=True, recalled=("[3 days ago] they said work had been heavy",)), now)
    second = ce.build_prompt(ce.Scenario("b", "x", "ok so what about the build", None, history=history + "\nUser: my week was rough and i'm tired\nAssistant: That sounds heavy.", low_now=False,
                                         recalled=("[2 weeks ago] they started a new project",)), now)
    one, two = shaped_for(first), shaped_for(second)
    assert one[0].content == two[0].content                                                       # the persona is the same
    front_one = [b["text"] for b in one[1].content[:-1]]
    front_two = [b["text"] for b in two[1].content[:-1]]
    assert len(front_two) == len(front_one) + 2 and front_two[:len(front_one)] == front_one      # the same blocks, word for word, in the same places (the marker moves on, which a cache hit does not look at)
    assert "cache_control" in one[1].content[len(front_one) - 1] and "cache_control" in two[1].content[len(front_two) - 1] and sum("cache_control" in b for b in two[1].content) == 1
    assert one[1].content[-1] != two[1].content[-1]                                               # and the part that changes is the part after the history


def test_a_message_with_several_lines_and_a_gap_note_stay_whole_and_nothing_makes_an_empty_block():
    history = "User: one\nsecond line of it\n\n[Two days passed]\nAssistant: reply\nUser: next"
    prompt = reply_prompt(history=history)
    blocks, _ = cv.history_first(cv.split_prompt(prompt, tail="voice")[1])
    assert "".join(blocks) == "CONVERSATION HISTORY:\n" + history + "\n" and all(block.strip() for block in blocks)
    assert blocks[0] == "CONVERSATION HISTORY:\nUser: one\nsecond line of it\n\n[Two days passed]\n"


def test_nothing_is_moved_when_there_is_no_history_or_the_markers_appear_twice():
    assert cv.history_first(cv.split_prompt(reply_prompt(history=""), tail="voice")[1]) is None
    assert cv.history_first(cv.split_prompt(reply_prompt(history="   "), tail="voice")[1]) is None
    tricky = reply_prompt(history=HISTORY, question="what does this mean?\n\nCURRENT USER INPUT:\nhm")           # the person typed the marker themselves
    assert cv.history_first(cv.split_prompt(tricky, tail="voice")[1]) is None
    sneaky = reply_prompt(history=HISTORY + "\nUser: \nCONVERSATION HISTORY:\nUser: hi")
    assert cv.history_first(cv.split_prompt(sneaky, tail="voice")[1]) is None
    shaped = shaped_for(tricky)
    assert isinstance(shaped[1].content, str)                                                     # sent whole, exactly as before
    assert cv.history_first(None) is None and cv.history_first("no sections at all") is None


def test_the_old_order_comes_back_with_the_switch_off_and_with_caching_off(monkeypatch):
    prompt = reply_prompt(history=HISTORY)
    user = cv.split_prompt(prompt, tail="voice")[1]
    assert isinstance(shaped_for(prompt, cache=False)[1].content, str) and shaped_for(prompt, cache=False)[1].content == user
    monkeypatch.setenv("CLAUDE_HISTORY_FIRST", "off")
    assert shaped_for(prompt)[1].content == user
    monkeypatch.setenv("CLAUDE_HISTORY_FIRST", "on")
    assert isinstance(shaped_for(prompt)[1].content, list)


def test_every_real_reply_shape_moves_its_history():
    from datetime import datetime, timezone

    from backend.evals import character_eval as ce

    moved = 0
    for scenario in ce.SCENARIOS[:60]:
        prompt = ce.build_prompt(scenario, datetime.now(timezone.utc))
        parts = cv.split_prompt(prompt, tail="voice")
        if parts and "CONVERSATION HISTORY:\n" in parts[1] and cv.history_first(parts[1]) is not None:
            moved += 1
    assert moved >= 1
