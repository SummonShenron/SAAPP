"""The new-account welcome cards (backend/utils/example_questions.py onboarding_cards; the first-ten-minutes work, 2026-11-07): what a brand-new signed-in account sees on its first screen."""
import random

from backend.utils import example_questions as eq
from backend.utils.example_questions import ExampleInputs

NEW = dict(onboarding=True, owns_scope=True)


def cards(inputs, seed=0, limit=4):
    return eq.build_welcome_cards(inputs, limit=limit, rng=random.Random(seed))


def kinds(out):
    return [c.get("kind") for c in out]


def test_a_brand_new_account_sees_the_four_things_an_account_adds_in_the_order_the_guest_cards_promised_them():
    for seed in range(10):
        out = cards(ExampleInputs(**NEW), seed)
        assert kinds(out) == ["demo", "remember", "runner", "document"], seed
        assert {c["label"] for c in out} == {eq.LABEL_SETUP}


def test_each_card_leads_somewhere_real():
    demo, remember, runner, document = cards(ExampleInputs(**NEW))
    assert demo["demo"] is True and "demo project" in demo["text"] and "fix it" in demo["text"] and "action" not in demo      # sent as a question, on the demo project
    assert remember["action"] == "prefill" and remember["prompt"].startswith("Remember that ")                              # left in the box to edit, not sent
    assert runner["action"] == "open" and runner["path"] == "/account"                                                      # the runner is set up on the Account page
    assert document["action"] == "open" and document["path"] == "/self-service"                                             # where a document is added to the knowledge base


def test_the_photo_card_is_not_among_them_and_the_plain_how_to_question_the_document_card_replaces_is_gone():
    out = cards(ExampleInputs(**NEW))
    assert eq.ATTACH_CARD not in out and not any(c.get("action") == "attach" for c in out)
    assert not any(c["text"] == eq.HOW_TO_ADD_A_DOCUMENT for c in out)


def test_a_step_that_is_done_is_not_offered_again_and_the_screen_is_filled_from_what_they_have():
    out = cards(ExampleInputs(**NEW, folder_done=True))
    assert kinds(out)[:3] == ["remember", "runner", "document"] and "demo" not in kinds(out)
    out = cards(ExampleInputs(**NEW, folder_done=True, memory_done=True, runner_done=True, documents_done=True, doc_names=["notes.pdf"], has_memories=True))
    assert not any(c.get("kind") for c in out)                                                                                # all four done: the usual cards
    assert eq.LABEL_YOURS in {c["label"] for c in out}                                                                       # led by their own stuff
    two_left = cards(ExampleInputs(**NEW, folder_done=True, memory_done=True, doc_names=["notes.pdf"], documents_done=True))
    assert kinds(two_left)[:1] == ["runner"] and len(two_left) == 4


def test_the_document_card_needs_a_knowledge_base_of_their_own():
    out = cards(ExampleInputs(onboarding=True, owns_scope=False))
    assert "document" not in kinds(out) and kinds(out)[:3] == ["demo", "remember", "runner"]


def test_a_shared_identity_and_anyone_the_server_does_not_mark_as_new_get_no_onboarding_cards():
    assert eq.onboarding_cards(ExampleInputs()) == []                                                                         # the default: nothing changes for the cards that already existed
    assert eq.onboarding_cards(ExampleInputs(onboarding=False, owns_scope=True)) == []
    assert not any(c.get("kind") for c in cards(ExampleInputs(owns_scope=True, calendar_available=False)))


def test_only_four_cards_ever_show_and_the_cards_are_copies_so_one_cannot_change_the_next():
    out = cards(ExampleInputs(**NEW, calendar_connected=True, target_repo="x/y"), limit=4)
    assert len(out) == 4
    out[0]["text"] = "changed"
    assert cards(ExampleInputs(**NEW))[0]["text"] == eq.DEMO_CARD["text"]


def test_the_demo_card_is_the_same_request_the_guest_demo_card_makes():
    from pathlib import Path

    guest_cards = Path("local/src/welcomeCards.ts").read_text(encoding="utf-8")
    assert eq.DEMO_CARD["text"] in guest_cards                                                                                # one wording, tested against the demo project by test_demo_project.py


def test_gather_reads_what_the_person_has_done_from_their_real_state(monkeypatch):
    from backend.tests.test_example_questions import DIRECTORY, _patch

    _patch(monkeypatch)
    monkeypatch.setattr("backend.services.local_workspace.workspace_status", lambda u: {"connected": True})
    monkeypatch.setattr("backend.utils.activation.activated", lambda db, u: {"memory"})
    monkeypatch.setattr("backend.utils.runner_jobs.runners_for", lambda db, u, now=None: [{"connected": False}])
    inputs = eq.gather_example_inputs("user_1", "All", DIRECTORY)
    assert inputs.onboarding and inputs.folder_done and inputs.memory_done and not inputs.runner_done
    monkeypatch.setattr("backend.services.local_workspace.workspace_status", lambda u: {"connected": False})
    monkeypatch.setattr("backend.utils.activation.activated", lambda db, u: {"folder", "runner", "document"})
    inputs = eq.gather_example_inputs("user_1", "All", DIRECTORY)
    assert inputs.folder_done and inputs.runner_done and inputs.documents_done                                              # done once stays done: an expired folder copy does not bring the demo card back


def test_a_shared_identity_is_never_marked_as_new(monkeypatch):
    from backend.tests.test_example_questions import _patch

    _patch(monkeypatch)
    inputs = eq.gather_example_inputs("guest_bty", "All", {"guest_bty": {"username": "guest_bty", "groups": ["Affiliate_A"]}})
    assert not inputs.onboarding
