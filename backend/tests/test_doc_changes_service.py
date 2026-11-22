"""Preparing and applying a Google Docs change (backend/services/doc_changes_service.py). Google is replaced by fakes that record every call, so each test can say exactly what was and was not sent: `prepare`
changes NOTHING, and `apply` sends only the requests that were stored with the card, guarded by the revision the card was drawn from."""
import json
import logging
from types import SimpleNamespace

import pytest

from backend.services import doc_changes_service as svc
from backend.services.google_calendar_oauth import GoogleCalendarConnectionError, GoogleCalendarTemporaryError
from backend.tests.test_doc_changes import DOC_ID, LAST_IS_HEADING, NOTES, make_doc
from backend.utils import doc_changes as dc
from backend.utils.doc_changes import DocChangeError

NAME = "Quarterly board minutes"
SECRET = "Dr Okafor's root canal results"          # text a document holds: none of it may reach a log or an error message


class HttpError(Exception):
    def __init__(self, status, reason=None):
        super().__init__(f"HTTP {status}: {NAME} {SECRET}")
        self.resp = SimpleNamespace(status=status)
        detail = {"error": {"message": SECRET, "errors": [{"reason": reason}] if reason else []}}
        self.content = json.dumps(detail).encode("utf-8")


class FakeGoogle:
    def __init__(self, doc, mime=dc.DOC_MIME, shared=False, trashed=False):
        self.doc, self.mime, self.shared, self.trashed = doc, mime, shared, trashed
        self.calls = []
        self.fail_on = {}                       # "info" | "get_document" | "create" | "batch" -> an exception to raise
        self.batch_reply = {"replies": [{"replaceAllText": {"occurrencesChanged": 2}}]}

    def _maybe_fail(self, where):
        if where in self.fail_on:
            raise self.fail_on[where]

    def get_file_info(self, token, file_id):
        self.calls.append(("info", file_id))
        self._maybe_fail("info")
        return {"id": file_id, "name": NAME, "mimeType": self.mime, "trashed": self.trashed, "shared": self.shared, "webViewLink": f"https://docs.example/{file_id}"}

    def get_document(self, token, doc_id):
        self.calls.append(("get_document", doc_id))
        self._maybe_fail("get_document")
        return self.doc

    def create_document(self, token, title):
        self.calls.append(("create", title))
        self._maybe_fail("create")
        return {"documentId": "newdoc1234567890", "title": title}

    def batch_update(self, token, doc_id, requests, required_revision=None):
        self.calls.append(("batch", doc_id, requests, required_revision))
        self._maybe_fail("batch")
        return self.batch_reply

    def writes(self):
        return [c for c in self.calls if c[0] in ("create", "batch")]


@pytest.fixture()
def google(monkeypatch):
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


def prep(**args):
    return svc.prepare("jack", args)


# ---- preparing: nothing is changed ---------------------------------------------------------------------------------------------------------------

def test_a_new_document_is_prepared_without_calling_google_at_all(google):
    p = prep(action="create", title="Trip plan", text="Day 1")
    assert google.calls == [] and p["request"]["kind"] == "create" and p["card"]["heading"] == "Create a Google Doc" and p["title"] == "Trip plan" and p["subject"] == "document" and callable(p["apply"])
    assert p["link"] is None and len(p["proposal_id"]) == 32


def test_an_append_reads_only_what_the_document_is_and_writes_nothing(google):
    p = prep(action="append", doc_id=DOC_ID, text="New line")
    assert google.calls == [("info", DOC_ID)] and google.writes() == []                # no need to read the whole document to add to its end
    assert p["title"] == NAME and p["request"]["requests"] == [{"insertText": {"endOfSegmentLocation": {}, "text": "\nNew line"}}] and p["request"]["required_revision"] is None
    assert p["link"] == f"https://docs.example/{DOC_ID}"


