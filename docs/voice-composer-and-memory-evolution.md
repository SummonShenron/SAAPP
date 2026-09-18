# Voice Composer & Memory Evolution

Session notes covering two related changes: unifying how Sonic Assistant generates
its final response text, and activating dormant fields in the long-term memory
system so it can reinforce, decay, and synthesize patterns from what it knows about
a user.

---

## 1. Background: what was wrong

Before this session, response generation was scattered across **7 independent
prompt templates** — `CONVERSATIONAL_PROMPT`, `WEB_SEARCH_PROMPT`,
`CODE_INTERPRETER_PROMPT`, `GITHUB_FORMAT_PROMPT`, `PR_FORMAT_PROMPT`, plus a
`FORMATTER_PROMPT` used by `formatter_node` itself. Each one independently
invented its own voice, its own disclaimers, its own way of talking to the user —
none of them were named "Sonic Assistant" or shared a consistent personality.

On top of that, `formatter_node` — a node that runs on *nearly every request* — was
making a real, blocking LLM call to `FORMATTER_PROMPT`, and writing the result to
`state["formatted_output"]`. A repo-wide grep turned up exactly **2 references** to
that field: the write site and the declaration. Nothing downstream ever read it.
It was a fully wasted generation call on almost every turn.

## 2. The fix: one persona, one composer, nodes stay silent

### `SONIC_ASSISTANT_PERSONA`

A single persona constant now defines who's speaking, everywhere:

```python
SONIC_ASSISTANT_PERSONA = """
You are Sonic Assistant — not a generic support bot, but a personal AI agent for this
specific user. You remember them: their facts, preferences, ongoing projects, and the
patterns in what they care about, carried across every conversation you have with them.
...
"""
```

This was rewritten mid-session on direct feedback: the first draft read like a
support bot; the ask was to make it feel like "a personal agent for every user
since it now has the memory capability to act like one."

### `GROUNDING_BLOCKS`

The old templates weren't *only* persona — they also encoded real behavioral
contracts (refusal strings, citation formats, when to say "I don't know"). Those
were extracted, kept word-for-word where it mattered, and keyed by source type:

```python
GROUNDING_BLOCKS = {
    "kb_strict": KB_STRICT_GROUNDING,
    "kb_open": KB_OPEN_GROUNDING,
    "web": WEB_GROUNDING,
    "tool_output": TOOL_OUTPUT_GROUNDING,
    "conversational": CONVERSATIONAL_GROUNDING,
}
```

### `build_voice_prompt()`

One function assembles persona + grounding + data + memory insight + history into
the final prompt, replacing the old per-branch template selection:

```python
def build_voice_prompt(grounding_block, data, history, question,
                        affiliate_override="", insight=""):
    sections = [SONIC_ASSISTANT_PERSONA]
    if affiliate_override:
        sections.append(affiliate_override)
    sections.append(grounding_block)
    sections.append(f"\nDATA:\n{data}\n" if data else "\nDATA:\n(none for this turn)\n")
    if insight:
        sections.append(f"\nWHAT YOU REMEMBER / JUST DID:\n{insight}\n")
    sections.append(f"\nCONVERSATION HISTORY:\n{history}\n\nCURRENT USER INPUT:\n{question}\n\nASSISTANT RESPONSE:\n")
    sections.append(FOLLOW_UP_CONSTRAINT)
    return "\n".join(sections)
```

### `formatter_node` stopped generating anything

Its LLM call was deleted outright. It now does pure bookkeeping — mapping
`relevance_grade` to a `source_type` and packaging whatever the upstream node
already produced into a normalized `voice_payload`:

```python
state["voice_payload"] = {
    "source_type": source_type,       # "kb_strict" | "kb_open" | "web" | "tool_output" | "conversational"
    "data": state.get("content_to_format"),
    "insight": insight_answer,
    "relevance_grade": relevance_grade,
}
```

`app.py`'s streaming handler reads `voice_payload` and calls `build_voice_prompt()`
once, right before the actual `response_llm.astream(prompt)` call — collapsing a
7-branch if/elif chain into a handful of lines.

### Follow-up-up question regression, fixed same session

Because `FOLLOW_UP_CONSTRAINT` was now universally applied (previously only some
paths had it), and its original wording was a mandatory "CRITICAL: always end with
a follow-up question," the assistant started tacking questions onto farewells and
goodnights. Fixed by rewriting the constraint to be judgment-based:

> "Only when a natural, genuinely useful follow-up question would add real value...
> Do NOT include this tag for farewells, sign-offs, simple acknowledgments... When
> in doubt, leave it out."

### Anti-fabrication guardrail

