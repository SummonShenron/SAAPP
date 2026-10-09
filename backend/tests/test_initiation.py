import asyncio
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import initiation as ini
from backend.utils import open_loops as ol
from backend.utils.time_utils import stamp

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)  # 10:00 Friday in Chicago
TZ = "America/Chicago"


# ---- an in-memory stand-in for the two collections this feature touches ---------------------------------------------

class _Result:
    def __init__(self, matched=0, deleted=0):
        self.matched_count = matched
        self.deleted_count = deleted


def _matches(doc, flt):
    for key, want in flt.items():
        have = doc.get(key)
        if isinstance(want, dict):
            if "$gt" in want and not (have is not None and have > want["$gt"]):
                return False
        elif have != want:
            return False
    return True


class _Col:
    def __init__(self):
        self.docs = []

    def create_index(self, *a, **k):
        pass

    def find(self, flt, projection=None):
        return [{k: v for k, v in d.items() if k != "_id"} for d in self.docs if _matches(d, flt)]

    def find_one(self, flt, projection=None):
        hits = self.find(flt)
        return hits[0] if hits else None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, flt, update, upsert=False):
        hits = [d for d in self.docs if _matches(d, flt)][:1]
        if not hits and upsert:
            doc = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            self.docs.append(doc)
            hits = [doc]
        for d in hits:
            d.update(update.get("$set", {}))
            for key, by in update.get("$inc", {}).items():
                d[key] = d.get(key, 0) + by
            for key, value in update.get("$max", {}).items():
                if d.get(key) is None or value > d[key]:
                    d[key] = value
        return _Result(matched=len(hits))


class _DB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, _Col())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(ol, "_index_ready", False)
    monkeypatch.delenv("PROACTIVE_OPENING_ENABLED", raising=False)
    monkeypatch.delenv("OPEN_LOOPS_ENABLED", raising=False)


def human(text="hello", when=NOW - timedelta(days=1)):
    return stamp(HumanMessage(content=text), when)


def ai(text="hi", when=NOW - timedelta(days=1), initiated=False):
    kwargs = {ini.INITIATED_KEY: True} if initiated else {}
    return stamp(AIMessage(content=text, additional_kwargs=kwargs), when)


def thread(*extra):
    return [human("first"), ai("reply"), *extra]


STATE = dict(enabled=True, last_initiated_at=None, unanswered=0, quiet_until=None)
LOOP = {"kind": "loop", "id": "a1", "text": "has a first date", "due_date": "2026-10-08"}
GOAL = {"kind": "goal", "id": "g1", "text": "Is training for a half marathon."}


# ---- the gates ------------------------------------------------------------------------------------------------------

def reason(state=None, transcript=None, **kwargs):
    return ini.refusal_reason({**STATE, **(state or {})}, transcript if transcript is not None else thread(), NOW, **kwargs)


def test_every_gate_passing_means_it_may_open():
    assert reason() is None


@pytest.mark.parametrize("state,transcript,kwargs,expected", [
    ({"enabled": False}, None, {}, "disabled"),
    ({}, None, {"is_guest": True}, "guest"),
    ({"quiet_until": NOW + timedelta(days=2)}, None, {}, "recent_risk"),
    ({"unanswered": 3}, None, {}, "ignored"),
    ({"last_initiated_at": NOW - timedelta(hours=19)}, None, {}, "too_soon"),
    ({}, [ai("hi")], {}, "new_thread"),
    ({}, [], {}, "new_thread"),
    ({}, [human(), ai("a opener", initiated=True)], {}, "already_waiting"),
    ({}, [human("x", NOW - timedelta(minutes=30)), ai("y", NOW - timedelta(minutes=29))], {}, "recent_activity"),
])
def test_each_gate_refuses_for_its_own_reason(state, transcript, kwargs, expected):
    assert reason(state, transcript, **kwargs) == expected


def test_it_may_open_again_once_the_wall_clock_cap_has_passed_and_after_a_quiet_week():
    assert reason({"last_initiated_at": NOW - timedelta(hours=21)}) is None
    assert reason({"quiet_until": NOW - timedelta(minutes=1)}) is None


def test_an_unstamped_old_thread_is_never_guessed_at():
    old = [HumanMessage(content="x"), AIMessage(content="y")]
    assert reason(transcript=old) == "unknown_gap"


def test_two_in_a_row_are_impossible_even_when_the_cap_has_elapsed():
    waiting = [human(), ai("first opener", initiated=True)]
    assert reason({"last_initiated_at": NOW - timedelta(days=5)}, waiting) == "already_waiting"


