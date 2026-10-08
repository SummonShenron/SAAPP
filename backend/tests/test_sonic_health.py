import pathlib
import re
from datetime import datetime, timedelta, timezone

from backend.utils import admin_utils as au
from backend.utils import self_observations as so
from backend.utils import sonic_health as sh

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
APP_SOURCE = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------------ who counts as an admin ------------------

def test_only_a_directory_entry_with_the_admin_group_is_an_admin():
    directory = {
        "u_admin": {"groups": ["Affiliate_A", "Global_Admins"]},
        "u_plain": {"groups": ["Affiliate_A"]},
        "u_odd": {"groups": "Global_Admins"},       # a string, not a list: not an admin
        "u_none": {},
    }
    assert au.is_global_admin(directory, "u_admin")
    for who in ("u_plain", "u_odd", "u_none", "someone_else", None, ""):
        assert not au.is_global_admin(directory, who), who
    assert not au.is_global_admin({}, "u_admin") and not au.is_global_admin(None, "u_admin")


# ------------------------------------------------------------------------ observations as the page shows them ------

def doc_with(approved=False, proposed=True, status="pending", confirmed_days_ago=1, retired=False):
    d = {"key": "emotional_fit", "status": status, "applies_to": ["emotional"],
         "last_confirmed_at": (NOW - timedelta(days=confirmed_days_ago)).isoformat()}
    if approved:
        d.update(approved_text="In about 6.0% of recent replies something.", approved_evidence={"rate": 0.06}, approved_at=NOW.isoformat(),
                 approved_by="admin_1", status="approved")
    if proposed:
        d.update(proposed_text="In about 9.0% of recent replies something.", proposed_evidence={"rate": 0.09, "previous_rate": 0.06})
    if retired:
        d["status"] = "retired"
    return d


def test_a_new_proposal_describes_as_pending_with_no_approved_wording():
    v = so.describe_observation(doc_with(proposed=True), NOW)
    assert v["state"] == "pending" and v["approved_text"] is None and v["proposed_text"] and v["expires_at"] is None


def test_an_approved_observation_is_live_with_an_expiry_and_who_approved_it():
    v = so.describe_observation(doc_with(approved=True, proposed=False), NOW)
    assert v["state"] == "live" and v["approved_by"] == "admin_1"
    assert v["expires_at"] == (NOW - timedelta(days=1) + timedelta(days=so.EXPIRY_DAYS)).isoformat()


def test_a_live_observation_can_have_a_changed_proposal_waiting():
    v = so.describe_observation(doc_with(approved=True, proposed=True), NOW)
    assert v["state"] == "live" and v["approved_text"] and v["proposed_text"]


def test_an_unconfirmed_approved_observation_reads_as_expired_and_a_retired_one_as_retired():
    assert so.describe_observation(doc_with(approved=True, proposed=False, confirmed_days_ago=so.EXPIRY_DAYS + 5), NOW)["state"] == "expired"
    assert so.describe_observation(doc_with(approved=True, proposed=False, retired=True), NOW)["state"] == "retired"


def test_the_description_carries_nothing_beyond_the_fields_the_page_uses():
    assert set(so.describe_observation(doc_with(approved=True), NOW)) == {
        "key", "state", "applies_to", "approved_text", "approved_evidence", "approved_at", "approved_by",
        "proposed_text", "proposed_evidence", "last_confirmed_at", "expires_at",
    }


# ------------------------------------------------------------------------ approval records who -------------------

def proposal_doc():
    p = so.Proposal("emotional_fit", "In about 6.0% of recent replies something.", {"rate": 0.06, "previous_rate": None, "denominator": 100}, ["emotional"])
    return so.merge_proposal(None, p, NOW)


def test_approval_and_retirement_record_who_did_it():
    approved = so.approve_doc(proposal_doc(), NOW, "admin_1")
    assert approved["approved_by"] == "admin_1"
    assert so.retire_doc(approved, NOW, "admin_2")["retired_by"] == "admin_2"
    assert so.approve_doc(proposal_doc(), NOW)["approved_by"] is None  # the CLI has no signed-in admin


class _Col:
    def __init__(self, docs=()):
        self.docs = {d["key"]: dict(d) for d in docs}

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.docs.values()]

    def find_one(self, query, projection=None):
        d = self.docs.get(query["key"])
        return dict(d) if d else None

    def replace_one(self, query, doc, upsert=False):
        self.docs[query["key"]] = dict(doc)


