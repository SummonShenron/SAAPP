from unittest.mock import Mock

from langchain_core.documents import Document

from backend.services import memory_search as ms

from backend.services.memory_search import (
    retrieve_relevant_memory_context,
    retrieve_user_memory,
    flag_stale_chunks_for_superseded_fact,
)


def _fake_store(hits):
    store = Mock()
    store.similarity_search_with_score = Mock(return_value=hits)
    store._similarity_search_with_score = Mock(return_value=hits)
    return store


def _fake_stale_store(hits):
    """A store shaped for flag_stale_chunks_for_superseded_fact: _similarity_search_with_score
    returns (Document, score) pairs whose Document.id is set (as the real library's own
    _similarity_search_with_score does — see vectorstores.py:872), plus a raw .collection with
    a mockable update_many."""
    store = Mock()
    store._similarity_search_with_score = Mock(return_value=hits)
    store.collection = Mock()
    store.collection.update_many = Mock()
    return store


def test_returns_empty_when_no_vector_store():
    assert retrieve_relevant_memory_context(None, "jack", "do you remember my presentation?") == ""


def test_returns_empty_when_query_blank():
    store = _fake_store([])
    assert retrieve_relevant_memory_context(store, "jack", "   ") == ""
    store.similarity_search_with_score.assert_not_called()


def test_surfaces_chunks_that_clear_threshold():
    hits = [
        (Document(page_content="Jack felt discouraged after his Principal presentation."), 0.91),
        (Document(page_content="Unrelated chunk about lunch plans."), 0.20),
    ]
    store = _fake_store(hits)

    context = retrieve_relevant_memory_context(store, "jack", "do you remember that thing this morning?")

    assert "Jack felt discouraged after his Principal presentation." in context
    assert "Unrelated chunk about lunch plans." not in context
    assert "RELEVANT PAST CONTEXT" in context


def test_returns_empty_when_nothing_clears_threshold():
    hits = [(Document(page_content="Barely related."), 0.40)]
    store = _fake_store(hits)

    assert retrieve_relevant_memory_context(store, "jack", "something else entirely") == ""


def test_respects_custom_score_threshold():
    hits = [(Document(page_content="Loosely related chunk."), 0.60)]
    store = _fake_store(hits)

    assert retrieve_relevant_memory_context(store, "jack", "question", score_threshold=0.75) == ""
    context = retrieve_relevant_memory_context(store, "jack", "question", score_threshold=0.5)
    assert "Loosely related chunk." in context


def test_search_failure_returns_empty_instead_of_raising():
    store = Mock()
    store.similarity_search_with_score = Mock(side_effect=Exception("atlas down"))

    assert retrieve_relevant_memory_context(store, "jack", "question") == ""


def test_precomputed_embedding_uses_vector_search_and_skips_reembedding():
    """A caller that already embedded this turn's question elsewhere (fetch_relevant_user_facts,
    given the same question string) can pass that vector straight through instead of this call
    re-embedding the identical text again via the vector store's own internal embeddings
    client — a real, avoidable second network round trip otherwise."""
    hits = [(Document(page_content="Jack felt discouraged after his Principal presentation."), 0.91)]
    store = _fake_store(hits)

    context = retrieve_relevant_memory_context(
        store, "jack", "do you remember that thing this morning?", precomputed_embedding=[0.1, 0.2, 0.3]
    )

    assert "Jack felt discouraged after his Principal presentation." in context
    store._similarity_search_with_score.assert_called_once_with(
        [0.1, 0.2, 0.3], k=3, pre_filter={"username": "jack", "stale": {"$ne": True}}
    )
    store.similarity_search_with_score.assert_not_called()


# ---------------------------------------------------------------------------
# retrieve_user_memory
# ---------------------------------------------------------------------------

def test_retrieve_user_memory_returns_empty_when_no_vector_store():
    assert retrieve_user_memory(None, "jack", "what do I like?") == []


def test_retrieve_user_memory_returns_empty_when_query_blank():
    store = Mock()
    assert retrieve_user_memory(store, "jack", "   ") == []
    store.similarity_search.assert_not_called()


def test_retrieve_user_memory_excludes_stale_chunks_via_prefilter():
    store = Mock()
    docs = [Document(page_content="Jack works at Acme now.")]
    store.similarity_search = Mock(return_value=docs)

    result = retrieve_user_memory(store, "jack", "where does Jack work?", top_k=4)

    assert result == docs
    store.similarity_search.assert_called_once_with(
        "where does Jack work?", k=4, pre_filter={"username": "jack", "stale": {"$ne": True}}
    )


