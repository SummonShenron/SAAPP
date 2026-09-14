from backend.state.graph_state import route_after_grading


def test_open_rag_mode_skips_rewrite_loop_even_when_irrelevant():
    state = {"relevance_grade": "no", "loop_count": 0, "rag_mode": "open"}
    assert route_after_grading(state) == "generate_node"


def test_open_rag_mode_skips_rewrite_loop_at_max_loops_too():
    state = {"relevance_grade": "no", "loop_count": 5, "rag_mode": "open"}
    assert route_after_grading(state) == "generate_node"


def test_strict_rag_mode_preserves_existing_rewrite_behavior():
    state = {"relevance_grade": "no", "loop_count": 0, "rag_mode": "strict"}
    assert route_after_grading(state) == "rewrite_query_node"


def test_missing_rag_mode_defaults_to_strict_behavior():
    state = {"relevance_grade": "no", "loop_count": 0}
    assert route_after_grading(state) == "rewrite_query_node"


def test_relevant_grade_still_routes_to_generate_regardless_of_mode():
    assert route_after_grading({"relevance_grade": "yes", "loop_count": 0, "rag_mode": "strict"}) == "generate_node"
    assert route_after_grading({"relevance_grade": "yes", "loop_count": 0, "rag_mode": "open"}) == "generate_node"


def test_attachment_summaries_still_take_priority_over_rag_mode():
    state = {"attachment_summaries": ["x"], "relevance_grade": "no", "loop_count": 0, "rag_mode": "strict"}
    assert route_after_grading(state) == "generate_node"
