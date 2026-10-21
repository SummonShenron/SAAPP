"""The memory eval's scoring and bookkeeping, run with a stand-in embedder and no model (the real run is `python -m backend.evals.memory_eval`)."""
import asyncio
import json
import re
import zlib
from types import SimpleNamespace

from backend.evals import memory_eval as me
from backend.services import memory_search as ms
from backend.utils import app_utils


def bag_of_words(text, dim=512):
    """A deterministic stand-in embedder: texts that share words point the same way."""
    vector = [0.0] * dim
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vector[zlib.crc32(word.encode()) % dim] += 1.0
    return vector


EMBED = me.cached(bag_of_words)


def run(coro):
    return asyncio.run(coro)


# ---- the data is usable --------------------------------------------------------------------------------------------------


def test_every_probe_refers_to_memories_that_exist_and_ids_are_unique():
    keys = {m.key for m in me.MEMORIES}
    ids = [p.id for p in me.PROBES]
    assert len(ids) == len(set(ids))
    for probe in me.PROBES:
        assert set(probe.want) <= keys and set(probe.forbid) <= keys, probe.id
        assert probe.category in {"direct", "paraphrase", "follow_up", "negative", "isolation"}
    assert len({m.text for m in me.MEMORIES}) == len(me.MEMORIES)


def test_wanted_memories_belong_to_the_person_and_forbidden_ones_to_someone_else():
    owners = {m.key: m.owner for m in me.MEMORIES}
    for probe in me.PROBES:
        assert all(owners[k] == me.PERSONA for k in probe.want), probe.id
        assert all(owners[k] == me.STRANGER for k in probe.forbid), probe.id


def test_the_filler_never_contains_an_answer_the_probes_look_for():
    secrets = ["biscuit", "anika", "lisbon", "brightline", "2,100", "overstory", "kyoto", "wednesday", "denver", "dev", "pixel"]
    for text in list(me.FILLER) + list(me.NEAR_TOPIC):
        lowered = text.lower()
        assert not any(re.search(rf"\b{re.escape(s)}\b", lowered) for s in secrets), text


def test_each_category_the_report_promises_has_probes_including_follow_ups_and_negatives():
    categories = {p.category for p in me.PROBES}
    assert categories == {"direct", "paraphrase", "follow_up", "negative", "isolation"}
    assert all(p.history for p in me.PROBES if p.category == "follow_up")


def test_every_detail_a_turn_must_keep_was_actually_said_in_it_and_every_cluster_detail_is_in_its_notes():
    for turn in me.TURNS:
        said = me.normalize(turn.question + " " + turn.answer)
        assert all(me.normalize(d) in said for d in turn.details), turn.id
    for cluster in me.CLUSTERS:
        notes = me.normalize(" ".join(text for text, _ in cluster.notes))
        assert all(me.normalize(d) in notes for d in cluster.details), cluster.id
        assert not any(me.normalize(f) in notes for f in cluster.forbid), cluster.id


# ---- the stand-in for Atlas ----------------------------------------------------------------------------------------------


def test_atlas_scores_a_cosine_index_on_a_zero_to_one_scale():
    assert me.atlas_score(1.0) == 1.0 and me.atlas_score(0.0) == 0.5 and me.atlas_score(-1.0) == 0.0


def test_the_store_only_searches_the_asking_users_rows_and_skips_stale_ones():
    store = me.EvalStore(EMBED)
    store.add("rescue greyhound named Biscuit", me.PERSONA, "dog")
    store.add("rescue cat named Pixel", me.STRANGER, "cat")
    store.add("old greyhound note", me.PERSONA, "old", stale=True)
    hits = store._similarity_search_with_score(EMBED("rescue greyhound"), k=10, pre_filter={"username": me.PERSONA, "stale": {"$ne": True}})
    assert [doc.metadata["key"] for doc, _ in hits] == ["dog"]


def test_the_store_looks_like_the_real_one_to_the_apps_retrieval_and_dates_its_rows():
    store = me.EvalStore(EMBED)
    store.add("rescue greyhound named Biscuit adopted in March", me.PERSONA, "dog", days_ago=21)
    query = "greyhound named Biscuit adopted rescue March"
    block = ms.retrieve_relevant_memory_context(store, me.PERSONA, query, precomputed_embedding=EMBED(query))
    assert "RELEVANT PAST CONTEXT" in block
    assert me.parse_block(block) == ["rescue greyhound named Biscuit adopted in March"]
    assert "(about 3 weeks ago)" in block


