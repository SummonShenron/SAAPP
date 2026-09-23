from backend.utils import isolation_kb_utils as ik


# ---------------------------------------------------------------------------
# get_accessible_affiliates — now derived purely from the caller's own groups,
# no fixed enum, no Global_Admins bypass.
# ---------------------------------------------------------------------------

def test_get_accessible_affiliates_excludes_role_and_ingester_groups():
    directory = {
        "u1": {
            "groups": [
                "Affiliate_A", "Global_Admins", "PAAPP_Admins",
                "Taskboard_Admins", "Affiliate_A Ingesters",
            ]
        }
    }
    result = ik.get_accessible_affiliates("u1", directory)
    assert result == {"accessible_affiliates": ["Affiliate_A"]}


def test_get_accessible_affiliates_includes_personal_kb_id():
    directory = {"u1": {"groups": ["Affiliate_A", "kb_abc123", "kb_abc123 Ingesters"]}}
    result = ik.get_accessible_affiliates("u1", directory)
    assert result == {"accessible_affiliates": ["Affiliate_A", "kb_abc123"]}


def test_get_accessible_affiliates_global_admin_no_longer_bypasses():
    """Regression guard: Global_Admins used to see every one of the 4 hardcoded affiliates
    regardless of its own groups. That bypass is deliberately removed."""
    directory = {"admin": {"groups": ["Global_Admins"]}}
    result = ik.get_accessible_affiliates("admin", directory)
    assert result == {"accessible_affiliates": []}


def test_get_accessible_affiliates_unknown_user_returns_empty():
    result = ik.get_accessible_affiliates("nobody", {})
    assert result == {"accessible_affiliates": []}


# ---------------------------------------------------------------------------
# make_personal_kb_id
# ---------------------------------------------------------------------------

def test_make_personal_kb_id_deterministic():
    assert ik.make_personal_kb_id("sub_123") == ik.make_personal_kb_id("sub_123")


def test_make_personal_kb_id_unique_per_clerk_id():
    assert ik.make_personal_kb_id("sub_123") != ik.make_personal_kb_id("sub_456")


def test_make_personal_kb_id_format():
    kb_id = ik.make_personal_kb_id("sub_123")
    assert kb_id.startswith("kb_")
    assert kb_id != "kb_"


# ---------------------------------------------------------------------------
# resolve_kb_display_names
# ---------------------------------------------------------------------------

def test_resolve_kb_display_names_builds_lookup_across_users():
    directory = {
        "u1": {"personal_kb": {"id": "kb_aaa", "display_name": "Jack's Knowledge Base"}},
        "u2": {"personal_kb": {"id": "kb_bbb", "display_name": "Alice's Knowledge Base"}},
    }
    result = ik.resolve_kb_display_names(directory)
    assert result == {"kb_aaa": "Jack's Knowledge Base", "kb_bbb": "Alice's Knowledge Base"}


def test_resolve_kb_display_names_skips_users_without_personal_kb():
    directory = {
        "u1": {"groups": ["Affiliate_A"]},  # older account, no personal_kb field at all
        "u2": {"personal_kb": {"id": "kb_bbb", "display_name": "Alice's Knowledge Base"}},
    }
    result = ik.resolve_kb_display_names(directory)
    assert result == {"kb_bbb": "Alice's Knowledge Base"}


def test_resolve_kb_display_names_empty_directory():
    assert ik.resolve_kb_display_names({}) == {}


# ---------------------------------------------------------------------------
# verify_user_ingest_access — the cross-tenant negative case this whole
# feature's privacy guarantee depends on.
# ---------------------------------------------------------------------------

def test_verify_user_ingest_access_personal_kb_owner_passes(monkeypatch):
    monkeypatch.setattr(
        ik, "load_directory",
        lambda: {"owner": {"groups": ["kb_xyz", "kb_xyz Ingesters"]}},
    )
    assert ik.verify_user_ingest_access("owner", "kb_xyz") is True


def test_verify_user_ingest_access_other_user_denied(monkeypatch):
    monkeypatch.setattr(
        ik, "load_directory",
        lambda: {
            "owner": {"groups": ["kb_xyz", "kb_xyz Ingesters"]},
            "intruder": {"groups": ["Affiliate_A"]},
        },
    )
    assert ik.verify_user_ingest_access("intruder", "kb_xyz") is False


def test_verify_user_ingest_access_global_admin_still_bypasses(monkeypatch):
    """This bypass is deliberately left in place — only get_accessible_affiliates' visibility
    bypass was removed, not this one."""
    monkeypatch.setattr(ik, "load_directory", lambda: {"admin": {"groups": ["Global_Admins"]}})
    assert ik.verify_user_ingest_access("admin", "kb_xyz") is True