# ---- what it can be about -------------------------------------------------------------------------------------------

def anchor(loops=(), goal=None):
    find = (lambda: goal) if goal is not None else (lambda: None)
    return ini.pick_anchor("u", "2026-10-09", NOW, NOW - timedelta(days=1), lambda: list(loops), find)


def loop_doc(**kw):
    return {"id": "a1", "username": "u", "text": "has a first date", "due_date": "2026-10-08", "kind": "social", "status": ol.OPEN, **kw}


def test_a_due_open_loop_is_the_anchor_and_beats_a_quiet_goal():
    got = anchor([loop_doc()], goal=SimpleNamespace(id="g1", fact="Is training for a half marathon."))
    assert got == {"kind": "loop", "id": "a1", "text": "has a first date", "due_date": "2026-10-08"}


def test_with_no_due_loop_a_quiet_goal_is_the_anchor():
    got = anchor([loop_doc(due_date="2026-10-20")], goal=SimpleNamespace(id="g1", fact="Is training for a half marathon."))
    assert got["kind"] == "goal" and got["id"] == "g1"


def test_nothing_due_and_no_quiet_goal_means_nothing_to_say():
    assert anchor() is None
    assert anchor([loop_doc(status=ol.ASKED)]) is None


def test_a_failing_goal_lookup_means_nothing_to_say_not_an_error():
    def boom():
        raise RuntimeError("db down")

    assert ini.pick_anchor("u", "2026-10-09", NOW, NOW - timedelta(days=1), lambda: [], boom) is None


# ---- checking what was written ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "How did the first date go yesterday?",
    "You mentioned a first date coming up. How did it go?",
    "Did the half marathon training stay on track this month?",
])
def test_a_short_anchored_single_question_passes(text):
    a = LOOP if "date" in text else GOAL
    assert ini.opening_issue(text, a) is None, text


@pytest.mark.parametrize("text,fragment", [
    ("", "empty"),
    ("How did the first date go? And how are you feeling now?", "exactly one question"),
    ("The first date was yesterday, right.", "exactly one question"),
    ("How did the first date go!?", "exclamation"),
    ("How did the first date go 😊?", "emoji"),
    ("I wanted to check in: how did the first date go?", "wanted to check in"),
    ("I was wondering how the first date went. How did it go?", "wondering"),
    ("I couldn't stop thinking about your first date. How did it go?", "kept thinking"),
    ("Welcome back. How did the first date go?", "absence"),
    ("It's been a while. How did the first date go?", "absence"),
    ("While you were away I looked into dating tips. How did the first date go?", "away"),
    ("I looked into it for you. How did the first date go?", "claims work"),
    ("I missed you. How did the first date go?", "identity check"),
    ("How was your week? Anything fun planned?", "exactly one question"),
    ("How is the weather in your city today, by the way?", "never referred"),
    ("How did the first date go?\n\nAlso, here is a long story about my own week and various unrelated things to say.", "more than one short message"),
])
def test_an_opener_that_is_not_anchored_or_claims_a_wish_or_work_is_rejected(text, fragment):
    issue = ini.opening_issue(text, LOOP)
    assert issue and fragment in issue, (text, issue)


def test_an_opener_that_runs_long_is_rejected():
    long = "How did the first date go " + "really and truly " * 20 + "yesterday?"
    assert "words" in ini.opening_issue(long, LOOP)


@pytest.mark.parametrize("a", [LOOP, GOAL, {"kind": "loop", "id": "x", "text": "is presenting the quarterly roadmap to the executive team and the board about the migration plan and budget " * 2, "due_date": "2026-10-08"}])
def test_the_template_fallback_always_passes_its_own_checks(a):
    assert ini.opening_issue(ini.fallback_opening(a), a) is None


# ---- the prompt -----------------------------------------------------------------------------------------------------

def test_the_prompt_ties_the_message_to_the_anchor_and_forbids_wishes_absence_and_settings():
    prompt = ini.build_opening_prompt(LOOP, "2026-10-09", TZ, NOW)
    assert "YOU ARE OPENING THIS CONVERSATION" in prompt and "has a first date" in prompt and "Yesterday" in prompt
    for rule in ("ask exactly one question", "Don't say you were thinking about them", "you didn't exist between conversations",
                 "Don't say 'welcome back'", "don't mention settings"):
        assert rule in prompt, rule
    assert "ask how this is going" in ini.build_opening_prompt(GOAL, "2026-10-09", TZ, NOW)
    assert "was rejected because it ran to 60 words" in ini.build_opening_prompt(LOOP, "2026-10-09", TZ, NOW, "ran to 60 words")