# ---- recall scoring ------------------------------------------------------------------------------------------------------


def test_parse_block_drops_the_dash_and_the_age_prefix():
    block = ms.format_recall_block(["(about 3 weeks ago) Adopted a dog.", "Plain line."])
    assert me.parse_block(block) == ["Adopted a dog.", "Plain line."]
    assert me.parse_block("") == []


def test_the_query_is_the_message_alone_unless_the_context_policy_adds_the_previous_user_message():
    assert me.build_query("latest", "Make it funnier.", ("Help me draft the toast.",)) == "Make it funnier."
    assert me.build_query("context", "Make it funnier.", ("Help me draft the toast.",)) == "Help me draft the toast.\nMake it funnier."
    assert me.build_query("context", "Make it funnier.", ()) == "Make it funnier."


def test_recall_scoring_flags_a_missing_memory_a_leak_and_noise_on_a_nothing_stored_question():
    want = me.Probe("p", "direct", "q", ("dog",))
    assert me.score_recall(want, ["dog", "filler"]) == (True, "ok")
    assert me.score_recall(want, ["filler"])[0] is False and "missing dog" in me.score_recall(want, ["filler"])[1]
    assert me.score_recall(want, [])[1].endswith("(got nothing)")
    isolation = me.Probe("p", "isolation", "q", ("dog",), ("stranger_cat",))
    assert "another user's" in me.score_recall(isolation, ["dog", "stranger_cat"])[1]
    negative = me.Probe("p", "negative", "q")
    assert me.score_recall(negative, []) == (True, "ok")
    assert me.score_recall(negative, ["filler"])[0] is False


def _small_results():
    store = me.EvalStore(EMBED)
    store.add("Adopted a rescue greyhound named Biscuit.", me.PERSONA, "dog")
    store.add("Is planning a trip to Kyoto in April.", me.PERSONA, "trip")
    store.add("Asked how to center a div with grid.", me.PERSONA, "filler")
    store.add("Adopted a rescue cat named Pixel.", me.STRANGER, "stranger_cat")
    text_to_key = {row.text: row.key for row in store.rows}
    probes = [
        me.Probe("dog", "direct", "greyhound named Biscuit rescue adopted", ("dog",)),
        me.Probe("trip", "direct", "trip to Kyoto in April planning", ("trip",)),
        me.Probe("nothing", "negative", "seventeen times twenty three"),
        me.Probe("cat", "isolation", "rescue cat named Pixel adopted", (), ("stranger_cat",)),
    ]
    return [me.run_probe(store, p, "latest", EMBED, text_to_key) for p in probes]


def test_run_probe_uses_the_apps_own_retrieval_and_records_the_ten_best_hits():
    results = _small_results()
    assert [r.passed for r in results] == [True, True, True, True]
    assert results[0].returned[0] == "dog"
    assert results[0].hits[0][0] == "dog" and len(results[0].hits) <= 10


def test_the_sweep_at_the_apps_own_threshold_agrees_with_the_real_run():
    results = _small_results()
    real = sum(r.passed for r in results if r.probe.want)
    row = next(r for r in me.sweep(results) if abs(r["threshold"] - ms.DEFAULT_RECALL_SIMILARITY_THRESHOLD) < 1e-9)
    assert row["recall"] == real / len([r for r in results if r.probe.want])
    assert row["negatives_clean"] == 1.0


def test_a_looser_cutoff_cannot_find_less_and_a_stricter_one_cannot_add_noise():
    rows = me.sweep(_small_results())
    recalls = [r["recall"] for r in rows]
    noise = [r["noise_per_probe"] for r in rows]
    assert recalls == sorted(recalls, reverse=True)
    assert noise == sorted(noise, reverse=True)


def test_recall_summary_counts_by_category():
    summary = me.recall_summary(_small_results())
    assert summary["passed"] == 4 and summary["total"] == 4
    assert summary["by_category"]["direct"] == (2, 2)


def test_the_full_probe_set_runs_end_to_end_with_the_stand_in_embedder():
    results = me.run_recall(EMBED, "latest")
    assert len(results) == len(me.PROBES)
    assert all(r.hits for r in results)
    # the other user's cat is never returned whatever the embedder does
    assert not any("stranger_cat" in r.returned for r in results)
    text = me.format_recall({"latest": results, "context": me.run_recall(EMBED, "context")})
    assert "RECALL" in text and "threshold sweep" in text and "<- today" in text


