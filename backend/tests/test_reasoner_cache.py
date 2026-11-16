"""The reasoner's prompt cache. Its prompt is one message whose START is the same instructions on every call and whose END (the history, the message) changes, so the cache breakpoint must sit at the end of the
stable start (ClaudeLLM stable_prefix). With the older automatic caching the breakpoint moved to the end of the prompt, which changes every call: the whole prompt was written at 1.25x and never read
(measured on the usage rows, 2026-11-15: 70 calls, 0% read, 100% written)."""
from backend.components import constraints as c
from backend.models import models
from backend.models.claude_llm import ClaudeLLM


def flat(message):
    content = message.content
    return content if isinstance(content, str) else "".join(block["text"] for block in content)


def reasoner_prompt(history="User: hi\nAssistant: hello", question="what is on my calendar?"):
    return c.REASONER_PROMPT.format(history=history, question=question)


PREFIX = models.reasoner_stable_prefix()


def test_the_stable_prefix_is_the_start_of_every_reasoner_prompt_and_holds_nothing_from_the_turn():
    one = reasoner_prompt()
    other = reasoner_prompt(history="User: something else\nAssistant: yes", question="a completely different message")
    assert one.startswith(PREFIX) and other.startswith(PREFIX)
    assert len(PREFIX) > 10_000                                    # the instructions and the schema's rules: most of the prompt
    assert "{history}" not in PREFIX and "{question}" not in PREFIX and "{{" not in PREFIX
    assert "what is on my calendar" not in PREFIX and "something else" not in PREFIX
    assert len(PREFIX) / len(one) > 0.85                          # the stable start is the bulk of it, which is what makes caching it worthwhile


def test_the_prompt_is_sent_as_the_same_words_in_two_blocks_with_only_the_stable_one_marked():
    prompt = reasoner_prompt()
    messages = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, stable_prefix=PREFIX)._to_messages(prompt)
    assert len(messages) == 1
    blocks = messages[0].content
    assert flat(messages[0]) == prompt                                                           # not a word lost or added
    assert blocks[0] == {"type": "text", "text": PREFIX, "cache_control": {"type": "ephemeral"}}
    assert "cache_control" not in blocks[1]                                                      # the part that changes is never marked
    assert "what is on my calendar?" in blocks[1]["text"] and "what is on my calendar?" not in blocks[0]["text"]


def test_two_different_calls_start_with_an_identical_marked_block():
    # The cache is a prefix match: what the next call reads is only what this call's marked block was, character for character.
    llm = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, stable_prefix=PREFIX)
    first = llm._to_messages(reasoner_prompt(question="one thing"))[0].content[0]
    second = llm._to_messages(reasoner_prompt(history="User: x\nAssistant: y", question="another thing entirely"))[0].content[0]
    assert first == second


def test_without_a_prefix_or_with_caching_off_or_a_prompt_that_does_not_start_with_it_the_prompt_is_sent_as_it_was():
    prompt = reasoner_prompt()
    plain = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None)._to_messages(prompt)
    assert plain[0].content == prompt
    off = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, stable_prefix=PREFIX, cache=False)._to_messages(prompt)
    assert off[0].content == prompt
    other = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, stable_prefix=PREFIX)._to_messages("a different job's prompt")
    assert other[0].content == "a different job's prompt"
    longer_but_different = "Some other job's instructions. " * (len(PREFIX) // 20 + 10)          # longer than the prefix, but it does not start with it: it must not be cut at the prefix's length
    assert len(longer_but_different) > len(PREFIX)
    wrong_start = ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, stable_prefix=PREFIX)._to_messages(longer_but_different)
    assert wrong_start[0].content == longer_but_different
    only_the_prefix =ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, stable_prefix=PREFIX)._to_messages(PREFIX)
    assert only_the_prefix[0].content == PREFIX                                                  # nothing after it: no empty second block


def test_a_job_with_a_prefix_does_not_also_ask_for_automatic_caching(monkeypatch):
    built = {}

    class Recorder:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", Recorder)
    ClaudeLLM(model_name="claude-haiku-5-5", fallback=None, stable_prefix=PREFIX)._client()
    assert "model_kwargs" not in built                       # the moving breakpoint wrote the whole prompt each call and read none of it
    built.clear()
    ClaudeLLM(model_name="claude-haiku-5-5", fallback=None)._client()
    assert built["model_kwargs"] == {"cache_control": {"type": "ephemeral"}}   # every other job is unchanged


def test_the_haiku_reasoner_is_built_with_the_prefix_and_everyone_else_is_untouched(monkeypatch):
    monkeypatch.setattr(models, "haiku_reply_available", lambda: True)
    models._reasoner_haiku.clear()
    llm, kind = models.reasoner_llm_for("haiku")
    assert kind == "haiku" and llm.stable_prefix == PREFIX
    models._reasoner_haiku.clear()
    lite, lite_kind = models.reasoner_llm_for("gemini")
    assert lite_kind == "lite" and not isinstance(lite, ClaudeLLM)
