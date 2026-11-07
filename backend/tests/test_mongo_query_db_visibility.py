import pathlib

SOURCE = (pathlib.Path(__file__).resolve().parents[2] / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")


def test_db_and_username_are_in_the_snippet_globals_so_comprehensions_can_see_them():
    # A name only in the local scope raises NameError inside a comprehension or lambda.
    start = SOURCE.index("def _run_mongo_code(code: str):")
    body = SOURCE[start: start + 500]
    assert 'snippet_globals["db"] = db' in body
    assert 'snippet_globals["username"] = username' in body
    assert 'local_scope = {"result": None}' in body