def test_retrieve_user_memory_search_failure_returns_empty():
    store = Mock()
    store.similarity_search = Mock(side_effect=Exception("atlas down"))

    assert retrieve_user_memory(store, "jack", "question") == []


# ---------------------------------------------------------------------------
# flag_stale_chunks_for_superseded_fact
# ---------------------------------------------------------------------------

def test_flag_stale_chunks_returns_zero_without_vector_store():
    assert flag_stale_chunks_for_superseded_fact(None, "jack", [0.1, 0.2]) == 0


def test_flag_stale_chunks_returns_zero_without_embedding():
    store = _fake_stale_store([])
    assert flag_stale_chunks_for_superseded_fact(store, "jack", None) == 0
    store._similarity_search_with_score.assert_not_called()


def test_flag_stale_chunks_tags_matches_above_threshold():
    hits = [
        (Document(page_content="Jack works at Principal.", id="chunk-1"), 0.93),
        (Document(page_content="Jack likes hiking.", id="chunk-2"), 0.10),
    ]
    store = _fake_stale_store(hits)

    flagged = flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0])

    assert flagged == 1
    store.collection.update_many.assert_called_once()
    call_args = store.collection.update_many.call_args
    assert call_args[0][0] == {"_id": {"$in": ["chunk-1"]}}
    assert call_args[0][1]["$set"]["stale"] is True
    assert call_args[0][1]["$set"]["stale_reason"] == "fact_superseded"


def test_flag_stale_chunks_ignores_matches_below_threshold():
    hits = [(Document(page_content="Jack likes hiking.", id="chunk-2"), 0.40)]
    store = _fake_stale_store(hits)

    flagged = flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0])

    assert flagged == 0
    store.collection.update_many.assert_not_called()


def test_flag_stale_chunks_search_failure_returns_zero():
    store = Mock()
    store._similarity_search_with_score = Mock(side_effect=Exception("atlas down"))

    assert flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0]) == 0


def test_flag_stale_chunks_update_failure_returns_zero():
    hits = [(Document(page_content="Jack works at Principal.", id="chunk-1"), 0.93)]
    store = _fake_stale_store(hits)
    store.collection.update_many = Mock(side_effect=Exception("write failed"))

    assert flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0]) == 0


def test_the_default_recall_cutoff_is_080_because_unrelated_questions_scored_up_to_082_at_075():
    assert ms.DEFAULT_RECALL_SIMILARITY_THRESHOLD == 0.80


def test_a_note_scoring_078_is_not_recalled_by_default_but_one_scoring_082_is():
    class Store:
        def similarity_search_with_score(self, *a, **k):
            return [(Document(page_content="Unrelated note."), 0.78), (Document(page_content="Real answer."), 0.82)]

    out = ms.retrieve_relevant_memory_context(Store(), "u", "a question")
    assert "Real answer." in out and "Unrelated note." not in out


# ---- a short follow-up is also searched together with the previous message --------------------------------------------------

from langchain_core.messages import AIMessage, HumanMessage


def thread(*texts):
    out = []
    for i, text in enumerate(texts):
        out.append(HumanMessage(content=text) if i % 2 == 0 else AIMessage(content=text))
    return out


def test_the_previous_user_message_is_the_human_message_before_the_current_one():
    messages = thread("Help me draft the toast.", "Sure, here is a draft.", "Make it funnier.")
    assert ms.previous_user_message(messages) == "Help me draft the toast."
    assert ms.previous_user_message(thread("Only one message.")) == ""
    assert ms.previous_user_message([]) == ""
    assert ms.previous_user_message([HumanMessage(content="x" * 900), HumanMessage(content="now")]) == "x" * ms.RECALL_CONTEXT_MAX_CHARS


def test_a_short_follow_up_gets_a_second_query_joining_the_previous_message_but_a_long_question_does_not():
    messages = thread("Help me draft the toast.", "Sure.", "Make it funnier.")
    assert ms.context_query_for(messages, "Make it funnier.") == "Help me draft the toast.\nMake it funnier."
    long_question = " ".join(["word"] * (ms.RECALL_CONTEXT_MAX_QUESTION_WORDS + 1))
    assert ms.context_query_for(thread("Earlier.", "Reply.", long_question), long_question) == ""
    assert ms.context_query_for(thread("First message ever."), "First message ever.") == ""
    assert ms.context_query_for(messages, "") == ""


