"""Whether a message asks how the person's OWN work has gone over time ("how has my work progressed since I started at X?", "review my last seven weeks"): the one thing that sends a turn to the
tool agent for their libraries instead of the one-pass retrieval.

WHY A GATE. The one-pass retrieval takes the three passages closest to the question across every library the person may read. "How has my work progressed" is closest to documents ABOUT progress
(a cover letter, a recommendation letter), not to the dated weekly reports that hold the answer, and it has no way to keep to one employer or one library, so an answer about one job came back mixed with
another (2026-11-18). Only the tool agent can list the files, choose the library and read what belongs to the question. It costs several model calls, so it is taken only for this kind of question, in code,
with no model call here (the reasoner has a flag for every other path, and each new flag is paid for on every turn). A miss keeps the old one-pass answer; a false hit costs one tool-agent turn.
Pure: no model, no network. Software words ("progress bar", a file name, "the repo") keep a coding message out."""
import re

# Words about getting better, or about what was done, over a stretch of time.
_GROWTH = r"progress(?:ed|ion)?|grown|growth|grow(?:ing)?|improv(?:ed|ement|ements|ing)|develop(?:ed|ment)|accomplish(?:ed|ments?)?|achiev(?:ed|ements?)|track record|trajectory|performance|evolv(?:ed|ing)|come along|leveled up|levelled up"
# A cue that it is about work or a stretch of time, so "my progress on the bridge game" is not taken for it.
_WORK_OR_TIME = r"work|job|role|career|position|company|team|employer|since|so far|lately|weeks?|months?|years?|over the (?:last|past)|time (?:here|there|at)|(?:i(?:'ve| have)|i) been (?:at|with|working)"
_OWN = r"(?:my|i|i've|i have|i'd|me|we've)"

_ASKS_GROWTH = re.compile(rf"\b{_OWN}\b[^.?!]{{0,60}}\b(?:{_GROWTH})\b|\b(?:{_GROWTH})\b[^.?!]{{0,40}}\b(?:my|mine|i've|i have)\b", re.IGNORECASE)
_HAS_CUE = re.compile(rf"\b(?:{_WORK_OR_TIME})\b", re.IGNORECASE)
# Asking for the whole stretch to be gone back over.
_REVIEW = re.compile(
    r"\b(?:review|recap|look (?:back|over)|go (?:back )?over|walk me through|summari[sz]e|sum up|what (?:have|did) i (?:done|do|accomplish|get done|work on|ship|ship?ped|build|built))\b"
    r"[^.?!]{0,50}\b(?:my|i've|i have|i)\b[^.?!]{0,40}\b(?:weeks?|months?|year|progress|career|track record|time (?:at|there|here|so far)|reports?|history|since)\b"
    r"|\b(?:what|how much) (?:have|did) i (?:done|do|accomplish|get done|work on|ship|shipped|build|built)\b[^.?!]{0,50}\b(?:since|over the (?:last|past)|these (?:last )?\w+ (?:weeks|months))\b",
    re.IGNORECASE,
)
_SOFTWARE = re.compile(
    r"\bprogress\s+(?:bars?|indicators?|rings?|spinners?)\b|[\w./-]+\.(?:py|tsx?|jsx?|css|json|md|html|ya?ml|toml|sh)\b|"
    r"\b(?:repo|repository|function|component|endpoint|commit|branch|pull request|stack trace|traceback|api|css|jsx|tsx|build|deploy|tests?)\b",
    re.IGNORECASE,
)


def asks_about_own_record(text: str) -> bool:
    """True when the text asks how the person's own work or time somewhere has gone (progress, growth, what they got done) over a stretch of time, or asks for such a stretch to be reviewed."""
    sample = str(text or "")
    if not sample.strip() or _SOFTWARE.search(sample):
        return False
    if _REVIEW.search(sample):
        return True
    return bool(_ASKS_GROWTH.search(sample)) and bool(_HAS_CUE.search(sample))
