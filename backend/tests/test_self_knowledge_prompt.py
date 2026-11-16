"""Sonic knows that SAAPP is the app it runs inside and that "Sonic" in the repo means it.

Seen 2026-11-15: reviewing a diff of its own instructions, Sonic said they were "closest to my instructions" and, a few lines later, that the change "tells Sonic to report a tool's ERROR text", as if Sonic were
someone else. Nothing told it the repo it was reading is its own program: the loop's prompt introduced it as "Sonic Assistant's research agent" and said "What Sonic said earlier" (third person), and the repo's
instructions describe "Sonic is the assistant inside it" like a stranger. A prompt teaches by example, so the wording is first person now, with one rule that says what the repo is."""
import pathlib
import re

from backend.services import native_loop

REPO = pathlib.Path(__file__).resolve().parents[2]
RULES = native_loop.SYSTEM_PROMPT


def test_the_loop_prompt_speaks_to_sonic_as_sonic_not_as_a_separate_research_agent():
    assert RULES.startswith("You are Sonic Assistant, working in tool mode.")
    assert "research agent" not in RULES
    assert not re.search(r"\bSonic (said|says|should|will|can|must|would)\b", RULES)        # no third-person Sonic anywhere in the rules


def test_the_loop_prompt_says_what_the_repo_is_and_how_to_describe_changes_to_it():
    assert 'SAAPP is the app you run inside, and "Sonic" and "Sonic Assistant" mean you wherever you meet them.' in RULES
    assert "constraints.py and sonic_profile.py are your own instructions" in RULES
    assert "describe a change to them as a change to you" in RULES and "this adds a rule for me" in RULES
    assert "never as something done to a separate assistant called Sonic" in RULES


def test_every_file_the_rule_names_exists_so_it_never_points_at_something_that_moved():
    for path in ("backend/components/constraints.py", "backend/components/sonic_profile.py", "backend/services/native_loop.py", "backend/services/agent_workflow.py"):
        assert (REPO / path).is_file(), path
        assert path.rsplit("/", 1)[-1] in RULES, path


def test_the_repo_instructions_were_left_alone_because_the_loop_prompt_alone_fixes_it_and_the_file_has_no_room():
    # Measured 2026-11-15 on real Haiku (n=4 per arm, the real diff): third-person "Sonic" in 4 of 4 answers before; 0 of 4 with the new loop prompt whether or not .sonic/AGENTS.md said "You are Sonic".
    # That file is read only up to project_guidance.MAX_CHARS (a longer one is cut off at its end) and had 13 characters of room, so it is not spent on a line that measured as unnecessary.
    from backend.utils import project_guidance

    sonic = (REPO / ".sonic" / "AGENTS.md").read_text(encoding="utf-8")
    assert project_guidance.fits(sonic)
    shared = (REPO / "AGENTS.md").read_text(encoding="utf-8")        # Claude Code reads this one too: "you are Sonic" would mislead it
    assert "You are Sonic" not in shared
