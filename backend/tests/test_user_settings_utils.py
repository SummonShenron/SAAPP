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
