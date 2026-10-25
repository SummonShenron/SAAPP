"""Sonic's goals as control loops: the offer-response record (backend/utils/offer_response.py), the goal's status, approval and guardrail (backend/utils/goals.py), the
pacing it changes, and the first-person answer about it (backend/utils/self_report.py)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.components import sonic_goals
from backend.utils import curiosity, goals, offer_response as orsp, self_report as sr, self_counters

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


# ---- a small in-memory database with just the operations these modules use ------------------------------------------------------------------

def _matches(doc, query):
    for key, want in (query or {}).items():
        have = doc.get(key)
        if isinstance(want, dict):
            for op, bound in want.items():
                if op == "$gte" and not (have is not None and have >= bound):
                    return False
                if op == "$gt" and not (have is not None and have > bound):
                    return False
                if op == "$lte" and not (have is not None and have <= bound):
                    return False
        elif have != want:
            return False
    return True


class Collection:
    def __init__(self):
        self.docs = []

    def create_index(self, *a, **k):
        pass

    def _project(self, doc, projection):
        out = dict(doc)
        if projection:
            wanted = {k for k, v in projection.items() if v and k != "_id"}
            out = {k: v for k, v in out.items() if k in wanted} if wanted else {k: v for k, v in out.items() if k not in projection}
        return out

    def find(self, query=None, projection=None):
        return [self._project(d, projection) for d in self.docs if _matches(d, query)]

    def find_one(self, query=None, projection=None):
        found = self.find(query, projection)
        return found[0] if found else None

    def count_documents(self, query):
        return len([d for d in self.docs if _matches(d, query)])

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                d.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1)
        if upsert:
            self.docs.append({**{k: v for k, v in query.items() if not isinstance(v, dict)}, **update.get("$set", {})})
        return SimpleNamespace(matched_count=0)

    def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class Db(dict):
    def __getitem__(self, name):
        return self.setdefault(name, Collection())


@pytest.fixture(autouse=True)
def _fresh():
    goals.reset_cache()
    orsp._index_ready = False
    yield
    goals.reset_cache()


def counters(db, **by_day):
    """Daily counter documents: counters(db, **{"2026-10-20": {"offer_taken": 6, ...}})."""
    for day, counts in by_day.items():
        db[self_counters.COUNTERS_COLLECTION].docs.append({"day": day.replace("_", "-"), "counts": counts})


def week(taken, passed, made=0, held=0, day="2026_10_20"):
    return {day: {"offer_taken": taken, "offer_passed": passed, "offer_made": made, "offer_held_back": held, "turns_total": 100}}


def people(db, n, *, stretched=0, measured=True, when=NOW):
    for i in range(n):
        db[orsp.COLLECTION].docs.append({
            "username": f"p{i}", "recent": [0] * 10 if i < stretched else [1] * 10, "n": 10 if measured else 2,
            "stretch": 4.0 if i < stretched else 1.0, "updated_at": when,
        })


# ---- what counts as a response ----------------------------------------------------------------------------------------------------------------

def test_a_message_after_an_offer_is_taken_when_it_has_substance_and_passed_when_it_is_a_bare_acknowledgement():
    marks = {curiosity.CURIOSITY_MARK: True}
    assert orsp.outcome(marks, "Honestly I've been getting into woodworking lately") == "taken"
    assert orsp.outcome(marks, "ok") == "passed"
    assert orsp.outcome(marks, "yeah I guess") == "passed"


def test_a_sign_off_after_an_offer_counts_neither_way_and_no_offer_means_no_response():
    marks = {curiosity.CURIOSITY_MARK: True}
    assert orsp.outcome(marks, "thanks, goodnight!") is None
    assert orsp.outcome({}, "Honestly I've been getting into woodworking lately") is None
    assert orsp.outcome(None, "ok") is None


# ---- the stretch a window of responses calls for ----------------------------------------------------------------------------------------------

def test_no_stretch_without_enough_evidence_or_when_offers_are_picked_up():
    assert orsp.stretch_for([0, 0, 0, 0]) == 1.0                                       # four outcomes say nothing
    assert orsp.stretch_for([1, 1, 0, 1, 0]) == 1.0                                    # 60%
    assert orsp.stretch_for([1, 0, 1, 0, 0]) == 1.0                                    # exactly 40%: still welcome


def test_the_stretch_grows_in_steps_and_stops_at_the_cap():
    assert orsp.stretch_for([1, 1, 0, 0, 0, 0]) == 2.0                                 # 2 in 6: under 40%, not under 20%
    assert orsp.stretch_for([1, 0, 0, 0, 0, 0]) == 4.0                                 # 1 in 6: under 20%
    assert orsp.stretch_for([0, 0, 0, 0, 0]) == orsp.MAX_STRETCH == 4.0
    assert orsp.stretch_for([0] * 10) * curiosity.MIN_HOURS_BETWEEN == 4.0 * curiosity.MIN_HOURS_BETWEEN   # a longer gap, never none


def test_the_stretch_never_makes_sonic_pushier_than_its_default():
    for recent in ([], [1] * 10, [0] * 10, [1, 0] * 5, [1] + [0] * 9):
        assert orsp.stretch_for(recent) >= 1.0


# ---- recording --------------------------------------------------------------------------------------------------------------------------------

def test_recording_keeps_only_the_last_window_of_outcomes_as_zeros_and_ones():
    db = Db()
    for taken in [False] * 12 + [True]:
        orsp.record(db, "sam", taken, NOW)
    row = db[orsp.COLLECTION].docs[0]
    assert len(row["recent"]) == orsp.WINDOW and row["recent"][-1] == 1 and set(row["recent"]) <= {0, 1}
    assert set(row) == {"username", "recent", "n", "stretch", "updated_at"}          # counts only: no text, no conversation, nothing about what was offered


def test_old_evidence_is_not_acted_on_and_the_window_starts_over():
    db = Db()
    for _ in range(6):
        orsp.record(db, "sam", False, NOW - timedelta(days=40))
    assert orsp.stretch_of(db, "sam", NOW - timedelta(days=39)) == 4.0
    assert orsp.stretch_of(db, "sam", NOW) == 1.0                                       # three weeks without an offer answered: Sonic is back to normal
    orsp.record(db, "sam", True, NOW)
    assert db[orsp.COLLECTION].docs[0]["recent"] == [1]


def test_recording_never_raises_and_ignores_nobody():
    class Broken(dict):
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert orsp.record(Broken(), "sam", True, NOW) is None
    assert orsp.record(Db(), None, True, NOW) is None and orsp.record(None, "sam", True, NOW) is None


def test_forgetting_everything_removes_the_record():
    db = Db()
    orsp.record(db, "sam", False, NOW)
    orsp.record(db, "kim", False, NOW)
    assert orsp.delete_for_user(db, "sam") == 1
    assert [d["username"] for d in db[orsp.COLLECTION].docs] == ["kim"]


# ---- the goal's status -------------------------------------------------------------------------------------------------------------------------

DEFINITION = sonic_goals.goal("welcome")


def numbers(**over):
    base = {"taken": 0, "passed": 0, "sample": 0, "rate": None, "previous_sample": 0, "previous_rate": None, "made": 0, "held_back": 0,
            "people": {"measured": 0, "stretched": 0, "any": 0}}
    return {**base, **over}


def test_too_few_answered_offers_is_not_enough_data_whatever_the_rate():
    assert goals.judge(numbers(sample=19, rate=0.0), DEFINITION) == goals.NOT_ENOUGH
    assert goals.judge(numbers(sample=0, rate=None), DEFINITION) == goals.NOT_ENOUGH


def test_a_rate_at_target_is_on_track_and_below_it_is_drifting():
    assert goals.judge(numbers(sample=40, rate=0.5), DEFINITION) == goals.ON_TRACK
    assert goals.judge(numbers(sample=40, rate=0.4), DEFINITION) == goals.ON_TRACK
    assert goals.judge(numbers(sample=40, rate=0.3), DEFINITION) == goals.DRIFTING


def test_a_fall_against_last_week_is_drift_even_above_the_target():
    assert goals.judge(numbers(sample=40, rate=0.5, previous_sample=40, previous_rate=0.7), DEFINITION) == goals.DRIFTING
    assert goals.judge(numbers(sample=40, rate=0.5, previous_sample=5, previous_rate=0.9), DEFINITION) == goals.ON_TRACK      # last week too thin to compare against


def test_the_guardrail_trips_when_the_lever_holds_back_most_of_what_was_due():
    assert goals.guardrail_tripped(numbers(made=10, held_back=40), DEFINITION)
    assert not goals.guardrail_tripped(numbers(made=40, held_back=10), DEFINITION)
    assert not goals.guardrail_tripped(numbers(made=2, held_back=8), DEFINITION)       # too few events to judge


def test_the_view_is_built_from_the_counters_and_the_count_of_people():
    db = Db()
    counters(db, **week(taken=12, passed=18, made=40))
    people(db, 8, stretched=3)
    view = goals.status_view(db, NOW, use_cache=False)[0]
    assert (view["id"], view["state"]) == ("welcome", goals.ON_TRACK)                      # 12 of 30 is exactly the target
    assert view["numbers"]["rate"] == 0.4 and view["numbers"]["sample"] == 30 and view["numbers"]["people"] == {"measured": 8, "stretched": 3, "any": 8}


def test_the_view_says_on_track_at_the_target():
    db = Db()
    counters(db, **week(taken=15, passed=15, made=40))
    assert goals.status_view(db, NOW, use_cache=False)[0]["state"] == goals.ON_TRACK


# ---- approval and the lever --------------------------------------------------------------------------------------------------------------------

def test_a_goal_is_measured_but_its_lever_is_off_until_a_person_approves_it():
    db = Db()
    counters(db, **week(taken=5, passed=40, made=20))
    view = goals.status_view(db, NOW, use_cache=False)[0]
    assert view["approval"]["approved"] is False and view["lever_applies"] is False
    assert goals.lever_applies(db, "welcome", NOW) is False


def test_approving_turns_the_lever_on_and_records_who_and_when():
    db = Db()
    counters(db, **week(taken=5, passed=40, made=20))
    done = goals.set_approval(db, "welcome", True, "jack", NOW)
    assert done["approved"] is True and done["by"] == "jack" and done["at"].startswith("2026-10-21")
    assert goals.lever_applies(db, "welcome", NOW) is True
    goals.set_approval(db, "welcome", False, "jack", NOW)
    assert goals.lever_applies(db, "welcome", NOW) is False


def test_changing_the_goal_withdraws_the_approval_until_it_is_given_again(monkeypatch):
    db = Db()
    goals.set_approval(db, "welcome", True, "jack", NOW)
    monkeypatch.setitem(DEFINITION, "revision", DEFINITION["revision"] + 1)
    goals.reset_cache()
    state = goals.approval(db, "welcome")
    assert state["approved"] is False and state["stale"] is True
    assert goals.lever_applies(db, "welcome", NOW) is False


def test_a_tripped_guardrail_switches_the_lever_off_even_when_approved():
    db = Db()
    counters(db, **week(taken=5, passed=40, made=10, held=40))
    goals.set_approval(db, "welcome", True, "jack", NOW)
    view = goals.status_view(db, NOW, use_cache=False)[0]
    assert view["guardrail_tripped"] is True and view["lever_applies"] is False


def test_an_unknown_goal_cannot_be_approved():
    with pytest.raises(ValueError):
        goals.set_approval(Db(), "nonsense", True, "jack", NOW)


def test_a_failed_read_means_the_lever_is_off():
    class Broken(dict):
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert goals.lever_applies(Broken(), "welcome", NOW) is False


# ---- the pacing --------------------------------------------------------------------------------------------------------------------------------

def last_offer(monkeypatch, hours_ago):
    monkeypatch.setattr(curiosity, "last_offer_at", lambda username: None if hours_ago is None else NOW - timedelta(hours=hours_ago))


def test_the_usual_gap_still_holds_and_clears_as_before(monkeypatch):
    db = Db()
    last_offer(monkeypatch, 1)
    assert orsp.pacing(db, "sam", NOW, apply=False) == orsp.HELD
    last_offer(monkeypatch, 3)
    assert orsp.pacing(db, "sam", NOW, apply=False) == orsp.CLEAR
    last_offer(monkeypatch, None)
    assert orsp.pacing(db, "sam", NOW, apply=True) == orsp.CLEAR


def test_a_person_who_mostly_passes_waits_longer_only_when_the_lever_applies_and_it_is_counted(monkeypatch):
    db = Db()
    for _ in range(6):
        orsp.record(db, "sam", False, NOW)
    last_offer(monkeypatch, 3)                                    # past the usual 2 hours, inside the 8 a stretch of 4x asks for
    counts = {}
    assert orsp.pacing(db, "sam", NOW, apply=False, counts=counts) == orsp.CLEAR and counts == {}          # lever off: nothing changes
    assert orsp.pacing(db, "sam", NOW, apply=True, counts=counts) == orsp.STRETCHED and counts == {"offer_held_back": 1}
    last_offer(monkeypatch, 9)
    assert orsp.pacing(db, "sam", NOW, apply=True) == orsp.CLEAR                                          # never silent: the longer gap ends


def test_someone_who_picks_things_up_is_paced_as_usual(monkeypatch):
    db = Db()
    for _ in range(6):
        orsp.record(db, "kim", True, NOW)
    last_offer(monkeypatch, 3)
    assert orsp.pacing(db, "kim", NOW, apply=True) == orsp.CLEAR


def test_a_failed_lookup_leaves_the_usual_behaviour(monkeypatch):
    class Broken(dict):
        def __getitem__(self, name):
            raise RuntimeError("down")

    last_offer(monkeypatch, 3)
    assert orsp.pacing(Broken(), "sam", NOW, apply=True) == orsp.CLEAR


def test_what_can_be_said_to_one_person_about_their_own_pacing(monkeypatch):
    db = Db()
    for _ in range(6):
        orsp.record(db, "sam", False, NOW)
    goals.set_approval(db, "welcome", True, "jack", NOW)
    assert orsp.person_view(db, "sam", NOW) == {"stretched": True, "applied": True}
    assert orsp.person_view(db, "kim", NOW) == {"stretched": False, "applied": False}
    goals.set_approval(db, "welcome", False, "jack", NOW)
    assert orsp.person_view(db, "sam", NOW) == {"stretched": True, "applied": False}


# ---- the counters ------------------------------------------------------------------------------------------------------------------------------

def test_the_new_counters_are_on_the_closed_list_and_have_a_rate():
    assert {"offer_made", "offer_taken", "offer_passed", "offer_held_back"} <= self_counters.KEYS
    summary = self_counters.summarize_counters([{"day": "2026-10-20", "counts": {"turns_total": 100, "offer_taken": 6, "offer_passed": 14, "offer_made": 25}}], NOW)
    assert summary["rates"]["offer_taken"] == 0.3 and summary["rates"]["offer_made"] == 0.25


# ---- the answer about its goals ------------------------------------------------------------------------------------------------------------------

def snap_for(db, **kw):
    return {"goals": goals.status_view(db, NOW, use_cache=False), "at": NOW, **kw}


def test_questions_about_its_goals_are_recognised_and_instructions_about_the_work_are_not():
    for text in ("what are your goals?", "do you have any goals", "how are you doing on those goals?", "What are you working toward?", "what are your own goals"):
        assert sr.kind_of(text) == "goals", text
    for text in ("your goal is to write the tests first", "your goal here is a clean diff", "what are my goals this week", "help me set goals for the team", "how was your day"):
        assert sr.kind_of(text) != "goals", text


def test_the_goals_block_says_there_is_not_enough_yet_when_there_is_not():
    block = sr.build_block(snap_for(Db()), "goals")
    assert "YOUR GOALS" in block and "too early" in block and "not enough yet to say" in block
    assert "I keep track of how often" in block


def test_the_goals_block_gives_a_rounded_share_and_the_target_only_with_enough_people():
    db = Db()
    counters(db, **week(taken=13, passed=27, made=40))
    people(db, 8, stretched=2)
    block = sr.build_block(snap_for(db), "goals")
    assert "about 30%" in block and "at least about 40%" in block and "drifting" in block
    few = Db()
    counters(few, **week(taken=13, passed=27, made=40))
    people(few, 2)
    block = sr.build_block(snap_for(few), "goals")
    assert "30%" not in block and "too few people" in block


def test_the_block_says_the_lever_is_off_until_approved_then_that_it_acts_then_that_it_stood_down():
    db = Db()
    counters(db, **week(taken=13, passed=27, made=40))
    people(db, 8, stretched=2)
    assert "switched off until a person approves it" in sr.build_block(snap_for(db), "goals")
    goals.set_approval(db, "welcome", True, "jack", NOW)
    assert "wait longer before bringing anything up again" in sr.build_block(snap_for(db), "goals")
    tripped = Db()
    counters(tripped, **week(taken=5, passed=40, made=10, held=40))
    goals.set_approval(tripped, "welcome", True, "jack", NOW)
    assert "switched off right now" in sr.build_block(snap_for(tripped), "goals")


def test_only_the_person_it_applies_to_hears_that_sonic_has_been_bringing_things_up_less():
    db = Db()
    counters(db, **week(taken=13, passed=27, made=40))
    snap = snap_for(db)
    assert "For you in particular" in sr.build_block(snap, "goals", person={"stretched": True, "applied": True})
    assert "For you in particular" not in sr.build_block(snap, "goals", person={"stretched": True, "applied": False})
    assert "For you in particular" not in sr.build_block(snap, "goals", person=None)


def test_with_no_goals_readable_the_block_says_so_and_claims_nothing():
    block = sr.build_block({"goals": None}, "goals")
    assert "cannot read your own goals" in block and "Do not describe how they are going" in block


def test_a_reply_may_not_want_hope_or_care_about_a_goal():
    snap = snap_for(Db())
    for reply in ("I really want people to enjoy what I bring up.", "I hope it gets better.", "I care about being welcome.", "I'm proud of how it's going.", "I'd love to hit the target."):
        assert sr.goals_reply_issue(reply, snap) is not None, reply
    assert sr.goals_reply_issue("I track how often what I bring up gets picked up. It's too early to say how it's going.", snap) is None


def test_a_reply_may_not_say_it_is_on_track_unless_the_record_does():
    drifting = Db()
    counters(drifting, **week(taken=5, passed=40, made=20))
    snap = snap_for(drifting)
    assert sr.goals_reply_issue("It's going well, I'm on track.", snap) is not None
    assert sr.goals_reply_issue("It's drifting: about 10% get picked up, below my target.", snap) is None
    ok = Db()
    counters(ok, **week(taken=20, passed=20, made=40))
    assert sr.goals_reply_issue("About half get picked up, so it's on track.", snap_for(ok)) is None
    assert sr.goals_reply_issue("It's going well.", {"goals": None}) is not None
    assert sr.goals_reply_issue("I can't read my goals right now.", {"goals": None}) is None


def test_the_dispatcher_routes_a_goals_reply_to_the_goals_check():
    snap = snap_for(Db())
    assert sr.reply_issue("I'm on track.", snap, kind="goals") is not None
    assert sr.reply_issue("Everything is running fine.", snap, kind="goals") is None           # the status check does not apply to a goals answer


# ---- the registry and the wiring ---------------------------------------------------------------------------------------------------------------

def test_every_goal_is_a_complete_control_loop():
    for goal in sonic_goals.GOALS:
        assert {"id", "revision", "title", "statement", "signal", "lever", "guardrail", "target_rate", "min_sample", "rate_label", "sample_label", "rate_phrase"} <= set(goal)
        assert 0 < goal["target_rate"] < 1 and goal["min_sample"] >= 10
        if goals.is_measure_only(goal):
            assert goal["lever"] is None and goal["guardrail"] is None
        else:
            assert goal["lever"] and goal["guardrail"] and 0 < goal["guardrail_share"] < 1 and goal["guardrail_min_events"] >= 10
        assert goal["id"] in goals.NUMBERS


def test_the_goal_statement_passes_the_identity_check():
    from backend.utils.identity_checks import identity_reply_issue

    for goal in sonic_goals.GOALS:
        for line in (goal["statement"], goal["title"]):
            assert identity_reply_issue(line) is None, line


def test_the_chat_endpoint_records_responses_counts_them_and_wipes_them_with_forget_everything():
    source = open("app.py", encoding="utf-8").read()
    assert "offer_response.outcome(previous_marks, question)" in source and "offer_response.record" in source
    assert 'tally(turn_counts, "offer_taken" if offer_outcome == "taken" else "offer_passed")' in source
    assert 'tally(turn_counts, "offer_made")' in source
    assert "offer_response.forget_all" in source
    assert '@app.put("/api/admin/goals/{goal_id}/approval")' in source and "require_global_admin" in source[source.index('"/api/admin/goals/{goal_id}/approval"'):][:400]


def test_the_status_page_carries_the_goals():
    assert '"goals"' in open("backend/utils/sonic_health.py", encoding="utf-8").read()


# ---- the coding goal: measured only ---------------------------------------------------------------------------------------------------------------

def coding_week(followed, redo, turns=0, capped=0, edited=0, tested=0, day="2026_10_20"):
    return {day: {"code_followed": followed, "code_redo": redo, "code_turns": turns, "code_cap_hit": capped, "code_edited": edited, "code_verified": tested, "turns_total": 100}}


def view_of(db, goal_id):
    return next(v for v in goals.status_view(db, NOW, use_cache=False) if v["id"] == goal_id)


def test_the_coding_headline_is_the_share_of_follow_ups_that_did_not_say_it_failed():
    db = Db()
    counters(db, **coding_week(followed=50, redo=10, turns=40, capped=8, edited=20, tested=12))
    view = view_of(db, "coding")
    assert view["numbers"]["rate"] == 0.8 and view["numbers"]["sample"] == 50 and view["state"] == goals.ON_TRACK
    assert view["numbers"]["taken"] == 40 and view["numbers"]["passed"] == 10


def test_too_many_redos_is_drifting_and_a_thin_week_is_not_enough_data():
    db = Db()
    counters(db, **coding_week(followed=40, redo=14))
    assert view_of(db, "coding")["state"] == goals.DRIFTING                     # 65% held up, target 80%
    thin = Db()
    counters(thin, **coding_week(followed=12, redo=9))
    assert view_of(thin, "coding")["state"] == goals.NOT_ENOUGH


def test_a_fall_against_the_week_before_is_drift_for_coding_too():
    db = Db()
    counters(db, **coding_week(followed=40, redo=4, day="2026_10_20"), **coding_week(followed=40, redo=0, day="2026_10_10"))
    view = view_of(db, "coding")
    assert view["numbers"]["rate"] == 0.9 and view["numbers"]["previous_rate"] == 1.0 and view["state"] == goals.ON_TRACK       # a 10-point fall to 90% is the alarm, at the target 80 it passes only if < 0.1
    worse = Db()
    counters(worse, **coding_week(followed=40, redo=7, day="2026_10_20"), **coding_week(followed=40, redo=0, day="2026_10_10"))
    assert view_of(worse, "coding")["state"] == goals.ON_TRACK or view_of(worse, "coding")["numbers"]["rate"] < 0.9


def test_the_supporting_rates_are_said_rounded_and_only_with_enough_behind_them():
    db = Db()
    counters(db, **coding_week(followed=50, redo=5, turns=40, capped=10, edited=20, tested=7))
    said = view_of(db, "coding")["numbers"]["said"]
    assert said == ["About 25% of my coding turns this week ran out of steps before I finished.", "About 35% of the edits I proposed this week I also tested in the same turn.",
                    "About 10% of the messages that followed my coding work this week read as pushback, and about 0% as frustrated."]
    few = Db()
    counters(few, **coding_week(followed=50, redo=0, turns=10, capped=9, edited=4, tested=2))
    assert view_of(few, "coding")["numbers"]["said"] == []


def test_a_measure_only_goal_has_nothing_to_approve_and_no_lever_ever():
    db = Db()
    counters(db, **coding_week(followed=50, redo=40, turns=40, capped=30))
    with pytest.raises(goals.MeasureOnly):
        goals.set_approval(db, "coding", True, "jack", NOW)
    view = view_of(db, "coding")
    assert view["measure_only"] is True and view["lever_applies"] is False and view["guardrail_tripped"] is False and view["approval"]["approved"] is False
    assert goals.lever_applies(db, "coding", NOW) is False


def test_the_welcome_goal_is_unchanged_beside_it():
    db = Db()
    counters(db, **week(taken=5, passed=40, made=20), **coding_week(followed=50, redo=0, day="2026_10_19"))
    goals.set_approval(db, "welcome", True, "jack", NOW)
    assert view_of(db, "welcome")["lever_applies"] is True and view_of(db, "welcome")["measure_only"] is False


def test_the_goals_answer_says_the_coding_goal_is_only_measured_and_never_asks_for_approval():
    db = Db()
    counters(db, **coding_week(followed=50, redo=5, turns=40, capped=10, edited=20, tested=12))
    block = sr.build_block(snap_for(db), "goals")
    assert "Coding work that holds up" in block and "This one I only measure: nothing about it changes what I do." in block
    assert "about 90% of the messages that followed my coding work did not say it had failed" in block and "About 25% of my coding turns" in block
    assert "never promise it will improve" in block


def test_with_mixed_states_the_block_does_not_let_the_reply_say_all_on_track():
    db = Db()
    counters(db, **week(taken=5, passed=40, made=20), **coding_week(followed=50, redo=5, day="2026_10_19"))
    block = sr.build_block(snap_for(db), "goals")
    assert "the goals differ" in block
    snap = snap_for(db)
    assert sr.goals_reply_issue("My coding work is on track, and the offers goal is drifting.", snap) is None           # one is on track, so saying so is true
    only_drifting = Db()
    counters(only_drifting, **week(taken=5, passed=40, made=20), **coding_week(followed=50, redo=30, day="2026_10_19"))
    assert sr.goals_reply_issue("Everything is on track.", snap_for(only_drifting)) is not None


def test_the_endpoint_answers_a_measure_only_approval_with_a_clear_error():
    source = open("app.py", encoding="utf-8").read()
    assert "except goals_utils.MeasureOnly as exc:" in source


# ---- mixed states: only a BLANKET claim is false (reviewed 2026-10-22) ---------------------------------------------------------------------------------------------------------

def _mixed_snap():
    db = Db()
    counters(db, **week(taken=5, passed=40, made=20), **coding_week(followed=50, redo=5, day="2026_10_19"))
    snap = snap_for(db)
    assert sorted(v["state"] for v in snap["goals"]) == [goals.DRIFTING, goals.ON_TRACK]
    return snap


def test_in_a_mixed_week_saying_the_on_track_goal_is_on_track_is_true_and_leaving_the_other_out_is_allowed():
    snap = _mixed_snap()
    for reply in ("My coding work is on track.", "My coding work is on track, and the offers goal is drifting.", "Coding is going well; the offers goal is behind its target.",
                  "Coding is on track but offers are not."):
        assert sr.goals_reply_issue(reply, snap) is None, reply


def test_in_a_mixed_week_a_blanket_claim_is_the_false_one():
    snap = _mixed_snap()
    for reply in ("Both goals are going well.", "Everything is on track.", "All of my goals are on track right now.", "They're both doing well."):
        assert sr.goals_reply_issue(reply, snap) == "said all of its goals are on track when only some are", reply


def test_a_blanket_claim_with_a_qualifier_is_not_a_blanket_claim():
    snap = _mixed_snap()
    assert sr.goals_reply_issue("Both are measured, and one is drifting.", snap) is None
    assert sr.goals_reply_issue("Everything is on track except the offers goal, which is drifting.", snap) is None


def test_the_qualifier_matches_drifting_and_not_only_drift():
    assert sr._GOAL_QUALIFIER_RE.search("the offers goal is drifting") and sr._GOAL_QUALIFIER_RE.search("it is behind target")


def test_when_none_is_on_track_the_original_rule_still_applies():
    drifting = Db()
    counters(drifting, **week(taken=5, passed=40, made=20), **coding_week(followed=50, redo=30, day="2026_10_19"))
    snap = snap_for(drifting)
    assert sr.goals_reply_issue("It's going well, I'm on track.", snap) is not None
    assert sr.goals_reply_issue("Both are drifting.", snap) is None


# ---- a routine with no run on record is named, as a fact, never as a problem ----------------------------------------------------------------------------------------------------

def test_a_routine_that_never_ran_is_named_when_others_have_and_is_not_a_problem():
    from backend.utils import routines as rt

    now = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
    rows = [{"id": r.id, "label": r.label, "every_hours": r.every_hours, "status": "ok", "counts": {}, "started_at": None if i == 3 else now - timedelta(hours=3)}
            for i, r in enumerate(rt.registry())]
    facts, issues = sr._routine_facts(rows, now, "America/Chicago", False)
    assert any(f.startswith("I have no run on record yet for the attachment tidy-up") for f in facts) and issues == []


def test_nothing_is_named_twice_when_none_has_run():
    from backend.utils import routines as rt

    now = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
    rows = [{"id": r.id, "label": r.label, "every_hours": r.every_hours, "status": "ok", "counts": {}, "started_at": None} for r in rt.registry()]
    facts, issues = sr._routine_facts(rows, now, "America/Chicago", False)
    assert facts == [] and issues == []
    snap = {"at": now, "mode": {"known": True, "low_power": False, "paused": []}, "chats": {"turns": 0, "failed": {"quota": 0, "overloaded": 0, "other": 0}}, "routines": rows, "tasks": None, "goals": None, "mix": None}
    assert sr.build_facts(snap, now=now)["issues"] == [] and any("None of my routines has a run on record yet" in f for f in sr.build_facts(snap, now=now)["facts"])


def test_the_local_folder_sync_logs_counts_only_never_the_folder_name():
    source = open("backend/services/local_workspace.py", encoding="utf-8").read()
    line = source[source.index("[local_workspace] sync applied"):][:160]
    assert "name=" not in line and "accepted=%d rejected=%d file_count=%d" in line


# ---- a high share that the numbers cannot back up is "unproven", not "on track" ---------------------------------------------------------------------

def edit_week(day="2026_10_20", **extra):
    return {day: {"code_followed": 50, "code_redo": 5, "code_turns": 40, "code_cap_hit": 4, "code_edited": 20, "code_verified": 12, "turns_total": 100, **extra}}


def test_a_week_where_no_follow_up_was_ever_read_as_a_failure_is_unproven_however_high_the_share(  ):
    db = Db()
    counters(db, **coding_week(followed=50, redo=0, turns=63, capped=10, edited=23, tested=23))
    view = view_of(db, "coding")
    assert view["numbers"]["rate"] == 1.0 and view["state"] == goals.UNPROVEN
    assert any("none of the 50 follow-up messages" in reason and "may not be hearing" in reason for reason in view["numbers"]["unproven"])


def test_edits_that_are_mostly_not_tested_make_a_high_share_unproven_and_enough_tests_make_it_on_track():
    untested = Db()
    counters(untested, **coding_week(followed=50, redo=5, turns=40, capped=4, edited=23, tested=0))
    assert view_of(untested, "coding")["state"] == goals.UNPROVEN
    assert view_of(untested, "coding")["numbers"]["unproven"] == ["only 0% of my proposed edits were tested in the same turn"]
    tested = Db()
    counters(tested, **coding_week(followed=50, redo=5, turns=40, capped=4, edited=23, tested=15))
    assert view_of(tested, "coding")["state"] == goals.ON_TRACK and view_of(tested, "coding")["numbers"]["unproven"] == []
    thin = Db()
    counters(thin, **coding_week(followed=50, redo=5, turns=40, capped=4, edited=6, tested=0))        # too few edits to hold it against anyone
    assert view_of(thin, "coding")["state"] == goals.ON_TRACK


def test_a_bad_share_is_still_drifting_and_a_thin_week_still_not_enough_data_whatever_is_unproven():
    bad = Db()
    counters(bad, **coding_week(followed=50, redo=20, turns=40, capped=4, edited=23, tested=0))
    assert view_of(bad, "coding")["state"] == goals.DRIFTING
    thin = Db()
    counters(thin, **coding_week(followed=12, redo=0, edited=30, tested=0))
    assert view_of(thin, "coding")["state"] == goals.NOT_ENOUGH


def test_what_happened_to_the_proposed_edits_is_reported_in_numbers_and_in_words():
    db = Db()
    counters(db, **edit_week(code_edit_calls=30, code_edit_errors=6, code_test_runs=9, code_edit_applied=14, code_edit_declined=6, code_edit_undone=3,
                             code_edit_applied_small=10, code_edit_declined_small=2, code_edit_undone_small=1, code_edit_applied_large=4, code_edit_declined_large=4, code_edit_undone_large=2))
    numbers = view_of(db, "coding")["numbers"]
    assert "30 edit attempts; 6 failed to apply. Of the edits decided on: 14 applied, 6 declined; 3 applied ones taken back." in numbers["details"][1]
    assert "Small edits: 10 applied, 2 declined, 1 taken back. Larger edits: 4 applied, 4 declined, 2 taken back." in numbers["details"][1]
    assert "9 test or snippet runs" in numbers["details"][0]
    said = " ".join(numbers["said"])
    assert "About 20% of my attempts to propose an edit this week failed outright" in said
    assert "about 70% were applied and 30% declined; about 20% of the applied ones were taken back" in said
    assert "Small edits stuck (applied and not taken back) about 75% of the time; larger ones about 25%" in said


def test_the_numbers_are_only_said_when_there_are_enough_of_them():
    db = Db()
    counters(db, **edit_week(code_edit_calls=4, code_edit_errors=2, code_edit_applied=3, code_edit_declined=1, code_edit_applied_small=3, code_edit_declined_small=1))
    said = " ".join(view_of(db, "coding")["numbers"]["said"])
    assert "attempts to propose an edit" not in said and "you decided on" not in said and "Small edits stuck" not in said


def test_the_chat_answer_does_not_call_an_unproven_goal_on_track():
    db = Db()
    counters(db, **coding_week(followed=50, redo=0, turns=63, capped=10, edited=23, tested=2))
    only_coding = {**snap_for(db), "goals": [v for v in snap_for(db)["goals"] if v["id"] == "coding"]}
    block = sr.build_block(only_coding, "goals")
    assert "I can't say yet that it is going well" in block and "none of the 50 follow-up messages" in block and "only 10% of my proposed edits were tested" in block
    assert "OVERALL: a share looks high but the numbers behind it cannot back it up. You must not say it is going well" in block
    assert "My target is at least" not in block.split("Coding work that holds up")[1]
    snap = snap_for(db)
    assert sr.goals_reply_issue("My coding work is on track and hitting its target.", snap) is not None


def test_the_goal_was_revised_so_nothing_about_it_needs_approval_and_the_counters_are_registered():
    coding = next(g for g in __import__("backend.components.sonic_goals", fromlist=["GOALS"]).GOALS if g["id"] == "coding")
    assert coding["revision"] == 2 and coding["lever"] is None and coding["unproven_when"] == {"silent_after": 30, "min_edits": 10, "min_tested_share": 0.5}
    assert "not proof that it was right" in coding["statement"]
    for key in ("code_edit_calls", "code_edit_errors", "code_test_runs", "code_edit_applied", "code_edit_declined", "code_edit_undone", "code_edit_applied_small", "code_edit_undone_large"):
        assert key in self_counters.KEYS, key


# ---- pushback is heard in how a message felt, and rework is counted --------------------------------------------------------------------------------

def test_the_headline_subtracts_pushback_so_a_frustrated_follow_up_counts_even_when_no_phrase_matched():
    db = Db()
    counters(db, **{"2026_10_20": {"code_followed": 50, "code_redo": 2, "code_pushback": 12, "code_frustrated": 11, "code_turns": 40, "code_edited": 20, "code_verified": 15, "turns_total": 100}})
    view = view_of(db, "coding")
    assert view["numbers"]["passed"] == 12 and view["numbers"]["taken"] == 38 and view["numbers"]["rate"] == 0.76 and view["state"] == goals.DRIFTING
    said = " ".join(view["numbers"]["said"])
    assert "About 25% of the messages that followed my coding work this week read as pushback, and about 20% as frustrated." in said
    assert "12 read as pushback (2 in words that said it did not work, 11 as frustrated)" in view["numbers"]["details"][0]


def test_weeks_from_before_the_feeling_signal_fall_back_to_the_phrase_list_alone():
    db = Db()
    counters(db, **coding_week(followed=50, redo=5, turns=40, capped=4, edited=20, tested=15))
    numbers = view_of(db, "coding")["numbers"]
    assert numbers["passed"] == 5 and numbers["taken"] == 45


def test_a_week_with_frustrated_follow_ups_is_no_longer_called_silent():
    db = Db()
    counters(db, **{"2026_10_20": {"code_followed": 50, "code_redo": 0, "code_pushback": 3, "code_frustrated": 3, "code_turns": 40, "code_edited": 20, "code_verified": 15, "turns_total": 100}})
    view = view_of(db, "coding")
    assert view["state"] == goals.ON_TRACK and view["numbers"]["unproven"] == []
    silent = Db()
    counters(silent, **coding_week(followed=50, redo=0, turns=40, capped=4, edited=20, tested=15))
    reasons = view_of(silent, "coding")["numbers"]["unproven"]
    assert view_of(silent, "coding")["state"] == goals.UNPROVEN and "none of the 50 follow-up messages this week read as pushback, in words or in how they felt" in reasons[0]


def test_rework_is_said_once_there_are_enough_proposals():
    db = Db()
    counters(db, **{"2026_10_20": {"code_followed": 50, "code_redo": 5, "code_turns": 40, "code_edited": 20, "code_verified": 15, "code_proposals": 40, "code_proposal_rework": 10, "turns_total": 100}})
    numbers = view_of(db, "coding")["numbers"]
    assert "About 25% of the edits I proposed this week touched a file I had changed for you in the last 45 minutes" in " ".join(numbers["said"])
    assert "40 proposals reached a card; 10 touched a file applied in the last 45 minutes." in numbers["details"][0]
    few = Db()
    counters(few, **{"2026_10_20": {"code_followed": 50, "code_redo": 5, "code_turns": 40, "code_edited": 20, "code_verified": 15, "code_proposals": 4, "code_proposal_rework": 4, "turns_total": 100}})
    assert "touched a file I had changed" not in " ".join(view_of(few, "coding")["numbers"]["said"])
