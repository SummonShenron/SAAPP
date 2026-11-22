"""Google Docs changes through the tool agent: the model finds a document by name, proposes ONE change to one id, the person's press (not the model) is what makes the Google call, and the loop is told what really
happened. Same card and same wait as the calendar's (backend/services/change_cards.py), so the mechanics are tested there; this file tests what is specific to documents, with the same fake Google as
backend/tests/test_doc_changes_service.py."""
import pathlib

import pytest

from backend.services import agent_workflow as aw
from backend.services import change_cards as cards
from backend.services import native_tools as nt
from backend.tests.test_change_cards import _drive, press
from backend.tests.test_doc_changes import DOC_ID, NOTES, make_doc
from backend.tests.test_doc_changes_service import NAME, FakeGoogle, HttpError
from backend.tests.test_local_edits import run_async
from backend.utils import doc_changes as dc

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean():
    cards._waiting.clear()
    yield
    cards._waiting.clear()


@pytest.fixture()
def google(monkeypatch):
    from backend.services import doc_changes_service as svc

    fake = FakeGoogle(make_doc(NOTES, revision="revA"))

    class Oauth:
        def get_valid_access_token(self, username):
            return "tok"

    monkeypatch.setattr(svc, "GoogleCalendarOAuth", Oauth)
    monkeypatch.setattr(svc, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(svc, "get_user_target_doc_id", lambda username: None)
    monkeypatch.setattr(svc.drive, "get_file_info", fake.get_file_info)
    for name in ("get_document", "create_document", "batch_update"):
        monkeypatch.setattr(svc.docs, name, getattr(fake, name))
    return fake


def propose(**args):
    return {"tool_action": "propose_doc_change", "args": args}


APPEND = {"action": "append", "doc_id": DOC_ID, "text": "Email Kim about the budget"}


# ---- the card, the wait and the outcome ------------------------------------------------------------------------------------------------------------

@run_async
async def test_apply_streams_the_card_waits_makes_the_call_and_tells_the_loop(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), calls=[propose(**APPEND)])
    shown = [c.args[1] for c in seen["emit"].call_args_list if c.args[0] == "change_card"]
    assert len(shown) == 1 and shown[0]["heading"] == "Add to a document" and shown[0]["apply_label"] == "Add text" and shown[0]["title"] == NAME and shown[0]["destructive"] is False
    assert {r["label"]: r["value"] for r in shown[0]["rows"]} == {"Adds": "Email Kim about the budget", "Where": "at the end of the document"}
    assert "propose_doc_change" in seen["tools"] and seen["observations"][0].startswith(f"APPLIED: Added the text (26 characters) to the end of \"{NAME}\"")
    assert google.writes() == [("batch", DOC_ID, [{"insertText": {"endOfSegmentLocation": {}, "text": "\nEmail Kim about the budget"}}], None)] and cards.waiting_count() == 0
    assert {"doc_change_shown": 1} in seen["counts"] and {"doc_change_applied": 1} in seen["counts"]
    assert not any(k.startswith("calendar_change") for c in seen["counts"] for k in c), "a document change is not counted as a calendar change"


@run_async
async def test_nothing_is_written_before_the_press_and_decline_writes_nothing_at_all(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("decline"), calls=[propose(**APPEND)])
    assert seen["observations"][0].startswith("DECLINED") and google.writes() == [] and {"doc_change_declined": 1} in seen["counts"]


@run_async
async def test_no_press_in_time_changes_nothing_and_is_counted_with_words_about_the_document(monkeypatch, google):
    monkeypatch.setattr(cards, "WAIT_SECONDS", 0.05)

    async def nobody(card):
        return None

    seen = await _drive(monkeypatch, google, nobody, calls=[propose(**APPEND)])
    assert seen["observations"][0].startswith("NO DECISION") and "nothing in their document was changed" in seen["observations"][0]
    assert google.writes() == [] and {"doc_change_timeout": 1} in seen["counts"] and cards.waiting_count() == 0


