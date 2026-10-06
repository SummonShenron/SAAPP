import copy

from backend.utils import isolation_kb_utils as kb


class _FakeDirectory:
    """A directory collection that understands the two update operators the provisioning uses."""

    def __init__(self, docs):
        self.docs = docs
        self.writes = 0

    def find(self, _filter):
        return [copy.deepcopy(d) for d in self.docs]

    def update_one(self, flt, update):
        self.writes += 1
        doc = next(d for d in self.docs if d["_id"] == flt["_id"])
        doc.update(update.get("$set", {}))
        for field, spec in update.get("$addToSet", {}).items():
            for value in spec["$each"]:
                if value not in doc.setdefault(field, []):
                    doc[field].append(value)


class _FakeDB:
    def __init__(self, docs):
        self.directory = _FakeDirectory(docs)

    def __getitem__(self, name):
        return getattr(self, name)


def _user(i, username, clerk_id="uses-username", **extra):
    doc = {"_id": i, "username": username, "email": f"{username}@example.com", "groups": ["Affiliate_A", "Global_Admins"]}
    if clerk_id:
        doc["clerk_id"] = clerk_id if clerk_id != "uses-username" else f"user_{username}"
    doc.update(extra)
    return doc


# ---------------------------------------------------------------------------
# personal_kb_update
# ---------------------------------------------------------------------------

def test_an_existing_user_gets_the_same_personal_kb_a_new_user_would():
    doc = _user(1, "alice")
    change = kb.personal_kb_update(doc)
    kb_id = kb.make_personal_kb_id("user_alice")
    assert change["filter"] == {"_id": 1}
    record = change["update"]["$set"]["personal_kb"]
    assert record["id"] == kb_id and record["display_name"] == "alice's Knowledge Base" and record["created_at"]
    assert change["update"]["$addToSet"]["groups"]["$each"] == [kb_id, f"{kb_id} Ingesters"]


def test_the_display_name_falls_back_to_the_email_then_the_clerk_id():
    no_username = {"_id": 1, "clerk_id": "c1", "email": "sam@example.com", "groups": []}
    assert kb.personal_kb_update(no_username)["update"]["$set"]["personal_kb"]["display_name"] == "sam's Knowledge Base"
    bare = {"_id": 2, "clerk_id": "user_xyz", "groups": []}
    assert kb.personal_kb_update(bare)["update"]["$set"]["personal_kb"]["display_name"] == "user_xyz's Knowledge Base"


def test_two_users_with_the_same_username_get_different_knowledge_bases():
    a = kb.personal_kb_update(_user(1, "sam", clerk_id="user_a"))["update"]["$set"]["personal_kb"]["id"]
    b = kb.personal_kb_update(_user(2, "sam", clerk_id="user_b"))["update"]["$set"]["personal_kb"]["id"]
    assert a != b


def test_a_user_with_no_clerk_id_is_not_provisioned_because_the_id_would_collide():
    assert kb.personal_kb_update(_user(1, "legacy", clerk_id=None)) is None


def test_shared_guest_identities_never_get_a_personal_kb():
    assert kb.personal_kb_update(_user(1, "guest", clerk_id="guest-recruiter@example.com")) is None
    assert kb.personal_kb_update(_user(2, "guest_bty", clerk_id="guest_bty")) is None
    assert kb.personal_kb_update(_user(3, "someone", clerk_id="guest_bty")) is None


def test_a_user_who_already_has_one_is_left_alone():
    doc = _user(1, "alice")
    kb_id = kb.make_personal_kb_id("user_alice")
    doc["personal_kb"] = {"id": kb_id, "display_name": "x"}
    doc["groups"] += [kb_id, f"{kb_id} Ingesters"]
    assert kb.personal_kb_update(doc) is None


def test_a_half_finished_provisioning_only_gets_its_missing_groups_back():
    doc = _user(1, "alice")
    kb_id = kb.make_personal_kb_id("user_alice")
    doc["personal_kb"] = {"id": kb_id, "display_name": "x", "created_at": "keep-me"}
    doc["groups"].append(kb_id)  # the write group is missing
    change = kb.personal_kb_update(doc)
    assert "$set" not in change["update"]  # never rewrites the record (and its created_at)
    assert change["update"]["$addToSet"]["groups"]["$each"] == [f"{kb_id} Ingesters"]


# ---------------------------------------------------------------------------
# provision_personal_kbs
# ---------------------------------------------------------------------------

def _directory():
    return _FakeDB([
        _user(1, "alice"),
        _user(2, "bob", groups=["Affiliate_B", "Affiliate_B Ingesters", "PAAPP_Admins"]),
        _user(3, "legacy", clerk_id=None),
        _user(4, "guest", clerk_id="guest-recruiter@example.com"),
    ])


def test_a_dry_run_reports_what_would_change_and_writes_nothing():
    db = _directory()
    before = copy.deepcopy(db.directory.docs)
    report = kb.provision_personal_kbs(db)
    assert report["provisioned"] == ["alice", "bob"]
    assert dict(report["skipped"]).keys() == {"legacy", "guest"}
    assert db.directory.docs == before and db.directory.writes == 0


def test_applying_it_gives_every_eligible_user_a_kb_and_keeps_their_other_groups():
    db = _directory()
    report = kb.provision_personal_kbs(db, apply=True)
    assert report["provisioned"] == ["alice", "bob"]
    alice, bob, legacy, guest = db.directory.docs
    for doc, clerk in ((alice, "user_alice"), (bob, "user_bob")):
        kb_id = kb.make_personal_kb_id(clerk)
        assert doc["personal_kb"]["id"] == kb_id
        assert kb_id in doc["groups"] and f"{kb_id} Ingesters" in doc["groups"]
    # nothing else was granted or taken away
    assert alice["groups"][:2] == ["Affiliate_A", "Global_Admins"]
    assert bob["groups"][:3] == ["Affiliate_B", "Affiliate_B Ingesters", "PAAPP_Admins"]
    assert "personal_kb" not in legacy and "personal_kb" not in guest
    assert legacy["groups"] == ["Affiliate_A", "Global_Admins"]


def test_running_it_twice_changes_nothing_the_second_time():
    db = _directory()
    kb.provision_personal_kbs(db, apply=True)
    snapshot = copy.deepcopy(db.directory.docs)
    writes = db.directory.writes
    report = kb.provision_personal_kbs(db, apply=True)
    assert report["provisioned"] == [] and report["already"] == ["alice", "bob"]
    assert db.directory.docs == snapshot and db.directory.writes == writes


def test_the_new_kb_shows_up_in_the_scope_picker_and_grants_ingest_rights():
    db = _directory()
    kb.provision_personal_kbs(db, apply=True)
    alice = db.directory.docs[0]
    kb_id = kb.make_personal_kb_id("user_alice")
    directory = {alice["clerk_id"]: alice}
    assert kb_id in kb.get_accessible_affiliates(alice["clerk_id"], directory)["accessible_affiliates"]
    assert f"{kb_id} Ingesters" not in kb.get_accessible_affiliates(alice["clerk_id"], directory)["accessible_affiliates"]
    assert kb.resolve_kb_display_names(directory)[kb_id] == "alice's Knowledge Base"
    assert f"{kb_id} Ingesters" in alice["groups"]
