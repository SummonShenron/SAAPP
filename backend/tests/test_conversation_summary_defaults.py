"""The shipped fold threshold of the conversation summary: 20 messages or about 12,000 characters (raised from 14 and 8,000 on 2026-11-06; docs/conversation-summary-plan.md). The folding mechanics are tested at a
fixed threshold in test_conversation_summary.py; this file pins the defaults themselves and what they do to the word-for-word window and the ring."""
import copy
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

import importlib.util
import os

_SHIPPED = {"CONVERSATION_FOLD_AT_MESSAGES": "50", "CONVERSATION_FOLD_AT_CHARS": "30000", "CONVERSATION_KEEP_TAIL_MESSAGES": "6"}


def _module_with_shipped_defaults():
    """A private copy of the module read with the shipped numbers, whatever the environment says. The real module reads CONVERSATION_FOLD_AT_MESSAGES and the others at import, and a deployment (or a developer's .env, which the
    first test that imports app.py loads into the environment) may set them higher; these tests pin what the code ships with, not what one machine has chosen. The first test below also checks the numbers in the source."""
    saved = {name: os.environ.get(name) for name in _SHIPPED}
    os.environ.update(_SHIPPED)
    try:
        spec = importlib.util.spec_from_file_location("conversation_summary_shipped_defaults", Path(__file__).resolve().parents[1] / "utils" / "conversation_summary.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


cs = _module_with_shipped_defaults()


def chat(total):
    return [HumanMessage(content=f"filler question number {i}") if i % 2 == 0 else AIMessage(content=f"filler answer number {i}") for i in range(total)]


def fold_now(summary, messages):
    start, end = cs.fold_range(summary, messages)
    new = copy.deepcopy(summary) if summary else cs.empty_summary()
    new.update(folded_upto=end, anchor=cs.anchor_of(messages[end - 1 - cs.base_of(messages)]), folds=int(new.get("folds", 0)) + 1)
    return new


def test_the_shipped_defaults_are_twenty_messages_or_twelve_thousand_characters_keeping_six():
    source = (Path(__file__).resolve().parents[1] / "utils" / "conversation_summary.py").read_text(encoding="utf-8")
    for name, value in _SHIPPED.items():
        assert f'os.getenv("{name}", "{value}")' in source, name               # the literal defaults in the code itself
    assert cs.FOLD_AT_MESSAGES == 50 and cs.FOLD_AT_CHARS == 30000 and cs.KEEP_TAIL_MESSAGES == 6
    assert cs.HARD_MAX_MESSAGES == 52                                  # what a turn sends word for word at most, so a paused or failing fold cannot let the prompt grow


def test_the_characters_scale_with_the_messages_so_a_chatty_conversation_does_not_fold_long_before_the_ring_says_so():
    # About 600 characters a reply: twenty messages are about 12,000, so the character trigger lands near the message one and not at thirteen messages.
    assert 550 <= cs.FOLD_AT_CHARS / cs.FOLD_AT_MESSAGES <= 650
    thirteen_chatty = [HumanMessage(content="x" * 600) if i % 2 == 0 else AIMessage(content="y" * 600) for i in range(13)]
    assert not cs.due(None, thirteen_chatty)                           # 13 x 600 = 7,800 characters: not yet (it was due at 8,000 under the old default)
    twenty = chat(cs.FOLD_AT_MESSAGES)
    assert cs.due(None, twenty) and not cs.due(None, chat(cs.FOLD_AT_MESSAGES - 1))


def test_the_word_for_word_part_runs_six_to_twenty_and_averages_about_thirteen():
    summary, sizes = None, []
    for total in range(30, 150):
        messages = chat(total)
        sizes.append(len(cs.history_window(messages, summary, active=True)))
        assert sizes[-1] <= cs.HARD_MAX_MESSAGES
        if cs.due(summary, messages):
            summary = fold_now(summary, messages)
    assert 24 <= sum(sizes) / len(sizes) <= 32                          # the old default averaged about 10; the cost of the longer window is what this number is for (docs/conversation-summary-plan.md)
    assert min(sizes) >= cs.KEEP_TAIL_MESSAGES


def test_a_fold_happens_about_once_per_fourteen_messages_so_there_are_fewer_of_them():
    summary, folds_at = None, []
    for total in range(1, 200):
        messages = chat(total)
        if cs.due(summary, messages):
            summary = fold_now(summary, messages)
            folds_at.append(total)
    gaps = [b - a for a, b in zip(folds_at, folds_at[1:])]
    assert gaps and all(gap == cs.FOLD_AT_MESSAGES - cs.KEEP_TAIL_MESSAGES for gap in gaps)


def test_the_page_ring_gets_twenty_as_its_denominator_and_the_environment_can_still_change_it():
    assert cs.progress(None, 7) == {"unfolded": 7, "fold_at": 50, "keep": 6}
    source = Path("backend/utils/conversation_summary.py").read_text(encoding="utf-8")
    assert 'os.getenv("CONVERSATION_FOLD_AT_MESSAGES", "50")' in source and 'os.getenv("CONVERSATION_FOLD_AT_CHARS", "30000")' in source
