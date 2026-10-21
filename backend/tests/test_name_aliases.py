"""One person, two spellings (backend/utils/name_aliases.py): narrow on purpose, within what the person's own notes already bind together, and always reversible by the person."""
import asyncio
import json
import pathlib
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from backend.utils import event_calendar as ec
from backend.utils import event_fields as ef
from backend.utils import memory_utils as mu
from backend.utils import name_aliases as na
from backend.utils import open_loops as ol

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
TZ = "America/New_York"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    na.clear_cache()
    monkeypatch.setattr(ol, "_index_ready", False)
    ol._events_cache.clear()
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None)


# ------------------------------------------------------------------------ what counts as a variant ---------------------

@pytest.mark.parametrize("a,b", [
    ("Ashlyn", "Ashlynn"), ("ashlyn", "ASHLYNN"), ("Katherine", "Katharine"), ("Jonathan", "Jonathon"), ("Sophia", "Sophie"), ("Allen", "Allan"),
    ("Michael", "Micheal"), ("Stephen", "Stephan"),
])
def test_near_identical_spellings_are_variants(a, b):
    assert na.is_variant(a, b) is True


@pytest.mark.parametrize("a,b", [
    ("Ashlyn", "Ashlyn"), ("Ashlyn", "ashlyn"),                  # the same, not a variant
    ("Sam", "Sean"), ("Mark", "Marc"), ("Dana", "Dane"),         # short names: too easy to be two people
    ("Ashlyn", "Bshlyn"),                                         # a different first letter
    ("Ashlyn", "Ashlynne"), ("Marcus", "Marianne"), ("Alexis", "Alexander"),    # more than one edit
    ("Mary Ann", "Mary Anne Lee"), ("Mary Ann", "Mary Beth"), ("Katherine Lee", "Katharine Leigh"),   # several words: exactly one word may differ
    ("", "Ashlyn"),
])
def test_anything_else_is_not(a, b):
    assert na.is_variant(a, b) is False


def test_a_name_of_several_words_may_differ_in_one_word():
    assert na.is_variant("Katherine Lee", "Katharine Lee") is True
    assert na.same_person("katherine lee", "Katherine Lee") is True


def test_a_pair_the_person_said_are_different_is_never_one_person_whatever_the_order_or_case():
    denied = [na.pair_key("Allan", "Allen")]
    assert na.same_person("Allen", "allan", denied) is False and na.same_person("ALLAN", "allen", denied) is False
    assert na.same_person("Ashlyn", "Ashlynn", denied) is True
    assert na.same_person("Allen", "Allen", denied) is True                           # the same name is still the same name


def test_the_spelling_shown_is_the_one_written_most_recently():
    assert na.canonical([("Ashlyn", "2026-10-01"), ("Ashlynn", "2026-10-20")]) == "Ashlynn"
    assert na.canonical([("Ashlynn", "2026-10-20"), ("Ashlyn", "2026-10-01")]) == "Ashlynn"
    assert na.canonical([]) == ""


def test_edit_distance_covers_a_swap_an_added_a_dropped_and_a_changed_letter():
    assert na._within_one_edit("jhonny", "johnny") is True            # two neighbouring letters swapped
    assert na._within_one_edit("ashlyn", "ashlynn") is True and na._within_one_edit("ashlynn", "ashlyn") is True
    assert na._within_one_edit("ashlin", "ashlyn") is True
    assert na._within_one_edit("ashlyn", "ashlyn") is False and na._within_one_edit("ashlyn", "ashleigh") is False


# ------------------------------------------------------------------------ names in facts, within linked clusters ---------

def fact(id, text, related=(), updated="2026-10-10T00:00:00+00:00", username="u"):
    return mu.UserFact(id=id, username=username, category="relationship", fact=text, created_at=updated, updated_at=updated, related=list(related))


def test_only_capitalised_words_inside_a_sentence_count_as_names():
    assert na.names_in("Has a date with Ashlyn tonight at Tidos.") == ["Ashlyn", "Tidos"]
    assert na.names_in("Ashlyn lives nearby.") == []                           # a sentence's first word may be a verb ("Likes" / "Lives")
    assert na.names_in("Meets Ashlyn on Friday in October.") == ["Ashlyn"]     # weekdays and months are not people
    assert na.names_in("Works on Python at Principal with Dana.") == []        # a product, an employer, and a name too short to compare


def test_a_name_written_two_ways_in_linked_notes_is_one_group_shown_as_the_latest_spelling():
    facts = [
        fact("a", "Is dating Ashlyn, met in June.", related=["b"], updated="2026-10-01T00:00:00+00:00"),
        fact("b", "Has a date with Ashlynn at Tidos.", related=["a"], updated="2026-10-20T00:00:00+00:00"),
    ]
    groups = na.variant_groups(facts)
    assert groups == [{"names": ["Ashlyn", "Ashlynn"], "shown_as": "Ashlynn", "fact_ids": ["a", "b"]}]


