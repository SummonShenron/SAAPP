"""The tool agent must not retract what Sonic said earlier from its own records (2026-10-23).

In a CSS conversation the tool agent ended its answer with "I need to clear something up from earlier: I told you I had background tasks running today like a security audit and a
health check, but I didn't actually check any real tool or system. Disregard those times and findings entirely." Those earlier replies were grounded in Sonic's job record (the
routines block, backend/utils/routines.py), which the tool agent never sees, and the tool agent's own rule says nothing is true unless a tool verified it this turn. So it judged
a true statement against evidence it could not have and took it back, unasked, in the middle of an unrelated answer.
"""
from backend.components.constraints import TOOL_AGENT_PROMPT
from backend.services import native_loop


def test_the_tool_agent_prompt_leaves_earlier_replies_alone_unless_asked():
    for phrase in ("EARLIER REPLIES", "their own records", "Never retract, correct or comment on", "unless the person asks about it"):
        assert phrase in TOOL_AGENT_PROMPT, phrase


def test_the_native_prompt_says_the_same():
    assert "Never retract, correct or comment on" in native_loop.SYSTEM_PROMPT and "their own records" in native_loop.SYSTEM_PROMPT
