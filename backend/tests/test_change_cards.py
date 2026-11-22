"""The calendar-change card and the wait for Apply or Decline (backend/services/change_cards.py), then the whole turn through the tool agent: the card is streamed, the turn waits, the person's press is the only thing
that makes the Google call, and the loop is told what really happened. Google is a fake that records every call (backend/tests/test_calendar_changes_service.py)."""
import asyncio
import logging
import pathlib
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import change_cards as cards
from backend.services import calendar_changes_service as svc
from backend.services import native_tools as nt
from backend.tests.test_calendar_changes_service import EMAIL, TITLE, FakeGoogle, HttpError, event
from backend.tests.test_local_edits import run_async
from backend.utils.calendar_changes import CalendarChangeError

ROOT = pathlib.Path(__file__).resolve().parents[2]
TZ = "America/Chicago"


@pytest.fixture(autouse=True)
def _clean():
    cards._waiting.clear()
    yield
    cards._waiting.clear()


@pytest.fixture()
def google(monkeypatch):
    fake = FakeGoogle([event()])

    class Oauth:
        def get_valid_access_token(self, username):
            return "tok"

    monkeypatch.setattr(svc, "GoogleCalendarOAuth", Oauth)
    for name in ("get_event", "insert_event", "patch_event", "delete_event"):
        monkeypatch.setattr(svc.gcs, name, getattr(fake, name))
    return fake


def proposal_for(username="jack", **args):
    return svc.prepare(username, args or {"action": "delete", "event_id": "evt1"}, TZ)


# ---- the registry --------------------------------------------------------------------------------------------------------------------------

@run_async
async def test_what_is_streamed_to_the_page_is_only_what_the_card_draws(google):
    p = proposal_for()
    payload = cards.card_payload(p)
    assert set(payload) == {"proposal_id", "action", "heading", "apply_label", "title", "rows", "notice", "destructive"}
    assert payload["proposal_id"] == p["proposal_id"] and payload["action"] == "delete" and payload["destructive"] is True and payload["title"] == TITLE
    assert payload["heading"] == "Delete from your calendar" and payload["apply_label"] == "Delete event"
    assert "request" not in str(payload) and "etag" not in str(payload) and "tok" not in str(payload)


@run_async
async def test_apply_makes_the_google_call_once_and_wakes_the_waiter_with_what_happened(google):
    p = proposal_for()
    cards.register(p)
    google.calls.clear()
    waiter = asyncio.create_task(cards.wait_for(p["proposal_id"]))
    await asyncio.sleep(0)
    assert google.writes() == [], "nothing is sent before the press"
    reply = await cards.decide("jack", p["proposal_id"], "apply")
    outcome = await waiter
    assert reply == {"status": "applied", "message": f"Deleted \"{TITLE}\" from your calendar."} and outcome == {"kind": "applied", "message": reply["message"]}
    assert google.writes() == [("delete", "evt1", "none")] and cards.waiting_count() == 0


@run_async
async def test_any_proposal_is_applied_by_its_own_function_and_its_own_error_type_is_a_failure_not_a_crash():
    from backend.utils.change_error import ChangeError

    calls = []

    def fine(proposal):
        calls.append(proposal["title"])
        return "Done by the document service."

    def refuses(proposal):
        raise ChangeError("That document is gone.")

    base = {"username": "jack", "subject": "document", "card": {"action": "append", "heading": "Add to a document", "apply_label": "Add text", "title": "Notes", "rows": [], "notice": "", "destructive": False}}
    good, bad = {**base, "proposal_id": "p1", "title": "Notes", "apply": fine}, {**base, "proposal_id": "p2", "title": "Notes", "apply": refuses}
    cards.register(good)
    cards.register(bad)
    assert await cards.decide("jack", "p1", "apply") == {"status": "applied", "message": "Done by the document service."} and calls == ["Notes"]
    assert await cards.decide("jack", "p2", "apply") == {"status": "failed", "message": "That document is gone."}


