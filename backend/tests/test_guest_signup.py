"""The sign-up button counts for the shared guest (backend/utils/guest_signup.py; docs/guest-mode-plan.md step 4)."""
import re
from pathlib import Path

import pytest

from backend.utils import guest_limits, guest_nudge, guest_signup as gs, self_counters

APP = Path("app.py").read_text(encoding="utf-8")
CHAT = Path("local/src/pages/Chat.tsx").read_text(encoding="utf-8")
ALLOWANCE = Path("local/src/components/GuestAllowance.tsx").read_text(encoding="utf-8")
API = Path("local/src/api.ts").read_text(encoding="utf-8")
GUEST = guest_limits.PORTFOLIO_GUEST


# ---- the names and the buckets ----------------------------------------------------------------------------------------------------------------------------------------------

def test_the_button_names_and_buckets_are_the_counter_groups_and_use_the_nudge_feature_words():
    assert self_counters._GROUPS["guest_signup"] == gs.SOURCES
    assert self_counters._GROUPS["guest_signup_after"] == gs.BUCKETS
    assert gs.FEATURES == guest_nudge.FEATURES                           # the same four words the page's account cards and the nudge use
    for source in gs.SOURCES:
        assert f"guest_signup_{source}" in self_counters.KEYS
    for bucket in gs.BUCKETS:
        assert f"guest_signup_after_{bucket}" in self_counters.KEYS


def test_how_many_turns_a_visitor_had_used_falls_in_one_bucket_and_junk_reads_as_none():
    assert [gs.bucket_of(n) for n in (0, 1, 2, 3, 5, 6, 8, 40)] == ["none", "one_or_two", "one_or_two", "three_to_five", "three_to_five", "six_plus", "six_plus", "six_plus"]
    for junk in (None, "x", -3, [], {}):
        assert gs.bucket_of(junk) == "none"


# ---- counting ---------------------------------------------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def recorded(monkeypatch):
    seen = []
    monkeypatch.setattr(gs, "record_turn_counts", lambda counts, *a, **k: seen.append(dict(counts)))
    monkeypatch.setattr(guest_limits, "allowance", lambda db, visitor, address, now=None: {"used": 4})
    return seen


def test_a_press_by_the_shared_guest_counts_the_button_and_how_many_turns_they_had_used(recorded):
    assert gs.count_click(object(), GUEST, "nudge_fix", "visitor", "1.2.3.4") == "nudge_fix"
    assert recorded == [{"guest_signup_nudge_fix": 1, "guest_signup_after_three_to_five": 1}]            # nothing else: no message, no name, no address, no id


def test_only_the_shared_guest_and_only_a_known_button_is_counted(recorded):
    for who in ("jack@example.com", "guest_bty", "guest", "", None):
        assert gs.count_click(object(), who, "banner", "v", "a") is None, who
    for source in ("", "nudge_", "card_unknown", "banner; drop table", "BANNER", None, 5, ["banner"]):
        assert gs.count_click(object(), GUEST, source, "v", "a") is None, source
    assert recorded == []


def test_every_known_button_is_counted_under_its_own_key(recorded):
    for source in gs.SOURCES:
        assert gs.count_click(object(), GUEST, source, "v", "a") == source
    assert [list(c)[0] for c in recorded] == [f"guest_signup_{s}" for s in gs.SOURCES]


def test_a_failure_in_counting_never_reaches_the_page(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the database is down")

    monkeypatch.setattr(guest_limits, "allowance", lambda *a, **k: {"used": 1})
    monkeypatch.setattr(gs, "record_turn_counts", boom)
    assert gs.count_click(object(), GUEST, "banner", "v", "a") is None
    monkeypatch.setattr(guest_limits, "allowance", boom)
    assert gs.count_click(object(), GUEST, "banner", "v", "a") is None


def test_the_endpoint_counts_off_the_event_loop_and_always_answers_the_same():
    start = APP.index('@app.post("/api/guest/signup-click")')
    block = APP[start: start + 1200]
    assert "Depends(get_current_user)" in block and "asyncio.to_thread" in block and "guest_signup.count_click" in block
    assert 'return {"ok": True}' in block
    assert "class SignupClick(BaseModel):\n    source: str" in APP


# ---- the page: every sign-up button goes through the one counted path ---------------------------------------------------------------------------------------------------

def test_every_guest_sign_up_button_counts_itself_with_a_name_the_server_knows():
    sources = re.findall(r"guestSignUp\('([a-z_]+)'\)", CHAT + ALLOWANCE)
    assert sorted(set(sources)) == ["banner", "demo_diff", "limit"]                 # the three fixed buttons
    templates = re.findall(r"guestSignUp\(`([a-z_]+)\$\{[^}]+\}`\)", CHAT)
    assert sorted(templates) == ["card_", "nudge_"]                                  # the two that carry an account feature
    for source in set(sources):
        assert source in gs.SOURCES
    for prefix in templates:
        assert all(f"{prefix}{feature}" in gs.SOURCES for feature in gs.FEATURES), prefix
    assert "api.logout()" not in ALLOWANCE and "onSignUp={() => { void api.logout(); }}" not in CHAT          # none of them signs up without counting
    assert len(re.findall(r"guestSignUp\(", CHAT + ALLOWANCE)) == 5                  # the five places that say "Create a free account" or "Sign up to apply"


def test_the_page_counts_with_keepalive_waits_only_a_moment_and_then_signs_up_whatever_happened():
    start = API.index("export async function guestSignUp(")
    body = API[start: API.index("export async function getBudget", start)]
    assert "keepalive: true" in body and "/api/guest/signup-click" in body
    assert "Promise.race([sent, new Promise(resolve => setTimeout(resolve, 800))])" in body            # a slow count never holds a sign-up back
    assert body.index("catch {") < body.index("await api.logout();")                                   # signing up runs after a failed count too
    assert "guestSignUp,\n" in API
