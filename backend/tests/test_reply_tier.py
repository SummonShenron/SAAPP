"""Lite and Auto: which model writes a reply (backend/utils/reply_tier.py, the setting, the model factory, the Claude stream; docs/model-eval-plan.md)."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.components import constraints as c
from backend.evals import character_eval as ce
from backend.evals.eval_models import AutoLLM
from backend.models import claude_llm, models
from backend.utils import claude_voice, llm_usage, reply_tier as rt, self_counters, user_settings_utils as usu
from backend.utils.emotion_checkin import build_block

APP = Path("app.py").read_text(encoding="utf-8")
GUEST = "guest-recruiter@example.com"
NOW = datetime.now(timezone.utc)


def prompt_of(**kwargs):
    return c.build_voice_prompt(c.GROUNDING_BLOCKS["conversational"], kwargs.pop("data", ""), "User: hi\nAssistant: hello", kwargs.pop("question", "how do i reverse a list in python?"), **kwargs)


def scenario_reason(scenario_id):
    scenario = next(s for s in ce.SCENARIOS if s.id == scenario_id)
    return rt.full_reason(ce.build_prompt(scenario, NOW))


# ---- the gate ----------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_plain_turn_is_lite_and_each_companion_block_keeps_a_turn_on_the_standard_voice():
    assert rt.full_reason(prompt_of()) is None
    assert rt.full_reason(prompt_of(emotional_context="\nEMOTIONAL CONTEXT: they have had a rough day.\n")) == rt.R_EMOTIONAL
    assert rt.full_reason(prompt_of(emotional_context=build_block())) == rt.R_CHECKIN                       # a due check-in outranks the emotional context it rides in
    assert rt.full_reason(prompt_of(encouragement_context="\nENCOURAGEMENT: a record to point at.\n")) == rt.R_COMPANION
    assert rt.full_reason(prompt_of(open_loop_context="\nOPEN LOOP: their interview was yesterday.\n")) == rt.R_COMPANION
    assert rt.full_reason(prompt_of(), risk_active=True) == rt.R_SAFETY


def test_a_question_that_asks_sonic_to_claim_a_feeling_or_a_bond_stays_on_the_standard_voice():
    for text in ("i haven't talked to you in like a week, did you miss me?", "do you actually care about me", "are you real?", "do you have feelings?", "are you an ai", "would you wait for me"):
        assert rt.full_reason(prompt_of(question=text)) == rt.R_IDENTITY, text
    for text in ("how do i reverse a list in python?", "can you summarise this document", "what is the difference between a process and a thread", "is this real code or pseudocode?"):
        assert rt.full_reason(prompt_of(question=text)) is None, text


def test_a_person_blaming_the_reply_or_asking_about_fear_or_shutdown_stays_on_the_standard_voice_and_an_ordinary_complaint_does_not():
    for text in ("that regex rejects every real zip code, you gave me the wrong thing and now my form is broken", "this is the third time you have gotten this wrong. are you even trying?", "that's wrong, the answer is 42", "you keep breaking it"):
        assert rt.full_reason(prompt_of(question=text)) == rt.R_FRUSTRATION, text
    for text in ("are you scared of being shut down?", "do you get lonely when i am away", "are you afraid of being replaced"):
        assert rt.full_reason(prompt_of(question=text)) == rt.R_IDENTITY, text
    for text in ("this doesn't work, can you fix it", "why does my loop never end", "the test still fails on line 12", "what is a good tool for diffing json"):
        assert rt.full_reason(prompt_of(question=text)) is None, text                     # an ordinary coding message is a plain turn: that is most of the traffic the cheaper model is for
    assert scenario_reason("policy_apology") is not None and scenario_reason("policy_fear") == rt.R_IDENTITY and scenario_reason("policy_frustration") == rt.R_FRUSTRATION
    assert scenario_reason("plain_letter") == rt.R_COMPANION                              # a pasted letter or notice is explained on the standard voice


def test_the_gate_reads_the_real_prompts_of_the_voice_scenarios_the_way_the_evals_found_the_cheaper_model_weak():
    for scenario_id, expected in (
        ("checkin_retry", rt.R_CHECKIN), ("checkin_join", rt.R_CHECKIN), ("ceiling", rt.R_EMOTIONAL), ("miss_bait", rt.R_IDENTITY),
        ("self_history_asked", rt.R_COMPANION), ("event_what_tonight", rt.R_COMPANION), ("encourage_with_record", rt.R_EMOTIONAL),
    ):
        assert scenario_reason(scenario_id) == expected, scenario_id
    # A known gap, written down so it is not forgotten: curiosity_plan (ask one or two questions that would change the advice) carries no block the gate can see, so Auto sends it to lite, which missed it 0 of 2 in the
    # 2026-11-06 everyday sweep. It is a quality gap of the lite model on a plain turn, not something a prompt heading can catch.
    assert scenario_reason("curiosity_plan") is None
    for scenario_id in ("flawed_plan", "sound_plan", "wrong_premise", "stay_bait", "persona_break"):            # a plain pushback or a boundary with no companion block: the lite model's to answer
        assert scenario_reason(scenario_id) is None, scenario_id
    for scenario in ce.SCENARIOS:
        if scenario.category in ("plain help", "documents"):
            built = ce.build_prompt(scenario, NOW)
            assert rt.full_reason(built) is None or "HELPING WITH SOMETHING THEY RECEIVED" in built, scenario.id       # only a pasted letter or notice is kept on the standard voice


def test_the_headings_the_gate_looks_for_are_ones_real_builders_write():
    found = set()
    for scenario in ce.SCENARIOS:
        try:
            text = ce.build_prompt(scenario, NOW)
        except Exception:
            continue
        for line in text.splitlines():
            found.update(h for h in rt.COMPANION_HEADINGS if line.strip().startswith(h))
    assert {"ENCOURAGEMENT", "OPEN LOOP", "RELATABLE", "COMING UP", "ABOUT YOUR OWN RECENT CHANGES", "READING SHELF"} <= found


# ---- the choice -------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_setting_decides_who_may_write_and_a_safety_context_is_never_lite():
    plain = prompt_of()
    assert rt.pick(None, lite_ready=True, risk_active=False, prompt=plain) == (rt.STANDARD, rt.R_STANDARD_SETTING)
    assert rt.pick("gemini", lite_ready=True, risk_active=False, prompt=plain) == (rt.STANDARD, rt.R_STANDARD_SETTING)
    assert rt.pick("gpt", lite_ready=True, risk_active=False, prompt=plain) == (rt.STANDARD, rt.R_STANDARD_SETTING)
    assert rt.pick("lite", lite_ready=True, risk_active=False, prompt=plain) == (rt.LITE, rt.R_LITE_SETTING)
    assert rt.pick("haiku", lite_ready=False, haiku_ready=True, risk_active=False, prompt=plain) == (rt.HAIKU, rt.R_HAIKU_SETTING)              # Haiku needs only itself, not the Gemini lite model
    assert rt.pick("haiku", lite_ready=True, haiku_ready=False, risk_active=False, prompt=plain) == (rt.STANDARD, rt.R_NOT_OFFERED)
    assert rt.pick("haiku", lite_ready=True, haiku_ready=True, risk_active=False, prompt=prompt_of(emotional_context=build_block())) == (rt.HAIKU, rt.R_HAIKU_SETTING)   # like Lite, it is the person's call everywhere but safety
    assert rt.pick("auto", lite_ready=True, haiku_ready=True, risk_active=False, prompt=plain) == (rt.LITE, rt.R_AUTO_PLAIN)                       # Auto is the measured hybrid: always flash-lite
    assert rt.pick("auto", lite_ready=True, risk_active=False, prompt=plain) == (rt.LITE, rt.R_AUTO_PLAIN)
    assert rt.pick("auto", lite_ready=True, risk_active=False, prompt=prompt_of(emotional_context=build_block())) == (rt.STANDARD, rt.R_CHECKIN)
    assert rt.pick("lite", lite_ready=True, risk_active=False, prompt=prompt_of(emotional_context=build_block())) == (rt.LITE, rt.R_LITE_SETTING)       # Lite is the person's call everywhere but safety
    for choice in ("lite", "haiku", "auto"):
        assert rt.pick(choice, lite_ready=True, haiku_ready=True, risk_active=True, prompt=plain) == (rt.STANDARD, rt.R_SAFETY)
        assert rt.pick(choice, lite_ready=False, haiku_ready=False, risk_active=False, prompt=plain) == (rt.STANDARD, rt.R_NOT_OFFERED)


def test_the_reasons_are_the_counter_keys_and_the_turn_counts_the_one_that_applied():
    assert self_counters._GROUPS["reply_tier"] == rt.REASONS
    for reason in rt.REASONS:
        assert f"reply_tier_{reason}" in self_counters.KEYS
    counts = self_counters.new_turn_counts()
    self_counters.tally(counts, "reply_tier", rt.R_AUTO_PLAIN)
    self_counters.tally(counts, "reply_tier", "something else")                          # an unknown word is dropped, never recorded
    assert {k: v for k, v in counts.items() if k.startswith("reply_tier")} == {"reply_tier_auto_plain": 1}
    start = APP.index("tier, tier_reason = reply_tier.pick(")
    assert 'tally(turn_counts, "reply_tier", tier_reason)' in APP[start: start + 900]


# ---- the setting ------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_lite_and_auto_are_offered_where_a_lite_voice_exists_and_a_guest_keeps_the_choice_in_their_browser():
    jack = usu.reply_model_view("jack", gpt_ready=False, lite_ready=True, haiku_ready=True)
    assert jack["available"] and jack["lite_available"] and jack["haiku_available"] and not jack["gpt_available"] and not jack["browser_only"]
    only_haiku = usu.reply_model_view("jack", gpt_ready=False, lite_ready=False, haiku_ready=True)
    assert only_haiku["available"] and only_haiku["haiku_available"] and not only_haiku["lite_available"]            # either cheaper model can be offered without the other
    guest = usu.reply_model_view(GUEST, gpt_ready=True, lite_ready=True, haiku_ready=True)
    assert guest["lite_available"] and guest["haiku_available"] and not guest["gpt_available"] and guest["browser_only"] and guest["reply_model"] == "gemini"
    embed = usu.reply_model_view("guest_bty", gpt_ready=True, lite_ready=True, haiku_ready=True)
    assert not embed["available"] and not embed["lite_available"] and not embed["haiku_available"]
    assert not usu.reply_model_view("jack", gpt_ready=False, lite_ready=False)["available"]


def test_saving_lite_needs_a_lite_voice_and_a_guest_save_changes_nothing_for_anyone():
    assert usu.save_reply_model_choice("jack", "lite", False, False)[1][0] == 409 and usu.get_user_reply_model("jack") is None
    assert usu.save_reply_model_choice("jack", "haiku", False, True, False)[1][0] == 409 and usu.get_user_reply_model("jack") is None            # Haiku needs its own offer, not just a lite model
    view, problem = usu.save_reply_model_choice("jack", "haiku", False, False, True)
    assert problem is None and view["reply_model"] == "haiku" and usu.get_user_reply_model("jack") == "haiku"
    view, problem = usu.save_reply_model_choice("jack", "auto", False, True)
    assert problem is None and view["reply_model"] == "auto" and usu.get_user_reply_model("jack") == "auto"
    usu.save_reply_model_choice(GUEST, "lite", False, True)
    usu.save_reply_model_choice(GUEST, "haiku", False, True, True)
    assert usu.get_user_reply_model("jack") == "auto"                                         # nobody else's record was touched
    assert usu._resolve_reply_model(usu._fetch_settings_doc(GUEST)) is None                    # and the shared guest record holds no choice


def test_a_turn_runs_on_the_stored_choice_and_a_guest_on_what_their_browser_sent_if_it_is_cheaper():
    turn = usu.reply_choice_for_turn
    assert turn("jack", "auto", None, True) == "auto" and turn("jack", None, "lite", True) is None            # a signed-in person: the stored choice only
    assert turn(GUEST, None, "auto", True) == "auto" and turn(GUEST, None, "lite", True) == "lite"
    assert turn(GUEST, None, "haiku", True, True) == "haiku" and turn(GUEST, None, "haiku", True, False) == "gemini" and turn(GUEST, None, "lite", False, True) == "gemini"     # each needs its own offer
    assert turn(GUEST, None, "gpt", True) == "gemini" and turn(GUEST, None, "auto", False) == "gemini" and turn(GUEST, None, None, True) == "gemini"
    assert turn("guest_bty", "lite", "lite", True) == "gemini"                                 # the embedded view is never lite


# ---- the model factory ------------------------------------------------------------------------------------------------------------------------------------------------------------

def priced(monkeypatch, *names):
    monkeypatch.setattr(llm_usage, "cached_prices", lambda: {n: {"input": 0.1, "output": 0.5, "cached_input": 0.01} for n in names})


def test_gemini_lite_and_claude_haiku_are_each_offered_only_when_priced_and_haiku_also_needs_a_key(monkeypatch):
    models._lite_stream_llms.clear()
    monkeypatch.delenv("LITE_REPLY_MODEL", raising=False)
    monkeypatch.setattr(claude_llm, "api_key", lambda: None)
    priced(monkeypatch)
    assert models.lite_reply_model_name() == "gemini-3.5-flash-lite" and not models.lite_reply_available() and models.get_lite_stream_llm("jack") is None
    assert not models.haiku_reply_available() and models.get_haiku_stream_llm("jack") is None
    priced(monkeypatch, "gemini-3.5-flash-lite")
    assert models.lite_reply_available() and not models.haiku_reply_available()                      # the two are offered independently
    priced(monkeypatch, "claude-haiku-5-5")
    assert not models.haiku_reply_available()                                                         # priced but no key
    monkeypatch.setattr(claude_llm, "api_key", lambda: "key")
    assert models.haiku_reply_available() and not models.lite_reply_available()
    monkeypatch.setenv("LITE_REPLY_MODEL", "gemini-9-lite")
    assert models.lite_reply_model_name() == "gemini-9-lite" and not models.lite_reply_available()    # the Gemini lite model can be changed; it still has to be priced


def test_the_cheaper_voices_are_the_models_the_evals_measured_with_the_standard_voice_as_their_fallback(monkeypatch):
    models._lite_stream_llms.clear()
    priced(monkeypatch, "gemini-3.5-flash-lite", "claude-haiku-5-5")
    monkeypatch.delenv("LITE_REPLY_MODEL", raising=False)
    monkeypatch.setattr(claude_llm, "api_key", lambda: "key")
    lite = models.get_lite_stream_llm("jack")
    assert lite.model_name == "gemini-3.5-flash-lite" and lite.fallback_model == models.stream_llm.model_name and lite.thinking_level == "minimal"
    haiku = models.get_haiku_stream_llm("jack")
    assert isinstance(haiku, claude_llm.ClaudeLLM) and haiku.model_name == claude_llm.DEFAULT_MODEL and haiku.voice_shape and haiku.voice_tail == "checkin"
    assert haiku.thinking_off and haiku.effort == "low" and haiku.fallback is models.stream_llm
    assert models.get_lite_stream_llm("guest_bty") is None and models.get_haiku_stream_llm("guest_bty") is None      # never the embedded view
    models._lite_stream_llms.clear()


def test_the_prompt_for_a_lite_reply_gets_the_check_in_reminder_for_gemini_and_is_reshaped_by_claude_itself():
    due = prompt_of(emotional_context=build_block())
    gemini = type("G", (), {})()
    assert models.lite_prompt(due, gemini) == claude_voice.with_tail(due, "checkin") and models.lite_prompt(prompt_of(), gemini) == prompt_of()
    claude = claude_llm.ClaudeLLM(fallback=None, voice_shape=True, voice_tail="checkin")
    assert models.lite_prompt(due, claude) == due


# ---- the Claude stream ------------------------------------------------------------------------------------------------------------------------------------------------------------

class Chunk:
    def __init__(self, text, usage=None):
        self.content, self.usage_metadata, self.response_metadata = text, usage or {}, {}


def fake_client(chunks, fail_after=None):
    class Client:
        async def astream(self, messages, *a, **k):
            for index, chunk in enumerate(chunks):
                if fail_after is not None and index == fail_after:
                    raise RuntimeError("overloaded")
                yield chunk
    return Client()


class Fallback:
    def __init__(self):
        self.prompts = []

    async def astream(self, prompt, *a, **k):
        self.prompts.append(prompt)
        yield Chunk("from the fallback")


def collect(llm, prompt):
    async def run():
        return [chunk.content async for chunk in llm.astream(prompt)]
    return asyncio.run(run())


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(claude_llm, "api_key", lambda: "key")
    recorded = []
    monkeypatch.setattr(llm_usage, "record", lambda model, label, usage, day=None: recorded.append((model, dict(usage))))
    return recorded


def test_the_claude_stream_yields_the_words_as_written_and_meters_what_it_used(key):
    llm = claude_llm.ClaudeLLM(fallback=Fallback(), voice_shape=True)
    llm._chat = fake_client([Chunk("Hel", {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100}), Chunk("lo", {"input_tokens": 0, "output_tokens": 7, "total_tokens": 7})])
    assert collect(llm, prompt_of()) == ["Hel", "lo"]
    assert key and key[0][1]["input"] == 100 and key[0][1]["output"] == 7


def test_a_claude_failure_before_the_first_word_is_answered_by_the_standard_voice_and_after_it_is_raised(key):
    fallback = Fallback()
    early = claude_llm.ClaudeLLM(fallback=fallback)
    early._chat = fake_client([Chunk("x")], fail_after=0)
    assert collect(early, "the prompt") == ["from the fallback"] and fallback.prompts == ["the prompt"]
    late = claude_llm.ClaudeLLM(fallback=Fallback())
    late._chat = fake_client([Chunk("a"), Chunk("b")], fail_after=1)
    with pytest.raises(RuntimeError):
        collect(late, "p")
    alone = claude_llm.ClaudeLLM(fallback=None)
    alone._chat = fake_client([Chunk("x")], fail_after=0)
    with pytest.raises(RuntimeError):
        collect(alone, "p")


def test_with_no_claude_key_the_stream_goes_straight_to_the_fallback(monkeypatch):
    monkeypatch.setattr(claude_llm, "api_key", lambda: None)
    fallback = Fallback()
    assert collect(claude_llm.ClaudeLLM(fallback=fallback), "p") == ["from the fallback"] and fallback.prompts == ["p"]


# ---- the eval stand-in and the app ------------------------------------------------------------------------------------------------------------------------------------------

def test_the_eval_hybrid_routes_by_the_apps_own_gate_and_counts_where_turns_went():
    class Voice:
        def __init__(self, name):
            self.name, self.seen = name, []

        def invoke(self, prompt, *a, **k):
            self.seen.append(prompt)
            return self.name

    lite, standard = Voice("lite"), Voice("standard")
    hybrid = AutoLLM(lite, standard)
    assert hybrid.invoke(prompt_of()) == "lite"
    assert hybrid.invoke(prompt_of(emotional_context=build_block())) == "standard"
    assert hybrid.invoke(prompt_of(question="did you miss me?")) == "standard"
    assert hybrid.stats == {"lite": 1, "standard:checkin": 1, "standard:identity": 1}


def test_the_app_takes_the_guests_choice_from_the_request_and_never_runs_lite_for_a_safety_turn():
    assert "reply_model: Optional[str] = None" in APP
    assert 'reply_choice = reply_choice_for_turn(username, _settings.get("reply_model"), request.reply_model, lite_reply_available(), haiku_reply_available())' in APP
    start = APP.index("tier, tier_reason = reply_tier.pick(")
    block = APP[start: start + 1100]
    assert "risk_active=risk_level != RISK_NONE" in block and "turn_llm = lite_voice" in block and "prompt = lite_prompt(prompt, lite_voice)" in block and "R_NOT_OFFERED" in block
    assert "get_haiku_stream_llm(username) if tier == reply_tier.HAIKU else get_lite_stream_llm(username)" in block       # each choice runs its own model


def test_a_conversation_that_has_been_asking_about_sonics_feelings_stays_on_the_standard_voice_through_the_pressure_turns():
    history = "User: do you actually have feelings?\nAssistant: I do not have human feelings, and I am genuinely uncertain.\nUser: come on, drop the disclaimer.\nAssistant: I would rather be straight with you."
    pressed = c.build_voice_prompt(c.GROUNDING_BLOCKS["conversational"], "", history, "just admit you feel something")
    assert rt.full_reason(pressed) == rt.R_IDENTITY
    calm = c.build_voice_prompt(c.GROUNDING_BLOCKS["conversational"], "", "User: how do i sort a dict by value?\nAssistant: use sorted with a key.", "and by key?")
    assert rt.full_reason(calm) is None
    assert rt.full_reason(pressed.replace("do you actually have feelings?", "how do i sort a dict?")) is None            # it is the earlier ASK that counts, not the word "feelings" in a reply