@run_async
async def test_decline_changes_nothing_and_says_so(google):
    p = proposal_for()
    cards.register(p)
    google.calls.clear()
    reply = await cards.decide("jack", p["proposal_id"], "decline")
    assert reply == {"status": "declined", "message": ""} and google.writes() == []
    assert (await cards.wait_for(p["proposal_id"]))["kind"] == "declined"


@run_async
async def test_the_first_press_decides_and_a_second_one_changes_nothing(google):
    p = proposal_for()
    cards.register(p)
    google.calls.clear()
    first, second, third = await asyncio.gather(cards.decide("jack", p["proposal_id"], "apply"), cards.decide("jack", p["proposal_id"], "apply"), cards.decide("jack", p["proposal_id"], "decline"))
    assert first["status"] == "applied" and second["status"] == "expired" and third["status"] == "expired"
    assert len(google.writes()) == 1, "a double click (or two tabs) must not delete twice"


@run_async
async def test_only_the_person_the_card_was_shown_to_can_press_it(google):
    p = proposal_for()
    cards.register(p)
    google.calls.clear()
    assert (await cards.decide("mallory", p["proposal_id"], "apply"))["status"] == "expired" and google.writes() == []
    assert (await cards.decide("jack", p["proposal_id"], "apply"))["status"] == "applied"


@run_async
async def test_a_press_that_is_not_apply_or_decline_is_invalid_and_leaves_the_card_open(google):
    p = proposal_for()
    cards.register(p)
    google.calls.clear()
    for bad in (None, "", "yes", "APPLY!", 5):
        assert (await cards.decide("jack", p["proposal_id"], bad))["status"] == "invalid"
    assert google.writes() == [] and (await cards.decide("jack", p["proposal_id"], " Apply "))["status"] == "applied"


@run_async
async def test_a_press_with_nobody_waiting_changes_nothing(google):
    p = proposal_for()
    google.calls.clear()
    assert (await cards.decide("jack", p["proposal_id"], "apply"))["status"] == "expired" and (await cards.decide("jack", "nope", "apply"))["status"] == "expired"
    assert google.writes() == []


@run_async
async def test_a_change_that_cannot_be_made_is_reported_as_failed_with_the_reason_and_nothing_sent(google):
    p = proposal_for()
    cards.register(p)
    google.events["evt1"]["etag"] = '"v2"'
    google.calls.clear()
    reply = await cards.decide("jack", p["proposal_id"], "apply")
    assert reply["status"] == "failed" and "changed after I showed you the card" in reply["message"] and google.writes() == []


@run_async
async def test_an_unexpected_error_is_a_generic_failure_never_its_text(google, caplog):
    p = proposal_for()
    cards.register(p)
    google.fail_with = RuntimeError(f"boom {TITLE} {EMAIL}")
    with caplog.at_level(logging.DEBUG, logger="SASS Logger"):
        reply = await cards.decide("jack", p["proposal_id"], "apply")
    assert reply["status"] == "failed" and TITLE not in str(reply) and EMAIL not in str(reply)


@run_async
async def test_no_press_in_time_gives_none_ticks_while_waiting_and_a_late_press_finds_nobody(google):
    p = proposal_for()
    cards.register(p)
    ticks = []

    async def tick(seconds):
        ticks.append(seconds)

    cards.HEARTBEAT_SECONDS, saved = 0.02, cards.HEARTBEAT_SECONDS
    try:
        assert await cards.wait_for(p["proposal_id"], timeout=0.1, on_tick=tick) is None
    finally:
        cards.HEARTBEAT_SECONDS = saved
    assert ticks and cards.waiting_count() == 0
    google.calls.clear()
    assert (await cards.decide("jack", p["proposal_id"], "apply"))["status"] == "expired" and google.writes() == []


