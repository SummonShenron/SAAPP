import os

import pytest

from backend.utils import stored_file_access as sfa

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

PRIVATE = "kb_aaaaaaaaaa"          # Jack's personal library
OTHER = "kb_bbbbbbbbbb"            # someone else's personal library
DIRECTORY = {
    "jack": {"groups": [PRIVATE, f"{PRIVATE} Ingesters", "Affiliate_C"]},
    "alice": {"groups": [OTHER, f"{OTHER} Ingesters"]},
    "bob": {"groups": ["Affiliate_C"]},
    "carol": {"groups": ["Affiliate_C", f"{PRIVATE} Ingesters"]},              # runs jack's library without reading it as a group
    "root": {"groups": ["Global_Admins"]},
    "guest-recruiter@example.com": {"groups": ["Affiliate_C"]},
}


def file(filename, affiliate=None, **meta):
    return {"_id": filename, "filename": filename, "metadata": ({"affiliate": affiliate} if affiliate else {}) | meta}


WEEKLY = file("week-2026-09-28.json", PRIVATE)
PORTFOLIO = file("resume.pdf", "Affiliate_C")
PHOTO = file("family.jpg", None, kind="chat_attachment", username="jack")


def can(doc, who, directory=DIRECTORY):
    return sfa.can_read_stored_file(doc, who, directory)


# ---------------------------------------------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------------------------------------------

def test_the_owner_of_a_private_library_can_read_its_files():
    assert can(WEEKLY, "jack")


def test_someone_else_cannot_read_it_even_knowing_the_exact_name():
    """The hole: the download endpoint used to serve any file to any signed-in user who named it."""
    for who in ("alice", "bob", "guest-recruiter@example.com"):
        assert not can(WEEKLY, who), who


def test_a_shared_library_is_readable_by_its_members_only():
    assert can(PORTFOLIO, "bob") and can(PORTFOLIO, "guest-recruiter@example.com") and can(PORTFOLIO, "jack")
    assert not can(PORTFOLIO, "alice")


def test_someone_who_manages_a_library_can_open_its_files():
    assert can(WEEKLY, "carol")


def test_a_global_admin_can_open_anything_that_names_a_library():
    assert can(WEEKLY, "root") and can(PORTFOLIO, "root")


def test_a_chat_photo_is_its_owners_alone_even_for_an_admin():
    assert can(PHOTO, "jack")
    for who in ("alice", "bob", "root", "guest-recruiter@example.com", "carol"):
        assert not can(PHOTO, who), who


def test_a_file_that_names_no_library_is_for_an_admin_only():
    legacy = file("old-upload.pdf")
    assert can(legacy, "root")
    assert not can(legacy, "jack") and not can(legacy, "bob")


def test_nobody_known_means_no_access():
    assert not can(WEEKLY, None) and not can(WEEKLY, "") and not can(WEEKLY, "stranger")
    assert not can(PHOTO, None) and not can(PHOTO, "")


def test_a_missing_or_failed_directory_fails_closed_for_library_files():
    assert not can(WEEKLY, "jack", {}) and not can(PORTFOLIO, "bob", {})
    assert can(PHOTO, "jack", {}), "a chat photo needs no directory: it is the owner's by its own record"


def test_a_malformed_groups_field_is_no_access_not_a_crash():
    assert not can(WEEKLY, "jack", {"jack": {"groups": "kb_aaaaaaaaaa"}})
    assert not can(WEEKLY, "jack", {"jack": {"groups": None}})


def test_a_missing_file_record_is_never_readable():
    assert not can(None, "jack") and not can({}, "jack")


# ---------------------------------------------------------------------------------------------------------------
# Picking among files that share a name
# ---------------------------------------------------------------------------------------------------------------

def test_the_same_name_in_two_libraries_gives_each_person_their_own_copy():
    mine, theirs = file("notes.json", PRIVATE), file("notes.json", OTHER)
    candidates = [theirs, mine]                 # newest first: the other person's was uploaded last
    assert sfa.first_readable(candidates, "jack", DIRECTORY) is mine
    assert sfa.first_readable(candidates, "alice", DIRECTORY) is theirs


def test_a_name_that_only_exists_where_they_cannot_read_is_not_found():
    assert sfa.first_readable([WEEKLY], "bob", DIRECTORY) is None
    assert sfa.first_readable([], "jack", DIRECTORY) is None


def test_the_newest_readable_copy_wins():
    older, newer = file("a.json", PRIVATE), file("a.json", PRIVATE)
    assert sfa.first_readable([newer, older], "jack", DIRECTORY) is newer


# ---------------------------------------------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------------------------------------------

APP = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()


def handler(route):
    start = APP.index(route)
    return APP[start:APP.index("\n@app.", start + 10)]


def test_the_download_endpoint_picks_only_among_files_the_caller_may_read():
    body = handler('@app.get("/api/documents/download/{filename:path}")')
    assert "first_readable(candidates, current_user.get(\"sub\"), load_directory())" in body
    assert 'find_one({\n        "$or"' not in body, "the old lookup returned the first file with the name, whoever it belonged to"


def test_the_image_endpoint_applies_the_same_rule_to_library_pictures_and_chat_photos():
    body = handler('@app.get("/api/attachments/image/{file_id}")')
    assert 'can_read_stored_file(file_doc, current_user.get("sub"), load_directory())' in body


def test_a_refusal_looks_exactly_like_a_missing_file():
    body = handler('@app.get("/api/attachments/image/{file_id}")')
    assert body.count("status_code=404") >= 2 and "403" not in body
    download = handler('@app.get("/api/documents/download/{filename:path}")')
    assert "403" not in download
