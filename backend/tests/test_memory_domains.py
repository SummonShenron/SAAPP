"""Which part of their life a fact is about (work / own / personal / general): the field, how facts are shown, how new ones are written, and the reversible cleanup of old ones."""
import json
import os
from types import SimpleNamespace

from backend.utils import memory_domains as md
from backend.utils import memory_utils as mu

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def fact(text, domain=None, category="project", source="inferred", fid=None, updated_at="2026-10-01T00:00:00+00:00", **extra):
    return mu.UserFact(id=fid or text[:12], username="u", category=category, fact=text, source=source, created_at="2026-10-01T00:00:00+00:00",
                       updated_at=updated_at, domain=domain, **extra)


# ---- the field ------------------------------------------------------------------------------------------------------------------------------

def test_domains_are_four_and_anything_else_is_not_a_domain():
    assert mu.VALID_DOMAINS == ("work", "own", "personal", "general")
    assert [mu.normalize_domain(v) for v in ("Work", " own ", "PERSONAL", "general", "nope", None, "")] == ["work", "own", "personal", "general", None, None, None]


def test_a_fact_saved_before_domains_loads_with_none_and_a_new_one_defaults_to_general(monkeypatch):
    assert mu.UserFact(id="a", username="u", category="career", fact="x", created_at="t", updated_at="t").domain is None
    store = {}
    monkeypatch.setattr(mu, "load_user_facts", lambda username, category=None: list(store.get("facts", [])))
    monkeypatch.setattr(mu, "save_user_facts", lambda username, facts: store.update(facts=list(facts)))
    monkeypatch.setattr(mu, "embed_text", lambda text: None)
    monkeypatch.setattr(mu.fact_links, "link_new_fact", lambda *a, **k: None)
    monkeypatch.setattr(mu, "_prune_excess_facts", lambda facts, protect_id=None: facts)
    mu.save_user_fact("u", "Works on the rules parser at Principal.", category="career", domain="work")
    mu.save_user_fact("u", "Likes dark mode.")
    mu.save_user_fact("u", "Builds Sonic.", category="project", domain="nonsense")
    assert [f.domain for f in store["facts"]] == ["work", "general", "general"]


# ---- how facts are shown --------------------------------------------------------------------------------------------------------------------

def test_facts_are_grouped_by_part_of_life_with_the_rule_that_keeps_them_apart():
    block = mu.format_facts_block([fact("Works at Principal on a rules parser.", "work"), fact("Builds Sonic.", "own"), fact("Has a dog.", "personal"),
                                   fact("Name is Jack.", "general"), fact("An old fact.")])
    assert block.index("At their job:") < block.index("Their own projects:") < block.index("Their personal life:") < block.index("About them in general:") < block.index("Other:")
    assert "- Works at Principal on a rules parser." in block and "- An old fact." in block
    assert mu.SEPARATE_LIFE_NOTE in block and "no \"we\"" in block and "separate things" in block


def test_nothing_that_every_user_sees_names_this_product_as_the_persons_own_project():
    """"Sonic", "SAAPP" and "Patchy" are the builder's projects; for everyone else Sonic is the assistant they talk to. The shared prompts and notes must not say otherwise."""
    from backend.components import constraints as c

    shared = [c.MEMORY_EXTRACTION_PROMPT, c.MEMORY_COMPACTION_PROMPT, c.MEMORY_TURN_SUMMARY_PROMPT, c.DOMAIN_CLASSIFY_PROMPT, mu.SEPARATE_LIFE_NOTE, *mu.DOMAIN_LABELS.values()]
    for text in shared:
        for name in ("Sonic", "SAAPP", "Patchy"):
            assert name not in text, name
        assert "including this assistant" not in text
    assert "only when they say they are building it" in " ".join(c.MEMORY_EXTRACTION_PROMPT.split())