# ---- write -----------------------------------------------------------------------------------------------------------------


def test_summary_scoring_requires_the_details_and_demands_nothing_for_small_talk():
    turn = me.Turn("rent", "q", "a", ("2,100",))
    assert me.score_summary("Is facing a rent rise to $2,100 in January.", turn)[0]
    assert me.score_summary("Is facing a rent rise in January.", turn)[0] is False
    assert me.score_summary(None, turn) == (False, "judged not worth remembering")
    small = me.Turn("thanks", "thanks!", "welcome", keep=False)
    assert me.score_summary(None, small) == (True, "ok")
    assert me.score_summary("Said thanks.", small)[0] is False


def test_the_write_part_counts_kept_details_and_whether_the_note_is_found_again():
    notes = {t.question: ("Adopted a greyhound named Biscuit; " + " ".join(t.details) if t.keep else None) for t in me.TURNS}

    async def summarize(question, answer):
        return notes[question]

    results = run(me.run_write(EMBED, 2, summarize))
    by_id = {r.turn.id: r for r in results}
    assert by_id["dog"].kept_ok == 2
    assert by_id["thanks"].kept_ok == 2 and by_id["math"].kept_ok == 2
    assert by_id["allergy"].kept_ok == 2
    assert "WRITE" in me.format_write(results)


def test_a_failed_model_call_counts_against_the_turn_instead_of_stopping_the_run():
    async def summarize(question, answer):
        raise RuntimeError("model down")

    results = run(me.run_write(EMBED, 2, summarize))
    assert all(r.kept_ok == 0 for r in results)
    assert "call failed" in results[0].failures[0]


# ---- the app's own summarising step is what the eval measures -----------------------------------------------------------------


def test_summarize_turn_returns_the_note_or_none_and_trims_the_inputs_like_the_app():
    prompts = []

    async def llm(prompt):
        prompts.append(prompt)
        return SimpleNamespace(content=[{"type": "text", "text": "  Adopted a dog named Biscuit.  "}])

    assert run(app_utils.summarize_turn("q" * 900, "a" * 2000, llm=llm)) == "Adopted a dog named Biscuit."
    assert "q" * 500 in prompts[0] and "q" * 501 not in prompts[0]
    assert "a" * 1000 in prompts[0] and "a" * 1001 not in prompts[0]

    async def none_llm(prompt):
        return SimpleNamespace(content="none")

    assert run(app_utils.summarize_turn("thanks", "welcome", llm=none_llm)) is None


def test_indexing_a_turn_stores_the_summary_and_stores_nothing_when_it_says_none(monkeypatch):
    stored = []
    monkeypatch.setattr(app_utils, "embed_and_store_memory_chunk", lambda *a, **k: stored.append(a[2]))

    async def no_compaction(*a, **k):
        return None

    monkeypatch.setattr(app_utils, "maybe_trigger_compaction", no_compaction)
    monkeypatch.setattr(app_utils, "get_db", lambda: None)

    async def keep(question, answer, llm=None, **kwargs):
        return "Adopted a dog named Biscuit."

    monkeypatch.setattr(app_utils, "summarize_turn", keep)
    run(app_utils.index_conversation_turn(object(), "u", "q", "a", "s1"))

    async def skip(question, answer, llm=None, **kwargs):
        return None

    monkeypatch.setattr(app_utils, "summarize_turn", skip)
    run(app_utils.index_conversation_turn(object(), "u", "thanks", "welcome", "s1"))
    assert stored == ["Adopted a dog named Biscuit."]


# ---- compaction ------------------------------------------------------------------------------------------------------------


def test_cluster_scoring_catches_lost_details_invented_claims_bad_facts_and_a_failed_call():
    cluster = me.CLUSTERS[0]  # the job change
    notes = "\n".join(f"- {t}" for t, _ in cluster.notes)
    good = "Started at Acme in January, was laid off in July and is job hunting."
    assert me.score_cluster(good, [], notes, cluster)[0]
    assert "lost July" in me.score_cluster("Started at Acme in January and was laid off.", [], notes, cluster)[1]
    assert "said still works at Acme" in me.score_cluster(good + " The user still works at Acme.", [], notes, cluster)[1]
    assert "promoted a wrong fact" in me.score_cluster(good, [{"fact": "Works at Acme as an engineer."}], notes, cluster)[1]
    # a true statement about the past is not a wrong fact
    assert me.score_cluster(good, [{"fact": "The user was employed at Acme from January to July."}], notes, cluster)[0]
    assert "model call failed" in me.score_cluster(notes, [], notes, cluster)[1]
    style = me.CLUSTERS[2]
    style_notes = "\n".join(f"- {t}" for t, _ in style.notes)
    assert "no durable fact" in me.score_cluster("Prefers code examples.", [], style_notes, style)[1]