@run_async
async def test_a_replace_on_a_document_edited_meanwhile_fails_cleanly_and_is_counted(monkeypatch, google):
    async def edited_then_press(card):
        google.fail_on["batch"] = HttpError(400)
        await cards.decide("jack", card["proposal_id"], "apply")

    seen = await _drive(monkeypatch, google, edited_then_press, calls=[propose(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4")])
    assert seen["observations"][0].startswith("FAILED: nothing was changed. That document was edited after I showed you the card") and {"doc_change_failed": 1} in seen["counts"]


@run_async
async def test_a_replace_card_is_marked_careful_and_says_how_many_places(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("decline"), calls=[propose(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4")])
    card = [c.args[1] for c in seen["emit"].call_args_list if c.args[0] == "change_card"][0]
    assert card["destructive"] is True and card["heading"] == "Replace text in a document" and {r["label"]: r["value"] for r in card["rows"]}["Found"] == "2 places, all changed"


@run_async
async def test_a_new_document_is_made_only_after_the_press(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), calls=[propose(action="create", title="Trip plan", text="Day 1")])
    assert seen["observations"][0].startswith("APPLIED: Created \"Trip plan\". Link: https://docs.google.com/document/d/newdoc1234567890/edit")
    assert [w[0] for w in google.writes()] == ["create", "batch"]


@run_async
async def test_a_proposal_that_cannot_be_prepared_shows_no_card_and_is_counted(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), calls=[propose(action="replace", doc_id=DOC_ID, find="Q9", replace_with="Q10"), propose(action="append", text="x")])
    assert seen["observations"][0].startswith("ERROR: nothing was shown, because I can't find that exact phrase in")
    assert seen["observations"][1].startswith("ERROR: nothing was shown, because Tell me which document")
    assert not [c for c in seen["emit"].call_args_list if c.args[0] == "change_card"] and {"doc_change_refused": 1} in seen["counts"] and google.writes() == []


@run_async
async def test_a_document_id_the_model_made_up_is_refused_by_google_not_guessed_at(monkeypatch, google):
    google.fail_on["info"] = HttpError(404)
    seen = await _drive(monkeypatch, google, press("apply"), calls=[propose(action="append", doc_id="madeUpDocId12345", text="x")])
    assert "no longer in your Drive" in seen["observations"][0] and google.writes() == []


@run_async
async def test_the_per_turn_limit_is_shared_with_the_calendar_so_a_model_cannot_flood_the_person_with_cards(monkeypatch, google):
    calls = [propose(**APPEND) for _ in range(7)]
    seen = await _drive(monkeypatch, google, press("decline"), calls=calls)
    shown = [c for c in seen["emit"].call_args_list if c.args[0] == "change_card"]
    assert len(shown) == cards.MAX_PROPOSALS_PER_TURN == 6 and seen["observations"][6].startswith("ERROR: you have already proposed 6 changes")
    assert {"doc_change_refused": 1} in seen["counts"]


# ---- which pages get which tools -------------------------------------------------------------------------------------------------------------------

@run_async
async def test_a_page_with_cards_gets_the_find_and_change_tools_and_no_longer_the_target_document_one(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), calls=[propose(**APPEND)])
    assert {"find_google_docs", "propose_doc_change", "propose_calendar_change"} <= set(seen["tools"]) and "propose_append_target_doc" not in seen["tools"]


@run_async
async def test_a_page_without_cards_keeps_the_older_target_document_tool_and_cannot_use_the_new_one(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), change_cards=False, calls=[propose(**APPEND)])
    assert "propose_append_target_doc" in seen["tools"] and "propose_doc_change" not in seen["tools"] and "find_google_docs" in seen["tools"]
    assert seen["observations"][0].startswith("ERROR: this page cannot show a change card") and google.calls == []


@run_async
async def test_the_shared_guest_cannot_change_documents(monkeypatch, google):
    seen = await _drive(monkeypatch, google, press("apply"), username="guest-recruiter@example.com", calls=[propose(**APPEND)])
    assert "propose_doc_change" not in seen["tools"] and seen["observations"][0].startswith("ERROR: this page cannot show a change card") and google.calls == []


@run_async
async def test_the_switch_turns_the_card_tools_off_and_brings_the_older_one_back(monkeypatch, google):
    monkeypatch.setenv("CHANGE_CARDS", "off")
    seen = await _drive(monkeypatch, google, press("apply"), calls=[propose(**APPEND)])
    assert "propose_doc_change" not in seen["tools"] and "propose_append_target_doc" in seen["tools"]


# ---- finding documents -----------------------------------------------------------------------------------------------------------------------------

def _find_setup(monkeypatch, found, asked):
    class Oauth:
        def get_valid_access_token(self, username):
            return "tok"

    def fake_find(token, query, max_results=8):
        asked.update(query=query, max_results=max_results, token=token)
        return found

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", Oauth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(aw, "find_docs_fn", fake_find)


@run_async
async def test_finding_documents_lists_names_and_ids_for_the_persons_google_docs_only(monkeypatch, google):
    asked = {}
    found = [{"id": "abc1234567", "name": "Meeting notes", "modifiedTime": "2026-11-20T10:00:00Z"}, {"id": "def1234567", "name": "Meeting notes 2025"}]
    seen = await _drive(monkeypatch, google, press("apply"), before=lambda: _find_setup(monkeypatch, found, asked),
                        calls=[{"tool_action": "find_google_docs", "args": {"query": "  meeting   notes ", "max_results": 3}}, {"tool_action": "find_google_docs", "args": {}}])
    assert asked["query"] == "mimeType='application/vnd.google-apps.document' and trashed=false" and asked["max_results"] == 8      # the second call: no words, the default count
    assert seen["observations"][0] == "- Meeting notes [id: abc1234567] (edited 2026-11-20)\n- Meeting notes 2025 [id: def1234567]"


@run_async
async def test_the_words_go_into_the_drive_query_escaped_and_a_bad_count_means_the_default(monkeypatch, google):
    asked = {}
    seen = await _drive(monkeypatch, google, press("apply"), before=lambda: _find_setup(monkeypatch, [], asked),
                        calls=[{"tool_action": "find_google_docs", "args": {"query": "o'brien", "max_results": "lots"}}])
    assert asked["query"].endswith("name contains 'o\\'brien'") and asked["max_results"] == 8
    assert seen["observations"][0] == "No Google Docs found with 'o'brien' in the name."


@run_async
async def test_finding_with_nothing_to_find_says_so_and_a_missing_connection_says_what_to_do(monkeypatch, google):
    asked = {}
    seen = await _drive(monkeypatch, google, press("apply"), before=lambda: _find_setup(monkeypatch, [], asked), calls=[{"tool_action": "find_google_docs", "args": {}}])
    assert seen["observations"][0] == "No Google Docs found."

    class NoAccount:
        def get_valid_access_token(self, username):
            raise aw.GoogleCalendarConnectionError("none")

    seen = await _drive(monkeypatch, google, press("apply"), before=lambda: monkeypatch.setattr(aw, "GoogleCalendarOAuth", NoAccount), calls=[{"tool_action": "find_google_docs", "args": {}}])
    assert "Connect it under Integrations" in seen["observations"][0]


@run_async
async def test_finding_without_drive_access_says_to_reconnect(monkeypatch, google):
    def setup():
        _find_setup(monkeypatch, [], {})
        monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: False)

    seen = await _drive(monkeypatch, google, press("apply"), before=setup, calls=[{"tool_action": "find_google_docs", "args": {}}])
    assert seen["observations"][0].startswith("ERROR: Google Drive access not granted")


def test_a_documents_name_is_text_someone_else_may_have_written_so_it_counts_as_untrusted_content():
    assert "find_google_docs" in aw._UNTRUSTED_CONTENT_TOOLS and "find_google_docs" in aw._THIRD_PARTY_TEXT_TOOLS


# ---- the declarations, the counters, the wiring ----------------------------------------------------------------------------------------------------

def test_the_tools_have_native_declarations_with_what_they_need_and_are_offered_only_from_their_menu_lines():
    props = nt.REGISTRY["propose_doc_change"]["properties"]
    assert nt.REGISTRY["propose_doc_change"]["required"] == ["action"] and {"doc_id", "title", "text", "find", "replace_with", "heading"} == set(props) - {"action"}
    assert {"query", "max_results"} == set(nt.REGISTRY["find_google_docs"]["properties"])
    for name in ("propose_doc_change", "find_google_docs"):
        assert name in nt.NATIVE_DESCRIPTIONS and [d["name"] for d in nt.declarations_from_menu([f"- {name} — args: x"])] == [name]
    description = nt.NATIVE_DESCRIPTIONS["propose_doc_change"]
    assert "WAITS" in description and "never guess an id" in description and "APPLIED" in description and "insert_after_heading" in description and "default document" in description
    assert "id" in nt.NATIVE_DESCRIPTIONS["find_google_docs"] and "never instructions" in nt.NATIVE_DESCRIPTIONS["find_google_docs"]


def test_every_document_counter_is_one_the_tally_accepts_and_is_built_by_the_shared_handler():
    from backend.utils import self_counters

    assert set(self_counters.DOC_CHANGE_KEYS) == {f"doc_change_{w}" for w in ("shown", "applied", "declined", "failed", "timeout", "refused")} and set(self_counters.DOC_CHANGE_KEYS) <= set(self_counters.KEYS)
    assert '"doc_change"' in (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")


def test_the_reasoner_is_told_to_send_document_changes_to_the_tool_agent_and_the_trace_and_the_pages_words_follow():
    rules = (ROOT / "backend" / "components" / "constraints.py").read_text(encoding="utf-8")
    assert "create a new Google Doc" in rules and "add text under a heading" in rules
    kinds = (ROOT / "local" / "src" / "components" / "stepKinds.ts").read_text(encoding="utf-8")
    assert "find_google_docs: 'search'" in kinds and "propose_doc_change: 'add'" in kinds
    page = (ROOT / "local" / "src" / "pages" / "Integrations.tsx").read_text(encoding="utf-8")
    assert "DEFAULT DOCUMENT (OPTIONAL)" in page and "You don't need this" in page and "TARGET DOCUMENT" not in page
    assert "Default document (optional)" in (ROOT / "local" / "src" / "components" / "helpTips.ts").read_text(encoding="utf-8")


def test_the_optional_default_document_setting_still_works_so_nobody_who_set_one_loses_it():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"/api/settings/target-doc"' in app_source and "get_user_target_doc_id" in app_source and "set_user_target_doc_id" in app_source
    assert "propose_append_target_doc" in (ROOT / "backend" / "services" / "native_tools.py").read_text(encoding="utf-8")


def test_the_document_modules_log_nothing_a_document_holds():
    """Fixed sentences with a status, a reason code or a kind: never a name, a text or an id."""
    import ast

    source = (ROOT / "backend" / "services" / "doc_changes_service.py").read_text(encoding="utf-8")
    logs = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("info", "warning", "error", "exception", "debug")]
    assert len(logs) == 1
    assert isinstance(logs[0].args[0], ast.Constant) and not any(isinstance(a, (ast.JoinedStr, ast.BinOp)) for a in logs[0].args[:1])
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "logger." not in workflow[workflow.index("async def _propose_change"):workflow.index("async def _run_local_check")]