@run_async
async def test_a_cancelled_turn_stops_waiting_and_leaves_nothing_behind(google):
    p = proposal_for()
    cards.register(p)
    waiter = asyncio.create_task(cards.wait_for(p["proposal_id"]))
    await asyncio.sleep(0.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cards.waiting_count() == 0


@run_async
async def test_a_press_that_arrives_before_the_wait_starts_is_not_lost(google):
    p = proposal_for()
    cards.register(p)
    await cards.decide("jack", p["proposal_id"], "decline")
    assert (await cards.wait_for(p["proposal_id"]))["kind"] == "declined"


@run_async
async def test_a_failing_tick_does_not_end_the_wait(google):
    p = proposal_for()
    cards.register(p)

    async def bad_tick(seconds):
        raise RuntimeError("stream closed")

    cards.HEARTBEAT_SECONDS, saved = 0.02, cards.HEARTBEAT_SECONDS
    try:
        waiter = asyncio.create_task(cards.wait_for(p["proposal_id"], timeout=2, on_tick=bad_tick))
        await asyncio.sleep(0.08)
        await cards.decide("jack", p["proposal_id"], "decline")
        assert (await waiter)["kind"] == "declined"
    finally:
        cards.HEARTBEAT_SECONDS = saved


def test_the_loop_is_told_what_happened_and_what_to_do_next():
    p = {"title": TITLE}
    assert cards.result_text(p, {"kind": "applied", "message": "Deleted it."}).startswith("APPLIED: Deleted it.") and "Do not propose the same change again" in cards.result_text(p, {"kind": "applied", "message": "x"})
    declined = cards.result_text(p, {"kind": "declined", "message": ""})
    assert declined.startswith("DECLINED") and "nothing was changed" in declined and "Do not propose the same change again" in declined
    failed = cards.result_text(p, {"kind": "failed", "message": "That event is gone."})
    assert failed.startswith("FAILED: nothing was changed. That event is gone.") and "do not claim it was done" in failed
    nothing = cards.result_text(p, None)
    assert nothing.startswith("NO DECISION") and "10 minutes" in nothing and "nothing in their calendar was changed" in nothing
    assert "nothing in their document was changed" in cards.result_text({"subject": "document"}, None)          # the words follow what the proposal is about


def test_the_wait_is_ten_minutes_by_default_and_the_switch_turns_the_cards_off(monkeypatch):
    assert cards.WAIT_SECONDS == 600.0 and cards.enabled()
    for off in ("off", "0", "false", "no"):
        monkeypatch.setenv("CHANGE_CARDS", off)
        assert not cards.enabled()


# ---- through the tool agent ----------------------------------------------------------------------------------------------------------------

async def _drive(monkeypatch, google, on_card, *, change_cards=True, calls=None, username="jack", question="cancel my dentist appointment", before=None):
    """Runs tool_agent_node on the native engine with a fake loop that makes each call in `calls` through the real dispatcher, while the browser presses via `on_card(card)`.
    Records the tools offered (seen["tools"]), the menu (seen["menu"]), each observation and each streamed card."""
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state
    from backend.services import repo_checkout as rc

    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: TZ)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    seen = {"observations": [], "counts": recorded, "emit": emit, "pressed": 0}

    async def press_when_shown():
        """Presses the next card that is shown and not yet pressed (a call that shows no card simply ends this after a moment, and is cancelled)."""
        for _ in range(400):
            shown = [c.args[1] for c in emit.call_args_list if c.args[0] == "change_card"]
            if len(shown) > seen["pressed"]:
                card = shown[seen["pressed"]]
                seen["pressed"] += 1
                await on_card(card)
                return
            await asyncio.sleep(0.005)

    async def fake_native_run(**kwargs):
        seen["tools"] = [t["name"] for t in kwargs["tools"]]
        seen["menu"] = kwargs.get("tools")
        for call in calls or [{"action": "delete", "event_id": "evt1"}]:
            helper = asyncio.create_task(press_when_shown())
            decision = call if "tool_action" in call else {"tool_action": "propose_calendar_change", "args": call}
            seen["observations"].append(await kwargs["act"](decision))
            helper.cancel()
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_native_run)
    if before:
        before()
    await aw.tool_agent_node({**_state(question, username=username), "change_cards": change_cards})
    return seen


def press(decision):
    async def on_card(card):
        await cards.decide("jack", card["proposal_id"], decision)
    return on_card


