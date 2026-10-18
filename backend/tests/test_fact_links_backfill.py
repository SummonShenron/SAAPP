"""Binding the facts saved before links existed: proposals are read before anything is written (backend/utils/fact_links_backfill.py)."""
import json
import re
from types import SimpleNamespace

import pytest

from backend.utils import fact_links as fl
from backend.utils import fact_links_backfill as bf


def fact(id, text, day, category="project", related=None, embedding=(1.0,), active=True):
    return SimpleNamespace(id=id, fact=text, category=category, created_at=f"2026-10-{day:02d}T00:00:00+00:00", related=list(related or []), embedding=list(embedding) if embedding else None, active=active)


def similarity(a, b):
    return 0.9


def judge_by_word(word):
    """A fake model: it links the new note to every listed note that shares `word` with it, naming the thing."""
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        new = re.search(r"NEW NOTE: (.*)", prompt).group(1)
        if word not in new:
            return '{"links": []}'
        listed = re.findall(r"^(\d+): (.*)$", prompt, flags=re.MULTILINE)
        picks = [{"n": int(n), "thing": f"the {word}"} for n, text in listed if word in text]
        return json.dumps({"links": picks})

    invoke.calls = calls
    return invoke


STORE = [
    fact("a", "Works at Principal.", 1, "career"),
    fact("b", "Is working on the PAG redesign at Principal.", 2),
    fact("c", "Has a dog named Max.", 3, "relationship"),
    fact("d", "Principal gave a raise.", 4, "career"),
]


def test_facts_about_the_same_thing_are_proposed_together_and_the_rest_left_alone():
    proposals = bf.plan_links(STORE, similarity, judge_by_word("Principal"))
    assert {(p["new"], p["other"]) for p in proposals} == {("b", "a"), ("d", "a"), ("d", "b")}
    assert all(p["thing"] == "the Principal" for p in proposals)
    assert all("c" not in (p["new"], p["other"]) for p in proposals)


def test_a_fact_is_judged_only_against_older_facts_like_one_being_saved():
    invoke = judge_by_word("Principal")
    bf.plan_links(STORE, similarity, invoke)
    assert len(invoke.calls) == 3                       # the oldest fact has nothing older to be judged against, so no call
    first_new = [c for c in invoke.calls if "NEW NOTE: Is working on" in c][0]
    assert "Principal gave a raise" not in first_new     # a newer fact is never a candidate


def test_nothing_is_written_while_planning():
    facts = [fact(f.id, f.fact, int(f.created_at[8:10]), f.category) for f in STORE]
    bf.plan_links(facts, similarity, judge_by_word("Principal"))
    assert all(f.related == [] for f in facts)


def test_identity_inactive_and_unembedded_facts_are_never_linked():
    facts = [
        fact("a", "Works at Principal.", 1, "career"),
        fact("i", "Is called Jack at Principal.", 2, "identity"),
        fact("x", "Used to work at Principal.", 3, active=False),
        fact("n", "Principal has a gym.", 4, embedding=None),
        fact("b", "Is on the Principal redesign.", 5),
    ]
    proposals = bf.plan_links(facts, similarity, judge_by_word("Principal"))
    assert {(p["new"], p["other"]) for p in proposals} == {("b", "a")}


def test_a_pair_already_linked_is_not_proposed_again_and_the_caps_hold():
    already = [fact("a", "Works at Principal.", 1, "career", related=["b"]), fact("b", "The Principal redesign.", 2, related=["a"])]
    assert bf.plan_links(already, similarity, judge_by_word("Principal")) == []
    crowd = [fact(f"f{i}", f"Principal note {i}.", i + 1) for i in range(12)]
    proposals = bf.plan_links(crowd, similarity, judge_by_word("Principal"))
    held = {}
    for p in proposals:
        for key in ("new", "other"):
            held[p[key]] = held.get(p[key], 0) + 1
    assert held and max(held.values()) <= fl.MAX_LINKS_PER_FACT


def test_a_sample_judges_only_the_first_facts_and_a_failing_model_proposes_nothing():
    invoke = judge_by_word("Principal")
    bf.plan_links(STORE, similarity, invoke, limit=2)
    assert len(invoke.calls) == 1

    def broken(prompt):
        raise RuntimeError("model down")

    assert bf.plan_links(STORE, similarity, broken) == []


def test_applying_makes_links_both_ways_leaves_out_what_was_skipped_and_is_safe_to_repeat():
    facts = [fact(f.id, f.fact, int(f.created_at[8:10]), f.category) for f in STORE]
    proposals = bf.plan_links(facts, similarity, judge_by_word("Principal"))
    numbered = {(p["new"], p["other"]): n for n, p in enumerate(proposals, start=1)}
    skip = numbered[("d", "b")]
    made = bf.apply_links(facts, proposals, skip=[skip])
    by_id = {f.id: f for f in facts}
    assert made == 2 and "a" in by_id["b"].related and "b" in by_id["a"].related and "a" in by_id["d"].related and "b" not in by_id["d"].related
    assert bf.apply_links(facts, proposals, skip=[skip]) == 0       # a second run changes nothing


def test_a_proposal_whose_fact_has_since_gone_or_been_switched_off_is_ignored():
    facts = [fact("a", "Works at Principal.", 1, "career"), fact("b", "The Principal redesign.", 2, active=False)]
    assert bf.apply_links(facts, [{"new": "b", "other": "a", "thing": "t"}, {"new": "zz", "other": "a", "thing": "t"}]) == 0
    assert facts[0].related == []


def test_the_report_shows_both_notes_and_the_thing_they_share():
    proposals = [{"new": "b", "other": "a", "thing": "the job at Principal"}]
    text = bf.report(STORE, proposals)
    assert "the job at Principal" in text and "Works at Principal." in text and "PAG redesign" in text and text.startswith("# Proposed fact links (1)")


@pytest.mark.parametrize("argv,expected", [(["--skip", "4,17"], [4, 17]), (["--skip", "x, 3"], [3]), ([], []), (["--skip"], [])])
def test_the_skip_list_is_read_forgivingly(argv, expected):
    assert bf._parse_skip(argv) == expected


def test_the_command_defaults_to_a_dry_run_that_needs_a_person_named(capsys):
    assert bf.main([]) == 2
    assert "--user" in capsys.readouterr().out


def test_the_parser_still_gives_the_thing_it_was_named():
    assert fl.parse_links('{"links": [{"n": 2, "thing": "the job"}, {"n": 2, "thing": "dup"}, {"n": 9, "thing": "off"}, {"n": 1}]}', 3) == [(2, "the job")]
    assert fl.parse_links("[1, 2]", 3) == []                                  # not an object: unreadable
    assert fl.parse_links('{"links": [1, 2]}', 3) == [(1, ""), (2, "")]       # an older plain list still works