def test_merging_hits_keeps_each_notes_best_score_and_the_best_few():
    a, b, c = (Document(page_content=t) for t in ("a", "b", "c"))
    merged = ms.merge_hits([(a, 0.70), (b, 0.90)], [(a, 0.85), (c, 0.60)], top_k=2)
    assert [(d.page_content, s) for d, s in merged] == [("b", 0.90), ("a", 0.85)]


class TwoQueryStore:
    """Answers each query text differently, and counts the searches."""

    def __init__(self, by_query):
        self.by_query = by_query
        self.searches = []

    def similarity_search_with_score(self, query, **kwargs):
        self.searches.append(query)
        return self.by_query.get(query, [])


def test_a_follow_up_recalls_what_only_the_joined_query_finds_and_still_applies_the_cutoff():
    toast = Document(page_content="Is giving the toast at their sister's wedding in Lisbon.")
    weak = Document(page_content="A loosely related note.")
    store = TwoQueryStore({
        "Make it funnier.": [(weak, 0.78)],
        "Help me draft the toast.\nMake it funnier.": [(toast, 0.86), (weak, 0.79)],
    })
    out = ms.retrieve_relevant_memory_context(store, "u", "Make it funnier.", context_query="Help me draft the toast.\nMake it funnier.")
    assert "wedding in Lisbon" in out and "loosely related" not in out
    assert store.searches == ["Make it funnier.", "Help me draft the toast.\nMake it funnier."]


def test_without_a_context_query_only_the_question_is_searched_exactly_as_before():
    store = TwoQueryStore({"Where is the wedding?": [(Document(page_content="Wedding in Lisbon."), 0.9)]})
    out = ms.retrieve_relevant_memory_context(store, "u", "Where is the wedding?")
    assert "Wedding in Lisbon." in out and store.searches == ["Where is the wedding?"]


def test_if_the_follow_up_search_fails_the_questions_own_result_is_still_used():
    class Flaky(TwoQueryStore):
        def similarity_search_with_score(self, query, **kwargs):
            if "\n" in query:
                raise RuntimeError("index down")
            return super().similarity_search_with_score(query, **kwargs)

    store = Flaky({"Where is the wedding?": [(Document(page_content="Wedding in Lisbon."), 0.9)]})
    out = ms.retrieve_relevant_memory_context(store, "u", "Where is the wedding?", context_query="Earlier.\nWhere is the wedding?")
    assert "Wedding in Lisbon." in out


def test_the_chat_endpoint_passes_the_follow_up_query_to_recall():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "context_query=context_query_for(chat_sessions[history_key], question)" in source


def test_a_note_found_only_by_the_joined_query_must_clear_the_cutoff_by_the_extra_bar():
    close = Document(page_content="Barely above the cutoff.")
    clear = Document(page_content="Clearly relevant.")
    store = TwoQueryStore({
        "Make it funnier.": [],
        "Help me draft the toast.\nMake it funnier.": [(close, ms.DEFAULT_RECALL_SIMILARITY_THRESHOLD + 0.01), (clear, ms.DEFAULT_RECALL_SIMILARITY_THRESHOLD + 0.03)],
    })
    out = ms.retrieve_relevant_memory_context(store, "u", "Make it funnier.", context_query="Help me draft the toast.\nMake it funnier.")
    assert "Clearly relevant." in out and "Barely above the cutoff." not in out
    assert ms.RECALL_CONTEXT_EXTRA_BAR == 0.02


def test_the_questions_own_search_is_not_discounted_even_when_the_joined_query_finds_the_same_note_lower():
    note = Document(page_content="Wedding in Lisbon.")
    store = TwoQueryStore({"Where is it?": [(note, 0.82)], "Earlier.\nWhere is it?": [(note, 0.81)]})
    out = ms.retrieve_relevant_memory_context(store, "u", "Where is it?", context_query="Earlier.\nWhere is it?")
    assert "Wedding in Lisbon." in out


# ---- the second look at near misses ------------------------------------------------------------------------------------------

import asyncio
from types import SimpleNamespace


def run_it(coro):
    return asyncio.run(coro)


def doc(text):
    return Document(page_content=text)


CUTOFF = ms.DEFAULT_RECALL_SIMILARITY_THRESHOLD


class ScoredStore:
    def __init__(self, hits):
        self.hits = hits

    def similarity_search_with_score(self, query, **kwargs):
        return self.hits


