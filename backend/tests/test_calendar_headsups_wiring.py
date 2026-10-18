"""How calendar heads-ups are placed in a chat turn and who can reach them (app.py)."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def block():
    start = APP.index("headsup_entry = None\n")
    return APP[start:start + 1300]


def test_a_heads_up_is_offered_only_when_safe_not_in_a_meeting_and_never_to_a_guest():
    text = block()
    for condition in ("not meeting_context", "not is_guest_username(username)", "risk_level == RISK_NONE", 'source_type == "conversational"', "not is_closing_message(question)",
                      '== "open"', "headsups_utils.is_return(last_message_at", "curiosity_utils.is_lull(question)", 'budget_guard.allows("calendar_headsups", budget_state)'):
        assert condition in text, condition


def test_the_meeting_is_decided_before_the_heads_up_so_a_meeting_can_hold_it_back():
    assert APP.index('meeting_context = ""') < APP.index("headsup_entry = None\n")


def test_an_urgent_heads_up_beats_an_open_loop_and_a_light_one_yields_to_it_and_both_yield_to_encouragement_and_a_relatable_line_yields_to_both():
    text = block()
    assert 'if headsup_entry["urgent"]:\n                    open_loop_entry = None\n                else:\n                    headsup_entry = None' in text
    assert "relatable_entry = None if (open_loop_entry or headsup_entry)" in APP
    assert "headsup_entry = None    # nor a calendar line" in APP


def test_what_was_said_is_recorded_after_the_reply_and_the_turn_is_counted():
    assert 'headsups_utils.mark_mentioned, username, headsup_entry["event_id"], headsup_entry["phase"]' in APP and "headsups_utils.was_mentioned(headsup_entry, full_response)" in APP
    assert 'tally(turn_counts, "headsup_offered")' in APP
    assert "headsups_utils.build_headsup_block(headsup_entry" in APP and "headsups_utils.forget_all, username" in APP


def test_every_heads_up_endpoint_refuses_a_guest_and_is_scoped_to_the_signed_in_person():
    start = APP.index("def _headsups_user")
    text = APP[start:APP.index('@app.get("/api/local-workspace")')]
    assert text.count("username = _headsups_user(current_user)") == 3 and "is_guest_username(username)" in text


def test_the_voice_prompt_has_a_place_for_it_and_low_power_mode_pauses_it():
    from backend.components.constraints import build_voice_prompt
    from backend.utils import budget_guard, self_counters
    assert "HEADSUP-MARKER" in build_voice_prompt("G", "D", "H", "Q", headsup_context="\nHEADSUP-MARKER\n")
    assert "HEADSUP-MARKER" not in build_voice_prompt("G", "D", "H", "Q")
    assert "headsup_offered" in self_counters.KEYS and "calendar heads-ups" in budget_guard.PAUSED_FEATURES


def test_the_calendar_range_read_never_returns_the_description_and_carries_what_tells_a_strangers_invite_apart():
    source = (ROOT / "backend" / "services" / "google_calendar_service.py").read_text(encoding="utf-8")
    body = source[source.index("def list_events_in_range"):]
    assert "description" not in body.split('"""')[2] and '"self_response"' in body and '"organizer_self"' in body and '"has_attendees"' in body and '"visibility"' in body