def test_a_replace_counts_the_places_it_will_change_and_is_guarded_by_the_revision_it_read(google):
    p = prep(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4")
    assert google.calls == [("info", DOC_ID), ("get_document", DOC_ID)] and google.writes() == []
    assert {r["label"]: r["value"] for r in p["card"]["rows"]}["Found"] == "2 places, all changed" and p["request"]["required_revision"] == "revA" and p["card"]["destructive"] is True


def test_an_insert_under_a_heading_is_built_at_the_real_index_and_guarded(google):
    p = prep(action="insert_after_heading", doc_id=DOC_ID, heading="Action Items", text="Email Kim")
    assert p["request"]["required_revision"] == "revA" and p["request"]["requests"][0]["insertText"]["text"] == "Email Kim\n" and google.writes() == []
    assert p["request"]["heading"] == "Action Items"


def test_the_card_says_when_the_document_is_shared(google):
    google.shared = True
    assert "shared" in prep(action="append", doc_id=DOC_ID, text="x")["card"]["notice"]
    google.shared = False
    assert prep(action="append", doc_id=DOC_ID, text="x")["card"]["notice"] == "Only you can open this document."


def test_the_default_document_is_used_only_when_no_document_is_named(google, monkeypatch):
    monkeypatch.setattr(svc, "get_user_target_doc_id", lambda username: "defaultdoc123456789")
    p = prep(action="append", text="Goes to the default")
    assert google.calls == [("info", "defaultdoc123456789")] and p["request"]["doc_id"] == "defaultdoc123456789"
    google.calls.clear()
    named = prep(action="append", doc_id=DOC_ID, text="Named")
    assert google.calls == [("info", DOC_ID)] and named["request"]["doc_id"] == DOC_ID


@pytest.mark.parametrize("setup,args,fragment", [
    (lambda g: None, {"action": "append", "text": "x"}, "Tell me which document"),
    (lambda g: setattr(g, "mime", "application/vnd.google-apps.spreadsheet"), {"action": "append", "doc_id": DOC_ID, "text": "x"}, "not a Google Doc"),
    (lambda g: setattr(g, "trashed", True), {"action": "append", "doc_id": DOC_ID, "text": "x"}, "in the trash"),
    (lambda g: None, {"action": "replace", "doc_id": DOC_ID, "find": "Q9", "replace_with": "Q10"}, "can't find that exact phrase"),
    (lambda g: None, {"action": "insert_after_heading", "doc_id": DOC_ID, "heading": "Next steps", "text": "x"}, "can't find a heading"),
    (lambda g: setattr(g, "doc", make_doc([("t", "TITLE")] + [("Q3 again", "NORMAL_TEXT")] * 51)), {"action": "replace", "doc_id": DOC_ID, "find": "Q3", "replace_with": "Q4"}, "more than I change at once"),
    (lambda g: None, {"action": "explode"}, "create, append, replace or insert_after_heading"),
])
def test_what_cannot_be_prepared_is_refused_before_any_card_and_after_no_write(google, setup, args, fragment):
    setup(google)
    with pytest.raises(DocChangeError) as refused:
        prep(**args)
    assert fragment in str(refused.value) and google.writes() == []


def test_a_phrase_must_match_the_documents_own_capitals(google):
    with pytest.raises(DocChangeError):
        prep(action="replace", doc_id=DOC_ID, find="call sam about q3.", replace_with="x")
    assert prep(action="replace", doc_id=DOC_ID, find="Call Sam about Q3.", replace_with="x")["request"]["required_revision"] == "revA"


@pytest.mark.parametrize("status,reason,fragment", [
    (404, None, "no longer in your Drive"), (403, "SERVICE_DISABLED", "isn't enabled for SAAPP"), (403, "insufficientPermissions", "Reconnect Google"),
    (403, None, "don't have edit access"), (401, None, "no longer accepts the connection"), (429, None, "busy right now"), (503, None, "busy right now"), (400, None, "did not accept that"),
])
def test_a_google_failure_while_looking_up_becomes_a_sentence_without_googles_own_words(google, caplog, status, reason, fragment):
    google.fail_on["info"] = HttpError(status, reason)
    with caplog.at_level(logging.DEBUG, logger="SASS Logger"):
        with pytest.raises(DocChangeError) as refused:
            prep(action="append", doc_id=DOC_ID, text="x")
    assert fragment in str(refused.value) and NAME not in str(refused.value) and SECRET not in str(refused.value)
    assert NAME not in caplog.text and SECRET not in caplog.text, "nothing a document holds is ever logged"


def test_connection_scope_and_account_problems_say_what_to_do(monkeypatch, google):
    class Missing:
        def get_valid_access_token(self, username):
            raise GoogleCalendarConnectionError("no connection")

    class Flaky:
        def get_valid_access_token(self, username):
            raise GoogleCalendarTemporaryError("Google is slow to answer; try again in a moment.")

    monkeypatch.setattr(svc, "GoogleCalendarOAuth", Missing)
    with pytest.raises(DocChangeError) as none:
        prep(action="create", title="x")
    assert "Connect it under Integrations" in str(none.value)
    monkeypatch.setattr(svc, "GoogleCalendarOAuth", Flaky)
    with pytest.raises(DocChangeError) as slow:
        prep(action="create", title="x")
    assert "try again in a moment" in str(slow.value)
    monkeypatch.undo()
    monkeypatch.setattr(svc, "has_granted_scope", lambda username, scope: False)
    monkeypatch.setattr(svc, "GoogleCalendarOAuth", type("Ok", (), {"get_valid_access_token": lambda self, u: "tok"}))
    with pytest.raises(DocChangeError) as scope:
        prep(action="create", title="x")
    assert "Reconnect Google" in str(scope.value)
    monkeypatch.setattr(svc, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(svc, "CALENDAR_LOCKED_USERS", {"jack"})
    with pytest.raises(DocChangeError) as locked:
        prep(action="create", title="x")
    assert "not available for this account" in str(locked.value)


# ---- applying: only the stored requests ------------------------------------------------------------------------------------------------------------

def test_applying_a_new_document_makes_it_then_puts_the_text_in_it(google):
    p = prep(action="create", title="Trip plan", text="Day 1")
    msg = svc.apply(p)
    assert google.calls == [("create", "Trip plan"), ("batch", "newdoc1234567890", [{"insertText": {"location": {"index": 1}, "text": "Day 1"}}], None)]
    assert msg == "Created \"Trip plan\". Link: https://docs.google.com/document/d/newdoc1234567890/edit"


def test_a_new_empty_document_makes_no_second_call(google):
    svc.apply(prep(action="create", title="Blank"))
    assert google.calls == [("create", "Blank")]


def test_if_the_text_cannot_be_put_in_the_new_document_the_person_is_told_it_exists_and_where(google):
    p = prep(action="create", title="Trip plan", text="Day 1")
    google.fail_on["batch"] = HttpError(500)
    with pytest.raises(DocChangeError) as half:
        svc.apply(p)
    assert "created \"Trip plan\" but could not put the text in it" in str(half.value) and "newdoc1234567890/edit" in str(half.value)


def test_applying_an_append_sends_exactly_the_stored_request_and_says_how_much_was_added(google):
    p = prep(action="append", doc_id=DOC_ID, text="New line")
    google.calls.clear()
    msg = svc.apply(p)
    assert google.calls == [("batch", DOC_ID, [{"insertText": {"endOfSegmentLocation": {}, "text": "\nNew line"}}], None)]
    assert msg == f"Added the text (8 characters) to the end of \"{NAME}\". Link: https://docs.google.com/document/d/{DOC_ID}/edit"


def test_applying_a_replace_is_guarded_by_the_revision_and_reports_how_many_places_changed(google):
    p = prep(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4")
    google.calls.clear()
    msg = svc.apply(p)
    assert google.calls == [("batch", DOC_ID, [{"replaceAllText": {"containsText": {"text": "Q3", "matchCase": True}, "replaceText": "Q4"}}], "revA")]
    assert msg.startswith(f"Replaced the phrase in 2 places in \"{NAME}\"")
    google.batch_reply = {"replies": [{"replaceAllText": {"occurrencesChanged": 1}}]}
    assert "in 1 place in" in svc.apply(p)
    google.batch_reply = {"replies": [{"replaceAllText": {}}]}
    assert svc.apply(p).startswith(f"Nothing was replaced in \"{NAME}\"")


def test_applying_an_insert_under_a_heading_is_guarded_and_names_the_heading(google):
    p = prep(action="insert_after_heading", doc_id=DOC_ID, heading="Action Items", text="Email Kim")
    google.calls.clear()
    msg = svc.apply(p)
    assert google.calls[0][3] == "revA" and msg.startswith(f"Added the text under the heading \"Action Items\" in \"{NAME}\"")


def test_a_document_edited_since_the_card_was_drawn_is_left_alone_for_the_guarded_actions(google):
    p = prep(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4")
    google.fail_on["batch"] = HttpError(400)                  # Google refuses an update made against a revision that is no longer current
    with pytest.raises(DocChangeError) as stale:
        svc.apply(p)
    assert "edited after I showed you the card" in str(stale.value)
    unguarded = prep(action="append", doc_id=DOC_ID, text="x")
    with pytest.raises(DocChangeError) as other:
        svc.apply(unguarded)
    assert "did not accept that" in str(other.value) and "edited after" not in str(other.value)            # a 400 on an append is not "someone edited it"


def test_a_google_failure_while_writing_says_nothing_was_changed_and_leaks_nothing(google, caplog):
    p = prep(action="append", doc_id=DOC_ID, text=SECRET)
    google.fail_on["batch"] = HttpError(500)
    with caplog.at_level(logging.DEBUG, logger="SASS Logger"):
        with pytest.raises(DocChangeError) as busy:
            svc.apply(p)
    assert "nothing was changed" in str(busy.value).lower() and NAME not in str(busy.value) and SECRET not in str(busy.value)
    assert NAME not in caplog.text and SECRET not in caplog.text and "status 500" in caplog.text


def test_an_error_that_is_not_googles_still_becomes_a_document_error_not_a_crash(google):
    p = prep(action="append", doc_id=DOC_ID, text="x")
    google.fail_on["batch"] = RuntimeError("socket closed")
    with pytest.raises(DocChangeError):
        svc.apply(p)


def test_a_last_line_heading_document_can_still_be_added_under(google):
    google.doc = make_doc(LAST_IS_HEADING, revision="revZ")
    p = prep(action="insert_after_heading", doc_id=DOC_ID, heading="Open questions", text="Who owns it?")
    assert p["request"]["requests"][0]["insertText"]["text"] == "\nWho owns it?" and p["request"]["required_revision"] == "revZ"