class _DB:
    def __init__(self, observations=(), counters=(), events=()):
        self.cols = {so.COLLECTION: _Col(observations), "sonic_counters": _Feed(counters), "safety_events": _Feed(events)}

    def __getitem__(self, name):
        return self.cols[name]


class _Feed:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query=None, projection=None):
        return _Cursor(self.docs)


class _Cursor(list):
    def limit(self, n):
        return self


def test_the_database_approve_and_retire_pass_the_admin_through():
    db = _DB([proposal_doc()])
    assert so.approve(db, "emotional_fit", NOW, "admin_1")
    assert db[so.COLLECTION].docs["emotional_fit"]["approved_by"] == "admin_1"
    assert so.retire(db, "emotional_fit", NOW, "admin_2")
    assert db[so.COLLECTION].docs["emotional_fit"]["retired_by"] == "admin_2"
    assert not so.approve(db, "nope", NOW, "admin_1") and not so.retire(db, "nope", NOW, "admin_1")


def test_list_observations_returns_views_in_priority_order():
    a = {**proposal_doc(), "key": "work_mix"}
    b = {**proposal_doc(), "key": "emotional_fit"}
    views = so.list_observations(_DB([a, b]), NOW)
    assert [v["key"] for v in views] == ["emotional_fit", "work_mix"]


# ------------------------------------------------------------------------ the combined payload --------------------

def test_the_health_payload_has_safety_counters_and_observations_for_the_period():
    counters = [{"day": (NOW - timedelta(days=1)).strftime("%Y-%m-%d"), "counts": {"turns_total": 40, "reply_fit_checked": 30}}]
    events = [{"username": "u1", "level": "acute", "source": "language", "at": NOW - timedelta(hours=2)}]
    out = sh.build_sonic_health(_DB([proposal_doc()], counters, events), 7, NOW)
    assert out["days"] == 7 and out["generated_at"] == NOW.isoformat()
    assert out["counters"]["totals"]["turns_total"] == 40
    assert out["safety"]["risk_raised"] == 1 and out["safety"]["people_with_risk_raised"] == 1
    assert [o["key"] for o in out["observations"]] == ["emotional_fit"]


def test_the_health_payload_never_contains_a_person_or_a_message():
    events = [{"username": "alice_the_user", "level": "acute", "source": "language", "at": NOW - timedelta(hours=2)}]
    out = sh.build_sonic_health(_DB([], [], events), 7, NOW)
    assert "alice_the_user" not in str(out)


def test_an_empty_system_gives_an_empty_but_complete_payload():
    out = sh.build_sonic_health(_DB(), 30, NOW)
    assert out["observations"] == [] and out["counters"]["totals"] == {} and out["safety"]["risk_raised"] == 0


# ------------------------------------------------------------------------ the endpoints ---------------------------

def test_every_admin_route_is_guarded_by_the_admin_dependency():
    # the signature runs to the "):" that ends it (it contains parentheses of its own, like Query(7, ge=1))
    routes = re.findall(r'@app\.(?:get|post|put|delete|patch)\("(/api/admin/[^"]*)"\)\s*\nasync def (\w+)\((.*?)\)\s*:\n', APP_SOURCE, re.DOTALL)
    assert len(routes) >= 5, routes
    for path, name, params in routes:
        assert "Depends(require_global_admin)" in params, f"{path} ({name}) is not admin-guarded"


def test_the_admin_routes_the_page_needs_exist_with_the_right_methods():
    for method, path in (
        ("get", "/api/admin/sonic-health"), ("post", "/api/admin/observations/reflect"),
        ("post", "/api/admin/observations/{key}/approve"), ("post", "/api/admin/observations/{key}/retire"),
        ("get", "/api/admin/safety-stats"),
    ):
        assert f'@app.{method}("{path}")' in APP_SOURCE, (method, path)


def test_the_dependency_checks_the_directory_and_returns_403():
    assert "is_global_admin(load_directory(), current_user.get(\"sub\"))" in APP_SOURCE
    assert 'raise HTTPException(status_code=403, detail="Admins only.")' in APP_SOURCE


def test_approval_records_the_signed_in_admin_and_runs_off_the_event_loop():
    assert "approve_observation_doc, db, key, None, admin.get(\"sub\")" in APP_SOURCE
    assert "retire_observation_doc, db, key, None, admin.get(\"sub\")" in APP_SOURCE
    assert "asyncio.to_thread(build_sonic_health" in APP_SOURCE


def test_the_reflect_request_is_bounded():
    assert "days: int = Field(30, ge=7, le=180)" in APP_SOURCE