def test_with_no_job_project_or_personal_fact_the_block_is_the_plain_list_it_always_was():
    assert mu.format_facts_block([fact("a"), fact("b")]) == "\n\nKNOWN USER CONTEXT (use naturally, do not restate unless relevant):\n- a\n- b\n"
    assert mu.format_facts_block([fact("a", "general")]) == "\n\nKNOWN USER CONTEXT (use naturally, do not restate unless relevant):\n- a\n"
    assert mu.format_facts_block([]) == ""


def _load(monkeypatch, facts, embeddings=None):
    monkeypatch.setattr(mu, "load_user_facts", lambda username, category=None: list(facts))
    monkeypatch.setattr(mu, "embed_text", lambda text: None)           # no embedding: relevance falls back to recency, which is all these tests need
    monkeypatch.setattr(mu.fact_links, "linked_extras", lambda matched, all_facts: [])


def test_only_foundational_identity_facts_are_always_shown_and_they_no_longer_use_up_the_relevant_slots(monkeypatch):
    foundational = [fact(f"Identity fact {n}.", "general", category="identity", fid=f"g{n}") for n in range(10)]            # more than the cap
    job_identity = fact("Is a senior engineer at Principal.", "work", category="identity", fid="wid")                         # about the job: competes, not always shown
    relevant = [fact(f"Relevant fact {n}.", "work", category="project", fid=f"r{n}", updated_at=f"2026-10-0{n + 1}T00:00:00+00:00") for n in range(6)]
    _load(monkeypatch, foundational + [job_identity] + relevant)
    block = mu.fetch_relevant_user_facts("u", "how is work going", limit=3)
    shown = [line for line in block.splitlines() if line.startswith("- ")]
    assert sum(1 for line in shown if line.startswith("- Identity fact")) == mu.MAX_ALWAYS_IDENTITY
    assert sum(1 for line in shown if line.startswith("- Relevant fact")) == 3 and len(shown) >= mu.MAX_ALWAYS_IDENTITY + 3


def test_identity_facts_that_were_never_sorted_still_behave_as_they_always_did(monkeypatch):
    legacy = [fact("Name is Jack.", None, category="identity", fid="n"), fact("A preference.", None, category="preference", fid="p")]
    _load(monkeypatch, legacy)
    block = mu.fetch_relevant_user_facts("u", "anything", limit=1)
    assert "- Name is Jack." in block and "- A preference." in block and "At their job" not in block


def test_each_of_job_and_own_project_gets_an_anchor_fact_the_best_evidenced_one(monkeypatch):
    facts = [
        fact("Works at Principal Financial Group (PFG).", "work", category="career", source="explicit", fid="alias"),
        fact("Is a software engineer at Principal.", "work", category="identity", source="inferred", fid="inferred-id"),
        fact("The user is the architect who built the assistant.", "own", category="identity", source="explicit", fid="own-id"),
        fact("The user referred a recruiter to the assistant.", "own", category="career", source="explicit", fid="own-career",
             updated_at="2026-10-20T00:00:00+00:00"),
        fact("Has a dog.", "personal", category="preference", fid="dog"),
    ]
    assert [f.id for f in mu.domain_anchors(facts)] == ["alias", "own-id"]       # stated over inferred; identity over career; newest after that
    assert mu.domain_anchors([fact("Has a dog.", "personal", category="preference")]) == []
    assert mu.domain_anchors([fact("Works somewhere.", None, category="career")]) == []
    _load(monkeypatch, facts)
    block = mu.fetch_relevant_user_facts("u", "how is work going", limit=0)
    assert "- Works at Principal Financial Group (PFG)." in block and "- The user is the architect who built the assistant." in block


# ---- how new facts are written ---------------------------------------------------------------------------------------------------------------

