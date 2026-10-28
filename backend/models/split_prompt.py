"""A prompt string that remembers where its stable parts end, so a model that caches can be told where to cache.

The old tool loop (backend/services/react_loop.py) sends ONE string every step: the fixed instructions, then this request's context, then the attempts so far, which grow. A cache can only reuse a
whole content block, so one big block never matched the step before it: that loop's calls on Claude Haiku were cached 1.1% of the time (2026-10-28, 14 days of usage), against 72% for the loop that sends a
conversation of separate messages. Split into three blocks (the fixed instructions, which are the same for every request and every person; this request's context, which is the same for every step of it; and
the attempts), the first two are read back at a tenth of the price on every later step and on the next request.

`SplitPrompt` IS a string (the whole prompt, exactly as before), so every model that does not know about it just uses it as one. `cuts` are the offsets where a stable part ends."""
from typing import Optional, Sequence, Tuple


class SplitPrompt(str):
    cuts: Tuple[int, ...]

    def __new__(cls, text: str, cuts: Sequence[int] = ()) -> "SplitPrompt":
        prompt = super().__new__(cls, text)
        prompt.cuts = tuple(int(c) for c in cuts if 0 < int(c) < len(text))
        return prompt

    def blocks(self) -> list:
        """The prompt as content blocks: each stable part marked as a cache breakpoint, then the changing tail. Empty parts are dropped (a model rejects an empty block)."""
        text, edges = str(self), [0, *sorted(set(self.cuts)), len(self)]
        pieces = [text[a:b] for a, b in zip(edges, edges[1:])]
        blocks = []
        for index, piece in enumerate(pieces):
            if not piece.strip():
                continue
            block = {"type": "text", "text": piece}
            if index < len(pieces) - 1:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks


def split_template(template: str, markers: Sequence[str], hold: Optional[Tuple[str, str]] = None, **fields: object) -> str:
    """`template.format(**fields)`, as a SplitPrompt cut just before each marker when every marker occurs exactly once and in order; otherwise the plain formatted string. The template is cut BEFORE it is filled
    in, so text a person wrote (in a question or the history) can never move a cut: only the template's own markers count.

    `hold=("question", base)` adds one more cut, just after `base` inside the field `question` (which must occur once in the template and start with `base`): the loop adds notices to the question at some
    steps ("2 steps left", a nudge), and those must fall after the cut, in the changing part, so the request itself stays in the cached part."""
    spots = [template.find(m) for m in markers]
    if not markers or any(s < 0 or template.count(m) != 1 for s, m in zip(spots, markers)) or spots != sorted(spots):
        return template.format(**fields)
    edges = [0, *spots, len(template)]
    parts = [template[a:b].format(**fields) for a, b in zip(edges, edges[1:])]
    cuts, total = [], 0
    for part in parts[:-1]:
        total += len(part)
        cuts.append(total)
    if hold is not None:
        name, base = hold
        token = "{" + name + "}"
        value = str(fields.get(name, ""))
        spot = template.find(token)
        if template.count(token) == 1 and base and value.startswith(base) and spot > spots[-1]:
            middle = len(template[:spot].format(**fields)) + len(base)
            if middle > cuts[-1]:
                cuts.append(middle)
    return SplitPrompt("".join(parts), cuts)


def cut_offsets(prompt: object) -> Optional[Tuple[int, ...]]:
    return getattr(prompt, "cuts", None)
