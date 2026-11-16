"""A tool that did not work must look like it, and the reply must say why in the tool's own words.

Seen on 2026-11-15: a `read_url` step showed in the trace as an ordinary "Read a web page", and the reply then said the page was not opened because the link came from saved notes and that it only opens
addresses in the current message. No code or prompt says that; the tool had returned an error and the reply made a reason up (and promised that pasting the link would help). Two fixes: the failed step's facts
carry the tool's real reason (the page marks the step failed from them), and the reply's stable rules (constraints.ACTION_HONESTY) say what to do with an ERROR result."""
from backend.components import constraints as c
from backend.utils import agent_utils as au

PAGE = "https://secret-site.example/private/report?token=abc123"
BLOCKED = f"ERROR: could not read {PAGE}: the site answered HTTP 403; the browser could not load it either (net::ERR_BLOCKED_BY_RESPONSE)"


def test_a_failed_tool_step_carries_the_tools_reason_without_the_address():
    facts = au.build_tool_facts("read_url", {"url": PAGE}, 1.2, BLOCKED)
    why = next(line for line in facts if line.startswith("Why: "))
    assert "HTTP 403" in why and "ERR_BLOCKED_BY_RESPONSE" in why and "<url>" in why
    assert "secret-site" not in why and "abc123" not in why
    assert not why.startswith("Why: ERROR")                                    # the word ERROR is the result line's job
    assert any(line.startswith("Result: error, ") for line in facts)           # what the page reads to mark the step failed
    assert len(why) <= au.TRACE_FACT_MAX_CHARS if hasattr(au, "TRACE_FACT_MAX_CHARS") else len(why) <= 200


def test_a_tool_that_raised_shows_the_name_of_what_went_wrong():
    facts = au.build_tool_facts("read_url", {"url": PAGE}, 1.2, "ERROR: read_url failed (TimeoutError).")
    assert "Why: read_url failed (TimeoutError)." in facts


def test_a_step_that_worked_or_found_nothing_has_no_reason_line_and_never_shows_what_it_read():
    for result in ("URL: " + PAGE + "\nPRIVATE_PAGE_TEXT " * 40, [], "", None):
        facts = au.build_tool_facts("read_url", {"url": PAGE}, 1.2, result)
        assert not any(line.startswith("Why: ") for line in facts)
        assert not any("PRIVATE_PAGE_TEXT" in line for line in facts)


def test_the_replys_stable_rules_say_what_to_do_with_a_tool_error():
    rules = c.ACTION_HONESTY
    assert "WHEN A TOOL FAILED" in rules and "starts with ERROR" in rules
    assert "Give no other reason" in rules
    assert "a link has to come from their message" in rules and "only open or read" in rules          # the invented reasons, named
    assert "sending the link again, pasting it" in rules                                              # and the invented promise
    assert "could not tell why it failed" in rules                                                    # what to say when DATA gives no reason


def test_the_rule_is_in_every_reply_prompt_and_still_ends_the_stable_part_so_the_cache_split_holds():
    from backend.utils import claude_voice as cv

    prompt = c.build_voice_prompt(c.GROUNDING_BLOCKS["conversational"], "DATA: ERROR: could not read the page", "User: hi", "how come you couldnt?")
    assert "WHEN A TOOL FAILED" in prompt
    system, user = cv.split_prompt(prompt)
    assert system.rstrip().endswith(c.ACTION_HONESTY.strip()) and "WHEN A TOOL FAILED" in system         # in the part that is cached, not the part that changes every turn
    assert "could not read the page" in user and "could not read the page" not in system


def test_the_tool_agents_own_prompt_has_the_same_rule_because_it_writes_the_draft_answer():
    from backend.services import native_loop

    rules = native_loop.SYSTEM_PROMPT
    assert "When a tool returns an error, the error is the real reason" in rules
    assert "Give no other reason" in rules and "a link has to come from the person's message" in rules
    assert "Never promise that sending the link again, pasting it" in rules and "could not tell why it failed" in rules
