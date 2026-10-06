"""Catches a name that is used but never imported or defined.

Real incident: a merge silently dropped one `from backend.services import steering` line from app.py.
Nothing imports app.py in the test suite, so everything passed, it deployed, and every chat request
on the live backend died with NameError. Python only discovers this when the line actually runs, so
this checks the compiled symbol tables instead: every global a function reads must be defined at
module level, imported, or a builtin.
"""
import builtins
import pathlib
import symtable

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKED = [
    "app.py",
    "backend/services/agent_workflow.py",
    "backend/services/react_loop.py",
    "backend/services/steering.py",
    "backend/utils/app_utils.py",
    "backend/utils/agent_utils.py",
    "backend/utils/fallback_utils.py",
    "backend/utils/emotion_utils.py",
    "backend/services/google_calendar_oauth.py",
    "backend/services/google_docs_service.py",
    "backend/services/github_service.py",
    "backend/utils/user_settings_utils.py",
    "backend/utils/secret_utils.py",
    "backend/utils/webhook_utils.py",
    "backend/utils/github_audit.py",
    "backend/services/url_reader.py",
    "backend/utils/emotion_checks.py",
]
_MODULE_DUNDERS = {"__file__", "__name__", "__doc__", "__builtins__", "__spec__", "__package__", "__path__"}


def undefined_global_names(source: str, filename: str = "<module>") -> set:
    table = symtable.symtable(source, filename, "exec")
    defined = {
        s.get_name() for s in table.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_namespace() or s.is_parameter()
    }
    known = defined | set(dir(builtins)) | _MODULE_DUNDERS
    missing = set()

    def visit(scope, is_module):
        for symbol in scope.get_symbols():
            name = symbol.get_name()
            if not symbol.is_referenced() or name in known:
                continue
            # At module level an unassigned, referenced name is a global read; inside a function
            # it must be flagged as a global (explicit or implicit). Free variables are closures.
            if (is_module and not symbol.is_assigned() and not symbol.is_imported()) or (
                not is_module and symbol.is_global()
            ):
                missing.add(name)
        for child in scope.get_children():
            visit(child, False)

    visit(table, True)
    return missing


@pytest.mark.parametrize("relative_path", CHECKED)
def test_every_global_name_used_is_defined_or_imported(relative_path):
    path = ROOT / relative_path
    assert undefined_global_names(path.read_text(encoding="utf-8"), str(path)) == set()


def test_the_check_would_have_caught_the_dropped_steering_import():
    source = "from backend.services import other\n\ndef handler():\n    return steering.submit('k', 'm')\n"
    assert undefined_global_names(source) == {"steering"}
    assert undefined_global_names("from backend.services import steering\n" + source) == set()


def test_closures_parameters_and_comprehension_variables_are_not_false_positives():
    source = (
        "def outer(items, flag):\n"
        "    total = 0\n"
        "    def inner(x):\n"
        "        return x + total + len(items)\n"
        "    return [inner(i) for i in items if flag]\n"
    )
    assert undefined_global_names(source) == set()
