"""A new account's first steps to value, and the welcome cards that lead to them (backend/utils/activation.py; docs/guest-mode-plan.md and the first-ten-minutes work, 2026-11-07)."""
from pathlib import Path

import pytest

from backend.utils import activation as act, self_counters

APP = Path("app.py").read_text(encoding="utf-8")


class Settings:
    """Just enough of the settings collection: one document per username, with dotted $set."""

    def __init__(self):
        self.docs = {}
        self.fail = False

    def find_one(self, query, projection=None):
        if self.fail:
            raise RuntimeError("the database is down")
        return self.docs.get(query["username"])

    def update_one(self, query, update, upsert=False):
        if self.fail:
            raise RuntimeError("the database is down")
        doc = self.docs.setdefault(query["username"], {"username": query["username"]})
        for dotted, value in update["$set"].items():
            head, tail = dotted.split(".")
            doc.setdefault(head, {})[tail] = value


class DB:
    def __init__(self):
        self.user_settings = Settings()

    def __getitem__(self, name):
        assert name == "user_settings"
        return self.user_settings


@pytest.fixture
def counted(monkeypatch):
    seen = []
    monkeypatch.setattr(act, "record_turn_counts", lambda counts, *a, **k: seen.append(dict(counts)))
    return seen


def test_the_names_are_the_counter_groups_and_letters_only():
    assert self_counters._GROUPS["activation"] == act.EVENTS and self_counters._GROUPS["welcome_card"] == act.KINDS
    for event in act.EVENTS:
        assert f"activation_{event}" in self_counters.KEYS
    for kind in act.KINDS:
        assert f"welcome_card_{kind}" in self_counters.KEYS


def test_a_step_is_noted_and_counted_once_per_person(counted):
    db = DB()
    assert act.mark(db, "jack@example.com", "folder") is True
    assert act.mark(db, "jack@example.com", "folder") is False                  # already noted: not counted twice
    assert act.mark(db, "jack@example.com", "memory") is True
    assert act.mark(db, "ann@example.com", "folder") is True                    # a different person starts their own
    assert act.activated(db, "jack@example.com") == {"folder", "memory"}
    assert counted == [{"activation_folder": 1}, {"activation_memory": 1}, {"activation_folder": 1}]          # a count and a name from the list; nothing about the person


def test_only_a_signed_in_person_and_a_known_step_is_noted(counted):
    db = DB()
    for who in ("guest-recruiter@example.com", "guest_bty", "guest", "", None):
        assert act.mark(db, who, "folder") is False, who
    for event in ("", "unknown", "FOLDER", None, 5):
        assert act.mark(db, "jack@example.com", event) is False, event
    assert act.mark(None, "jack@example.com", "folder") is False                # no database
    assert counted == [] and db.user_settings.docs == {}


def test_observe_notes_every_step_seen_and_returns_the_new_ones(counted):
    db = DB()
    assert act.observe(db, "jack@example.com", ["folder", "memory"]) == ["folder", "memory"]
    assert act.observe(db, "jack@example.com", ["folder", "memory", "runner"]) == ["runner"]
    assert act.observe(db, "jack@example.com", []) == []


def test_a_failing_database_never_reaches_the_person(counted):
    db = DB()
    db.user_settings.fail = True
    assert act.mark(db, "jack@example.com", "folder") is False
    assert act.activated(db, "jack@example.com") == set()
    assert counted == []


def test_a_welcome_card_press_is_counted_by_kind_for_a_signed_in_person_only(counted):
    assert act.count_card("jack@example.com", "demo") == "demo"
    assert [act.count_card("jack@example.com", k) for k in ("", "photo", None, 3, ["demo"])] == [None] * 5
    assert act.count_card("guest-recruiter@example.com", "demo") is None and act.count_card(None, "demo") is None
    assert counted == [{"welcome_card_demo": 1}]


def test_the_endpoints_note_the_steps_and_count_the_cards_off_the_event_loop():
    start = APP.index('@app.get("/api/example-questions")')
    block = APP[start: start + 1800]
    assert "if inputs.onboarding:" in block and "asyncio.to_thread(activation.observe, get_db(), username, done)" in block
    assert '("folder", inputs.folder_done), ("memory", inputs.memory_done), ("runner", inputs.runner_done), ("document", inputs.documents_done)' in block
    start = APP.index('@app.post("/api/welcome-click")')
    welcome = APP[start: start + 700]
    assert "Depends(get_current_user)" in welcome and "asyncio.to_thread(activation.count_card" in welcome and 'return {"ok": True}' in welcome
    assert 'class WelcomeClick(BaseModel):\n    kind: str' in APP
    decision = APP[APP.index('@app.post("/api/local-edits/{proposal_id}/decision")'):][:2400]
    assert 'asyncio.to_thread(activation.mark, get_db(), username, "applied")' in decision          # their first applied change