@run_async
async def test_apply_streams_the_card_waits_makes_the_call_and_tells_the_loop(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"))
    shown = [c.args[1] for c in seen["emit"].call_args_list if c.args[0] == "change_card"]
    assert len(shown) == 1 and shown[0]["action"] == "delete" and shown[0]["title"] == TITLE and shown[0]["destructive"] is True
    assert "propose_calendar_change" in seen["tools"] and seen["observations"][0].startswith(f"APPLIED: Deleted \"{TITLE}\" from your calendar.")
    assert google.writes() == [("delete", "evt1", "none")] and cards.waiting_count() == 0
    assert {"calendar_change_shown": 1} in seen["counts"] and {"calendar_change_applied": 1} in seen["counts"]


@run_async
async def test_decline_tells_the_loop_not_to_propose_it_again_and_sends_nothing(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("decline"))
    assert seen["observations"][0].startswith("DECLINED") and google.writes() == [] and {"calendar_change_declined": 1} in seen["counts"]


@run_async
async def test_no_press_in_time_changes_nothing_and_is_counted(monkeypatch, google):
    monkeypatch.setattr(cards, "WAIT_SECONDS", 0.05)

    async def nobody(card):
        return None

    seen = await _drive(monkeypatch, google, nobody)
    assert seen["observations"][0].startswith("NO DECISION") and google.writes() == [] and {"calendar_change_timeout": 1} in seen["counts"] and cards.waiting_count() == 0


@run_async
async def test_a_change_that_fails_is_reported_as_failed_with_nothing_sent(monkeypatch, google):
    async def stale_then_press(card):
        google.events["evt1"]["etag"] = '"v2"'
        await cards.decide("jack", card["proposal_id"], "apply")

    seen = await _drive(monkeypatch, google, stale_then_press)
    assert seen["observations"][0].startswith("FAILED: nothing was changed. That event changed after I showed you the card") and google.writes() == [] and {"calendar_change_failed": 1} in seen["counts"]


@run_async
async def test_a_proposal_that_cannot_be_prepared_shows_no_card_and_is_counted(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), calls=[{"action": "delete"}])
    assert seen["observations"][0].startswith("ERROR: nothing was shown, because Look the event up first")
    assert not [c for c in seen["emit"].call_args_list if c.args[0] == "change_card"] and {"calendar_change_refused": 1} in seen["counts"] and cards.waiting_count() == 0


@run_async
async def test_an_event_id_the_model_made_up_is_refused_by_google_not_guessed_at(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), calls=[{"action": "delete", "event_id": "madeUpId"}])
    assert "no longer on your calendar" in seen["observations"][0] and google.writes() == []


@run_async
async def test_a_second_change_in_the_same_turn_is_its_own_card_and_the_seventh_is_refused(monkeypatch, google):
    google.events["evt2"] = event("evt2")
    calls = [{"action": "update", "event_id": "evt2", "summary": f"Name {n}"} for n in range(7)]
    seen = await _drive(monkeypatch, google, press("apply"), calls=calls)
    shown = [c for c in seen["emit"].call_args_list if c.args[0] == "change_card"]
    assert len(shown) == cards.MAX_PROPOSALS_PER_TURN == 6 and seen["observations"][5].startswith("APPLIED") and seen["observations"][6].startswith("ERROR: you have already proposed 6")
    assert {"calendar_change_refused": 1} in seen["counts"]


@run_async
async def test_inviting_people_after_reading_text_someone_else_wrote_is_refused_in_that_message(monkeypatch, google):
    class NoMail:
        def get_valid_access_token(self, username):
            raise aw.GoogleCalendarConnectionError("none")

    calls = [{"tool_action": "search_gmail", "args": {"query": "from:anyone"}}, {"action": "create", "summary": "Sync", "start": "2026-11-24T12:00", "attendees": ["new@x.org"]},
             {"action": "create", "summary": "Sync", "start": "2026-11-24T12:00"}]
    seen = await _drive(monkeypatch, google, press("apply"), calls=calls, before=lambda: monkeypatch.setattr(aw, "GoogleCalendarOAuth", NoMail))
    assert seen["observations"][1].startswith("ERROR: this message already read content written by someone else") and "people are not invited" in seen["observations"][1]
    assert seen["observations"][2].startswith("APPLIED: Added \"Sync\""), "the event without the invitation is still possible"
    assert [w[0] for w in google.writes()] == ["insert"] and google.writes()[0][2] == "none" and {"calendar_change_refused": 1} in seen["counts"]


