"""Did the tool loop stop reading because it got LOST, or because it ran out of steps? (Jack, 2026-11-09: "I mainly want it to find out on the fly if it stopped reading because it got lost or if it simply ran out of steps.")

WHY. A turn that ends at the step limit looks the same whether it was still finding new material on its last step or had been going in circles for six steps. The loop already records HOW a turn ended (`stop_reason`:
the model answered, the limit forced an answer, it repeated itself), but nothing says whether the work was getting anywhere, so a "ran out" turn could not be told from a "lost" one, and nobody could say whether
more steps would have helped. This module is that missing signal, mechanical and free (no model call), kept in memory for the turn: counts and words, never a path, a term or a line of what was read.

PROGRESS. A step "gained ground" when any of its calls brought something new into the turn:
- a read of a file, or of a window of it, not read before (a different window of the same file is new ground: reading further);
- a search, listing or find that returned something other than "no matches" or an error, and named at least one path not seen in any earlier result;
- an applied edit (acting is progress);
- any other tool (web, memory, a check) that returned a non-empty, non-error result unlike one already seen.
No ground: an error, a "you already made this call" note, an empty search, a re-read of the same window, a result naming only places already seen.

STATUS (asked just before a step runs): "progressing" when any of the last STALL_WINDOW completed steps gained ground, "stalled" when none did, "unknown" before MIN_STEPS_FOR_STATUS steps have been taken (too early to say).

VERDICT (how a turn that used tools ended):
- "answered": the model chose to answer (nothing was cut short);
- "ran_out_progress": the limit forced the answer and the last steps were still finding things;
- "ran_out_lost": the limit forced the answer and the last steps found nothing new;
- "looped": it made the same call over and over until the loop stopped it.
A turn that made no tool call has no verdict. Pure: no clock, no I/O."""
import re
from typing import Any, Dict, List, Optional, Set

from backend.utils import reading_waste

STALL_WINDOW = 3
MIN_STEPS_FOR_STATUS = 2
STATUSES = ("progressing", "stalled", "unknown")
VERDICTS = ("answered", "ran_out_progress", "ran_out_lost", "looped")
READ_TOOLS = reading_waste.READ_TOOLS
LOOKUP_TOOLS = frozenset({"search_literal", "search_code", "find_file", "list_repo_tree", "search_library", "list_library_files", "search_memory", "search_my_knowledge"})
EMPTY_PREFIXES = reading_waste.EMPTY_PREFIXES
_PATH_RE = re.compile(r"[\w.\-]+(?:[/\\][\w.\-]+)+|[\w\-]+\.[A-Za-z0-9]{1,6}\b")
_FINGERPRINT_CHARS = 200


def _text(result: Any) -> str:
    return str(result or "").strip()


def _is_error_or_note(text: str) -> bool:
    return text.startswith("ERROR") or text.startswith("NOTE:")


def _window(args: Any) -> tuple:
    arguments = args if isinstance(args, dict) else {}
    return (str(arguments.get("path") or "").replace("\\", "/").strip(), arguments.get("start_line") or 0, arguments.get("line_count") or 0)


class ProgressLog:
    """Follows one loop's tool calls. `note(step, name, args, result)` for each call as it finishes, `status()` before a step runs, `verdict(...)` at the end."""

    def __init__(self) -> None:
        self._windows: Set[tuple] = set()
        self._paths: Set[str] = set()
        self._fingerprints: Set[str] = set()
        self._gained: Dict[int, bool] = {}
        self.calls = 0

    def note(self, step: int, name: str, args: Any, result: Any) -> bool:
        """Records one finished call and returns whether it brought new ground. A step gained ground when any of its calls did."""
        self.calls += 1
        text = _text(result)
        new = self._new_ground(str(name or ""), args, text)
        self._gained[step] = self._gained.get(step, False) or new
        return new

    def _new_ground(self, name: str, args: Any, text: str) -> bool:
        if not text or _is_error_or_note(text):
            return False
        if name in READ_TOOLS:
            window = _window(args)
            fresh = window not in self._windows
            self._windows.add(window)
            if window[0]:
                self._paths.add(window[0])
            return fresh
        if name == "propose_local_edits":
            return " APPLIED:" in text
        if name in LOOKUP_TOOLS:
            if text.lower().startswith(EMPTY_PREFIXES):
                return False
            found = {p.replace("\\", "/") for p in _PATH_RE.findall(text)}
            fresh = bool(found - self._paths)
            self._paths |= found
            return fresh
        fingerprint = " ".join(text.split())[:_FINGERPRINT_CHARS].lower()
        fresh = fingerprint not in self._fingerprints
        self._fingerprints.add(fingerprint)
        return fresh

    def steps_taken(self) -> int:
        return len(self._gained)

    def status(self) -> str:
        """Whether the work is getting anywhere, judged from the steps completed so far (see the module note). Pure."""
        steps = sorted(self._gained)
        if len(steps) < MIN_STEPS_FOR_STATUS:
            return "unknown"
        return "progressing" if any(self._gained[s] for s in steps[-STALL_WINDOW:]) else "stalled"

    def stalled_for(self) -> int:
        """How many of the most recent completed steps, in a row, gained nothing (0 when the latest did)."""
        run = 0
        for step in sorted(self._gained, reverse=True):
            if self._gained[step]:
                break
            run += 1
        return run


def verdict(log: ProgressLog, stop_reason: str, forced: bool) -> str:
    """How a turn that used tools ended, or "" (no tool was called, or an ending that is not about reading, such as the plan's own allowance on a turn that carried a plan out). Pure."""
    if log.calls == 0:
        return ""
    if stop_reason == "repeats":
        return "looped"
    if forced:
        return "ran_out_lost" if log.status() == "stalled" else "ran_out_progress"
    if stop_reason in ("model_answered", "checkpoint_answered", ""):
        return "answered"
    return ""


def explain(kind: str, steps_left: int = 0, budget: int = 0, stalled: int = 0) -> Dict[str, str]:
    """The title and detail the trace shows for a verdict. Words only (numbers are counts the person already sees). Pure."""
    if kind == "answered":
        left = f" with {steps_left} step{'s' if steps_left != 1 else ''} left" if steps_left > 0 else ""
        return {"title": "Finished: chose to answer", "detail": f"It answered on its own{left}; the step limit did not stop it."}
    if kind == "ran_out_progress":
        return {"title": "Stopped: ran out of steps", "detail": f"It used all {budget} steps and was still finding new material, so more steps would probably have helped."}
    if kind == "ran_out_lost":
        recent = f"The last {stalled} step{'s' if stalled != 1 else ''}" if stalled else "Its recent steps"
        return {"title": "Stopped: out of steps and going in circles", "detail": f"{recent} found nothing new (repeats, empty searches or places already read), so more steps would probably not have helped."}
    if kind == "looped":
        return {"title": "Stopped: repeating itself", "detail": "It made the same call again and again until the loop stopped it."}
    return {"title": "", "detail": ""}


def last_step_label(status: str) -> str:
    """The words on the final-step header. Pure."""
    if status == "progressing":
        return "still finding new material"
    if status == "stalled":
        return "the recent steps found nothing new"
    return ""


def counts(kind: str, last_step_status: Optional[str]) -> Dict[str, int]:
    """The counter increments for one turn (self_counters.PROGRESS_KEYS): how it ended, and what the final-step header said if there was one. Pure."""
    out: Dict[str, int] = {}
    if kind in VERDICTS:
        out[f"turn_end_{kind}"] = 1
    if last_step_status in ("progressing", "stalled"):
        out[f"last_step_{last_step_status}"] = 1
    return out
