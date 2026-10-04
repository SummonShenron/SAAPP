import asyncio
import functools
import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw
from backend.services import steering
from backend.utils.agent_utils import format_steering_notes


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


# ---------------------------------------------------------------------------
# The registry: one run per conversation turn, queued messages, bounded and isolated.
# ---------------------------------------------------------------------------

def _open(key="jack::c1"):
    transcript = []
    return steering.open_run(key, transcript), transcript


def test_submit_without_a_running_turn_says_so_so_the_browser_can_send_it_normally():
    assert steering.submit("jack::nothing-running", "change of plan") == "no_active_run"


def test_blank_messages_are_not_queued():
    _open()
    assert steering.submit("jack::c1", "   \n ") == "empty"
    assert steering.drain("jack::c1") == []


def test_queued_messages_are_drained_oldest_first_and_recorded_in_the_transcript_in_order():
    run, transcript = _open()
    assert steering.submit("jack::c1", "first") == "queued"
    assert steering.submit("jack::c1", "second") == "queued"

    assert steering.drain("jack::c1") == ["first", "second"]
    assert [m.content for m in transcript] == ["first", "second"]
    assert all(isinstance(m, HumanMessage) for m in transcript)
    assert steering.drain("jack::c1") == []  # nothing is applied twice
    steering.close_run("jack::c1", run)


def test_once_the_work_phase_is_over_a_steer_is_too_late():
    run, _ = _open()
    steering.stop_accepting("jack::c1")
    assert steering.submit("jack::c1", "too slow") == "too_late"
    steering.close_run("jack::c1", run)


def test_the_queue_is_bounded_and_long_messages_are_cut():
    run, _ = _open()
    for i in range(steering.MAX_PENDING_STEERS):
        assert steering.submit("jack::c1", f"m{i}") == "queued"
    assert steering.submit("jack::c1", "one too many") == "too_many"
    steering.drain("jack::c1")
    steering.submit("jack::c1", "x" * (steering.MAX_STEER_CHARS + 500))
    assert len(steering.drain("jack::c1")[0]) == steering.MAX_STEER_CHARS
    steering.close_run("jack::c1", run)


def test_conversations_and_users_do_not_see_each_others_steers():
    run_a, transcript_a = _open("jack::a")
    run_b, transcript_b = _open("jack::b")
    steering.submit("jack::a", "for a")
    assert steering.drain("jack::b") == []
    assert steering.submit("alice::a", "wrong user") == "no_active_run"
    assert steering.drain("jack::a") == ["for a"]
    assert transcript_b == []
    steering.close_run("jack::a", run_a)
    steering.close_run("jack::b", run_b)


def test_closing_a_run_returns_what_was_never_consumed_and_stops_accepting():
    run, _ = _open()
    steering.submit("jack::c1", "never used")
    assert steering.close_run("jack::c1", run) == ["never used"]
    assert steering.submit("jack::c1", "after") == "no_active_run"


def test_an_older_run_closing_does_not_tear_down_a_newer_one_for_the_same_conversation():
    old, _ = _open()
    new, _ = _open()  # same key: a newer turn replaced it
    steering.close_run("jack::c1", old)
    assert steering.submit("jack::c1", "still routed to the newer turn") == "queued"
    steering.close_run("jack::c1", new)


def test_the_prompt_block_lists_messages_oldest_first_and_says_they_override():
    block = format_steering_notes(["use the staging repo", "skip the tests"])
    assert block.index("1. use the staging repo") < block.index("2. skip the tests")
    assert "follow the steering" in block
    assert "return action=\"final\"" in block
    assert format_steering_notes([]) == ""


# ---------------------------------------------------------------------------
# The ReAct loop: steering is absorbed at step boundaries and shapes later steps.
# ---------------------------------------------------------------------------

def _loop_kwargs(llm, act, **extra):
    return dict(
        question="find the login flow",
        schema="repo=x",
        prompt_template="{question} | {schema} | {attempts}",
        act=act,
        node_name="test_node",
        llm=llm,
        **extra,
    )


class _ScriptedLLM:
    """Returns each scripted response in turn, records every prompt, and can make a steering
    message 'arrive' while a chosen call is in flight."""
    def __init__(self, responses, queue, arrive_during_call=None, message="use the staging repo"):
        self.responses = list(responses)
        self.queue = queue
        self.arrive_during_call = arrive_during_call
        self.message = message
        self.prompts = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        # The grounding check on a final is a separate LLM call; answer it as grounded.
        if "REAL TOOL OBSERVATIONS GATHERED THIS TURN" in prompt:
            return _llm_response(unsupported_claims=[], grounded=True, supported=True, reasons=[])
        call_number = len(self.prompts)
        if self.arrive_during_call == call_number:
            self.queue.append(self.message)
        return self.responses.pop(0)


def _source(queue):
    def take():
        taken = list(queue)
        queue.clear()
        return taken
    return take


