"""The chat stream's inner functions must not reuse a name the chat handler owns.

Real incident (2026-10-27): `secure_chat` sets `previous_reply` (the text of Sonic's last reply) near the top, and its inner `token_streamer` reads it at the end of every turn. A later change assigned
`previous_reply = next(...)` inside `token_streamer` for a different purpose. Python makes a name LOCAL to a function the moment it is assigned anywhere in it, so on every turn where that assignment did not
run, the read at the end raised UnboundLocalError. The reply had already been sent, so the chat looked fine, but the turn was counted as failed and everything after the reply (memory notes, plan capture, counters)
was skipped. Nothing imports app.py in this suite, so no test ran that path, and the undefined-names check cannot see it (the name IS defined). This checks the compiled symbol tables instead: a name that is assigned
in an inner function of `secure_chat` and also assigned in `secure_chat` itself is refused. Rename the inner one (or declare it `nonlocal` if it is meant to be the outer variable).
"""
import pathlib
import symtable

APP = pathlib.Path(__file__).resolve().parents[2] / "app.py"


def _functions(table, name):
    found = []
    for child in table.get_children():
        if child.get_type() == "function" and child.get_name() == name:
            found.append(child)
        found.extend(_functions(child, name))
    return found


def _assigned_locals(table):
    return {s.get_name() for s in table.get_symbols() if s.is_local() and s.is_assigned() and not s.is_parameter()}


def test_inner_functions_of_the_chat_handler_do_not_shadow_its_variables():
    top = symtable.symtable(APP.read_text(encoding="utf-8"), "app.py", "exec")
    handlers = _functions(top, "secure_chat")
    assert len(handlers) == 1
    handler = handlers[0]
    owned = _assigned_locals(handler)
    shadowed = {}
    for inner in (c for c in handler.get_children() if c.get_type() == "function"):
        clash = (_assigned_locals(inner) & owned) - {s.get_name() for s in inner.get_symbols() if s.is_nonlocal()}
        if clash:
            shadowed[inner.get_name()] = sorted(clash)
    assert not shadowed, (
        f"{shadowed}: an inner function of secure_chat assigns a name secure_chat also assigns, which makes it local there, so reading the outer value raises UnboundLocalError on any turn "
        "where the inner assignment did not run. Rename the inner one, or declare it nonlocal."
    )


def test_the_check_would_have_caught_the_real_bug(tmp_path):
    source = (
        "def secure_chat():\n"
        "    previous_reply = 'text'\n"
        "    async def token_streamer():\n"
        "        if True:\n"
        "            previous_reply = object()\n"
        "        return previous_reply\n"
        "    return token_streamer\n"
    )
    handler = _functions(symtable.symtable(source, "x.py", "exec"), "secure_chat")[0]
    inner = [c for c in handler.get_children() if c.get_type() == "function"][0]
    assert _assigned_locals(inner) & _assigned_locals(handler) == {"previous_reply"}
