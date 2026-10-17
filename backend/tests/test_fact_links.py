"""Links between facts (backend/utils/fact_links.py): notes about the same real thing are bound together and come along when one is recalled."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.utils import fact_links as fl
from backend.utils import memory_utils


class F:
    """Just enough of a fact for the link module."""
    def __init__(self, id, text="x", embedding=(1.0, 0.0), category="career", active=True):
        self.id, self.fact, self.embedding, self.category, self.active, self.related = id, text, list(embedding) if embedding else None, category, active, []


def sim(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5))


def answer(links):
    return SimpleNamespace(content=json.dumps({"links": [{"n": n, "thing": "the job"} for n in links]}))


# ---- reading the model's answer -------------------------------------------------------------------------------------------------------------

def test_the_numbers_the_model_names_are_read_in_order_without_repeats_and_within_range():
    assert fl.parse_related('{"links": [{"n": 2, "thing": "job"}, {"n": 1, "thing": "job"}, {"n": 2, "thing": "job"}, {"n": 9, "thing": "x"}]}', 3) == [2, 1]
    assert fl.parse_related('```json\n{"links": [{"n": "1", "thing": "the trip"}]}\n```', 3) == [1]
    assert fl.parse_related([{"type": "text", "text": '{"links": [{"n": 3, "thing": "the sister"}]}'}], 3) == [3]


def test_a_link_with_no_real_thing_named_is_not_made_and_an_older_plain_list_still_works():
    assert fl.parse_related('{"links": [{"n": 1, "thing": ""}, {"n": 2}]}', 3) == []
    assert fl.parse_related('{"related": [1, 3]}', 3) == [1, 3]


@pytest.mark.parametrize("raw", ["", "none", "not json", '{"links": "all"}', '{"links": null}', None, '[1, 2]', '{"links": [{"n": "x", "thing": "y"}]}'])
def test_anything_unreadable_means_no_link(raw):
    assert fl.parse_related(raw, 3) == []


def test_no_more_than_three_links_are_made_from_one_answer():
    raw = json.dumps({"links": [{"n": n, "thing": "t"} for n in range(1, 7)]})
    assert fl.parse_related(raw, 6) == [1, 2, 3]


# ---- who is asked about ---------------------------------------------------------------------------------------------------------------------

def test_the_shortlist_is_the_closest_facts_above_the_floor_never_itself_never_inactive_never_identity():
    new = F("new", embedding=(1.0, 0.0))
    close, closer = F("a", embedding=(0.8, 0.6)), F("b", embedding=(0.95, 0.3))
    far = F("far", embedding=(0.0, 1.0))
    others = [close, far, F("inactive", embedding=(1.0, 0.0), active=False), F("who", embedding=(1.0, 0.0), category="identity"), F("noemb", embedding=None), new, closer]
    assert [f.id for f in fl.candidate_facts(others, new, sim)] == ["b", "a"]


def test_the_shortlist_is_capped_and_a_fact_with_no_embedding_has_none():
    new = F("new", embedding=(1.0, 0.0))
    many = [F(f"f{i}", embedding=(1.0, i / 100)) for i in range(20)]
    assert len(fl.candidate_facts(many, new, sim)) == fl.CANDIDATES
    assert fl.candidate_facts(many, F("blank", embedding=None), sim) == []


# ---- asking the model -----------------------------------------------------------------------------------------------------------------------

def test_the_prompt_numbers_the_candidates_and_asks_for_the_one_real_thing_and_for_caution():
    prompt = fl.build_link_prompt("Is working on the PAG UI redesign at work.", [F("long-id-1", "Works at Principal."), F("long-id-2", "Has a dog named Max.")])
    assert "1: Works at Principal." in prompt and "2: Has a dog named Max." in prompt and "long-id-1" not in prompt
    for needle in ("SAME real thing", "If you are not sure, leave it out", "ONE real thing", "word overlaps"):
        assert needle in prompt, needle


def test_the_candidates_the_model_picks_are_returned_and_no_candidates_means_no_call():
    candidates = [F("a", "Works at Principal."), F("b", "Has a dog.")]
    invoke = Mock(return_value=answer([1]))
    assert [f.id for f in fl.judge_links("Is working on the PAG UI redesign at work.", candidates, invoke)] == ["a"]
    invoke.reset_mock()
    assert fl.judge_links("anything", [], invoke) == [] and not invoke.called


def test_a_failing_model_means_no_link_and_never_an_error():
    assert fl.judge_links("x", [F("a")], Mock(side_effect=RuntimeError("down"))) == []


# ---- making and keeping links ---------------------------------------------------------------------------------------------------------------

def test_links_are_made_both_ways_once_and_never_to_itself():
    a, b = F("a"), F("b")
    assert fl.add_links(a, [b, a, b]) == 1
    assert a.related == ["b"] and b.related == ["a"]


def test_a_fact_holds_at_most_so_many_links():
    hub = F("hub")
    for i in range(fl.MAX_LINKS_PER_FACT + 3):
        fl.add_links(hub, [F(f"f{i}")])
    assert len(hub.related) == fl.MAX_LINKS_PER_FACT


def test_dangling_duplicate_and_self_links_are_removed_and_good_ones_kept():
    a, b = F("a"), F("b")
    a.related, b.related = ["b", "gone", "b", "a"], ["a"]
    fl.drop_dangling_links([a, b])
    assert a.related == ["b"] and b.related == ["a"]


def test_switching_links_off_makes_none_and_asks_nothing(monkeypatch):
    monkeypatch.setenv("FACT_LINKS", "false")
    invoke = Mock()
    a, b = F("a"), F("b")
    assert fl.link_new_fact([a, b], a, sim, invoke) == 0 and not invoke.called


# ---- bringing them along --------------------------------------------------------------------------------------------------------------------

def test_the_facts_bound_to_a_chosen_fact_come_along_in_order_without_repeats_or_what_is_already_there():
    project, employer, boss, other = F("p"), F("e"), F("b"), F("o")
    project.related, employer.related = ["e", "b", "gone"], ["p"]
    extras = fl.linked_extras([project, employer], [project, employer, boss, other])
    assert [f.id for f in extras] == ["b"]                     # the employer was already chosen; "gone" no longer exists
    assert [f.id for f in fl.linked_extras([project], [project, employer, boss, other])] == ["e", "b"]


def test_only_a_few_extras_come_along_and_an_inactive_fact_never_does():
    chosen = F("c")
    pool = [F(f"x{i}") for i in range(6)]
    pool[0].active = False
    chosen.related = [f.id for f in pool]
    assert [f.id for f in fl.linked_extras([chosen], [chosen] + pool)] == ["x1", "x2", "x3"]
    assert fl.linked_extras([chosen], [chosen] + pool, limit=0) == [] and fl.linked_extras([], pool) == []


# ---- the whole path through saving and recall -----------------------------------------------------------------------------------------------

VECTORS = {
    "Works at Principal.": (1.0, 0.1, 0.0),
    "Is working on the PAG UI redesign at work.": (0.8, 0.6, 0.0),
    "Has a dog named Max.": (0.0, 0.2, 1.0),
    "How is the redesign going?": (0.7, 0.7, 0.0),
    "Tell me about my dog": (0.0, 0.1, 1.0),
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    memory_utils.clear_facts_cache()
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: list(VECTORS[text.strip()]) if text and text.strip() in VECTORS else None)

    def invoke(prompt):
        if "SAVED NOTES" in prompt:                         # the link judgment: bind the new note to the employer note
            lines = [line for line in prompt.splitlines() if line[:2] in ("1:", "2:", "3:")]
            picks = [int(line[0]) for line in lines if "Works at Principal" in line]
            return answer(picks)
        return SimpleNamespace(content=json.dumps({"action": "distinct"}))

    monkeypatch.setattr(memory_utils.lite_llm, "invoke", Mock(side_effect=invoke))
    yield
    memory_utils.clear_facts_cache()


def test_a_new_fact_is_bound_to_the_fact_it_belongs_with_and_the_unrelated_one_is_left_alone(store):
    employer = memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    memory_utils.save_user_fact("jack", "Has a dog named Max.", category="relationship")
    project = memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    facts = {f.id: f for f in memory_utils.load_user_facts("jack")}
    assert facts[project.id].related == [employer.id] and facts[employer.id].related == [project.id]
    assert all(not f.related for f in facts.values() if f.id not in (project.id, employer.id))


def test_links_survive_being_saved_and_loaded_and_an_older_fact_without_links_loads_fine(store):
    employer = memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    project = memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    memory_utils.clear_facts_cache()
    assert {f.id: f.related for f in memory_utils.load_user_facts("jack")} == {employer.id: [project.id], project.id: [employer.id]}
    assert memory_utils.UserFact(id="old", username="u", category="career", fact="x", created_at="t", updated_at="t").related == []


def test_asking_about_the_project_brings_the_employer_along_and_asking_about_the_dog_does_not(store):
    memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    memory_utils.save_user_fact("jack", "Has a dog named Max.", category="relationship")
    memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    block = memory_utils.fetch_relevant_user_facts("jack", "How is the redesign going?", limit=1)
    assert "PAG UI redesign" in block and "Works at Principal" in block and "dog" not in block
    assert "Works at Principal" not in memory_utils.fetch_relevant_user_facts("jack", "Tell me about my dog", limit=1)
    picked = memory_utils.select_relevant_user_facts("jack", "How is the redesign going?", limit=1, min_similarity=0.5)
    assert [f.fact for f in picked] == ["Is working on the PAG UI redesign at work.", "Works at Principal."]


def test_forgetting_a_fact_leaves_no_link_to_it(store):
    employer = memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    project = memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    assert memory_utils.delete_user_fact("jack", employer.id)
    assert {f.id: f.related for f in memory_utils.load_user_facts("jack")} == {project.id: []}


def test_a_failing_link_judgment_never_stops_a_fact_from_being_saved(store, monkeypatch):
    memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    monkeypatch.setattr(memory_utils.lite_llm, "invoke", Mock(side_effect=RuntimeError("model down")))
    saved = memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    assert saved.related == [] and len(memory_utils.load_user_facts("jack")) == 2


def test_when_a_fact_changes_what_it_was_bound_to_is_let_go_and_judged_again(store, monkeypatch):
    VECTORS["Works at Acme."] = (0.95, 0.1, 0.0)
    employer = memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    project = memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    assert project.related == [employer.id]

    def invoke(prompt):
        if "SAVED NOTES" in prompt:
            return answer([])                                  # the new employer is not the one the redesign was at
        return SimpleNamespace(content=json.dumps({"action": "supersede"}))

    monkeypatch.setattr(memory_utils.lite_llm, "invoke", Mock(side_effect=invoke))
    changed = memory_utils.save_user_fact("jack", "Works at Acme.", category="career")
    facts = {f.id: f for f in memory_utils.load_user_facts("jack")}
    assert changed.id == employer.id and facts[employer.id].fact == "Works at Acme."
    assert facts[employer.id].related == [] and facts[project.id].related == []         # not still claiming to belong with the old employer


# ---- what the Memory page shows and lets the person undo ------------------------------------------------------------------------------------

def test_a_link_the_person_disagrees_with_can_be_removed_both_ways(store):
    employer = memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    project = memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    assert not memory_utils.unlink_user_facts("jack", employer.id, "not-a-fact")
    assert memory_utils.unlink_user_facts("jack", employer.id, project.id)
    assert {f.id: f.related for f in memory_utils.load_user_facts("jack")} == {employer.id: [], project.id: []}
    assert not memory_utils.unlink_user_facts("jack", employer.id, project.id)            # already gone


def test_the_page_gets_the_text_of_what_a_note_is_linked_with_and_never_an_embedding(store):
    employer = memory_utils.save_user_fact("jack", "Works at Principal.", category="career")
    project = memory_utils.save_user_fact("jack", "Is working on the PAG UI redesign at work.", category="project")
    by_id = {f.id: f for f in memory_utils.load_user_facts("jack")}
    assert memory_utils.linked_fact_summaries(by_id[project.id], by_id) == [{"id": employer.id, "fact": "Works at Principal."}]
    by_id[employer.id].active = False
    assert memory_utils.linked_fact_summaries(by_id[project.id], by_id) == []              # a note that is no longer active is not shown
    shown = memory_utils.fact_for_state(by_id[project.id])
    assert "embedding" not in shown and shown["related"] == [employer.id]


def test_the_memory_endpoints_send_no_embeddings_and_let_a_link_be_removed():
    source = open("app.py", encoding="utf-8").read()
    listing = source[source.index('@app.get("/api/memory")'):source.index('@app.delete("/api/memory/{fact_id}/links/{other_id}")')]
    assert "fact_for_state(f)" in listing and "linked_fact_summaries(f, by_id)" in listing and "f.dict()" not in listing
    unlink = source[source.index('@app.delete("/api/memory/{fact_id}/links/{other_id}")'):source.index('@app.delete("/api/memory/{fact_id}")')]
    assert "Depends(get_current_user)" in unlink and "unlink_user_facts" in unlink and "status_code=404" in unlink
