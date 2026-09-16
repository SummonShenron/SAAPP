from backend.services.agent_workflow import classify_intent, build_agent_plan


def test_plain_conversational_message():
    assert classify_intent("hello, how are you?") == "conversational"


def test_tapioca_does_not_false_positive_into_tool():
    # Regression: "api" used to be matched as a bare substring, misfiring on words like
    # "tapioca" or "apiary". Now requires "api" as a standalone word.
    assert classify_intent("tell me about tapioca pudding recipes") != "tool"


def test_api_as_a_whole_word_still_matches_tool():
    assert classify_intent("what's a good api for weather data") == "tool"


def test_approval_with_natural_phrasing_executes_pending_pr():
    state = {"pending_action": {"status": "awaiting_approval", "action_type": "create_pr"}}
    assert classify_intent("yes let's do it", state=state) == "execute_pr"


def test_bare_yes_still_approves_pending_pr():
    state = {"pending_action": {"status": "awaiting_approval", "action_type": "create_pr"}}
    assert classify_intent("yes", state=state) == "execute_pr"


def test_approval_with_natural_phrasing_continues_pending_web_search():
    state = {"pending_action": {"status": "hitl_approval_required", "type": "web_search"}}
    assert classify_intent("yeah go for it", state=state) == "web_search"


def test_rejection_with_natural_phrasing_cancels_pending_action():
    state = {"pending_action": {"status": "hitl_approval_required", "type": "web_search"}}
    assert classify_intent("nah don't bother", state=state) == "cancel_action"


def test_rejection_of_pending_pr_also_cancels():
    state = {"pending_action": {"status": "awaiting_approval", "action_type": "create_pr"}}
    assert classify_intent("no, cancel that", state=state) == "cancel_action"


def test_no_pending_action_ignores_approval_words():
    # Approval/rejection vocabulary should only matter while something is actually pending.
    assert classify_intent("yes I think dogs are great") == "conversational"


def test_github_and_pr_summary_classification_unaffected():
    assert classify_intent("show me the github repository") == "github_search"
    assert classify_intent("can you give me a pr summary") == "pr_summary"
    assert classify_intent("create pr for my branch") == "create_pr"


def test_natural_pr_creation_phrasing_is_not_swallowed_by_pr_summary():
    # Regression: "create a pull request..." contains the bare bigram "pull request", which
    # used to match the pr_summary/review pattern before create_pr ever got a chance, making
    # the assistant respond as if the user were asking to review an existing PR by number.
    assert classify_intent(
        "can you create a pull request to merge feat/teams into main in the summonshenron/errAgent repo?"
    ) == "create_pr"
    assert classify_intent(
        "create a pr to merge feat/teams into main in repo summonshenron/erragent"
    ) == "create_pr"


def test_review_pr_phrasing_still_classifies_as_pr_summary():
    assert classify_intent("review pr #2") == "pr_summary"
    assert classify_intent("can you review this pull request") == "pr_summary"


def test_build_agent_plan_routes_create_pr_via_reasoner_flag_alone():
    # Regression: needs_create_pr was set by the reasoner LLM but never actually consulted
    # by build_agent_plan, so PR-creation intent the regex failed to catch was silently
    # dropped even when the LLM correctly identified it.
    plan = build_agent_plan("conversational", {"reasoner_flags": {"needs_create_pr": True}})
    assert plan["agents"] == ["draft_pr", "formatter"]


def test_build_agent_plan_routes_web_search_intent():
    plan = build_agent_plan("web_search", {"reasoner_flags": {}})
    assert plan["agents"][0] == "web_search"


def test_build_agent_plan_cancel_action_clears_pending_and_sets_insight():
    state = {"reasoner_flags": {}, "pending_action": {"status": "hitl_approval_required"}}
    plan = build_agent_plan("cancel_action", state)

    assert plan["agents"] == ["conversational"]
    assert state["pending_action"] is None
    assert "cancelled" in state["insight_answer"] or "rejected" in state["insight_answer"]