def run(coro):
    return asyncio.run(coro)


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=answer)


def write(model, a=LOOP):
    return run(ini.write_opening(model, a, "2026-10-09", TZ, NOW))


def test_a_good_first_draft_is_used_as_written():
    out = write(Model("How did the first date go yesterday?"))
    assert out == {"text": "How did the first date go yesterday?", "fallback": False}


def test_a_bad_draft_gets_one_retry_that_names_the_problem():
    model = Model("I wanted to check in. How did the first date go?", "How did the first date go yesterday?")
    out = write(model)
    assert out["fallback"] is False and len(model.prompts) == 2
    assert "rejected because it says it wanted to check in" in model.prompts[1]


def test_two_bad_drafts_or_a_model_failure_fall_back_to_the_template():
    two_bad = write(Model("I missed you. How did the first date go?", "Welcome back! How did it go?"))
    assert two_bad["fallback"] is True and two_bad["text"] == ini.fallback_opening(LOOP)
    failed = write(Model(RuntimeError("model down")))
    assert failed["fallback"] is True and "?" in failed["text"]


def test_a_stray_follow_up_tag_and_wrapping_quotes_are_stripped_from_the_draft():
    out = write(Model('"How did the first date go yesterday?" <<<FOLLOW_UP: tell me more>>>'))
    assert out["text"] == "How did the first date go yesterday?"


# ---- the state on the settings document -----------------------------------------------------------------------------

def test_the_setting_defaults_off_and_is_saved_per_user():
    db = _DB()
    assert ini.load_state(db, "u")["enabled"] is False
    assert ini.set_enabled(db, "u", True) is True
    assert ini.load_state(db, "u")["enabled"] is True and ini.load_state(db, "other")["enabled"] is False


def test_unanswered_openings_are_counted_and_reset_only_when_there_is_something_to_reset():
    db = _DB()
    ini.set_enabled(db, "u", True)
    ini.record_initiation(db, "u", NOW)
    ini.record_initiation(db, "u", NOW + timedelta(days=1))
    state = ini.load_state(db, "u")
    assert state["unanswered"] == 2 and state["last_initiated_at"] == NOW + timedelta(days=1)
    ini.reset_unanswered(db, "u")
    assert ini.load_state(db, "u")["unanswered"] == 0 and ini.load_state(db, "u")["enabled"] is True


def test_a_safety_escalation_silences_openings_for_a_week_and_only_ever_extends_it():
    db = _DB()
    ini.note_risk(db, "u", NOW)
    assert ini.load_state(db, "u")["quiet_until"] == NOW + timedelta(days=7)
    ini.note_risk(db, "u", NOW - timedelta(days=3))  # an earlier one never shortens it
    assert ini.load_state(db, "u")["quiet_until"] == NOW + timedelta(days=7)
    ini.note_risk(db, "u", NOW + timedelta(days=2))
    assert ini.load_state(db, "u")["quiet_until"] == NOW + timedelta(days=9)


# ---- the whole decision ----------------------------------------------------------------------------------------------

class World:
    """A user with the setting on, an existing conversation last touched yesterday, and a loop whose day has passed."""

    def __init__(self, monkeypatch, *, enabled=True, loops=True, answers=("How did the first date go yesterday?",)):
        self.db = _DB()
        if enabled:
            ini.set_enabled(self.db, "u", True)
        if loops:
            ol.save_loops(self.db, "u", [{"text": "has a first date", "due_date": "2026-10-08", "kind": "social"}], NOW - timedelta(days=2))
        self.transcript = thread()
        self.saved = []
        self.counts = []
        self.model = Model(*answers)
        monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: self.counts.append(counts))
        monkeypatch.setattr("backend.utils.memory_utils.find_stale_goal_to_nudge", lambda username, interval_days=None: None)

    def open(self, **kwargs):
        return run(ini.maybe_open_conversation(
            "u", "s1", self.transcript, tz_name=TZ, invoke=self.model, save=lambda *a: self.saved.append(a),
            now=kwargs.pop("now", NOW), db=self.db, **kwargs,
        ))