def test_the_extraction_prompts_ask_for_a_domain_and_forbid_mixing_the_two():
    from backend.components import constraints as c

    assert '"domain": "work" | "own" | "personal" | "general"' in c.MEMORY_EXTRACTION_PROMPT and "Never put a job task and an own project in the same fact" in c.MEMORY_EXTRACTION_PROMPT
    assert "never guess a domain" in c.MEMORY_EXTRACTION_PROMPT
    assert '"domain": "work" | "own" | "personal" | "general"' in c.MEMORY_COMPACTION_PROMPT and "A note that mixes the two becomes two facts" in c.MEMORY_COMPACTION_PROMPT
    assert "Keep their job and their own projects apart" in c.MEMORY_TURN_SUMMARY_PROMPT


def test_the_explicit_save_and_the_compaction_save_both_pass_the_domain_through():
    agent = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    node = agent[agent.index("async def memory_save_node"):agent.index("def memory_recall_node")]
    assert 'domain = parsed.get("domain")' in node and "memory_vector_store=memory_vector_store, domain=domain" in node
    compaction = open(os.path.join(REPO, "backend", "services", "memory_compaction.py"), encoding="utf-8").read()
    assert 'domain=fact.get("domain")' in compaction


# ---- sorting the old ones ------------------------------------------------------------------------------------------------------------------

def _answer(*items):
    return json.dumps({"items": list(items)})


def test_the_models_answer_is_read_even_with_fences_and_bad_values_are_dropped():
    out = md.parse_answer("```json\n" + _answer({"id": "a", "domain": "WORK", "rewrite": None}, {"id": "b", "domain": "elsewhere"}, {"domain": "work"}) + "\n```")
    assert out == {"a": {"domain": "work", "rewrite": None}, "b": {"domain": None, "rewrite": None}}
    assert md.parse_answer("no json") == {} and md.parse_answer("") == {}
    split = md.parse_answer(_answer({"id": "a", "domain": "work", "rewrite": [{"fact": "Builds a parser.", "domain": "work"}, {"fact": "Builds Sonic.", "domain": "bogus"}]}))
    assert [p["domain"] for p in split["a"]["rewrite"]] == ["work", "general"]


def test_classify_sorts_in_batches_and_a_failed_batch_leaves_its_facts_alone():
    facts = [fact(f"fact {n}", fid=f"id{n}") for n in range(5)]
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("model down")
        return SimpleNamespace(content=_answer(*({"id": f"id{n}", "domain": "work"} for n in range(5))))

    answers = md.classify(facts, invoke, "Principal Financial Group", "Sonic, SAAPP", batch_size=2)
    assert len(calls) == 3 and sorted(answers) == ["id0", "id1", "id4"]                    # batch 2 (id2, id3) failed
    assert "Principal Financial Group" in calls[0] and "Sonic, SAAPP" in calls[0] and "id0: fact 0" in calls[0]


def test_the_plan_tags_what_is_unsorted_and_leaves_sorted_and_protected_facts_alone():
    facts = [fact("job fact", fid="a"), fact("already sorted", "own", fid="b"), fact("support contact", fid="c", source=mu.SUPPORT_SOURCE), fact("no answer", fid="d")]
    plan = md.plan_changes(facts, {"a": {"domain": "work", "rewrite": None}, "b": {"domain": "work", "rewrite": None}, "c": {"domain": "work", "rewrite": None}})
    assert plan["tags"] == [{"id": "a", "domain": "work"}] and plan["skipped"] == {"already_sorted": 1, "protected": 1, "no_answer": 1, "rewrites_ignored": 0}
    redo = md.plan_changes(facts, {"b": {"domain": "work", "rewrite": None}}, redo=True)
    assert redo["tags"] == [{"id": "b", "domain": "work"}]


