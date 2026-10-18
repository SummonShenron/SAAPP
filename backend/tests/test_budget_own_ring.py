"""Everyone can see how far along their own usage limit is (backend/utils/budget_guard.py public_view): a percentage of their own limit, never an amount or the shared budget."""
from datetime import datetime, timezone

import pytest

from backend.utils import budget_guard as bg

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
GLOBAL_LIMITS = {"day_usd": 5.0}
OWN_LIMITS = {"my_day_usd": 1.0}


def state(totals, limits):
    return bg.evaluate(totals, limits, NOW)


def test_a_person_with_a_limit_of_their_own_sees_how_far_along_it_is_and_nothing_else():
    view = bg.public_view(state({"day_usd": 4.0, "my_day_usd": 0.54}, {**GLOBAL_LIMITS, **OWN_LIMITS}), False)
    assert view["own_fraction"] == 0.54 and view["own_scope"] == "my_day" and view["own_level"] == bg.OK
    for private in ("fraction", "spent", "limit", "detail", "scope", "unit", "can_extend"):
        assert private not in view                                      # not the shared budget's 80%, and no amounts


def test_the_ring_follows_their_own_limit_even_when_the_shared_budget_is_further_along():
    view = bg.public_view(state({"day_usd": 4.9, "my_day_usd": 0.1}, {**GLOBAL_LIMITS, **OWN_LIMITS}), False)
    assert view["own_fraction"] == 0.1 and view["own_level"] == bg.OK


@pytest.mark.parametrize("used,level", [(0.2, bg.OK), (0.79, bg.OK), (0.8, bg.NOTICE), (0.99, bg.NOTICE), (1.0, bg.THROTTLED), (1.2, bg.THROTTLED), (1.25, bg.BLOCKED)])
def test_the_colour_level_of_their_own_limit_uses_the_same_lines_as_the_guard(used, level):
    assert bg.public_view(state({"my_day_usd": used}, OWN_LIMITS), False)["own_level"] == level == bg.fraction_level(used)


def test_with_a_daily_limit_and_a_five_hour_window_the_one_further_along_is_shown_and_named():
    limits = {"my_day_usd": 1.0, "my_five_usd": 0.5}
    view = bg.public_view(state({"my_day_usd": 0.2, "my_five_usd": 0.4}, limits), False)
    assert view["own_scope"] == "my_five" and view["own_fraction"] == 0.8 and view["own_level"] == bg.NOTICE


def test_with_no_limit_of_their_own_nothing_is_shown_and_nothing_is_when_the_guard_is_off():
    view = bg.public_view(state({"day_usd": 4.0}, GLOBAL_LIMITS), False)
    assert not any(key.startswith("own_") and key != "own_limit" for key in view)
    assert "own_fraction" not in bg.public_view({"level": bg.OFF}, False) and "own_fraction" not in bg.public_view(None, False)


def test_an_admin_still_gets_the_shared_budget_view_and_their_own_number_too():
    view = bg.public_view(state({"day_usd": 4.0, "my_day_usd": 0.5}, {**GLOBAL_LIMITS, **OWN_LIMITS}), True)
    assert view["fraction"] == 0.8 and view["scope"] == "day" and view["own_fraction"] == 0.5
