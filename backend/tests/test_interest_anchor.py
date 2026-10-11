import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import initiation as ini
from backend.utils import interest_anchor as ia
from backend.utils.time_utils import stamp

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
TZ = "America/Chicago"


def run(coro):
    return asyncio.run(coro)


def fact(id="f1", text="Is learning to play the guitar", category="preference", days_old=20, confidence=0.9, active=True,
         asked_days_ago=None, updated_days_ago=None):
    return SimpleNamespace(
        id=id, fact=text, category=category, active=active, confidence=confidence,
        created_at=(NOW - timedelta(days=days_old)).isoformat(),
        updated_at=(NOW - timedelta(days=days_old if updated_days_ago is None else updated_days_ago)).isoformat(),
        last_nudged_at=None if asked_days_ago is None else (NOW - timedelta(days=asked_days_ago)).isoformat(),
    )


# ---- the fixed list of topics that are never asked about ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Has been dealing with depression", "Going through a divorce", "Follows politics closely", "Worried about money",
    "Is in therapy twice a week", "Lost their father last spring and still grieves", "Takes medication for ADHD",
    "Has a lot of debt", "Was laid off in March", "Is trying a new diet", "Is religious", "Has a lawsuit coming up",
    "Drinks too much and is trying to stay sober", "Is pregnant",
])
def test_sensitive_topics_are_never_eligible(text):
    assert ia.is_sensitive(text)
    assert not ia.eligible(fact(text=text), NOW)


@pytest.mark.parametrize("text", [
    "Is learning to play the guitar", "Plays in a band on weekends", "Loves Dragon Ball", "Does weightlifting three times a week",
    "Is into woodworking", "Collects vinyl records", "Follows Formula 1",
])
def test_ordinary_leisure_interests_are_not_blocked(text):
    assert not ia.is_sensitive(text)
    assert ia.eligible(fact(text=text), NOW)


@pytest.mark.parametrize("text", [
    "Prefers concise answers", "Likes short replies with bullets", "Wants explanations with code examples first",
    "Prefers type hints and docstrings in Python", "Likes the tone to be direct",
])
def test_how_they_like_sonic_to_work_is_not_an_interest(text):
    assert not ia.eligible(fact(text=text), NOW)


def test_the_other_things_that_make_a_fact_ineligible():
    assert not ia.eligible(fact(category="goal"), NOW)
    assert not ia.eligible(fact(category="career", text="Works as a nurse and loves it"), NOW)
    assert not ia.eligible(fact(active=False), NOW)
    assert not ia.eligible(fact(confidence=0.4), NOW)
    assert not ia.eligible(fact(days_old=1), NOW)           # said minutes or a day ago: never sprung on them
    assert ia.eligible(fact(days_old=4), NOW)
    assert not ia.eligible(fact(text="Likes X"), NOW)        # too short to be about anything
    broken = fact()
    broken.created_at = "not a date"
    assert not ia.eligible(broken, NOW)
    assert not ia.eligible(fact(asked_days_ago=30), NOW)     # asked about recently: each interest waits 90 days
    assert ia.eligible(fact(asked_days_ago=91), NOW)


def test_candidates_are_the_most_recently_reinforced_and_capped():
    facts = [fact(f"f{i}", f"Is into hobby number {i}", updated_days_ago=i + 5) for i in range(12)]
    pool = ia.candidates(facts + [fact("bad", "Has been dealing with depression")], NOW)
    assert [f.id for f in pool] == [f"f{i}" for i in range(8)]


# ---- the gates that belong to this kind of opener ---------------------------------------------------------------------------

def state(**kw):
    return {"unanswered": 0, "last_interest_at": None, **kw}


def test_it_waits_two_weeks_between_interest_openers():
    assert ia.allowed(state(), NOW)
    assert not ia.allowed(state(last_interest_at=NOW - timedelta(days=13)), NOW)
    assert ia.allowed(state(last_interest_at=NOW - timedelta(days=15)), NOW)


def test_it_only_speaks_to_someone_who_is_answering_and_can_be_switched_off(monkeypatch):
    assert not ia.allowed(state(unanswered=1), NOW)
    monkeypatch.setenv("INTEREST_OPENING_ENABLED", "false")
    assert not ia.allowed(state(), NOW)


# ---- letting the model choose, and only choose ------------------------------------------------------------------------------