def test_a_rewrite_is_accepted_only_if_it_adds_nothing_and_never_for_a_fact_the_person_wrote():
    mixed = fact("Is building an agentic RAG tool for the assistant for his work with his employer.", fid="m")
    good = [{"fact": "Is building an agentic RAG tool for the assistant.", "domain": "own"}]
    assert md.plan_changes([mixed], {"m": {"domain": "own", "rewrite": good}}, allow_rewrites=True)["rewrites"] == [{"id": "m", "parts": good}]
    invented = [{"fact": "Is building an agentic RAG tool for the assistant by March 3.", "domain": "own"}]
    plan = md.plan_changes([mixed], {"m": {"domain": "own", "rewrite": invented}}, allow_rewrites=True)
    assert plan["rewrites"] == [] and plan["rejected_rewrites"] == ["m"] and plan["tags"] == [{"id": "m", "domain": "own"}]       # still tagged, text untouched
    own_words = fact(mixed.fact, fid="e", source=mu.USER_EDIT_SOURCE)
    assert md.plan_changes([own_words], {"e": {"domain": "own", "rewrite": good}}, allow_rewrites=True)["rewrites"] == []


def test_the_model_never_rewrites_the_persons_facts_unless_the_caller_opts_in():
    mixed = fact("Is building an agentic RAG tool for the assistant for his work with his employer.", fid="m")
    good = [{"fact": "Is building an agentic RAG tool for the assistant.", "domain": "own"}]
    plan = md.plan_changes([mixed], {"m": {"domain": "own", "rewrite": good}})
    assert plan["rewrites"] == [] and plan["tags"] == [{"id": "m", "domain": "own"}] and plan["skipped"]["rewrites_ignored"] == 1
    source = open(os.path.join(REPO, "backend", "utils", "memory_maintenance.py"), encoding="utf-8").read()
    assert 'domains.add_argument("--rewrites", action="store_true"' in source and "allow_rewrites=rewrites" in source


class _Saved:
    def __init__(self):
        self.facts, self.backups = None, []


def _apply(facts, plan):
    saved = _Saved()
    result = md.apply_plan("u", facts, plan, save_facts=lambda user, fs: setattr(saved, "facts", list(fs)), save_backup=lambda doc: saved.backups.append(json.loads(json.dumps(doc))),
                           embed=lambda text: [0.1, 0.2])
    return result, saved


def test_applying_tags_and_splits_a_fact_keeps_the_originals_id_and_adds_a_linked_one_and_backs_up_first():
    parser = fact("Is developing a new Python rule parser and integrating Patchy with local support.", fid="p", category="project", related=["other"])
    tagged = fact("Works at Principal.", fid="t", category="career")
    plan = {"tags": [{"id": "t", "domain": "work"}], "rejected_rewrites": [], "skipped": {},
            "rewrites": [{"id": "p", "parts": [{"fact": "Is developing a new Python rule parser.", "domain": "work"}, {"fact": "Is integrating Patchy with local support.", "domain": "own"}]}]}
    result, saved = _apply([parser, tagged], plan)
    assert result["tagged"] == 1 and result["rewritten"] == 1 and result["added"] == 1
    by_text = {f.fact: f for f in saved.facts}
    assert by_text["Works at Principal."].domain == "work"
    first = by_text["Is developing a new Python rule parser."]
    second = by_text["Is integrating Patchy with local support."]
    assert first.id == "p" and first.domain == "work" and first.category == "project" and second.domain == "own" and second.id != "p"
    assert second.related == ["p"] and second.id in first.related and "other" in first.related and second.embedding == [0.1, 0.2]
    final = saved.backups[-1]
    assert final["run_id"] == result["run_id"] and len(final["changes"]) == 2
    assert next(c for c in final["changes"] if c["id"] == "p")["new_ids"] == [second.id]
    assert next(c for c in final["changes"] if c["id"] == "p")["before"]["fact"].startswith("Is developing a new Python rule parser and integrating")
    assert "embedding" not in final["changes"][0]["before"]                          # the backup holds no vectors