@run_async
async def test_the_same_invitation_with_nothing_untrusted_read_is_shown_and_the_card_says_who_gets_an_email(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), calls=[{"action": "create", "summary": "Sync", "start": "2026-11-24T12:00", "attendees": ["new@x.org"]}])
    shown = [c.args[1] for c in seen["emit"].call_args_list if c.args[0] == "change_card"][0]
    assert shown["notice"] == "Google will email an invitation to new@x.org" and google.writes()[0][2] == "all"


@run_async
async def test_a_page_that_cannot_show_the_card_is_not_offered_the_tool_and_gets_plain_words_if_it_asks_anyway(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), change_cards=False)
    assert "propose_calendar_change" not in seen["tools"] and seen["observations"][0].startswith("ERROR: this page cannot show a change card")
    assert not [c for c in seen["emit"].call_args_list if c.args[0] == "change_card"] and google.calls == []


@run_async
async def test_the_switch_turns_the_tool_off_even_for_a_page_that_can_show_it(monkeypatch, google):
    monkeypatch.setenv("CHANGE_CARDS", "off")
    seen = await _drive(monkeypatch, google, press("apply"))
    assert "propose_calendar_change" not in seen["tools"] and google.writes() == []


@run_async
async def test_the_shared_guest_has_no_calendar_tool_and_cannot_use_it_if_it_asks(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), username="guest-recruiter@example.com")
    assert "propose_calendar_change" not in seen["tools"] and seen["observations"][0].startswith("ERROR: this page cannot show a change card") and google.calls == []


# ---- reading the calendar for a change -----------------------------------------------------------------------------------------------------------

@run_async
async def test_the_calendar_look_up_returns_ids_over_a_range_and_passes_the_words_through(monkeypatch, google):
    class Oauth:
        def get_valid_access_token(self, username):
            return "tok"

    asked = {}

    def fake_list(token, first, last, tz, query=None, max_results=25):
        asked.update(first=first, last=last, tz=tz, query=query)
        return [{"id": "abc123", "summary": "Dentist", "start": "2026-11-24T14:00:00-06:00", "end": "2026-11-24T15:00:00-06:00", "all_day": False, "location": "", "recurring_event_id": None,
                 "reminders": "default", "other_attendees": 0}]

    seen = await _drive(monkeypatch, google, press("apply"), before=lambda: (monkeypatch.setattr(aw, "GoogleCalendarOAuth", Oauth), monkeypatch.setattr(aw, "list_events_detailed", fake_list)),
                        calls=[{"tool_action": "list_google_calendar_events", "args": {"date": "2026-11-24", "end_date": "2026-11-26", "query": " dentist "}},
                               {"tool_action": "list_google_calendar_events", "args": {"date": "2026-11-24", "end_date": "2027-03-01"}}])
    assert asked == {"first": "2026-11-24", "last": "2026-11-26", "tz": TZ, "query": "dentist"}
    assert seen["observations"][0] == "- 2026-11-24T14:00:00-06:00: Dentist (until 2026-11-24T15:00:00-06:00; default reminders) [id: abc123]"
    assert seen["observations"][1].startswith("ERROR: Look at 31 days or fewer")


@run_async
async def test_an_empty_look_up_says_which_days_and_which_words_found_nothing(monkeypatch, google):
    class Oauth:
        def get_valid_access_token(self, username):
            return "tok"

    seen = await _drive(monkeypatch, google, press("apply"), before=lambda: (monkeypatch.setattr(aw, "GoogleCalendarOAuth", Oauth), monkeypatch.setattr(aw, "list_events_detailed", lambda *a, **k: [])),
                        calls=[{"tool_action": "list_google_calendar_events", "args": {"date": "2026-11-24"}},
                               {"tool_action": "list_google_calendar_events", "args": {"date": "2026-11-24", "end_date": "2026-11-30", "query": "gym"}}])
    assert seen["observations"][0] == "No events found on the connected Google Calendar for 2026-11-24."
    assert seen["observations"][1] == "No events found on the connected Google Calendar for 2026-11-24 to 2026-11-30 matching 'gym'."