def test_an_eligible_conversation_gets_one_anchored_message_that_is_saved_marked_and_counted(monkeypatch):
    w = World(monkeypatch)
    out = w.open()
    assert out["content"] == "How did the first date go yesterday?" and out["type"] == "ai" and out[ini.INITIATED_KEY] is True
    assert out["sent_at"] == NOW.isoformat()  # stamped with when it was written, never earlier
    last = w.transcript[-1]
    assert isinstance(last, AIMessage) and ini.is_initiated(last) and last.additional_kwargs[ini.KIND_KEY] == "loop"
    assert len(w.saved) == 1 and w.saved[0][:2] == ("u", "s1")
    assert ol.list_loops(w.db, "u") == []  # the loop is closed: it is asked once
    state = ini.load_state(w.db, "u")
    assert state["unanswered"] == 1 and state["last_initiated_at"] == NOW
    assert w.counts == [{"initiation_offered": 1}]


def test_a_fallback_opener_is_counted_as_one(monkeypatch):
    w = World(monkeypatch, answers=("I missed you. How did it go?", "I missed you. How did it go?"))
    out = w.open()
    assert out["content"] == ini.fallback_opening({"kind": "loop", "text": "has a first date"})
    assert w.counts == [{"initiation_offered": 1, "initiation_fallback": 1}]


def test_it_never_opens_twice_in_a_row(monkeypatch):
    w = World(monkeypatch)
    assert w.open() is not None
    assert w.open(now=NOW + timedelta(days=3)) is None  # the opener is still the last message in the thread
    assert len(w.model.prompts) == 1


@pytest.mark.parametrize("make", [
    lambda mp: World(mp, enabled=False),
    lambda mp: World(mp, loops=False),
])
def test_without_the_setting_or_without_anything_to_say_it_stays_silent_and_never_calls_the_model(monkeypatch, make):
    w = make(monkeypatch)
    assert w.open() is None and w.model.prompts == [] and w.saved == []


def test_a_guest_the_kill_switch_and_a_recent_safety_escalation_each_keep_it_silent(monkeypatch):
    w = World(monkeypatch)
    assert w.open(is_guest=True) is None
    monkeypatch.setenv("PROACTIVE_OPENING_ENABLED", "false")
    assert not ini.proactive_opening_enabled() and w.open() is None
    monkeypatch.delenv("PROACTIVE_OPENING_ENABLED")
    ini.note_risk(w.db, "u", NOW - timedelta(days=1))
    assert w.open() is None
    assert w.model.prompts == [] and w.saved == []


def test_a_quiet_goal_is_raised_when_no_loop_is_due(monkeypatch):
    w = World(monkeypatch, loops=False, answers=("How is the half marathon training going?",))
    monkeypatch.setattr(
        "backend.utils.memory_utils.find_stale_goal_to_nudge",
        lambda username, interval_days=None: SimpleNamespace(id="g1", fact="Is training for a half marathon."),
    )
    stamped = []
    monkeypatch.setattr(ini, "_stamp_goal_nudged", lambda username, fact_id, now: stamped.append(fact_id))
    out = w.open()
    assert out["content"] == "How is the half marathon training going?" and stamped == ["g1"]
    assert w.transcript[-1].additional_kwargs[ini.KIND_KEY] == "goal"


def test_a_failure_anywhere_means_no_opener_and_never_an_error(monkeypatch):
    w = World(monkeypatch)
    w.model = Model(RuntimeError("model down"), RuntimeError("still down"))
    out = w.open()
    assert out is not None and out["content"] == ini.fallback_opening({"kind": "loop", "text": "has a first date"})

    def explode(*a):
        raise RuntimeError("save failed")

    w2 = World(monkeypatch)
    assert run(ini.maybe_open_conversation("u", "s1", w2.transcript, tz_name=TZ, invoke=w2.model, save=explode, now=NOW, db=w2.db)) is None


# ---- persistence and wiring ------------------------------------------------------------------------------------------

def test_the_initiated_mark_survives_being_saved_and_loaded_so_the_label_stays():
    from backend.utils.app_utils import _deserialize_messages, _serialize_messages

    message = stamp(AIMessage(content="How did it go?", additional_kwargs={ini.INITIATED_KEY: True, ini.KIND_KEY: "loop"}), NOW)
    stored = _serialize_messages([message])
    assert stored[0]["initiated"] is True and stored[0]["initiated_kind"] == "loop"
    restored = _deserialize_messages(stored)[0]
    assert ini.is_initiated(restored) and restored.additional_kwargs[ini.KIND_KEY] == "loop"
    plain = _serialize_messages([AIMessage(content="hi")])
    assert "initiated" not in plain[0]


