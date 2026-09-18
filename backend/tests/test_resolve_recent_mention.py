from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw


def test_resolve_recent_mention_prefers_most_recent_match():
    messages = [
        HumanMessage(content="look at owner/first-repo"),
        HumanMessage(content="actually never mind, look at owner/second-repo instead"),
    ]
    result = aw.resolve_recent_mention(messages, lambda c: aw.extract_github_repo(c, fallback=None))
    assert result == "owner/second-repo"


def test_resolve_recent_mention_falls_back_to_earlier_message_when_latest_has_no_match():
    messages = [
        HumanMessage(content="look at owner/some-repo"),
        HumanMessage(content="tell me the recent changes to it please"),
    ]
    result = aw.resolve_recent_mention(messages, lambda c: aw.extract_github_repo(c, fallback=None))
    assert result == "owner/some-repo"


def test_resolve_recent_mention_returns_none_when_nothing_matches():
    messages = [HumanMessage(content="hello there"), HumanMessage(content="how are you")]
    result = aw.resolve_recent_mention(messages, lambda c: aw.extract_github_repo(c, fallback=None))
    assert result is None


def test_resolve_recent_mention_skip_predicate_skips_bare_approvals():
    messages = [
        HumanMessage(content="what's the latest on this project?"),
        HumanMessage(content="ok"),
    ]
    result = aw.resolve_recent_mention(
        messages,
        lambda c: c.strip(),
        skip_predicate=lambda c: c.lower().strip("!., ") in {"ok", "yes"},
    )
    assert result == "what's the latest on this project?"


def test_resolve_recent_mention_empty_messages_returns_none():
    assert aw.resolve_recent_mention([], lambda c: c) is None
