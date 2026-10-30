"""How full the model's context window was on a turn, for the session column's context ring (local/src/components/SessionColumn.tsx).

WHAT IT MEASURES. A turn can make several model calls; the biggest request any of them sent (its input tokens, as the provider counted them: backend/utils/llm_usage.py) against the
window of the model that answered it. That is the number that says whether a long coding turn is nearing the limit. It is a COUNT: no text of any message, file or tool result is
involved, nothing is stored (it is sent to the page once, at the end of the turn) and nothing is logged.

THE WINDOWS ARE APPROXIMATE. They are by model family (the provider's published figure for it), not read from the provider, so a new model with a different window is not known until
it is added here; an unknown model shows no ring rather than a wrong one. CONTEXT_WINDOW_TOKENS overrides every model with one figure.
"""
import os
from typing import Any, Dict, Optional

FAMILY_WINDOWS = (("gemini", 1_048_576), ("claude", 200_000))


def window_for(model: Optional[str]) -> Optional[int]:
    """The context window, in tokens, of `model`; None when the model is not one this knows."""
    override = os.getenv("CONTEXT_WINDOW_TOKENS", "").strip()
    if override.isdigit() and int(override) > 0:
        return int(override)
    name = str(model or "").lower()
    for family, window in FAMILY_WINDOWS:
        if family in name:
            return window
    return None


def view(peak_input: Any, model: Optional[str]) -> Optional[Dict[str, Any]]:
    """The context ring's numbers for a turn: {"used", "window", "fraction"}, or None when no call reported its input or the model's window is not known. The fraction is capped at 1."""
    try:
        used = int(peak_input)
    except (TypeError, ValueError):
        return None
    window = window_for(model)
    if used <= 0 or window is None:
        return None
    return {"used": used, "window": window, "fraction": round(min(1.0, used / window), 4)}