def _app_source():
    return (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")


def test_the_endpoints_exist_require_a_signed_in_user_and_refuse_guests():
    source = _app_source()
    for route in ('@app.get("/api/settings/proactive-opening")', '@app.put("/api/settings/proactive-opening")',
                  '@app.post("/api/chat/opening")'):
        start = source.index(route)
        assert "Depends(get_current_user)" in source[start: start + 260], route
    assert "is_guest_username(username) or username in TOGGLE_LOCKED_USERS" in source
    start = source.index('@app.post("/api/chat/opening")')
    body = source[start: source.index('@app.get("/api/settings/target-repo")')]
    assert "initiation_utils.open_for_request(" in body and len(body.splitlines()) < 20  # an endpoint, not logic


def test_the_chat_turn_counts_an_answered_opening_and_marks_a_safety_context():
    source = _app_source()
    for needle in (
        "answers_an_opening = bool(chat_sessions[history_key]) and initiation_utils.is_initiated(chat_sessions[history_key][-1])",
        'tally(turn_counts, "initiation_answered")',
        "initiation_utils.answered, username",
        "initiation_utils.risk_escalated, username",
    ):
        assert needle in source, needle
    risk = source.index("initiation_utils.risk_escalated")
    assert "risk_level != RISK_NONE" in source[risk - 300: risk]


def test_the_counters_know_the_new_keys_and_report_how_often_openings_are_answered():
    from backend.utils.self_counters import KEYS, summarize_counters

    assert {"initiation_offered", "initiation_fallback", "initiation_answered"} <= KEYS
    day = {"day": "2026-10-08", "counts": {"turns_total": 100, "initiation_offered": 4, "initiation_answered": 3, "initiation_fallback": 1}}
    summary = summarize_counters([day], datetime(2026, 10, 9, tzinfo=timezone.utc), days=7)
    assert summary["rates"]["initiation_answered"] == 0.75 and summary["rates"]["initiation_fallback"] == 0.25


# ---- the request path, end to end with a fake database and model --------------------------------------------------------

def _request_world(monkeypatch, *, enabled=True):
    from backend.utils.time_utils import local_today

    real_now = datetime.now(timezone.utc)
    db = _DB()
    if enabled:
        ini.set_enabled(db, "u", True)
    yesterday = (datetime.fromisoformat(local_today(TZ, real_now)).date() - timedelta(days=1)).isoformat()
    ol.save_loops(db, "u", [{"text": "has a first date", "due_date": yesterday, "kind": "social"}], real_now - timedelta(days=2))
    world = SimpleNamespace(
        db=db, model=Model("How did the first date go yesterday?"), saved=[], loads=[],
        transcript=[human("first", real_now - timedelta(days=1)), ai("reply", real_now - timedelta(days=1))],
    )
    world.sessions = {"u::s1": world.transcript}
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None)
    monkeypatch.setattr("backend.utils.memory_utils.find_stale_goal_to_nudge", lambda username, interval_days=None: None)
    return world


def _request(world, username="u", session_id="s1", **kwargs):
    def load(u, s):
        world.loads.append((u, s))
        return world.transcript

    return run(ini.open_for_request(
        username, session_id, world.sessions, load=load, sync=lambda *a: None, get_timezone=lambda u: TZ,
        get_llm=lambda u: SimpleNamespace(ainvoke=world.model), save=lambda *a: world.saved.append(a), **kwargs,
    ))


def test_a_request_from_an_opted_in_user_writes_saves_and_returns_the_opener(monkeypatch):
    world = _request_world(monkeypatch)
    out = _request(world)
    assert out["content"] == "How did the first date go yesterday?" and out[ini.INITIATED_KEY] is True
    assert len(world.saved) == 1 and ini.is_initiated(world.transcript[-1])
    assert ol.list_loops(world.db, "u") == []  # asked once
    # Opening the same conversation again changes nothing: its last message is still the unanswered opener.
    assert _request(world) is None and len(world.model.prompts) == 1


def test_a_request_loads_the_conversation_the_way_a_chat_turn_would_when_it_is_not_in_memory(monkeypatch):
    world = _request_world(monkeypatch)
    world.sessions.clear()
    assert _request(world) is not None
    assert world.loads == [("u", "s1")] and world.sessions["u::s1"] is world.transcript


def test_a_user_who_has_not_opted_in_costs_nothing_and_nothing_is_loaded(monkeypatch):
    world = _request_world(monkeypatch, enabled=False)
    world.sessions.clear()
    assert _request(world) is None
    assert world.loads == [] and world.model.prompts == [] and world.saved == []


def test_guests_the_locked_shared_identity_and_a_blank_session_are_refused_up_front(monkeypatch):
    world = _request_world(monkeypatch)
    assert _request(world, is_guest=True) is None
    assert _request(world, username="guest_bty") is None
    assert _request(world, session_id="   ") is None
    assert world.model.prompts == [] and world.saved == []
