from backend.services.agent_workflow import run_mongo_snippet

# A name only in a separate local scope raises NameError inside a comprehension or lambda. Runs the real helper (see test_mongo_query_sandbox.py for the full set).


def test_db_and_username_are_in_the_snippet_globals_so_comprehensions_can_see_them():
    rows = [{"u": "jack"}, {"u": "ann"}]
    code = 'result = [d["u"] for d in db if d["u"] == username]'
    assert run_mongo_snippet(code, rows, "jack", {}) == ["jack"]
