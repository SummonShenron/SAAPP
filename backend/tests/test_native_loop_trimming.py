import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from backend.services import native_loop as nl

TOOLS = [{"name": "read_repo_file", "description": "read a file"}, {"name": "search_code", "description": "find things"}]
BIG = "A" * 5000
SMALL = "tiny result"


def asks(*calls):
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"id_{i}_{n}", "type": "tool_call"} for i, (n, a) in enumerate(calls)])


class Recorder:
    """A scripted model that snapshots the text of every tool result AS IT WAS SENT on each call (the loop edits messages in place later)."""

    def __init__(self, *responses):
        self.responses, self.sent = list(responses), []

    async def ainvoke_tools(self, messages, tools):
        self.sent.append([str(m.content) for m in messages if isinstance(m, ToolMessage)])
        if not tools:
            return AIMessage(content="final answer")
        return self.responses.pop(0) if self.responses else AIMessage(content="final answer")


class Acts:
    def __init__(self, results):
        self.results, self.decisions = results, []

    async def __call__(self, decision):
        self.decisions.append((decision["tool_action"], dict(decision["args"])))
        result = self.results(decision) if callable(self.results) else self.results
        return result


def run(llm, act, **kw):
    return asyncio.run(nl.run(llm=llm, tools=TOOLS, act=act, question="q", max_steps=kw.pop("max_steps", 8), **kw))


def reads(*names):
    return asks(*[("read_repo_file", {"path": n}) for n in names])


# ---- the helper -----------------------------------------------------------------------------------------------------------

def test_a_big_result_is_cut_to_its_start_with_a_plain_note_and_a_small_one_or_an_error_is_left_alone():
    out = nl.shorten_old_result(BIG)
    assert out.startswith("A" * nl.MASKED_HEAD_CHARS) and len(out) < 600 and "shortened" in out and "Call the tool again" in out
    assert nl.shorten_old_result(SMALL) is None
    assert nl.shorten_old_result("ERROR: " + "x" * 3000) is None
    assert nl.shorten_old_result("y" * (nl.MASK_MIN_CHARS - 1)) is None


# ---- in the loop ----------------------------------------------------------------------------------------------------------

def test_results_stay_in_full_for_the_recent_steps_and_are_shortened_after():
    llm = Recorder(reads("a.py"), reads("b.py"), reads("c.py"), reads("d.py"), reads("e.py"))
    run(llm, Acts(BIG))
    # What the model saw when it decided its Nth step (the first call has no results yet).
    sent = llm.sent
    assert sent[0] == []
    assert sent[1] == [BIG]                              # step 2 sees step 1's result in full
    assert sent[2] == [BIG, BIG]                         # step 3 sees steps 1 and 2 in full (KEEP_RECENT_STEPS = 2)
    assert "shortened" in sent[3][0] and sent[3][1:] == [BIG, BIG]   # step 4: step 1's result is shortened, 2 and 3 are not
    assert [("shortened" in r) for r in sent[4]] == [True, True, False, False]


def test_small_results_are_never_shortened_however_old():
    llm = Recorder(reads("a.py"), reads("b.py"), reads("c.py"), reads("d.py"))
    run(llm, Acts(SMALL))
    assert llm.sent[-1] == [SMALL] * 4


def test_the_receipt_and_the_attempts_keep_the_full_result_even_after_the_model_stopped_seeing_it():
    llm = Recorder(reads("a.py"), reads("b.py"), reads("c.py"), reads("d.py"), reads("e.py"), reads("f.py"), reads("g.py"), reads("h.py"))
    result = run(llm, Acts(BIG), max_steps=8)
    assert all(a["observation"] == BIG for a in result["attempts"])


def test_asking_again_for_something_that_was_shortened_is_allowed_and_returns_the_full_result():
    acts = Acts(BIG)
    llm = Recorder(reads("a.py"), reads("b.py"), reads("c.py"), reads("d.py"), reads("a.py"))  # step 5 asks for a.py again
    run(llm, acts)
    assert [d[1]["path"] for d in acts.decisions].count("a.py") == 2          # it really ran again
    assert llm.sent[-1][-1] == BIG                                              # ...and the full result came back to the model


def test_asking_again_for_something_still_in_full_is_still_a_repeat():
    acts = Acts(BIG)
    llm = Recorder(reads("a.py"), reads("a.py"), AIMessage(content="done"))
    result = run(llm, acts)
    assert [d[1]["path"] for d in acts.decisions] == ["a.py"]                  # not run twice
    assert any("already made this exact call" in a["observation"] for a in result["attempts"])


def test_rereading_is_bounded_so_trimming_cannot_cause_a_loop():
    acts = Acts(BIG)
    # Read a.py, then keep cycling through filler reads and asking for a.py again: only MAX_REREADS of those may run.
    script = [reads("a.py")]
    for i in range(6):
        script += [reads(f"x{i}.py"), reads(f"y{i}.py"), reads(f"z{i}.py"), reads("a.py")]
    run(Recorder(*script), acts, max_steps=8)
    assert [d[1]["path"] for d in acts.decisions].count("a.py") <= 1 + nl.MAX_REREADS


def test_the_forced_final_answer_sees_shortened_results_too():
    llm = Recorder(*[reads(f"f{i}.py") for i in range(8)])
    run(llm, Acts(BIG), max_steps=8)
    final_call = llm.sent[-1]
    assert len(final_call) == 8 and "shortened" in final_call[0] and final_call[-1] == BIG


def test_it_cuts_the_tokens_a_long_run_sends():
    def sent_chars(keep):
        original = nl.KEEP_RECENT_STEPS
        nl.KEEP_RECENT_STEPS = keep
        try:
            llm = Recorder(*[reads(f"f{i}.py") for i in range(7)])
            run(llm, Acts(BIG), max_steps=8)
            return sum(len(r) for call in llm.sent for r in call)
        finally:
            nl.KEEP_RECENT_STEPS = original

    assert sent_chars(2) < 0.6 * sent_chars(99)  # 99 = never shorten: the old behaviour
