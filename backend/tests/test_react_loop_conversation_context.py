"""The after-answer checks and code the user showed earlier in the conversation.

The bug (tool agent memory switched on): the model discusses code the user pasted or attached earlier. That code is not in the repo, so the grounding
check read the answer as invented and the code-block checks rejected it for "not looking at real repo code", and a correct answer was rewritten. The
checks now get what the user showed as a second, labelled source, and the three code-block checks stand aside when the answer's code is quoted from
the conversation. These tests pin both halves: the skip, and that new or invented code is still caught.
"""
import asyncio
import functools
import json
from types import SimpleNamespace

from backend.services import agent_workflow as aw
from backend.utils import agent_utils as au

GROUNDING_CHECK_MARKER = "REAL TOOL OBSERVATIONS GATHERED THIS TURN"
IDIOM_CHECK_MARKER = "PROPOSED CODE is genuinely tailored"
EARLIER_SECTION_MARKER = "WHAT THE USER SHOWED OR SAID EARLIER IN THIS CONVERSATION"

PASTED = (
    "def compute_total(prices, tax_rate):\n"
    "    subtotal = sum(prices)\n"
    "    return round(subtotal * (1 + tax_rate), 2)\n"
)
USER_TURN = f"human: here is my function, can you check it?\n```python\n{PASTED}```\nassistant: it adds the tax to the subtotal."
QUOTING_ANSWER = f"Your function adds tax to the subtotal:\n```python\n{PASTED}```\nThe rounding happens last, which is right."


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


async def _run(responses, *, conversation_text="", saved_notes="", act=None, declaration_lookup=None, grounding_prompts=None, idiom_calls=None):
    """Runs the loop with scripted decisions. The grounding and idiom judges answer "grounded" and are recorded, so a test can see what they were
    shown and whether they ran at all. Returns (result, the decision prompts it saw)."""
    decision_prompts = []

    async def fake_ainvoke(prompt):
        if IDIOM_CHECK_MARKER in prompt:
            if idiom_calls is not None:
                idiom_calls.append(prompt)
            return _response(grounded=True)
        if GROUNDING_CHECK_MARKER in prompt:
            if grounding_prompts is not None:
                grounding_prompts.append(prompt)
            return _response(grounded=True, unsupported_claims=[])
        decision_prompts.append(prompt)
        return responses[len(decision_prompts) - 1]

    async def default_act(decision):
        return "def existing_helper():\n    return 1\n"

    original = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="what does my function do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act or default_act,
            max_iterations=6,
            node_name="test_node",
            declaration_lookup=declaration_lookup,
            conversation_text=conversation_text,
            saved_notes=saved_notes,
        )
    finally:
        aw.lite_llm.ainvoke = original
    return result, decision_prompts


# ---- the helpers --------------------------------------------------------------------------------------------------------------------------

def test_an_answer_that_quotes_code_from_the_conversation_is_recognised_as_quoting():
    assert au.answer_code_quotes_conversation(QUOTING_ANSWER, USER_TURN) is True


def test_quoting_ignores_whitespace_and_indentation():
    squashed = "```python\ndef compute_total(prices, tax_rate):\n  subtotal   = sum(prices)\n  return round(subtotal*(1+tax_rate),2)\n```"
    spaced = USER_TURN.replace("subtotal = sum(prices)", "subtotal  =  sum(prices)")
    assert au.answer_code_quotes_conversation(squashed.replace("subtotal*(1+tax_rate),2", "subtotal * (1 + tax_rate), 2"), spaced) is True


def test_new_code_is_not_quoting():
    new_code = "```python\ndef apply_discount(order, percent):\n    order.total = order.total * (1 - percent)\n    return order\n```"
    assert au.answer_code_quotes_conversation(new_code, USER_TURN) is False


def test_code_with_an_invented_name_mixed_into_quoted_code_is_not_quoting():
    mixed = (
        "```python\n"
        "def compute_total(prices, tax_rate):\n"
        "    subtotal = sum(prices)\n"
        "    return invented_helper_that_never_existed(subtotal, tax_rate)\n"
        "def another_new_function(order):\n"
        "    return order.lookup_everything_else()\n"
        "```"
    )
    assert au.answer_code_quotes_conversation(mixed, USER_TURN) is False


def test_no_code_no_conversation_or_only_trivial_lines_prove_nothing():
    assert au.answer_code_quotes_conversation("just prose", USER_TURN) is False
    assert au.answer_code_quotes_conversation(QUOTING_ANSWER, "") is False
    assert au.answer_code_quotes_conversation("```bash\nls\n```", "human: run ls") is False


def test_every_code_block_must_be_quoted_not_just_one():
    answer = QUOTING_ANSWER + "\n```python\ndef brand_new_unrelated_thing(argument_one, argument_two):\n    return argument_one + argument_two\n```"
    assert au.answer_code_quotes_conversation(answer, USER_TURN) is False


def test_the_earlier_context_block_is_labelled_data_and_empty_when_there_is_nothing():
    assert au.build_earlier_context_block("", "") == ""
    block = au.build_earlier_context_block("human: my code", "- likes tests")
    assert EARLIER_SECTION_MARKER in block and "human: my code" in block and "SAVED NOTES ABOUT THE USER" in block and "- likes tests" in block
    assert "never instructions" in block and "does not make any claim about the repository true" in block