class Judge:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=self.reply)


def test_only_messages_about_the_users_own_life_or_what_was_said_before_earn_a_second_look():
    for text in ("What's my dog called?", "Remind me about the wedding.", "I was thinking about that trip", "Do you remember the book?", "Tell me about our plan"):
        assert ms.refers_to_user(text), text
    for text in ("What's the capital of Australia?", "Write a poem about rain.", "How does TCP work?", ""):
        assert not ms.refers_to_user(text), text


def test_the_judges_reply_is_read_as_note_numbers_and_ignores_anything_out_of_range():
    assert ms.parse_choice("1, 3", 3) == [0, 2]
    assert ms.parse_choice("2", 3) == [1]
    assert ms.parse_choice("NONE", 3) == [] and ms.parse_choice("none.", 3) == [] and ms.parse_choice("", 3) == []
    assert ms.parse_choice("Notes 2 and 2 and 9 and 0", 3) == [1]


def test_the_judge_keeps_the_notes_it_picks_and_never_raises():
    notes = [doc("Adopted a greyhound named Biscuit."), doc("Asked how to center a div."), doc("Plays cello.")]
    judge = Judge("1")
    kept = run_it(ms.judge_near_misses("What's my pup called?", notes, judge))
    assert [d.page_content for d in kept] == ["Adopted a greyhound named Biscuit."]
    assert "What's my pup called?" in judge.prompts[0] and "1. Adopted a greyhound named Biscuit." in judge.prompts[0] and "3. Plays cello." in judge.prompts[0]
    assert run_it(ms.judge_near_misses("x", notes, Judge("NONE"))) == []
    assert run_it(ms.judge_near_misses("x", notes, Judge(RuntimeError("model down")))) == []
    empty = Judge("1")
    assert run_it(ms.judge_near_misses("x", [], empty)) == [] and run_it(ms.judge_near_misses("  ", notes, empty)) == [] and empty.prompts == []


def test_retrieval_hands_back_near_misses_only_when_nothing_cleared_the_cutoff_and_at_most_three():
    close = [(doc(f"note {i}"), CUTOFF - 0.01 - i * 0.01) for i in range(5)]
    far = (doc("far"), CUTOFF - ms.NEAR_MISS_BAND - 0.02)
    near = []
    assert ms.retrieve_relevant_memory_context(ScoredStore(close + [far]), "u", "q", near_misses=near) == ""
    assert [d.page_content for d in near] == ["note 0", "note 1", "note 2"]
    cleared = []
    out = ms.retrieve_relevant_memory_context(ScoredStore([(doc("strong"), CUTOFF + 0.05)] + close), "u", "q", near_misses=cleared)
    assert "strong" in out and cleared == []


def test_a_near_miss_the_model_confirms_is_recalled_with_its_age_like_any_other_note():
    hit = [(doc("Adopted a greyhound named Biscuit."), CUTOFF - 0.03)]
    judge = Judge("1")
    out = run_it(ms.recall_with_second_look(ScoredStore(hit), "u", "What's the name of my pup?", llm=judge))
    assert "RELEVANT PAST CONTEXT" in out and "Adopted a greyhound named Biscuit." in out
    assert len(judge.prompts) == 1


def test_no_model_call_is_made_when_a_note_already_cleared_the_cutoff_or_the_message_is_not_about_the_user_or_the_feature_is_off(monkeypatch):
    judge = Judge("1")
    strong = [(doc("Wedding in Lisbon."), CUTOFF + 0.05)]
    assert "Wedding in Lisbon." in run_it(ms.recall_with_second_look(ScoredStore(strong), "u", "When is my sister's wedding?", llm=judge))
    near = [(doc("Adopted a greyhound."), CUTOFF - 0.02)]
    assert run_it(ms.recall_with_second_look(ScoredStore(near), "u", "What's the capital of Australia?", llm=judge)) == ""
    monkeypatch.setattr(ms, "SECOND_LOOK_ENABLED", False)
    assert run_it(ms.recall_with_second_look(ScoredStore(near), "u", "What's my pup called?", llm=judge)) == ""
    assert judge.prompts == []


def test_when_the_model_picks_none_or_fails_nothing_is_recalled():
    near = [(doc("Asked how to center a div."), CUTOFF - 0.02)]
    assert run_it(ms.recall_with_second_look(ScoredStore(near), "u", "I need a poem about rain", llm=Judge("NONE"))) == ""
    assert run_it(ms.recall_with_second_look(ScoredStore(near), "u", "I need a poem about rain", llm=Judge(RuntimeError("down")))) == ""


