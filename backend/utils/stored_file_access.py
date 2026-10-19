"""Who may read a file the app stored (GridFS): one rule, used by both endpoints that serve stored files.

Until now the document download endpoint found a file by NAME and streamed it to anyone signed in, and the image endpoint served any id, whichever library the file belonged to. A name such
as "week-2026-09-28.json" is easy to guess, and the guest sandbox is a signed-in identity, so a private library was only as private as its file names. Retrieval was always scoped (an answer
is only drawn from the libraries a person can read); serving the file itself was not.

The rule, from what each stored file carries in its metadata:

- A photo someone attached in a chat (and a card or picture made from it): `kind == "chat_attachment"`, readable by its owner (`username`) and nobody else.
- A library file (a PDF, a JSON file, a picture in the library, a picture embedded in a PDF): its `affiliate` is the library it was uploaded to. Readable by anyone who can READ that
  library (it is one of their groups, see get_accessible_affiliates), by anyone who manages it (the "<library> Ingesters" role, global admins included), nobody else.
- A file that says neither: only a global admin (old uploads from before files named their library). Never a guess.

Whoever fails the rule gets the same "not found" as for a file that does not exist, so a name or id cannot be used to learn what is stored.
"""
from typing import Any, Callable, Dict, Iterable, Optional


def stored_file_visible_to(
    file_doc: Optional[Dict[str, Any]],
    username: Optional[str],
    readable_affiliates: Iterable[str],
    can_manage: Callable[[str], bool] = lambda affiliate: False,
    is_admin: bool = False,
) -> bool:
    """Whether `username` may be served this stored file. `readable_affiliates` are the libraries they can read; `can_manage(affiliate)` says whether they run one."""
    meta = (file_doc or {}).get("metadata") or {}
    if meta.get("kind") == "chat_attachment":
        return bool(username) and meta.get("username") == username
    affiliate = meta.get("affiliate")
    if not affiliate:
        return bool(is_admin)
    return str(affiliate) in set(readable_affiliates or ()) or bool(can_manage(str(affiliate)))


def _access_of(username: Optional[str], directory: Dict[str, Any]):
    from backend.utils.admin_utils import is_global_admin
    from backend.utils.isolation_kb_utils import get_accessible_affiliates

    entry = (directory or {}).get(username) or {}
    well_formed = isinstance(entry.get("groups"), (list, tuple, set))           # a missing, null or text `groups` is no access, not an error
    groups = entry["groups"] if well_formed else []
    admin = is_global_admin(directory, username)
    readable = get_accessible_affiliates(username, directory)["accessible_affiliates"] if well_formed else []
    return readable, (lambda affiliate: admin or f"{affiliate} Ingesters" in groups), admin


def can_read_stored_file(file_doc: Optional[Dict[str, Any]], username: Optional[str], directory: Dict[str, Any]) -> bool:
    """stored_file_visible_to with what the directory says about this person (their libraries, the libraries they manage, whether they are a global admin)."""
    readable, can_manage, admin = _access_of(username, directory)
    return stored_file_visible_to(file_doc, username, readable, can_manage, admin)


def first_readable(candidates: Iterable[Dict[str, Any]], username: Optional[str], directory: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The first of `candidates` (newest first) this person may read, or None. The same file name can exist in several libraries, so a lookup by name must pick among the
    ones they are allowed to see, never simply the first match."""
    readable, can_manage, admin = _access_of(username, directory)
    for doc in candidates:
        if stored_file_visible_to(doc, username, readable, can_manage, admin):
            return doc
    return None
