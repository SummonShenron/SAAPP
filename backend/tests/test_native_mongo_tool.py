import pytest

from backend.services import native_loop as nl
from backend.services import native_tools as nt
from backend.utils import self_counters as sc

MONGO_TOOLS = [{"name": "search_code"}, {"name": "run_mongo_query"}]
PLAIN_TOOLS = [{"name": "search_code"}]


@pytest.fixture(autouse=True)
def native_on(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")


def test_schema_has_one_required_string_argument_code():
    schema = nt.REGISTRY["run_mongo_query"]
    assert set(schema["properties"]) == {"code"}
    assert schema["required"] == ["code"]
    assert schema["properties"]["code"]["type"] == "string"


def test_description_carries_the_menu_rules():
    text = nt.NATIVE_DESCRIPTIONS["run_mongo_query"]
    assert "Admins only" in text
    assert "result" in text
    assert "list(" in text
    assert "other people's data" in text





def test_the_query_has_its_own_solo_lane_and_the_page_lane_is_unchanged():
    assert nl._SOLO_TOOLS == frozenset({"run_mongo_query", "run_repo_tests", "run_snippet"})
    assert nl._SERIAL_TOOLS == nl.PAGE_TOOLS
    assert not (nl._SOLO_TOOLS & nl._SERIAL_TOOLS)


def test_the_counter_key_is_registered():
    assert "run_mongo_query_native" in sc.KEYS
