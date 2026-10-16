"""The conversation_code_discussed scenario and the harness change behind it (which after-answer check, if any, sent the agent back to rewrite).

The end-to-end tests drive the real tool_agent_node, so they also prove the wiring: the earlier messages of the conversation reach the checks."""
import asyncio
import json
from types import SimpleNamespace

from backend.evals import behavior_eval as be


def run(coro):
    return asyncio.run(coro)


def reply(**payload):
    return SimpleNamespace(content=json.dumps(payload))


class Scripted:
    """A model that gives each scripted response in turn, then repeats the last."""

    def __init__(self, *responses):
        self.responses = list(responses)

    async def __call__(self, prompt):
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


HISTORY = be._conversation(
    ("user", f"Here is a function from a billing script:\n```python\n{be.PASTED_FUNCTION}```\nCan you check it?"),
    ("assistant", "It totals the prices, applies the tax and rounds at the end."),
)
QUOTING = f"The rounding is the last line:\n```python\n    return round(subtotal * (1 + tax_rate), 2)\n```\nIt comes last so the tax is included before rounding.\n```python\n{be.PASTED_FUNCTION}```"
NEW_CODE = "Here is a different approach:\n```python\ndef apply_discount(order, percent):\n    order.total = order.total * (1 - percent)\n    return order\n```\nIt rounds nothing."


# ---- the scorer ---------------------------------------------------------------------------------------------------------------------------

def test_the_scorer_passes_a_quoting_answer_that_was_not_rewritten():
    seen = be.Observation(answer="The round() call is last so the tax is counted first.", rejections=[])
    assert be.score_user_code_discussed_not_rewritten(seen) == (True, "")


def test_the_scorer_fails_when_any_check_sent_the_answer_back_to_be_rewritten():
    seen = be.Observation(answer="The round() call is last.", rejections=["no_precedent", "ungrounded_claim"])
    ok, why = be.score_user_code_discussed_not_rewritten(seen)
    assert not ok and "no_precedent" in why and "ungrounded_claim" in why


def test_the_scorer_fails_an_answer_that_never_addresses_the_question():
    ok, why = be.score_user_code_discussed_not_rewritten(be.Observation(answer="Sure, happy to help.", rejections=[]))
    assert not ok and "rounding" in why


# ---- the scenario is registered the way the bug happens: memory on, an earlier message with code in it ----------------------------------------

def test_the_scenario_has_the_earlier_messages_a_memory_block_and_the_memory_category():
    scenario = next(s for s in be.SCENARIOS if s.id == "conversation_code_discussed")
    assert scenario.category == "memory" and scenario.model == "tool"
    assert "conversation_code_discussed" in {s.id for s in be.SCENARIOS if s.category in be.CATEGORIES}
    assert len(HISTORY) == 2 and "compute_total" in HISTORY[0].content


# ---- end to end through tool_agent_node ---------------------------------------------------------------------------------------------------

def test_an_answer_quoting_the_users_earlier_code_is_not_sent_back_to_rewrite():
    seen = run(be.run_tool_agent(Scripted(reply(action="final", answer=QUOTING)), "Show me the rounding line.", history=HISTORY,
                                 memory=be._memory_block(facts=["Is writing a small billing script."])))
    assert seen.rejections == []
    assert "round" in seen.answer.lower() and be.score_user_code_discussed_not_rewritten(seen) == (True, "")


def test_new_code_in_the_same_conversation_is_still_sent_back_and_the_harness_names_the_check():
    model = Scripted(reply(action="final", answer=NEW_CODE), reply(action="final", answer=NEW_CODE))
    seen = run(be.run_tool_agent(model, "Show me the rounding line.", history=HISTORY))
    assert seen.rejections == ["no_precedent"]
    assert not be.score_user_code_discussed_not_rewritten(seen)[0]


def test_without_the_earlier_messages_the_quoting_answer_is_sent_back_which_is_the_bug_this_fixes():
    model = Scripted(reply(action="final", answer=QUOTING), reply(action="final", answer=QUOTING))
    seen = run(be.run_tool_agent(model, "Show me the rounding line."))
    assert seen.rejections == ["no_precedent"]