@run_async
async def test_a_steer_that_arrives_during_a_decision_discards_it_and_redirects_the_next_step():
    queue, acted, announced = [], [], []

    async def act(decision):
        acted.append(decision.get("tool_action"))
        return "result"

    async def on_applied(messages, step):
        announced.append((messages, step))

    llm = _ScriptedLLM(
        [_llm_response(action="query", purpose="old plan", tool_action="search_code", args={"query": "login"}),
         _llm_response(action="final", answer="redirected answer", show_work=False)],
        queue, arrive_during_call=1,
    )

    result = await aw.run_react_loop(**_loop_kwargs(
        llm, act, max_iterations=5, steering_source=_source(queue), on_steering_applied=on_applied,
    ))

    assert acted == []  # the plan made before the steer was never executed
    assert result["final_answer"] == "redirected answer"
    assert "USER STEERING" not in llm.prompts[0]
    assert "use the staging repo" in llm.prompts[1]
    assert announced == [(["use the staging repo"], 1)]


@run_async
async def test_a_final_reached_while_a_steer_arrived_is_not_accepted():
    queue = []

    async def act(decision):
        return "result"

    llm = _ScriptedLLM(
        [_llm_response(action="final", answer="the stale conclusion", show_work=False),
         _llm_response(action="final", answer="the redirected conclusion", show_work=False)],
        queue, arrive_during_call=1, message="actually only look at the frontend",
    )

    result = await aw.run_react_loop(**_loop_kwargs(llm, act, max_iterations=4, steering_source=_source(queue)))

    assert result["final_answer"] == "the redirected conclusion"
    assert "actually only look at the frontend" in llm.prompts[1]


@run_async
async def test_steering_stays_in_every_later_prompt_not_just_the_next_one():
    queue = ["only touch the backend"]
    seen = []

    async def act(decision):
        seen.append(decision["tool_action"])
        return "ok"

    llm = _ScriptedLLM(
        [_llm_response(action="query", purpose="a", tool_action="search_code", args={"query": "a"}),
         _llm_response(action="query", purpose="b", tool_action="search_code", args={"query": "b"}),
         _llm_response(action="final", answer="done", show_work=False)],
        queue,
    )

    await aw.run_react_loop(**_loop_kwargs(llm, act, max_iterations=6, steering_source=_source(queue)))

    assert len(llm.prompts) >= 3
    assert all("only touch the backend" in p for p in llm.prompts[:3])


@run_async
async def test_a_steer_buys_extra_steps_so_the_redirect_is_not_cut_short():
    queue, acted = [], []

    async def act(decision):
        acted.append(decision["tool_action"])
        return "ok"

    class _KeepWorking(_ScriptedLLM):
        async def ainvoke(self, prompt):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                self.queue.append("also check the tests folder")
            if 'MUST return action="final" now' in prompt:
                return _llm_response(action="final", answer="wrapped up", show_work=False)
            return _llm_response(action="query", purpose="p", tool_action="search_code", args={"query": str(len(self.prompts))})

    llm = _KeepWorking([], queue)
    result = await aw.run_react_loop(**_loop_kwargs(llm, act, max_iterations=2, steering_source=_source(queue)))

    # Without the steer a 2-step budget allows one real action; the steer's 2 extra steps allow more.
    assert len(acted) == 2
    assert result["final_answer"] == "wrapped up"


@run_async
async def test_the_extra_steps_a_user_can_add_are_capped():
    queue = [f"change {i}" for i in range(6)]
    acted = []

    async def act(decision):
        acted.append(1)
        return "ok"

    class _KeepWorking(_ScriptedLLM):
        async def ainvoke(self, prompt):
            self.prompts.append(prompt)
            if 'MUST return action="final" now' in prompt:
                return _llm_response(action="final", answer="wrapped up", show_work=False)
            return _llm_response(action="query", purpose="p", tool_action="search_code", args={"query": str(len(self.prompts))})

    llm = _KeepWorking([], queue)
    await aw.run_react_loop(**_loop_kwargs(
        llm, act, max_iterations=1, steering_source=_source(queue), steering_max_extra_steps=4,
    ))

    assert len(acted) == 4  # 1 base step + 4 granted = 5; the last is the forced final


@run_async
async def test_a_failing_announcement_callback_never_breaks_the_run():
    queue = ["new direction"]

    async def act(decision):
        return "ok"

    async def broken(messages, step):
        raise RuntimeError("event bus down")

    llm = _ScriptedLLM([_llm_response(action="final", answer="still answered", show_work=False)], queue)
    result = await aw.run_react_loop(**_loop_kwargs(
        llm, act, max_iterations=3, steering_source=_source(queue), on_steering_applied=broken,
    ))

    assert result["final_answer"] == "still answered"


@run_async
async def test_a_run_with_no_steering_source_behaves_exactly_as_before():
    async def act(decision):
        return "ok"

    llm = _ScriptedLLM([_llm_response(action="final", answer="plain", show_work=False)], [])
    result = await aw.run_react_loop(**_loop_kwargs(llm, act, max_iterations=3))

    assert result["final_answer"] == "plain"
    assert "USER STEERING" not in llm.prompts[0]
