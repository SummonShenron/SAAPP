"""The timeline for a history question (backend/utils/timeline.py), built from facts and notes shaped like the real store (the Casey's case, 2026-11-23). No model, no database."""
import os
from types import SimpleNamespace

from backend.utils import self_counters
from backend.utils import timeline as tl

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def fact(text, at):
    return SimpleNamespace(fact=text, created_at=at, active=True)


FACTS = [
    fact("The user has interviewed with John Deere, Proofpoint, Casey's, and Source Allies.", "2026-09-27T10:00:00"),
    fact("Jack is following the progress of the interview with Casey.", "2026-11-10T10:00:00"),              # the person-Casey mix-up: a possessive question must not match it
    fact("Casey is a colleague of Jack.", "2026-11-03T10:00:00"),
    fact("Jack has a scheduled interview at the Casey's AI Center of Excellence on October 27, 2026.", "2026-10-25T10:00:00"),
]
NOTES = [
    {"text": "Missed the AI center of excellence interview and is looking to reschedule.", "created_at": "2026-10-21T09:00:00"},
    {"text": "Finished a four-round onsite interview for the Casey's role last Monday.", "created_at": "2026-11-23T09:00:00"},
    {"text": "Is waiting to hear back from Caseys regarding a final round interview.", "created_at": "2026-11-09T09:00:00"},
    {"text": "Finished a four round onsite interview for the Casey's role last Monday!", "created_at": "2026-11-23T10:00:00"},     # a near-duplicate
    {"text": "Completed a second-round interview at Casey's on November 9.", "created_at": "2026-11-01T09:00:00", "stale": True},
    {"text": "Had a generic interview with another company.", "created_at": "2026-11-20T09:00:00"},
]


def test_only_a_question_about_history_or_where_things_stand_gets_one():
    for q in ("what are all the interview steps ive had with caseys so far?", "remind me where things stand with caseys", "how many rounds did i do at caseys", "give me a recap of caseys"):
        assert tl.asks_for_history(q), q
    for q in ("caseys called me today", "how are you?", "i have an interview at caseys tomorrow"):
        assert not tl.asks_for_history(q), q


def test_the_casey_timeline_is_oldest_first_without_the_person_or_stale_or_repeated_lines():
    built = tl.build("what are all the interview steps ive had with caseys so far?", FACTS, NOTES)
    texts = [t for _at, t in built["lines"]]
    assert [at for at, _t in built["lines"]] == sorted(at for at, _t in built["lines"])                 # oldest first
    assert "Jack has a scheduled interview at the Casey's AI Center of Excellence on October 27, 2026." in texts
    assert "Finished a four-round onsite interview for the Casey's role last Monday." in texts or "Finished a four round onsite interview for the Casey's role last Monday!" in texts
    assert sum(1 for t in texts if "four" in t) == 1                                                       # the near-duplicate is dropped
    assert not any("colleague" in t for t in texts)                                                        # "caseys" is the company, not the person (a line about "the interview" may still match "interview")
    assert not any("second-round" in t for t in texts)                                                     # stale notes are left out


def test_the_rarest_words_name_the_matter_and_items_naming_more_of_them_come_first():
    terms = [w for w, _p in tl.choose_terms("what interview steps have i had for the caseys ai center of excellence?", [{"text": i.fact} for i in FACTS] + NOTES)]
    assert "caseys" in terms and len(terms) <= tl.MAX_TERMS
    many = [{"text": f"interview number {i} somewhere", "at": f"2026-11-2{i % 9}"} for i in range(20)]
    case = [{"text": "Interview at Casey's on October 27.", "at": "2026-10-25"}, {"text": "Casey's onsite interview, four rounds.", "at": "2026-11-16"},
            {"text": "Casey's coding exam interview.", "at": "2026-11-13"}]
    built = tl.build("what interview steps have i had with caseys so far", [], many + case)
    assert {"Interview at Casey's on October 27.", "Casey's onsite interview, four rounds.", "Casey's coding exam interview."} <= {t for _a, t in built["lines"]}


def test_a_word_named_too_rarely_or_too_often_says_nothing():
    assert tl.build("what are all the steps with zebras so far", FACTS, NOTES) is None                      # not in memory
    common = [{"text": f"work item {i}", "at": "2026-11-01"} for i in range(tl.MAX_TERM_ITEMS + 1)]
    assert tl.choose_terms("all my work so far", common) == []