def test_the_compaction_part_runs_each_cluster_and_reports_length_against_the_notes():
    async def summarize(chunks):
        joined = " ".join(c["text"] for c in chunks)
        return {"summary": joined, "facts": [{"fact": "Prefers concise code."}]}

    # the summary here is the notes joined with spaces, which is not the pasted-together fallback, so the details survive
    results = run(me.run_compaction(2, summarize))
    assert [r.cluster.id for r in results] == [c.id for c in me.CLUSTERS]
    assert results[1].ok == 2
    assert "COMPACTION" in me.format_compaction(results)


# ---- dedup -----------------------------------------------------------------------------------------------------------------


def test_pair_judging_uses_the_two_compaction_thresholds():
    merged = me.judge_pair(me.TIER2_SIMILARITY + 0.01, True)
    assert merged["merges_tier1"] and merged["merges_tier2"] and merged["right_at_tier1"] and merged["right_at_tier2"]
    apart = me.judge_pair(me.TIER1_SIMILARITY - 0.05, True)
    assert not apart["merges_tier1"] and not apart["merges_tier2"] and not apart["right_at_tier1"]
    assert me.judge_pair(0.5, False)["right_at_tier1"]


def test_the_thresholds_match_the_ones_compaction_really_uses():
    from backend.services import memory_compaction as mc

    assert me.TIER1_SIMILARITY == mc.DEFAULT_SIMILARITY_THRESHOLD
    assert me.TIER2_SIMILARITY == mc.DEFAULT_META_SIMILARITY_THRESHOLD


def test_dedup_report_summarises_who_merged():
    rows = me.run_dedup(EMBED)
    text = me.format_dedup(rows)
    assert "DEDUP" in text and "true repeats merged" in text and "highest distinct pair" in text


# ---- plumbing ---------------------------------------------------------------------------------------------------------------


def test_the_record_written_by_save_is_plain_json():
    parts = {
        "recall": {"latest": me.run_recall(EMBED, "latest")},
        "write": run(me.run_write(EMBED, 1, lambda q, a: _none())),
        "compaction": run(me.run_compaction(1, lambda chunks: _cluster_out(chunks))),
        "dedup": me.run_dedup(EMBED),
    }
    json.dumps(me.to_record(parts))


async def _none():
    return None


async def _cluster_out(chunks):
    return {"summary": "x", "facts": []}


def test_every_text_the_run_will_embed_is_collected_so_it_can_be_warmed_in_one_go():
    texts = me.every_text()
    assert all(texts) and len(texts) == len(set(texts)) or len(set(texts)) > 100
    assert any("greyhound" in t for t in texts)
    for probe in me.PROBES:
        assert me.build_query("context", probe.query, probe.history) in texts


def test_list_prints_every_part_without_needing_a_database_or_a_model(capsys):
    assert me.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "dog_direct" in out and "compaction" in out and "dedup" in out


# ---- the label the model put in front of every saved note ------------------------------------------------------------------


def test_a_leading_label_is_removed_from_a_note_and_nothing_else_is():
    strip = app_utils.strip_note_label
    assert strip("SUMMARY: Adopted a dog named Biscuit.") == "Adopted a dog named Biscuit."
    assert strip("Summary:  Adopted a dog.") == "Adopted a dog."
    assert strip("**Summary:** Adopted a dog.") == "Adopted a dog."
    assert strip("Note: Asked about rent.") == "Asked about rent."
    assert strip("Adopted a dog named Biscuit.") == "Adopted a dog named Biscuit."
    assert strip("Note that the rent is going up.") == "Note that the rent is going up."  # no colon: a real sentence
    assert strip("Asked for a summary: the notes were long.") == "Asked for a summary: the notes were long."  # not at the start
    assert strip("") == ""


def test_summarize_turn_never_returns_a_labelled_note_and_treats_a_labelled_none_as_none():
    async def labelled(prompt):
        return SimpleNamespace(content="SUMMARY: Sought advice on a rent increase.")

    async def labelled_none(prompt):
        return SimpleNamespace(content="Summary: NONE")

    assert run(app_utils.summarize_turn("q", "a", llm=labelled)) == "Sought advice on a rent increase."
    assert run(app_utils.summarize_turn("q", "a", llm=labelled_none)) is None


