"""Why a reply was rewritten, as one word (backend/utils/revision_reasons.py): every description the identity check can give lands in a named group, the emotional tags are kept, and the
count reaches the day's totals. No model, no database."""
import os

import pytest

from backend.utils import identity_checks as ic
from backend.utils import revision_reasons as rr
from backend.utils import self_counters

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def all_identity_descriptions():
    labels = {label for _pattern, label in ic._ALL} | {label for _pattern, label in ic._PERSON_CONCERN}
    return sorted(labels | {ic.FELT_RECOLLECTION, ic.ASKS_TO_REMEMBER})


def test_every_identity_description_lands_in_a_named_group():
    unsorted = [label for label in all_identity_descriptions() if rr.identity_reason(label) == "identity_other"]
    assert unsorted == [], unsorted                  # a new description must be given a group here, or the counts lose it


@pytest.mark.parametrize("description, reason", [
    ("claims a feeling", "feeling"),
    ("claims to love or enjoy something", "feeling"),
    ("says it missed them", "attachment"),
    ("hopes they will come back", "attachment"),
    ("claims a memory of its own", "invented_past"),
    (ic.FELT_RECOLLECTION, "invented_past"),
    (ic.ASKS_TO_REMEMBER, "invented_past"),
    ("describes a routine of its own", "human_life"),
    ("is concerned about the person with no emotional signal on the table", "concern_no_signal"),
    ("claims a stake in its own numbers", "stake"),
    ("puts a feeling on how long they have talked", "talk_length"),
])
def test_identity_descriptions_are_grouped_by_what_they_are_about(description, reason):
    assert rr.identity_reason(description) == reason


def test_the_other_checks_are_named_by_source_and_the_rest_by_tag():
    assert rr.reason_for("identity", "anything at all", "shelf") == "shelf"
    assert rr.reason_for("identity", "x", "event") == "event_time" and rr.reason_for("identity", "x", "status") == "status" and rr.reason_for("identity", "x", "routine") == "routine_time"
    assert rr.reason_for("identity", "says it missed them") == "attachment"
    assert rr.reason_for("probing_question") == "probing_question" and rr.reason_for("venting_advice") == "venting_advice"
    assert rr.reason_for("closing_engagement") == "closing" and rr.reason_for("safety") == "safety" and rr.reason_for("encouragement") == "encouragement"
    assert rr.reason_for("something new") == "other" and rr.reason_for(None) == "other"
    assert rr.identity_reason("an unknown description") == "identity_other"


def test_every_reason_is_a_counter_and_tally_records_it():
    assert {f"revised_reason_{reason}" for reason in rr.REASONS} <= self_counters.KEYS
    counts = {}
    self_counters.tally(counts, "revised_reason", rr.reason_for("identity", "claims a feeling"))
    assert counts == {"revised_reason_feeling": 1}


def test_the_rewrite_counts_its_reason_and_each_check_names_its_source():
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert 'tally(turn_counts, "revised_reason", revision_reasons.reason_for(revision_tag, identity_issue, identity_source))' in app
    for source in ("shelf", "event", "status", "routine"):
        assert f'identity_source = "{source}" if identity_issue else identity_source' in app
