"""Is a model provider rejecting Sonic's API key? Noticed, logged, counted and shown to an admin (2026-11-20).

WHY. On 2026-11-17 the Anthropic key on Render expired (the Console now gives keys an expiry date). Every Claude call then answered 401, and the app did the kind thing: it logged a WARNING and fell back to Gemini. Nobody
saw it. For three days all the work ran on the fallback, which cost about four times as much for the same tokens (Haiku $0.26 against Gemini $1.09 on 2026-11-18), and the first sign was a crash when the fallback met a tool
schema it could not take. A rejected key is not a blip, it is a setting that is wrong until someone fixes it, so it should be loud.

WHAT.
- `note_failure(provider, error)` is called where a provider wrapper gives up on a call. If the error is an AUTHENTICATION one (HTTP 401 or 403) the provider is marked rejected, an ERROR is logged (at most once every
  30 minutes while it lasts, and never the error's own text: an API error can quote what was sent), and a counter is kept for Sonic Health (at most once a minute).
- `note_ok(provider)` is called on every call that succeeds. The first success after a rejection clears it, logs that the key is accepted again, and counts the recovery.
- `alerts()` is what is currently rejected, for the admin banner (backend/utils/budget_guard.py public_view) and the health check (/api/health says "degraded").
State is in memory, per process (the app runs one worker). After a restart it is empty until the next failing call, which for a key that is still bad is the next call.
Nothing here can raise into a reply: every entry point swallows its own errors."""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SASS Logger")

PROVIDERS: Dict[str, Dict[str, str]] = {
    "claude": {
        "label": "Claude (Anthropic)",
        "fix": "Create a new key in the Claude Console (platform.claude.com/settings/keys; Workspace: Default, Expiration: Never) and set ANTHROPIC_API_KEY on Render.",
        "fallback": "Gemini",
    },
    "openai": {
        "label": "OpenAI",
        "fix": "Check the key at platform.openai.com and set OPENAI_API_KEY on Render.",
        "fallback": "Gemini",
    },
}
LOG_EVERY_SECONDS = 30 * 60
COUNT_EVERY_SECONDS = 60
_AUTH_STATUSES = (401, 403)

_lock = threading.Lock()
_rejected: Dict[str, Dict[str, Any]] = {}       # provider -> {"since": datetime, "last": datetime, "failures": int}
_last_logged: Dict[str, float] = {}
_last_counted: Dict[str, float] = {}


def is_auth_failure(error: BaseException) -> bool:
    """Whether an error from a provider is "your key was refused" (HTTP 401 or 403), as opposed to overload, a rate limit, a timeout or a bad request. Pure."""
    try:
        for holder in (error, getattr(error, "response", None)):
            status = getattr(holder, "status_code", None)
            if isinstance(status, int):
                return status in _AUTH_STATUSES
        name = type(error).__name__
        return "Authentication" in name or "PermissionDenied" in name
    except Exception:
        return False


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _record(key: str) -> None:
    """Counts one key for Sonic Health. A database write: done on its own thread, so a slow database can never hold up a reply. Never raises."""
    def write() -> None:
        try:
            from backend.utils import self_counters

            self_counters.record_turn_counts({key: 1})
        except Exception:
            logger.warning("[provider health] could not record a count.")

    try:
        threading.Thread(target=write, daemon=True).start()
    except Exception:
        pass


def _once_per(table: Dict[str, float], key: str, seconds: int, clock: Optional[float] = None) -> bool:
    moment = time.monotonic() if clock is None else clock
    if moment - table.get(key, -1e12) >= seconds:
        table[key] = moment
        return True
    return False


def note_failure(provider: str, error: BaseException, now: Optional[datetime] = None, clock: Optional[float] = None) -> bool:
    """A provider call failed. Returns True when it was an authentication failure (and so is now shown as a rejected key). Never raises."""
    try:
        if provider not in PROVIDERS or not is_auth_failure(error):
            return False
        moment = _now(now)
        with _lock:
            state = _rejected.setdefault(provider, {"since": moment, "last": moment, "failures": 0})
            state["last"] = moment
            state["failures"] += 1
            say = _once_per(_last_logged, provider, LOG_EVERY_SECONDS, clock)
            count = _once_per(_last_counted, provider, COUNT_EVERY_SECONDS, clock)
        if say:
            info = PROVIDERS[provider]
            logger.error("[provider health] %s rejected the API key (HTTP 401/403). Calls are falling back to %s, which can cost several times more. %s", info["label"], info["fallback"], info["fix"])
        if count:
            _record(f"llm_key_rejected_{provider}")
        return True
    except Exception:
        return False


def note_ok(provider: str) -> None:
    """A provider call succeeded: if it was marked rejected, it is accepted again. Cheap on the common path (one dict lookup). Never raises."""
    try:
        if provider not in _rejected:
            return
        with _lock:
            state = _rejected.pop(provider, None)
            _last_logged.pop(provider, None)
        if state is not None:
            logger.info("[provider health] %s accepted the API key again.", PROVIDERS[provider]["label"])
            _record(f"llm_key_recovered_{provider}")
    except Exception:
        pass


def alerts() -> List[Dict[str, Any]]:
    """What is rejected right now, for an admin: [{provider, label, since, last, failures, message}], oldest first. [] when every key is accepted. Never raises."""
    try:
        with _lock:
            current = [(provider, dict(state)) for provider, state in _rejected.items()]
        rows = []
        for provider, state in sorted(current, key=lambda item: item[1]["since"]):
            info = PROVIDERS[provider]
            rows.append({
                "provider": provider,
                "label": info["label"],
                "since": state["since"].isoformat(),
                "last": state["last"].isoformat(),
                "failures": state["failures"],
                "message": f"{info['label']} is rejecting Sonic's API key, so its calls are falling back to {info['fallback']} (which costs more). {info['fix']}",
            })
        return rows
    except Exception:
        return []


def degraded() -> bool:
    """Whether any key is rejected right now. For the health check, which says nothing about which one."""
    return bool(_rejected)


def reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _rejected.clear()
        _last_logged.clear()
        _last_counted.clear()