def test_a_hand_made_fix_can_also_correct_the_kind_of_fact_and_a_restore_puts_it_back():
    wrong = fact("Is building an agentic RAG tool for the assistant for his work with his employer.", fid="r", category="career")
    plan = {"tags": [], "skipped": {}, "rejected_rewrites": [],
            "rewrites": [{"id": "r", "category": "project", "parts": [{"fact": "Is building an agentic RAG tool for the assistant.", "domain": "own"}]}]}
    _, saved = _apply([wrong], plan)
    fixed = saved.facts[0]
    assert (fixed.fact, fixed.domain, fixed.category, fixed.id) == ("Is building an agentic RAG tool for the assistant.", "own", "project", "r")
    after = []
    md.restore_run("u", saved.backups[-1], saved.facts, save_facts=lambda user, fs: after.extend(fs))
    assert (after[0].category, after[0].domain) == ("career", None) and after[0].fact.endswith("for his work with his employer.")
    ignored = {"tags": [], "skipped": {}, "rejected_rewrites": [], "rewrites": [{"id": "r", "category": "not-a-kind", "parts": [{"fact": "Is building an agentic RAG tool for the assistant.", "domain": "own"}]}]}
    assert _apply([fact(wrong.fact, fid="r", category="career")], ignored)[1].facts[0].category == "career"


def test_a_failed_backup_stops_everything_before_a_single_fact_is_written():
    wrote = []

    def broken(doc):
        raise OSError("db down")

    try:
        md.apply_plan("u", [fact("x", fid="x")], {"tags": [{"id": "x", "domain": "work"}], "rewrites": []}, save_facts=lambda u, f: wrote.append(f), save_backup=broken, embed=lambda t: None)
    except OSError:
        pass
    assert wrote == []


def test_restoring_a_run_puts_text_domain_and_links_back_and_removes_what_the_run_added():
    original = fact("Is developing a new Python rule parser and integrating Patchy with local support.", fid="p", related=["other"])
    plan = {"tags": [], "skipped": {}, "rejected_rewrites": [],
            "rewrites": [{"id": "p", "parts": [{"fact": "Is developing a new Python rule parser.", "domain": "work"}, {"fact": "Is integrating Patchy with local support.", "domain": "own"}]}]}
    _, saved = _apply([original, fact("other fact", fid="other", related=["p"])], plan)
    backup = saved.backups[-1]
    after = []
    outcome = md.restore_run("u", backup, saved.facts, save_facts=lambda user, fs: after.extend(fs))
    assert outcome == {"restored": 1, "removed": 1}
    restored = next(f for f in after if f.id == "p")
    assert restored.fact.startswith("Is developing a new Python rule parser and integrating") and restored.domain is None and restored.related == ["other"]
    assert len(after) == 2 and all(len(f.related) <= 1 for f in after)


def test_the_summary_counts_domains_and_categories_without_any_text():
    facts = [fact("a", fid="a", category="project"), fact("b", fid="b", category="career"), fact("c", fid="c", category="project")]
    plan = {"tags": [{"id": "a", "domain": "work"}, {"id": "b", "domain": "work"}, {"id": "c", "domain": "own"}], "rewrites": [], "rejected_rewrites": [], "skipped": {}}
    summary = md.summarize(plan, facts)
    assert summary["domains"] == {"work": 2, "own": 1} and summary["categories"]["work"] == {"project": 1, "career": 1}
    assert "a" not in json.dumps(summary).replace('"domains"', "").replace("career", "").replace("categories", "").replace("project", "").replace("work", "").replace("own", "").replace("rewrites", "").replace("rejected", "").replace("skipped", "")[:0]


def test_the_cleanup_is_a_dry_run_by_default_and_forgetting_everything_covers_its_backups():
    source = open(os.path.join(REPO, "backend", "utils", "memory_maintenance.py"), encoding="utf-8").read()
    assert 'domains.add_argument("--apply", action="store_true"' in source and "Dry run: nothing changed" in source
    from backend.utils import memory_erase

    assert md.BACKUP_COLLECTION in memory_erase.ERASED_COLLECTIONS