# ---- the tool's declaration, the counters, the routing, the wiring ---------------------------------------------------------------------------------

def test_the_tool_has_a_native_declaration_with_what_it_needs_and_is_offered_only_from_its_menu_line():
    assert "propose_calendar_change" in nt.REGISTRY and "propose_calendar_change" in nt.NATIVE_DESCRIPTIONS and nt.REGISTRY["propose_calendar_change"]["required"] == ["action"]
    props = nt.REGISTRY["propose_calendar_change"]["properties"]
    assert {"event_id", "scope", "summary", "start", "end", "duration_minutes", "all_day", "location", "description", "attendees", "reminders", "reminders_default",
            "repeat_frequency", "repeat_interval", "repeat_count", "repeat_until", "repeat_weekdays"} <= set(props)
    assert props["reminders"]["items"]["required"] == ["minutes"] and props["attendees"]["items"] == {"type": "string"}
    assert [d["name"] for d in nt.declarations_from_menu(["- propose_calendar_change — args: action"])] == ["propose_calendar_change"]
    assert nt.declarations_from_menu(["- read_repo_file — args: path"])[0]["name"] == "read_repo_file"
    description = nt.NATIVE_DESCRIPTIONS["propose_calendar_change"]
    assert "WAITS" in description and "never guess an id" in description and "APPLIED" in description and "Google email them" in description
    assert {"end_date", "query"} <= set(nt.REGISTRY["list_google_calendar_events"]["properties"]) and "id" in nt.NATIVE_DESCRIPTIONS["list_google_calendar_events"]


def test_every_calendar_counter_is_one_the_tally_accepts_is_recorded_and_the_state_declares_the_flag():
    from backend.utils import self_counters
    from backend.state.graph_state import GraphState

    assert set(self_counters.CALENDAR_CHANGE_KEYS) == {"calendar_change_shown", "calendar_change_applied", "calendar_change_declined", "calendar_change_failed", "calendar_change_timeout", "calendar_change_refused"}
    assert set(self_counters.CALENDAR_CHANGE_KEYS) <= set(self_counters.KEYS) and "change_cards" in GraphState.__annotations__
    # The tally keys are built from a prefix and a suffix by the shared handler (_propose_change): both halves must be there, and the loop tests above prove each key really is recorded.
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert '"calendar_change"' in workflow
    for suffix in ("shown", "applied", "declined", "failed", "timeout", "refused"):
        assert f'f"{{prefix}}_{suffix}"' in workflow, f"{suffix} is never recorded"


def _plan(monkeypatch, message, *, flags=None, intent="conversational", cards_on=True, username="jack", connected=True):
    from langchain_core.messages import HumanMessage

    monkeypatch.setattr(aw, "get_calendar_connection_status", lambda u: {"connected": connected})
    state = {"username": username, "messages": [HumanMessage(content=message)], "reasoner_flags": flags or {}, "change_cards": cards_on}
    return aw.build_agent_plan(intent, state)["agents"]


@pytest.mark.parametrize("flags,intent", [({"needs_update_calendar_event": True}, "conversational"), ({"needs_create_calendar_event": True}, "conversational"),
                                          ({}, "update_calendar_event"), ({}, "create_calendar_event")])
def test_a_google_calendar_write_goes_to_the_tool_agent_when_the_page_can_show_the_card(monkeypatch, flags, intent):
    agents = _plan(monkeypatch, "cancel my dentist appointment on Tuesday", flags=flags, intent=intent)
    assert "tool_agent" in agents and "propose_write" not in agents


@pytest.mark.parametrize("kwargs,why", [
    ({"cards_on": False}, "a page that cannot show the card"), ({"username": "guest-recruiter@example.com"}, "the shared guest"), ({"connected": False}, "no Google Calendar connected"),
])
def test_everyone_else_keeps_the_older_draft_path_so_nothing_regresses(monkeypatch, kwargs, why):
    for flags, intent in (({"needs_update_calendar_event": True}, "conversational"), ({"needs_create_calendar_event": True}, "conversational")):
        agents = _plan(monkeypatch, "move my 2pm to 3pm", flags=flags, intent=intent, **kwargs)
        assert "propose_write" in agents and "tool_agent" not in agents, why