POOL = [fact("a1", "Is learning to play the guitar"), fact("b2", "Loves Dragon Ball")]


@pytest.mark.parametrize("reply,expected", [
    ('{"id": "b2"}', "b2"), ('```json\n{"id": "a1"}\n```', "a1"), ('Sure! {"id": "a1"} hope that helps', "a1"),
    ('{"id": null}', None), ('{"id": "zzz"}', None), ('{"id": 7}', None), ("none of them", None), ("{broken", None), ("", None),
    ('{"id": ["a1", "b2"]}', None),
])
def test_only_a_listed_id_is_ever_accepted(reply, expected):
    picked = ia.parse_pick(reply, POOL)
    assert (picked.id if picked else None) == expected


def test_the_prompt_lists_the_candidates_and_says_what_is_not_an_interest():
    prompt = ia.build_pick_prompt(POOL)
    assert "a1: Is learning to play the guitar" in prompt and "b2: Loves Dragon Ball" in prompt
    assert "Pick at most ONE" in prompt and "health" in prompt and 'Reply with JSON only' in prompt


class Chooser:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.prompts = reply, error, []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.reply)


def choose(model, facts):
    return run(ia.choose_interest("u", model, NOW, load_facts=lambda name: facts))


def test_the_chosen_interest_becomes_an_anchor_with_its_own_words():
    anchor = choose(Chooser('{"id": "b2"}'), POOL)
    assert anchor == {"kind": "interest", "id": "b2", "text": "Loves Dragon Ball"}


def test_nothing_is_asked_when_the_model_declines_or_fails_and_no_model_call_is_made_without_candidates():
    assert choose(Chooser('{"id": null}'), POOL) is None
    assert choose(Chooser(error=RuntimeError("down")), POOL) is None
    quiet = Chooser('{"id": "x"}')
    assert choose(quiet, [fact(text="Has been dealing with depression"), fact("g", category="goal")]) is None
    assert quiet.prompts == []


def test_a_long_fact_is_cut_to_the_anchor_limit():
    anchor = choose(Chooser('{"id": "long"}'), [fact("long", "Is into " + "very " * 80 + "specific things")])
    assert len(anchor["text"]) <= ia.MAX_ANCHOR_CHARS


# ---- the opening, end to end ------------------------------------------------------------------------------------------------

class _Col:
    def __init__(self):
        self.docs = []

    def find_one(self, flt, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    def update_one(self, flt, update, upsert=False):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items() if not isinstance(v, dict))][:1]
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


class _DB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, _Col())


class Writer:
    """Answers the picker's prompt with an id and the opener's prompt with a question."""

    def __init__(self, pick='{"id": "b2"}', opener="What have you been watching in Dragon Ball lately?"):
        self.pick, self.opener, self.prompts = pick, opener, []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.pick if "choosing whether any" in prompt else self.opener)

    def asked_to_pick(self):
        return sum("choosing whether any" in p for p in self.prompts)


class World:
    def __init__(self, monkeypatch, facts=None, *, loops=None, goal=None, **writer):
        self.db = _DB()
        ini.set_enabled(self.db, "u", True)
        self.facts = facts if facts is not None else [fact("a1", "Is learning to play the guitar", updated_days_ago=30), fact("b2", "Loves Dragon Ball")]
        self.model = Writer(**writer)
        self.saved, self.counts, self.stamped = [], [], []
        self.transcript = [stamp(HumanMessage(content="first"), NOW - timedelta(days=1)), stamp(AIMessage(content="reply"), NOW - timedelta(days=1))]
        monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: self.counts.append(counts))
        monkeypatch.setattr("backend.utils.memory_utils.find_stale_goal_to_nudge", lambda username, interval_days=None: goal)
        monkeypatch.setattr(ini.ol, "list_loops", lambda db, username: loops or [])
        monkeypatch.setattr("backend.utils.memory_utils.load_user_facts", lambda username, category=None: self.facts)
        monkeypatch.setattr("backend.utils.memory_utils.save_user_facts", lambda username, facts: self.stamped.append(facts))

    def open(self, now=NOW):
        return run(ini.maybe_open_conversation(
            "u", "s1", self.transcript, tz_name=TZ, invoke=self.model, save=lambda *a: self.saved.append(a), now=now, db=self.db,
        ))