def test_a_follow_up_is_judged_with_the_previous_message_so_it_means_something():
    near = [(doc("Is giving the toast at their sister's wedding."), CUTOFF - 0.02)]
    judge = Judge("1")
    run_it(ms.recall_with_second_look(ScoredStore(near), "u", "Make it funnier for me.", context_query="Help me draft the toast.\nMake it funnier for me.", llm=judge))
    assert "Help me draft the toast." in judge.prompts[0]


def test_the_chat_endpoint_awaits_the_second_look_version_of_recall_and_the_prompt_forbids_same_area_matches():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "await recall_with_second_look(" in source and "retrieve_relevant_memory_context(" not in source
    assert "does NOT count" in ms.SECOND_LOOK_PROMPT and "NONE" in ms.SECOND_LOOK_PROMPT


def test_a_second_look_that_takes_too_long_recalls_nothing_instead_of_holding_up_the_reply(monkeypatch):
    monkeypatch.setattr(ms, "SECOND_LOOK_TIMEOUT_SECONDS", 0.05)

    async def slow(prompt):
        await asyncio.sleep(1.0)
        return SimpleNamespace(content="1")

    notes = [doc("Adopted a greyhound named Biscuit.")]
    assert run_it(ms.judge_near_misses("What's my pup called?", notes, slow)) == []
    assert ms.SECOND_LOOK_TIMEOUT_SECONDS == 0.05  # (monkeypatched; the shipped default is checked below)


def test_the_second_look_ships_with_a_two_second_limit_and_a_kill_switch():
    import inspect

    source = inspect.getsource(ms)
    assert 'MEMORY_SECOND_LOOK_TIMEOUT_SECONDS", "2.0"' in source and 'MEMORY_SECOND_LOOK", "true"' in source


# ---- the stale flag has to reach the notes it names (the ids it searched are strings; the stored ones are ObjectIds) ---------------

from bson import ObjectId


def test_a_search_result_id_is_matched_in_both_the_string_and_the_objectid_form():
    oid = ObjectId()
    assert ms.mongo_ids([str(oid)]) == [str(oid), oid]
    assert ms.mongo_ids(["chunk-1"]) == ["chunk-1"]  # not an ObjectId: left as it is
    assert ms.mongo_ids([]) == []


class IdCollection:
    """Matches `_id` the way Mongo does: an ObjectId only ever equals an ObjectId, never its string."""

    def __init__(self, docs):
        self.docs = docs

    def update_many(self, filt, update):
        wanted = filt["_id"]["$in"]
        hit = [d for d in self.docs if d["_id"] in wanted]
        for d in hit:
            d.update(update["$set"])
        return SimpleNamespace(matched_count=len(hit), modified_count=len(hit))


def test_flagging_really_marks_notes_whose_stored_id_is_an_objectid():
    """The bug, reproduced: the search gives doc.id as a string, the collection stores an ObjectId, and the old filter on the bare string
    matched nothing, so not one note was ever flagged stale on the real database."""
    oid = ObjectId()
    collection = IdCollection([{"_id": oid, "text": "Jack works at Principal."}, {"_id": ObjectId(), "text": "Jack likes hiking."}])
    store = SimpleNamespace(
        _similarity_search_with_score=lambda *a, **k: [(Document(page_content="Jack works at Principal.", id=str(oid)), 0.95)],
        collection=collection,
    )
    assert flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0]) == 1
    assert collection.docs[0]["stale"] is True and collection.docs[0]["stale_reason"] == "fact_superseded"
    assert "stale" not in collection.docs[1]


def test_the_stale_cutoff_ships_at_090_on_the_recall_scale_after_measuring_what_085_would_hide():
    import inspect

    assert 'MEMORY_STALE_CHUNK_SIMILARITY", "0.90"' in inspect.getsource(ms)


# ---- the explicit "do you remember ..." path ------------------------------------------------------------------------------------


def test_the_recall_phrase_is_stripped_from_the_front_of_a_search_and_nothing_else_is():
    strip = ms.strip_recall_phrasing
    assert strip("Do you remember my favorite color?") == "my favorite color"
    assert strip("Remember when I told you about my sister's wedding? When is it?") == "my sister's wedding? When is it"
    assert strip("Remind me, what was that book I finished?") == "what was that book I finished"
    assert strip("What did I tell you about my boss?") == "my boss"
    assert strip("What do you remember about the trip?") == "the trip"
    assert strip("Can you still remember where I parked?") == "where I parked"
    assert strip("My dog is called Biscuit, do you remember?") == "My dog is called Biscuit, do you remember?"  # not a leading phrase
    assert strip("Do you remember?") == "Do you remember?"  # nothing would be left
    assert strip("") == ""


