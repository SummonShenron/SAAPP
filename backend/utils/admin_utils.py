from typing import Any, Dict, Optional

GLOBAL_ADMIN_GROUP = "Global_Admins"


def is_global_admin(directory: Dict[str, Any], subject: Optional[str]) -> bool:
    """Whether the signed-in subject is in the Global_Admins group of the user directory. An unknown subject, a missing
    directory entry or a malformed groups field is simply not an admin."""
    entry = (directory or {}).get(subject) or {}
    groups = entry.get("groups")
    return isinstance(groups, (list, tuple, set)) and GLOBAL_ADMIN_GROUP in groups