def test_with_nothing_else_to_ask_about_it_writes_saves_and_records_one_interest_opener(monkeypatch):
    w = World(monkeypatch)
    out = w.open()
    assert out["content"] == "What have you been watching in Dragon Ball lately?" and out[ini.INITIATED_KEY] is True
    last = w.transcript[-1]
    assert ini.is_initiated(last) and ini.opening_kind(last) == "interest" and len(w.saved) == 1
    assert w.counts == [{"initiation_offered": 1, "initiation_interest_offered": 1}]
    state = ini.load_state(w.db, "u")
    assert state["last_interest_at"] == NOW and state["last_initiated_at"] == NOW and state["unanswered"] == 1
    assert [f.last_nudged_at for f in w.facts if f.id == "b2"] == [NOW.isoformat()]  # that interest now waits 90 days
    assert [f.last_nudged_at for f in w.facts if f.id == "a1"] == [None]


def test_a_loop_or_a_goal_always_comes_first_and_the_picker_is_not_even_called(monkeypatch):
    goal = SimpleNamespace(id="g1", fact="Is training for a half marathon")
    w = World(monkeypatch, goal=goal, opener="How is the half marathon training going?")
    out = w.open()
    assert out["content"].startswith("How is the half marathon") and ini.opening_kind(w.transcript[-1]) == "goal"
    assert w.model.asked_to_pick() == 0 and "initiation_interest_offered" not in w.counts[0]


def test_nothing_is_sent_when_no_fact_is_eligible_and_nothing_is_asked_of_the_model(monkeypatch):
    w = World(monkeypatch, facts=[fact("s", "Prefers concise answers"), fact("d", "Has been dealing with depression"), fact("new", "Loves jazz", days_old=1)])
    assert w.open() is None and w.model.prompts == [] and w.saved == []


def test_a_picker_that_says_none_means_no_message(monkeypatch):
    w = World(monkeypatch, pick='{"id": null}')
    assert w.open() is None and w.model.asked_to_pick() == 1 and w.saved == []


def test_two_weeks_must_pass_before_the_next_interest_opener_and_a_reply_is_needed_first(monkeypatch):
    w = World(monkeypatch)
    assert w.open() is not None
    # Still waiting: nothing stacks.
    assert w.open(now=NOW + timedelta(days=30)) is None
    # They answered (the chat turn resets the run of unanswered openings) and chatted on.
    ini.reset_unanswered(w.db, "u")
    w.transcript.extend([stamp(HumanMessage(content="oh lots"), NOW + timedelta(days=1)), stamp(AIMessage(content="nice"), NOW + timedelta(days=1))])
    w.facts.append(fact("c3", "Collects vinyl records", days_old=40))
    w.model.pick = '{"id": "c3"}'
    w.model.opener = "What's the best record you've picked up recently?"
    assert w.open(now=NOW + timedelta(days=10)) is None            # inside the two weeks
    assert w.open(now=NOW + timedelta(days=16)) is not None         # after them


def test_without_a_reply_to_the_last_opener_no_interest_opener_is_even_considered(monkeypatch):
    w = World(monkeypatch)
    ini.record_initiation(w.db, "u", NOW - timedelta(days=40))      # one unanswered opener from long ago
    w.model.pick = '{"id": "b2"}'
    assert w.open() is None and w.model.asked_to_pick() == 0


def test_a_question_that_wanders_into_a_sensitive_topic_is_rejected_and_the_plain_template_stands_in(monkeypatch):
    w = World(monkeypatch, opener="Does Dragon Ball help with your depression?")
    out = w.open()
    assert out["content"] == 'You mentioned this a while back: "Loves Dragon Ball". What have you been up to with it lately?'
    assert "depression" not in out["content"]
    assert w.counts[0]["initiation_fallback"] == 1 and w.counts[0]["initiation_interest_offered"] == 1


def test_an_opener_that_claims_to_have_been_curious_is_rejected_for_this_kind_too(monkeypatch):
    w = World(monkeypatch, opener="I've been curious about your Dragon Ball favorites. Who is your favorite?")
    assert w.open()["content"].startswith('You mentioned this a while back')


def test_the_opener_prompt_for_an_interest_asks_about_the_topic_never_the_person(monkeypatch):
    w = World(monkeypatch)
    w.open()
    prompt = next(p for p in w.model.prompts if "YOU ARE OPENING" in p)
    assert "Loves Dragon Ball" in prompt and "never about their feelings, health, work, money or relationships" in prompt
    assert "wondering, curious" in prompt  # the standing ban on claiming to have wondered applies as ever