def test_two_spellings_in_notes_that_are_not_bound_together_are_left_alone():
    facts = [fact("a", "Is dating Ashlyn, met in June."), fact("b", "Has a date with Ashlynn at Tidos.")]
    assert na.variant_groups(facts) == []                                      # no link between them: not compared, so Allen and Allan elsewhere are never merged


def test_a_pair_marked_as_different_people_is_not_grouped_and_the_note_is_empty():
    facts = [fact("a", "Is dating Ashlyn, met in June.", related=["b"]), fact("b", "Has a date with Ashlynn at Tidos.", related=["a"])]
    assert na.variant_groups(facts, [na.pair_key("Ashlyn", "Ashlynn")]) == [] and na.facts_note(facts, [na.pair_key("Ashlyn", "Ashlynn")]) == ""


def test_the_reply_is_told_which_spelling_to_use_and_not_to_treat_them_as_two_people():
    facts = [fact("a", "Is dating Ashlyn.", related=["b"], updated="2026-10-01T00:00:00+00:00"), fact("b", "Has a date with Ashlynn.", related=["a"], updated="2026-10-20T00:00:00+00:00")]
    note = na.facts_note(facts)
    assert '"Ashlynn" and "Ashlyn"' in note and "almost certainly the same person" in note and "do not treat them as two people" in note


def test_the_facts_block_carries_the_note_only_when_a_pair_exists_and_reads_the_settings_only_then(monkeypatch):
    reads = []
    monkeypatch.setattr(na, "denied_for", lambda username, db=None: reads.append(username) or [])
    plain = [fact("a", "Likes tea."), fact("b", "Works at Principal.")]
    assert "same person" not in mu.format_facts_block(plain) and reads == []
    pair = [fact("a", "Is dating Ashlyn.", related=["b"]), fact("b", "Has a date with Ashlynn.", related=["a"])]
    block = mu.format_facts_block(pair)
    assert "almost certainly the same person" in block and reads == ["u"]
    assert mu.format_facts_block([]) == ""


# ------------------------------------------------------------------------ events and the calendar ----------------------

class _Result:
    def __init__(self, matched=0, deleted=0):
        self.matched_count, self.deleted_count = matched, deleted


class _Col:
    def __init__(self):
        self.docs = []

    def create_index(self, *a, **k):
        pass

    def find(self, flt, projection=None):
        return [{k: v for k, v in d.items() if k != "_id"} for d in self.docs if all(d.get(k) == v for k, v in flt.items())]

    def find_one(self, flt, projection=None):
        hits = self.find(flt)
        return hits[0] if hits else None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, flt, update, upsert=False):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())][:1]
        for d in hits:
            d.update(update["$set"])
        if not hits and upsert:
            self.docs.append({**flt, **update["$set"]})
        return _Result(matched=len(hits))


class _DB(dict):
    def __missing__(self, name):
        self[name] = _Col()
        return self[name]


def capture(message, loops, db, previous_reply=""):
    answer = json.dumps({"loops": loops, "resolved_ids": [], "cancelled_ids": []})

    async def invoke(prompt):
        return SimpleNamespace(content=answer)

    return asyncio.run(ol.capture_open_loops("u", message, TZ, now=NOW, invoke=invoke, get_db_fn=lambda: db, previous_reply=previous_reply))


DATE = {"what": "has a date", "due_date": "2026-10-21", "kind": "social", "time_said": "", "with": "", "where": ""}


def events_of(db):
    return db[ol.COLLECTION].docs


def test_a_respelled_name_is_the_same_event_with_the_latest_spelling_and_says_it_was_updated():
    db = _DB()
    capture("i have a date with ashlyn tonight", [{**DATE, "with": "Ashlyn"}], db)
    result = capture("her name is ashlynn actually, two n's. the date is tonight", [{**DATE, "with": "Ashlynn"}], db)
    assert len(events_of(db)) == 1 and events_of(db)[0]["fields"]["with"]["value"] == "Ashlynn"
    assert result["noted"] == ["Updated: you have a date today with Ashlynn, time not known."] and events_of(db)[0]["corrected_at"] == NOW.isoformat()


def test_two_people_whose_names_are_one_edit_apart_stay_two_events_once_the_person_said_so():
    db = _DB()
    na.set_different(db, "u", "Allan", "Allen")
    capture("dinner with allan tonight", [{**DATE, "what": "has dinner", "with": "Allan"}], db)
    capture("also dinner with allen tonight", [{**DATE, "what": "has dinner", "with": "Allen"}], db)
    assert sorted(e["fields"]["with"]["value"] for e in events_of(db)) == ["Allan", "Allen"]


def test_without_that_the_same_two_names_are_taken_for_one_person():
    db = _DB()
    capture("dinner with allan tonight", [{**DATE, "what": "has dinner", "with": "Allan"}], db)
    capture("dinner with allen tonight", [{**DATE, "what": "has dinner", "with": "Allen"}], db)
    assert len(events_of(db)) == 1


