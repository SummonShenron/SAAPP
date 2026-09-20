import pytest

from backend.utils import user_settings_utils as usu


@pytest.fixture(autouse=True)
def isolated_settings_dir(tmp_path, monkeypatch):
    """Redirects the JSON fallback store to a temp dir and forces get_db() to None
    so every test exercises the local-file fallback path deterministically."""
    monkeypatch.setattr(usu, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(usu, "get_db", lambda: None)
    yield


def test_default_rag_mode_is_strict():
    assert usu.get_user_rag_mode("jack") == "strict"


def test_set_and_get_rag_mode_round_trip():
    saved = usu.set_user_rag_mode("jack", "open")
    assert saved == "open"
    assert usu.get_user_rag_mode("jack") == "open"


def test_invalid_rag_mode_defaults_to_strict():
    saved = usu.set_user_rag_mode("jack", "not_a_real_mode")
    assert saved == "strict"
    assert usu.get_user_rag_mode("jack") == "strict"


def test_rag_mode_scoped_per_username():
    usu.set_user_rag_mode("jack", "open")
    usu.set_user_rag_mode("alice", "strict")
    assert usu.get_user_rag_mode("jack") == "open"
    assert usu.get_user_rag_mode("alice") == "strict"


def test_locked_user_cannot_enable_open_mode():
    saved = usu.set_user_rag_mode("guest_bty", "open")
    assert saved == "strict"
    assert usu.get_user_rag_mode("guest_bty") == "strict"


def test_locked_user_stays_strict_even_if_open_written_directly_to_store(monkeypatch, tmp_path):
    # Simulate some other code path having written "open" for guest_bty directly.
    import json
    path = usu._get_user_file("guest_bty")
    with open(path, "w") as f:
        json.dump({"rag_mode": "open"}, f)

    assert usu.get_user_rag_mode("guest_bty") == "strict"


def test_default_deep_thinking_is_off():
    assert usu.get_user_deep_thinking_mode("jack") is False


def test_set_and_get_deep_thinking_round_trip():
    saved = usu.set_user_deep_thinking_mode("jack", True)
    assert saved is True
    assert usu.get_user_deep_thinking_mode("jack") is True


def test_deep_thinking_scoped_per_username():
    usu.set_user_deep_thinking_mode("jack", True)
    usu.set_user_deep_thinking_mode("alice", False)
    assert usu.get_user_deep_thinking_mode("jack") is True
    assert usu.get_user_deep_thinking_mode("alice") is False


def test_locked_user_cannot_enable_deep_thinking():
    saved = usu.set_user_deep_thinking_mode("guest_bty", True)
    assert saved is False
    assert usu.get_user_deep_thinking_mode("guest_bty") is False


def test_setting_rag_mode_does_not_clobber_previously_saved_deep_thinking():
    """Regression: the local JSON fallback used to overwrite the whole settings file on every
    save, so setting rag_mode after deep_thinking (or vice versa) would silently erase it."""
    usu.set_user_deep_thinking_mode("jack", True)
    usu.set_user_rag_mode("jack", "open")
    assert usu.get_user_deep_thinking_mode("jack") is True
    assert usu.get_user_rag_mode("jack") == "open"


def test_setting_deep_thinking_does_not_clobber_previously_saved_rag_mode():
    usu.set_user_rag_mode("jack", "open")
    usu.set_user_deep_thinking_mode("jack", True)
    assert usu.get_user_rag_mode("jack") == "open"
    assert usu.get_user_deep_thinking_mode("jack") is True