Live-testing the new "personal agent who remembers you" persona against a
genuinely empty history produced a hallucinated callback ("are you feeling ready
to tackle that project we discussed") to a conversation that never happened. The
persona was working *too* well. `CONVERSATIONAL_GROUNDING` got an explicit
"NEVER FABRICATE FAMILIARITY" clause, then was re-verified to still use *real*
memory content naturally when it was actually provided.

---

## 3. Memory Evolution: confidence reinforcement, decay, and patterns

The persona work made "I remember you" an explicit promise. The memory system's
`UserFact.confidence` field, however, was dormant — every save path set it, nothing
ever read it back.

### Reinforcement instead of overwrite

`save_user_fact` already judges every incoming fact against existing ones as
`"duplicate"`, `"supersede"`, or `"distinct"`. Previously a `"duplicate"` judgment
just overwrote the stored fact with a fresh default confidence. Now:

```python
if relationship_action == "duplicate":
    existing.confidence = min(1.0, existing.confidence + FACT_REINFORCEMENT_INCREMENT)
else:  # "supersede" — genuinely changed, reset to a fresh baseline
    existing.confidence = confidence
```

Re-observing the same fact independently across conversations now measurably
increases trust in it. A real contradiction resets it instead of accumulating.

### Read-time-only decay

```python
def _effective_confidence(fact, now):
    if fact.category == "identity":
        return fact.confidence  # foundational, not time-sensitive
    days_since_update = (now - datetime.fromisoformat(fact.updated_at)).total_seconds() / 86400
    return fact.confidence * (0.5 ** (days_since_update / FACT_CONFIDENCE_HALF_LIFE_DAYS))
```

Nothing is ever mutated in storage — this is purely a ranking-time lens, applied
in `fetch_relevant_user_facts` so a heavily-decayed fact no longer outranks a
fresher or more-reinforced one at similar semantic relevance.

### Category expansion

`VALID_CATEGORIES` grew from `preference | identity | setting | trait` to also
include `career | project | goal | relationship`, plus a new `pattern` category
reserved exclusively for synthesized observations (atomic-fact extraction is
deliberately barred from self-labeling as `pattern`).

### Pattern extraction

A new periodic pass looks across a user's *entire* accumulated fact list (not raw
chat) to find things that only emerge in aggregate:

```python
PATTERN_EXTRACTION_PROMPT = """
... Only include a pattern that is genuinely supported by at least 3 of the facts
above pointing in the same direction — do not invent a pattern from a single fact...
"""
```

`extract_user_patterns()` saves each result via the *existing* `save_user_fact`
pipeline with `category="pattern", source="pattern"` — meaning patterns get
deduplication for free from infrastructure that already existed, rather than a new
dedup mechanism. It's triggered from the pre-existing Tier-2 compaction checkpoint
(`maybe_trigger_meta_compaction`), reusing the "enough has accumulated" cadence
instead of inventing new scheduling.

Live-verified: 8 saved facts about agent-systems work produced —

> "Focuses on the architectural reliability and structural governance of
> autonomous agent systems, prioritizing robust error handling, conditional logic,
> and performance evaluation."

— a genuine synthesis, not a restatement of any single fact, which then correctly
surfaced via `fetch_relevant_user_facts` on a related question.

---

## 4. Also fixed this session

- **KB images not rendering**: root cause was a one-character frontend bug —
  `AttachmentImage` fetched `` `${BASE_URL}api/attachments/...` `` (missing `/`),
  throwing before any request left the browser. Backend SSE emission was correct
  the entire time. Fixed in `Chat.tsx`.
- **Model saying "I can't render images" while images rendered fine above the
  text**: the prompt had no idea `kb_images` would accompany the response. Fixed
  by appending an explicit `IMAGE_RENDERING_NOTE` whenever images are attached.
- **`fetch_session_docs` `KeyError: 'filename'`**: pre-existing bug, unrelated to
  the rest of this work — `retrieve_from_session()` returns a nested
  `{"metadata": {"filename": ...}}` shape, not a flat one.
- **"show me a picture of X" misclassified as pure conversational**: the reasoner
  thought it was being asked to *generate* an image. Added a disambiguation rule
  to `REASONER_PROMPT`.

---

## 5. Test coverage

New/extended test files, all under `backend/tests/`:

- `test_voice_composer.py` — persona/grounding isolation, refusal strings never
  leak across source types.
- `test_formatter_node.py` — includes `test_formatter_node_never_calls_an_llm`,
  a regression guard for the exact bug this refactor fixed.
- `test_secure_chat_prompt_build.py` — regression guard for `app.py`'s collapsed
  prompt-assembly glue.
- `test_memory_utils.py` (+10 tests) — reinforcement, decay half-life math,
  decayed-vs-fresh ranking, category round-trips.
- `test_pattern_extraction.py` — skip-when-insufficient-facts, save-each-pattern,
  malformed-LLM-response fallback, and a real (non-mocked) integration test
  proving pattern dedup works via existing `save_user_fact` infrastructure.

Full suite at last run: **148 passed, 0 failed**
(`pytest backend/tests/ local_function_app/tests/`).