def test_possessive_and_plain_words_match_as_they_should():
    assert tl.term_pattern("caseys").search("an interview at Casey's") and not tl.term_pattern("caseys").search("Casey is a colleague")
    assert tl.term_pattern("Casey's").search("Caseys role") and tl.term_pattern("kelsey").search("Kelsey's shift")


def test_the_block_tells_the_reply_to_answer_in_order_and_never_invent():
    block = tl.build_block({"term": "caseys", "lines": [("2026-10-25", "Interview on October 27.")]})
    assert "OLDEST FIRST" in block and "- Oct 25: Interview on October 27." in block and "never invent a date" in block
    assert tl.build_block(None) == ""


class _Collection:
    def __init__(self):
        self.query = None

    def find(self, query, projection):
        self.query, self.projection = query, projection
        return self

    def limit(self, n):
        self.n = n
        return [{"text": "x", "created_at": "2026-11-01"}]


def test_notes_are_read_by_the_question_words_without_embeddings():
    collection = _Collection()
    db = {"user_memory_chunks": collection}
    assert tl.fetch_notes(db, "jack", "what are all the interview steps with caseys") == [{"text": "x", "created_at": "2026-11-01"}]
    assert collection.query["username"] == "jack" and "casey" in collection.query["text"]["$regex"] and "interview" in collection.query["text"]["$regex"]
    assert "embedding" not in collection.projection and collection.n == tl.MAX_NOTES_READ
    assert tl.fetch_notes(None, "jack", "caseys") == [] and tl.fetch_notes(db, "jack", "how are you") == []


def test_the_reply_adds_the_timeline_on_a_history_question():
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert "timeline_utils.asks_for_history(question)" in app and "prompt = prompt + timeline_utils.build_block(timeline_entry)" in app
    assert 'tally(turn_counts, "timeline_shown")' in app and "timeline_shown" in self_counters.KEYS


def test_a_question_that_is_not_about_history_gets_nothing_even_when_memory_knows_the_matter():
    assert tl.build("caseys called me today about the role", FACTS, NOTES) is None


def test_a_word_named_only_twice_is_not_a_matter():
    items = [{"text": "Met Priya at the gym.", "at": "2026-11-01"}, {"text": "Priya texted.", "at": "2026-11-02"}]
    assert tl.choose_terms("what have i told you about priya so far", items) == []


def test_the_commonest_qualifying_word_is_the_one_left_out():
    items = [{"text": f"alpha{'' if i < 3 else 'x'} beta{'' if i < 4 else 'x'} gamma{'' if i < 5 else 'x'} delta", "at": "2026-11-01"} for i in range(30)]
    terms = [w for w, _p in tl.choose_terms("alpha beta gamma delta", items)]
    assert terms == ["alpha", "beta", "gamma"]                                                   # 3, 4 and 5 mentions; "delta" (30) is the one left out


def test_a_possessive_never_matches_the_person():
    assert not tl.term_pattern("Casey's").search("Casey is a colleague of Jack.")
    assert not tl.term_pattern("caseys").search("Casey is a colleague of Jack.")


def test_items_naming_more_words_win_the_twelve_places_over_newer_generic_ones():
    generic = [{"text": f"Interview with acme{i} about role{i} went fine.", "at": f"2026-11-{10 + i:02d}"} for i in range(18)]
    case = [{"text": "Interview at Casey's on October 27.", "at": "2026-10-25"}, {"text": "Casey's onsite interview, four rounds.", "at": "2026-11-01"},
            {"text": "Casey's coding exam interview.", "at": "2026-11-02"}]
    built = tl.build("what interview steps have i had with caseys so far", [], generic + case)
    texts = [t for _a, t in built["lines"]]
    assert len(texts) == tl.MAX_ITEMS
    assert {"Interview at Casey's on October 27.", "Casey's onsite interview, four rounds.", "Casey's coding exam interview."} <= set(texts)


def test_only_text_date_and_the_stale_flag_are_read():
    collection = _Collection()
    tl.fetch_notes({"user_memory_chunks": collection}, "jack", "all the steps with caseys")
    assert collection.projection == {"_id": 0, "text": 1, "created_at": 1, "stale": 1}