def test_the_changelog_entry_and_counters_know_about_it():
    from backend.components.sonic_changelog import CHANGELOG
    from backend.utils.self_counters import KEYS, summarize_counters

    entry = next(e for e in CHANGELOG if e["id"] == "message_first")
    assert "something you said you're into" in entry["line"] and "backend/utils/interest_anchor.py::def choose_interest" in entry["requires"]
    assert {"initiation_interest_offered", "initiation_interest_answered"} <= KEYS
    day = {"day": "2026-10-08", "counts": {"turns_total": 100, "initiation_interest_offered": 4, "initiation_interest_answered": 1}}
    assert summarize_counters([day], datetime(2026, 10, 9, tzinfo=timezone.utc), days=7)["rates"]["initiation_interest_answered"] == 0.25


def test_the_chat_turn_tallies_an_answered_interest_opener():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert 'initiation_utils.opening_kind(chat_sessions[history_key][-1]) if answers_an_opening else None' in source
    assert 'tally(turn_counts, "initiation_interest_answered")' in source


# ---- the checks added after trying the real model, which invented details --------------------------------------------------

@pytest.mark.parametrize("anchor,question", [
    ("Plays in a band on weekends", "How is the band going lately?"),
    ("Loves Dragon Ball", "How is the Dragon Ball series holding up for you lately?"),
    ("Collects vinyl records", "Have you picked up any new vinyl records lately?"),
    ("Follows Formula 1", "How is the current Formula 1 season shaping up for you?"),
    ("Is learning to play the guitar", "How is your guitar practice coming along?"),
])
def test_the_plain_questions_the_real_model_wrote_pass(anchor, question):
    assert ia.opening_issue(question, anchor) is None


@pytest.mark.parametrize("anchor,question,fragment", [
    ("Loves Dragon Ball", "How is the rewatch of the Dragon Ball Z Namek saga going?", "named something they never said (Namek)"),
    ("Collects vinyl records", "Have you tracked down any of those Blue Note pressings you were looking for?", "named something they never said (Blue)"),
    ("Plays in a band on weekends", "How is the band going, and have you played any gigs?", "two things"),
    ("Plays in a band on weekends", "How is the band going lately, now that you have all been playing together for such a long time and writing so many new songs for the next show?", "words"),
    ("Is learning to play the guitar", "How is the guitar going, especially with your depression?", "isn't Sonic's"),
])
def test_invented_names_second_questions_length_and_sensitive_ground_are_refused(anchor, question, fragment):
    assert fragment in (ia.opening_issue(question, anchor) or "")


def test_the_first_word_is_not_mistaken_for_a_name_and_names_the_fact_contains_are_fine():
    assert ia.opening_issue("Which Beatles album is your favourite?", "Loves the Beatles") is None
    assert ia.opening_issue("Do you still play Zelda?", "Plays Zelda") is None


def test_the_general_opener_check_applies_it_to_interest_anchors_only():
    anchor = {"kind": "interest", "id": "x", "text": "Loves Dragon Ball"}
    assert "Namek" in ini.opening_issue("How is Dragon Ball going after the Namek saga?", anchor)
    goal = {"kind": "goal", "id": "g", "text": "Is training for a half marathon"}
    assert ini.opening_issue("How is the half marathon training going, and are you still running Lakefront?", goal) is None


def test_two_invented_openers_in_a_row_fall_back_to_the_plain_template(monkeypatch):
    w = World(monkeypatch, opener="How is the Namek saga rewatch going for you?")
    out = w.open()
    assert out["content"] == 'You mentioned this a while back: "Loves Dragon Ball". What have you been up to with it lately?'
    assert sum("YOU ARE OPENING" in p for p in w.model.prompts) == 2  # tried, told why it was refused, tried again, then the template


def test_the_prompt_forbids_adding_details_and_gives_the_model_a_model_question(monkeypatch):
    w = World(monkeypatch)
    w.open()
    prompt = next(p for p in w.model.prompts if "YOU ARE OPENING" in p)
    assert "do NOT add details, names, titles, dates or plans" in prompt and "under 20 words" in prompt
    assert "How's the band going lately?" in prompt