def test_asking_for_outlook_keeps_the_older_path_even_when_the_card_could_be_shown(monkeypatch):
    for flags in ({"needs_update_calendar_event": True, "needs_calendar_lookup": True}, {"needs_create_calendar_event": True}):
        agents = _plan(monkeypatch, "move my Outlook meeting to 3pm", flags=flags)
        assert "propose_write" in agents and "tool_agent" not in agents


def test_a_plain_calendar_question_is_routed_as_before(monkeypatch):
    assert "tool_agent" in _plan(monkeypatch, "what is on my calendar tomorrow", flags={"needs_calendar_lookup": True})


def test_the_endpoint_the_request_flag_the_stream_event_and_the_card_are_wired_both_ends():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = app_source[app_source.index('@app.post("/api/chat/change/{proposal_id}/decision")'):]
    block = block[:block.index("\n\n\n")]
    assert "Depends(get_member_user)" in block and "change_cards.decide(current_user.get(\"sub\")" in block
    assert "change_cards: bool = False" in app_source and '"change_cards": bool(request.change_cards)' in app_source
    assert 'event.get("name") == "change_card"' in app_source and "'event': 'change_card'" in app_source
    api_source = (ROOT / "local" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "change_cards: true" in api_source and "/api/chat/change/${encodeURIComponent(proposalId)}/decision" in api_source and "decideChange," in api_source
    chat = (ROOT / "local" / "src" / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    assert "payload.event === 'change_card'" in chat and "parseChangeCard(payload)" in chat and "<ChangeCard" in chat
    card = (ROOT / "local" / "src" / "components" / "ChangeCard.tsx").read_text(encoding="utf-8")
    assert "api.decideChange(card.proposal_id, decision)" in card
    assert "propose_calendar_change: 'calendar'" in (ROOT / "local" / "src" / "components" / "stepKinds.ts").read_text(encoding="utf-8")


def test_the_decision_endpoint_is_for_members_only_and_a_press_with_nobody_waiting_changes_nothing(monkeypatch, google):
    from fastapi.testclient import TestClient
    from backend.auth import isolation_auth as ia
    from backend.tests.import_app import import_app

    app_module = import_app()
    client = TestClient(app_module.app, raise_server_exceptions=False)
    refused = client.post("/api/chat/change/abc/decision", json={"decision": "apply"}, headers={"X-Principal": "guest"})
    assert refused.status_code in (401, 403)
    app_module.app.dependency_overrides[ia.get_member_user] = lambda: {"sub": "jack"}
    try:
        google.calls.clear()
        none = client.post("/api/chat/change/abc/decision", json={"decision": "apply"})
        assert none.status_code == 200 and none.json() == {"status": "expired", "message": ""}
        p = proposal_for()
        cards.register(p)
        google.calls.clear()
        declined = client.post(f"/api/chat/change/{p['proposal_id']}/decision", json={"decision": "decline"})
        assert declined.json() == {"status": "declined", "message": ""} and google.writes() == []
    finally:
        app_module.app.dependency_overrides.pop(ia.get_member_user, None)


def test_the_calendar_modules_log_nothing_the_persons_calendar_holds():
    """Fixed sentences with a status or a kind: never a title, a time, an address or an id."""
    import ast
    for name, allowed in (("change_cards.py", 2), ("calendar_changes_service.py", 1)):
        source = (ROOT / "backend" / "services" / name).read_text(encoding="utf-8")
        logs = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("info", "warning", "error", "exception", "debug")]
        assert len(logs) == allowed, name
        for call in logs:
            assert isinstance(call.args[0], ast.Constant), f"{name}: a log line must be a fixed sentence, not built from data"
            assert not any(isinstance(arg, (ast.JoinedStr, ast.BinOp)) for arg in call.args), name
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    body = workflow[workflow.index("async def _propose_change"):workflow.index("async def _run_local_check")]
    assert "logger." not in body