def test_the_calendar_matches_a_plan_whose_name_is_spelled_the_other_way():
    plan = {"id": "e1", "text": "has a date", "kind": "social", "due_date": "2026-10-18", "status": "open", "fields": {"with": {"value": "Ashlyn", "source": "user"}}}
    event = {"id": "a", "title": "Dinner with Ashlynn", "start": "2026-10-18T19:00:00-05:00", "end": "2026-10-18T20:00:00-05:00", "all_day": False, "location": "", "status": "confirmed"}
    other = {"id": "b", "title": "Standup", "start": "2026-10-18T19:30:00-05:00", "end": "2026-10-18T20:00:00-05:00", "all_day": False, "location": "", "status": "confirmed"}
    assert ec.match_calendar_event(plan, [plan], [event, other], "America/Chicago")["id"] == "a"
    assert ec.match_calendar_event(plan, [plan], [event, other], "America/Chicago", [na.pair_key("Ashlyn", "Ashlynn")]) is None


# ------------------------------------------------------------------------ "different people" is the person's, and kept ----

def test_saying_they_are_different_people_is_remembered_and_can_be_taken_back():
    db = _DB()
    assert na.set_different(db, "u", "Mark", "Marc") == []                                   # not spelled alike enough to need saying: nothing is stored
    assert na.set_different(db, "u", "Allen", "Allan") == [na.pair_key("Allen", "Allan")]
    assert na.denied_for("u", db) == [na.pair_key("Allen", "Allan")]
    assert na.set_different(db, "u", "allan", "ALLEN", different=False) == [] and na.denied_for("u", db) == []


def test_the_list_is_bounded_and_one_persons_list_is_theirs_alone():
    db = _DB()
    for i in range(na.MAX_DENIED + 5):
        base = f"Zenobia{chr(97 + i % 26)}{chr(97 + (i // 26) % 26)}"
        na.set_different(db, "u", base, base + "x")
    assert len(na.get_denied(db, "u")) == na.MAX_DENIED and na.get_denied(db, "someone-else") == []


def test_a_settings_read_that_fails_leaves_the_names_to_be_treated_as_one():
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("db down")

    assert na.denied_for("u", Broken()) == []
    assert ol._denied_names(Broken(), "u") == []


def test_it_is_wired_into_the_page_the_forget_everything_and_the_facts_block():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"name_variants"' in source and '@app.post("/api/memory/name-aliases")' in source and "name_aliases.forget_all" in source
    endpoint = source[source.index('@app.post("/api/memory/name-aliases")'):][:900]
    assert "is_guest_username(username)" in endpoint and "is_variant(payload.a, payload.b)" in endpoint
    assert "name_aliases.facts_note" in (ROOT / "backend" / "utils" / "memory_utils.py").read_text(encoding="utf-8")


def test_no_name_is_logged():
    module = (ROOT / "backend" / "utils" / "name_aliases.py").read_text(encoding="utf-8")
    for line in (l for l in module.splitlines() if "logger." in l):
        assert "name" not in line.split('"')[1].lower().replace("names marked", "") or "could not" in line, line


# ------------------------------------------------------------------------ the gate lets a confirmation through ---------

def test_a_yes_to_a_question_naming_the_time_and_place_reaches_the_extractor_and_is_saved_as_confirmed():
    db = _DB()
    capture("i have a date with ashlyn tonight", [{**DATE, "with": "Ashlyn"}], db)
    result = capture("yes", [{**DATE, "time_said": "7", "where": "Tidos"}], db, previous_reply="So the date is at 7 at Tidos, right?")
    fields = events_of(db)[0]["fields"]
    assert fields["time"]["source"] == "assistant_confirmed" and fields["time"]["value"] == "19:00" and fields["where"]["source"] == "assistant_confirmed"
    assert len(events_of(db)) == 1 and result["noted"][0].startswith("Updated: you have a date today with Ashlyn at Tidos, 7 PM")


def test_a_bare_yes_to_a_statement_or_with_nothing_coming_up_is_never_sent_to_the_model():
    db = _DB()
    calls = []

    async def invoke(prompt):
        calls.append(prompt)
        return SimpleNamespace(content='{"loops": []}')

    asyncio.run(ol.capture_open_loops("u", "yep", TZ, now=NOW, invoke=invoke, get_db_fn=lambda: db, previous_reply="So the date is at 7 at Tidos, right?"))
    assert calls == []                                                                         # nothing is coming up to confirm anything about
    capture("i have a date with ashlyn tonight", [{**DATE, "with": "Ashlyn"}], db)
    asyncio.run(ol.capture_open_loops("u", "yep", TZ, now=NOW, invoke=invoke, get_db_fn=lambda: db, previous_reply="You'll probably head out around 7:30."))
    assert calls == []                                                                         # a statement, not a question: confirms nothing


def test_a_yes_that_confirms_nothing_the_person_said_or_was_asked_adds_no_time():
    db = _DB()
    capture("i have a date with ashlyn tonight", [{**DATE, "with": "Ashlyn"}], db)
    capture("yeah", [{**DATE, "time_said": "9", "where": "The Rooftop"}], db, previous_reply="So the date is at 7 at Tidos, right?")
    assert "time" not in events_of(db)[0]["fields"] and "where" not in events_of(db)[0]["fields"]      # 9 and The Rooftop were in neither their words nor the question
