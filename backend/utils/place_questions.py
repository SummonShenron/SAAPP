"""Whether a message is about real places or getting somewhere: the one thing that makes Sonic's place tools (backend/services/maps_tools.py) part of a turn.

WHY A GATE. Sonic's tools are described to the model in every call of a turn, and a third party's tools (Google's maps service) would add their descriptions to every turn whether or not anyone asked about a place. So
they are attached only when the message (or the one before it, for "which one is closest?") reads as a question about places, distances or directions. A miss costs a worse answer (it falls back to a web search);
a false hit costs about 500 tokens of tool descriptions. Pure: no model, no network.

Deliberately plain words, no model call. Software words ("progress bar", "bar chart", "sidebar", "pub/sub", a file name, "the repo") keep a coding message from matching."""
import re

_NOUNS = (
    r"bars?|pubs?|taverns?|breweries|brewery|lounges?|clubs?|nightclubs?|restaurants?|cafes?|cafés?|coffee shops?|coffee|diners?|bakery|bakeries|bistros?|eateries|brunch|dinner spots?|food trucks?|"
    r"happy hours?|hotels?|motels?|hostels?|museums?|galleries|gallery|theat(?:er|re)s?|cinemas?|movie theat(?:er|re)s?|parks?|trails?|beaches|gyms?|pharmacy|pharmacies|grocery stores?|supermarkets?|"
    r"gas stations?|charging stations?|bookstores?|barbers?|salons?|spas?|shops?|stores?|markets?|"
)
_PLACE_WORDS = re.compile(rf"\b(?:{_NOUNS}things to do|date night|date ideas?|date spots?|places? to (?:eat|drink|stay|go|see|visit|hang out|grab)|where (?:should|can|could|do) (?:we|i|you) (?:go|eat|grab|drink|stay)|"
                          r"what(?:'s| is) (?:there )?to do|open now|open late|open until|late[- ]night food)\b", re.IGNORECASE)
# Phrases that are about places on their own, with no other word needed.
_STRONG = re.compile(r"\b(?:things to do|what(?:'s| is) (?:there )?to do|date night|date ideas?|date spots?|places? to (?:eat|drink|stay|visit|hang out|grab)|where (?:should|can|could) (?:we|i|you) (?:go|eat|grab|drink|stay))\b", re.IGNORECASE)
_NEAR = re.compile(r"\b(?:near(?:by| me| us| here)?|around here|close to (?:me|us|here)|in my area|in the area|walking distance|within walking|downtown|uptown|midtown|"
                   r"on the (?:west|east|north|south) side|(?:west|east|north|south)(?:east|west)? side)\b", re.IGNORECASE)
_TRAVEL = re.compile(r"\b(?:how far(?: is| are| away)?|how long (?:does it take|would it take|is the (?:drive|walk|ride|commute))|how many (?:minutes|miles|kilometers|km) (?:is|to|from)|drive time|driving time|"
                     r"travel time|walking time|walk time|commute|directions? (?:to|from)|route (?:to|from)|distance (?:to|from|between)|(?:drive|walk|bike|ride|get) (?:to|from) .{1,40} (?:from|to)\b|"
                     r"which (?:one )?(?:is|are) (?:the )?(?:closest|nearest|closer)|closest|nearest|the best way to get)\b", re.IGNORECASE)
_SOFTWARE = re.compile(r"\b(?:progress|status|nav|navigation|search|tool|side|scroll|menu|title|task|address|action|snack)\s*bars?\b|\bbar (?:chart|graph|plot)s?\b|\bpub/?sub\b|\bsidebars?\b|\bnavbars?\b|\bscrollbars?\b|"
                       r"\bclub?\b(?=.*\bclass\b)|[\w./-]+\.(?:py|tsx?|jsx?|css|json|md|html|ya?ml|toml|sh)\b|\b(?:repo|repository|function|component|endpoint|commit|branch|pull request|stack trace|traceback|api|css|jsx|tsx)\b", re.IGNORECASE)


def looks_like_place_question(text: str) -> bool:
    """True when the text is about places (what to visit, eat or drink, where something is) or about getting somewhere (how far, how long, directions). `text` is the message and the one before it."""
    sample = str(text or "")
    if not sample.strip() or _SOFTWARE.search(sample):
        return False
    if _TRAVEL.search(sample):
        return True
    if _STRONG.search(sample):
        return True
    return bool(_PLACE_WORDS.search(sample)) and (bool(_NEAR.search(sample)) or bool(re.search(r"\b(?:recommend|suggest|best|good|nice|find|looking for|any|some|where|which|ideas?|options?|picks?|spots?|date|open (?:now|late|until))\b", sample, re.IGNORECASE)))
