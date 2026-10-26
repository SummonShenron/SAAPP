"""Where a tool turn's prompt goes: sizes only, so the cost of a turn can be traced to the part of the prompt that causes it.

A tool turn is several model calls, and every call re-sends everything before it: the instructions, the tool list, what the person's memory and preferences added, the conversation, the
folder note, the request, and each tool result so far. So the cost of a turn is not the size of its prompt but that size times the number of calls, and a part that looks small can be the
biggest line on the bill. Nobody had measured the parts (the one number on file is a deep turn that sent about 195,000 tokens in ten calls). This counts them, in characters, per tool turn.

Counts only: the sizes of the parts and totals, in the aggregate counters (backend/utils/self_counters.py), shown on Sonic Health under the Turns tab. No text, no person.
"""
import json
import logging
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger("SASS Logger")

PARTS = ("instructions", "tools", "preferences", "architecture", "memory", "history", "leanings", "environment", "request")
COUNT_KEYS = ("native_runs", "native_calls", "native_chars_sent", "native_chars_results") + tuple(f"native_chars_{part}" for part in PARTS)
CHARS_PER_TOKEN = 3.8        # a rough figure for English prose and code, used only to show an estimate next to the character counts


def _size(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(part.get("text", "")) if isinstance(part, dict) else len(str(part)) for part in content)
    return len(str(content or ""))


class PromptMeter:
    """Collects the sizes for one run of the tool loop."""

    def __init__(self) -> None:
        self.parts: Dict[str, int] = {}
        self.calls = 0
        self.sent = 0
        self._tools_chars: Dict[int, int] = {}

    def part(self, name: str, text: Any) -> None:
        if name in PARTS:
            self.parts[name] = _size(text)

    def tools(self, declarations: Iterable[Dict[str, Any]]) -> None:
        try:
            self.parts["tools"] = len(json.dumps(list(declarations), default=str))
        except (TypeError, ValueError):
            self.parts["tools"] = 0

    def note_call(self, messages: Iterable[Any], declarations: Optional[Iterable[Dict[str, Any]]] = None) -> None:
        """One model call: everything sent to it. `declarations` is the tool list for that call (a call with none, the forced final answer, sends none)."""
        try:
            self.calls += 1
            sent = sum(_size(getattr(message, "content", "")) for message in messages)
            declared = list(declarations or [])
            if declared:
                key = id(declarations)
                if key not in self._tools_chars:
                    self._tools_chars[key] = len(json.dumps(declared, default=str))
                sent += self._tools_chars[key]
            self.sent += sent
        except Exception:
            logger.debug("[prompt meter] could not size a call.", exc_info=True)

    def counts(self, results_chars: int = 0) -> Dict[str, int]:
        out = {"native_runs": 1, "native_calls": self.calls, "native_chars_sent": self.sent, "native_chars_results": max(int(results_chars), 0)}
        for part in PARTS:
            out[f"native_chars_{part}"] = int(self.parts.get(part, 0))
        return out


def averages(totals: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Per-run averages from the summed counters, or None when no tool turn was measured. `sent_tokens` is an estimate. Pure."""
    runs = int(totals.get("native_runs", 0) or 0)
    if runs <= 0:
        return None
    avg = lambda key: round(int(totals.get(key, 0) or 0) / runs)
    return {
        "runs": runs, "calls": round(int(totals.get("native_calls", 0) or 0) / runs, 1), "sent_chars": avg("native_chars_sent"),
        "sent_tokens": round(avg("native_chars_sent") / CHARS_PER_TOKEN), "results_chars": avg("native_chars_results"),
        "parts": {part: avg(f"native_chars_{part}") for part in PARTS},
    }
