"""Tool declarations Gemini will accept (backend/models/gemini_schema.py). Found 2026-11-20 in production: Anthropic answered 401, every tool turn fell back to Gemini, and binding the Google Maps tool raised
"5 validation errors for Schema ... Input should be a valid string" because its `count` argument was declared with an INTEGER enum, which Gemini's schema does not take. No model is called here: the real converter the
Gemini client uses is run on the real declarations."""
import copy

import pytest

from backend.models import gemini_schema as gs
from backend.models import models as models_module
from backend.services import maps_tools, native_tools


def all_declarations():
    return native_tools.declarations_from_menu([f"- {name} — x" for name in native_tools.REGISTRY]) + maps_tools.declarations()


# ---- the sanitiser -------------------------------------------------------------------------------------------------------------------------------

def test_a_number_enum_becomes_a_sentence_in_the_description_and_keeps_its_type():
    schema = {"type": "object", "properties": {"count": {"type": "integer", "enum": [1, 2, 3], "description": "how many"}}}
    safe = gs.gemini_safe_schema(schema)
    assert safe["properties"]["count"] == {"type": "integer", "description": "how many (one of: 1, 2, 3)"}


def test_a_string_enum_is_left_alone_and_so_is_everything_without_one():
    schema = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["DRIVE", "WALK"]}, "q": {"type": "string", "description": "words"}}, "required": ["q"]}
    assert gs.gemini_safe_schema(schema) == schema


def test_it_works_at_any_depth_in_lists_and_nested_objects_and_with_no_description_yet():
    schema = {"type": "object", "properties": {
        "rows": {"type": "array", "items": {"type": "object", "properties": {"level": {"type": "integer", "enum": [1, 2]}, "kind": {"type": "string", "enum": ["a", "b"]}}}},
        "flag": {"enum": [True, False]},
        "either": {"anyOf": [{"type": "integer", "enum": [7, 8]}, {"type": "string"}]},
    }}
    safe = gs.gemini_safe_schema(schema)
    assert safe["properties"]["rows"]["items"]["properties"]["level"] == {"type": "integer", "description": "one of: 1, 2"}
    assert safe["properties"]["rows"]["items"]["properties"]["kind"]["enum"] == ["a", "b"]
    assert "enum" not in safe["properties"]["flag"] and safe["properties"]["flag"]["description"] == "one of: True, False"
    assert safe["properties"]["either"]["anyOf"][0] == {"type": "integer", "description": "one of: 7, 8"}


def test_the_callers_declarations_are_never_changed():
    tools = [{"name": "t", "description": "d", "parameters": {"type": "object", "properties": {"n": {"type": "integer", "enum": [1, 2]}}}}]
    before = copy.deepcopy(tools)
    safe = gs.gemini_safe_tools(tools)
    assert tools == before and safe is not tools and "enum" not in safe[0]["parameters"]["properties"]["n"]


def test_a_declaration_with_nothing_to_change_comes_back_equal_and_odd_input_does_not_raise():
    tools = [{"name": "plain", "description": "d", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}, {"name": "no_args", "description": "d"}]
    assert gs.gemini_safe_tools(tools) == tools
    assert gs.gemini_safe_tools([]) == [] and gs.gemini_safe_tools(None) == []
    assert gs.gemini_safe_schema({}) == {}


# ---- the real declarations through the real Gemini converter -------------------------------------------------------------------------------------

def test_every_tool_the_agent_can_offer_converts_for_gemini_once_made_safe_which_is_what_crashed_in_production():
    from langchain_google_genai import _function_utils as fu

    declarations = all_declarations()
    assert len(declarations) > 50 and any(d["name"].endswith("find_places") for d in declarations), "the real list, with the maps tools in it"
    failures = []
    for declaration in gs.gemini_safe_tools(declarations):
        try:
            fu.convert_to_genai_function_declarations([declaration])
        except Exception as error:      # noqa: BLE001
            failures.append((declaration["name"], str(error).splitlines()[0]))
    assert not failures, failures


def test_the_maps_tool_that_crashed_still_says_how_many_places_it_may_ask_for():
    find = next(d for d in gs.gemini_safe_tools(all_declarations()) if d["name"].endswith("find_places"))
    count = find["parameters"]["properties"]["count"]
    assert count["type"] == "integer" and "1, 2, 3, 4, 5" in count["description"] and "enum" not in count


# ---- where tools are bound to Gemini --------------------------------------------------------------------------------------------------------------

class Bound:
    def __init__(self, label, tools):
        self.label, self.tools, self.fallbacks = label, tools, []

    def with_fallbacks(self, items):
        self.fallbacks = list(items)
        return self


class Client:
    def __init__(self, label, fail=None):
        self.label, self.fail, self.seen = label, fail, []

    def bind_tools(self, tools):
        self.seen.append(tools)
        if self.fail:
            raise self.fail
        return Bound(self.label, tools)


TOOLS = [{"name": "find", "description": "d", "parameters": {"type": "object", "properties": {"count": {"type": "integer", "enum": [1, 2, 3, 4, 5]}, "q": {"type": "string"}}}}]


def llm(primary, fallback):
    model = models_module.LazyLLM(model_name="primary", fallback_model="backup")
    model._real_llm = object()
    model._primary_client, model._fallback_client = primary, fallback
    return model


def test_the_primary_and_the_fallback_are_both_given_declarations_gemini_accepts_and_the_callers_list_is_untouched():
    before = copy.deepcopy(TOOLS)
    primary, fallback = Client("primary"), Client("fallback")
    bound = llm(primary, fallback)._runnable_with_tools(TOOLS)
    for client in (primary, fallback):
        assert "enum" not in client.seen[0][0]["parameters"]["properties"]["count"], client.label
    assert len(bound.fallbacks) == 1 and TOOLS == before


def test_a_fallback_that_cannot_take_the_tools_does_not_cost_the_turn_the_primary_can_still_run():
    primary, fallback = Client("primary"), Client("fallback", fail=ValueError("5 validation errors for Schema"))
    bound = llm(primary, fallback)._runnable_with_tools(TOOLS)
    assert bound.label == "primary" and bound.fallbacks == []


def test_a_primary_that_cannot_take_them_still_raises_because_there_is_nothing_to_run():
    with pytest.raises(ValueError):
        llm(Client("primary", fail=ValueError("no")), Client("fallback"))._runnable_with_tools(TOOLS)