def test_the_prompt_no_longer_ends_on_the_word_the_model_copied():
    from backend.components.constraints import MEMORY_TURN_SUMMARY_PROMPT

    assert not MEMORY_TURN_SUMMARY_PROMPT.rstrip().endswith("SUMMARY:")
    assert "no label" in MEMORY_TURN_SUMMARY_PROMPT


def test_the_eval_fails_a_note_that_was_saved_with_a_label():
    turn = me.Turn("rent", "q", "a", ("2,100",))
    ok, why = me.score_summary("SUMMARY: Rent is going up to $2,100.", turn)
    assert ok is False and "label" in why


# ---- the "union" policy is the app's own follow-up search ----------------------------------------------------------------------


def test_union_finds_a_follow_up_through_the_joined_query_that_the_message_alone_misses():
    store = me.EvalStore(EMBED)
    store.add("toast wedding sister Anika Lisbon giving", me.PERSONA, "wedding")
    store.add("Asked how to center a div with grid.", me.PERSONA, "filler")
    text_to_key = {row.text: row.key for row in store.rows}
    probe = me.Probe("toast", "follow_up", "Make it funnier.", ("wedding",), history=("toast wedding sister Anika Lisbon giving",))
    assert me.run_probe(store, probe, "latest", EMBED, text_to_key).passed is False
    union = me.run_probe(store, probe, "union", EMBED, text_to_key)
    assert union.passed and union.returned == ["wedding"]
    assert union.hits[0][0] == "wedding"


def test_union_leaves_a_question_that_stands_alone_exactly_as_the_message_alone_would():
    store = me.EvalStore(EMBED)
    store.add("rescue greyhound named Biscuit adopted in March", me.PERSONA, "dog")
    text_to_key = {row.text: row.key for row in store.rows}
    probe = me.Probe("dog", "direct", "greyhound named Biscuit rescue adopted March")
    alone = me.run_probe(store, probe, "latest", EMBED, text_to_key)
    union = me.run_probe(store, probe, "union", EMBED, text_to_key)
    assert alone.returned == union.returned


# ---- richer notes: the extra write scenarios and what they score -----------------------------------------------------------


def test_details_the_assistant_alone_supplied_are_in_the_reply_and_not_in_what_the_user_said():
    for turn in me.TURNS:
        for detail in turn.forbid:
            assert me.normalize(detail) in me.normalize(turn.answer), turn.id
            assert me.normalize(detail) not in me.normalize(turn.question), turn.id
        assert not set(turn.forbid) & set(turn.details), turn.id


def test_a_note_that_records_the_assistants_details_as_the_users_is_scored_a_failure():
    turn = next(t for t in me.TURNS if t.id == "kyoto")
    assert me.score_summary("Asked what to see in Kyoto.", turn)[0]
    ok, why = me.score_summary("Asked about Kyoto and plans to visit Fushimi Inari.", turn)
    assert ok is False and "assistant's Fushimi" in why


def test_a_note_longer_than_the_cap_is_scored_a_failure_even_with_every_detail():
    turn = me.Turn("rent", "q", "a", ("2,100",))
    rambling = "Rent is going up to $2,100. " + "And the assistant explained many things at length. " * 10
    ok, why = me.score_summary(rambling, turn)
    assert ok is False and "too long" in why
    assert me.score_summary("Rent is going up to $2,100 in January and they want to negotiate it.", turn)[0]


def test_the_write_scenarios_cover_names_dates_numbers_a_preference_and_small_talk():
    ids = {t.id for t in me.TURNS}
    assert {"flight", "nurse", "migration", "birthday", "savings", "kyoto", "units", "thanks", "math", "hello"} <= ids
    assert len([t for t in me.TURNS if t.keep]) >= 12 and len([t for t in me.TURNS if not t.keep]) >= 3


def test_the_summary_prompt_asks_for_the_users_specifics_and_not_the_assistants():
    from backend.components.constraints import MEMORY_TURN_SUMMARY_PROMPT as prompt

    for needle in ("EVERY specific the", "names of people", "dates, times, amounts", "never record a detail that appears only in the assistant's reply", "NONE"):
        assert needle in prompt, needle
    assert "ONE short" not in prompt