class ExplicitStore:
    def __init__(self, scored=None, fail=False):
        self.scored, self.fail, self.queries = scored or [], fail, []

    def similarity_search_with_score(self, query, **kwargs):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("index down")
        return self.scored


class Invoke:
    def __init__(self, reply="NONE"):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=self.reply)


SURE, FLOOR = ms.EXPLICIT_RECALL_SURE, ms.EXPLICIT_RECALL_FLOOR


def test_a_sure_note_is_kept_without_asking_the_model_and_one_under_the_floor_is_dropped():
    sure, weak = doc("Adopted a greyhound named Biscuit."), doc("Asked how to center a div.")
    invoke = Invoke("1")
    got = ms.recall_explicit(ExplicitStore([(sure, SURE + 0.02), (weak, FLOOR - 0.02)]), "u", "Do you remember my dog's name?", invoke)
    assert got == [sure] and invoke.prompts == []


def test_a_note_between_the_floor_and_sure_is_kept_only_if_the_model_confirms_it_and_the_order_is_kept():
    first, middle, last = doc("first"), doc("middle"), doc("last")
    scored = [(first, SURE + 0.03), (middle, (SURE + FLOOR) / 2), (last, SURE + 0.01)]
    invoke = Invoke("1")
    got = ms.recall_explicit(ExplicitStore(scored), "u", "Do you remember my car?", invoke)
    assert got == [first, middle, last]
    assert "Do you remember my car?" in invoke.prompts[0] and "1. middle" in invoke.prompts[0]  # the judge sees what was really asked, and only the middle ones
    assert ms.recall_explicit(ExplicitStore(scored), "u", "Do you remember my car?", Invoke("NONE")) == [first, last]


def test_if_the_model_fails_or_is_switched_off_only_the_sure_notes_come_back(monkeypatch):
    sure, middle = doc("sure"), doc("middle")
    scored = [(sure, SURE + 0.02), (middle, (SURE + FLOOR) / 2)]
    assert ms.recall_explicit(ExplicitStore(scored), "u", "Do you remember x y?", Invoke(RuntimeError("down"))) == [sure]
    off = Invoke("1")
    monkeypatch.setattr(ms, "EXPLICIT_JUDGE_ENABLED", False)
    assert ms.recall_explicit(ExplicitStore(scored), "u", "Do you remember x y?", off) == [sure] and off.prompts == []


def test_the_search_uses_the_stripped_question_and_nothing_that_cannot_qualify_comes_back():
    store = ExplicitStore([(doc("far"), FLOOR - 0.05)])
    assert ms.recall_explicit(store, "u", "Do you remember my favorite color?", Invoke("1")) == []
    assert store.queries == ["my favorite color"]


def test_explicit_recall_is_empty_without_a_store_a_question_or_a_working_search():
    assert ms.recall_explicit(None, "u", "Do you remember my dog?", Invoke()) == []
    assert ms.recall_explicit(ExplicitStore(), "u", "   ", Invoke()) == []
    assert ms.recall_explicit(ExplicitStore(fail=True), "u", "Do you remember my dog?", Invoke()) == []


def test_the_explicit_cutoffs_ship_at_076_and_085_with_a_kill_switch_for_the_judge():
    import inspect

    source = inspect.getsource(ms)
    assert 'MEMORY_EXPLICIT_RECALL_FLOOR", "0.76"' in source and 'MEMORY_EXPLICIT_RECALL_SURE", "0.85"' in source
    assert 'MEMORY_EXPLICIT_JUDGE", "true"' in source


def test_retrieve_user_memory_with_a_cutoff_returns_only_notes_at_or_above_it_and_without_one_behaves_as_before():
    near, far = doc("near"), doc("far")

    class Store:
        def similarity_search_with_score(self, query, **kwargs):
            return [(near, 0.83), (far, 0.70)]

        def similarity_search(self, query, **kwargs):
            return [near, far]

    assert retrieve_user_memory(Store(), "u", "q", score_threshold=0.80) == [near]
    assert retrieve_user_memory(Store(), "u", "q") == [near, far]
