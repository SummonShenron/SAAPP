# backend/utils/taskboard_utils.py
import json
import os
from fastapi import HTTPException, Header, Query, status
from typing import Optional

# Resolve project root and directory.json next to app.py
# This works even when the backend package is imported from elsewhere.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIRECTORY_JSON_PATH = os.path.join(ROOT_DIR, "directory.json")

def load_directory():
    try:
        with open(DIRECTORY_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Helpful debug message but return empty dict so app doesn't crash
        return {}
    except Exception:
        return {}

def is_taskboard_admin_for_user(username: str) -> bool:
    directory = load_directory()
    entry = directory.get(username)
    if not entry:
        return False
    groups = entry.get("groups", [])
    return "Taskboard_Admins" in groups or "Global_Admins" in groups


def require_taskboard_admin(
    x_user_id: str | None = Header(None, alias="x-user-id"),
    username_q: str | None = Query(None, alias="username")
) -> str:
    """
    FastAPI dependency. Accepts x-user-id header or ?username= query param.
    Returns the validated username or raises 401/403.
    """
    user = x_user_id or username_q
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing username")

    if not is_taskboard_admin_for_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requires Taskboard_Admins membership")

    return user