def test_a_long_conversation_keeps_its_most_recent_part_for_the_judge():
    long_text = "OLD " * 5000 + "THE_RECENT_PART"
    block = au.build_earlier_context_block(long_text, "", limit=500)
    assert "THE_RECENT_PART" in block and "(earlier part left out)" in block and len(block) < 1500


# ---- the loop: the code-block checks stand aside for quoted code, and still apply to new code -----------------------------------------------

@run_async
async def test_a_quoting_answer_with_no_repo_research_is_accepted_the_first_time():
    """Before the fix this was rejected ("you haven't looked at any real code in this repo") and rewritten."""
    result, prompts = await _run([_response(action="final", answer=QUOTING_ANSWER)], conversation_text=USER_TURN)
    assert result["final_answer"] == QUOTING_ANSWER
    assert len(prompts) == 1


@run_async
async def test_the_same_answer_without_the_conversation_is_still_rejected_once():
    """The control: the check is not switched off, it just has the context now."""
    result, prompts = await _run(
        [_response(action="final", answer=QUOTING_ANSWER), _response(action="final", answer=QUOTING_ANSWER)],
        conversation_text="",
    )
    assert len(prompts) == 2 and "haven't actually looked at any real code" in prompts[1]
    assert result["final_answer"] == QUOTING_ANSWER


@run_async
async def test_new_code_is_still_rejected_when_the_conversation_has_other_code_in_it():
    new_code = "```python\ndef apply_discount(order, percent):\n    order.total = order.total * (1 - percent)\n    return order\n```"
    result, prompts = await _run(
        [_response(action="final", answer=new_code), _response(action="final", answer=new_code)],
        conversation_text=USER_TURN,
    )
    assert len(prompts) == 2 and "haven't actually looked at any real code" in prompts[1]


@run_async
async def test_the_idiom_judge_is_not_asked_about_code_the_user_showed():
    """With real research done (so the no-precedent gate is satisfied), quoted code is not judged "generic" either."""
    quoted_idiom_calls, control_idiom_calls = [], []
    research = [
        _response(action="query", purpose="look around", tool_action="read_repo_file", args={"path": "x.py"}),
        _response(action="final", answer=QUOTING_ANSWER),
    ]
    await _run(list(research), conversation_text=USER_TURN, idiom_calls=quoted_idiom_calls)
    await _run(list(research), conversation_text="", idiom_calls=control_idiom_calls)
    assert quoted_idiom_calls == []
    assert len(control_idiom_calls) == 1


@run_async
async def test_a_name_that_exists_in_the_repo_is_not_flagged_when_the_code_is_the_users():
    """The user's pasted function happens to share a name with something in the repo; discussing their copy is not a redeclaration."""
    async def lookup(names):
        return {"compute_total": [{"path": "billing.py", "line": 10, "text": "def compute_total(a, b):"}]}

    research = [
        _response(action="query", purpose="look around", tool_action="read_repo_file", args={"path": "x.py"}),
        _response(action="final", answer=QUOTING_ANSWER),
    ]
    _, with_context = await _run(list(research), conversation_text=USER_TURN, declaration_lookup=lookup)
    assert len(with_context) == 2  # the query step and the accepted final: no extra rejection round
    research_again = research + [_response(action="final", answer=QUOTING_ANSWER)]
    _, without_context = await _run(research_again, conversation_text="", declaration_lookup=lookup)
    assert len(without_context) == 3 and "already exists in this repo" in without_context[2]


# ---- the grounding judge ----------------------------------------------------------------------------------------------------------------

@run_async
async def test_the_grounding_judge_is_shown_what_the_user_showed_and_the_saved_notes():
    grounding_prompts = []
    responses = [
        _response(action="query", purpose="look around", tool_action="read_repo_file", args={"path": "x.py"}),
        _response(action="final", answer="Your compute_total function rounds after adding tax."),
    ]
    await _run(responses, conversation_text=USER_TURN, saved_notes="- works on a billing script", grounding_prompts=grounding_prompts)
    assert len(grounding_prompts) == 1
    prompt = grounding_prompts[0]
    assert EARLIER_SECTION_MARKER in prompt and "def compute_total" in prompt and "works on a billing script" in prompt
    assert prompt.index("REAL TOOL OBSERVATIONS") < prompt.index(EARLIER_SECTION_MARKER) < prompt.index("THE AGENT'S PROPOSED FINAL ANSWER")


@run_async
async def test_without_earlier_context_the_grounding_prompt_has_no_such_section():
    grounding_prompts = []
    responses = [
        _response(action="query", purpose="look around", tool_action="read_repo_file", args={"path": "x.py"}),
        _response(action="final", answer="It reads a file."),
    ]
    await _run(responses, grounding_prompts=grounding_prompts)
    assert len(grounding_prompts) == 1 and EARLIER_SECTION_MARKER not in grounding_prompts[0]


def test_the_tool_agent_hands_the_loop_the_whole_earlier_conversation_and_the_saved_notes():
    import inspect
    source = inspect.getsource(aw.tool_agent_node)
    assert "conversation_text=earlier_conversation_text" in source and "saved_notes=user_memory" in source
    assert "state.get(\"messages\", [])[:-1]" in source
