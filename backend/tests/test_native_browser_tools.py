import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from backend.services import native_loop, native_tools


class _LLM:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), list(tools)))
        if not tools:
            return self.responses[-1]
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _call(name, args=None, id=None):
    return {"name": name, "args": args or {}, "id": id or f"id_{name}", "type": "tool_call"}


def _drive(calls, act, names):
    llm = _LLM(AIMessage(content="", tool_calls=calls), AIMessage(content="done"))
    tools = [{"name": n, "description": n} for n in names]
    asyncio.run(native_loop.run(llm=llm, tools=tools, act=act, question="look at it"))
    return [m.content for m in llm.calls[1][0] if isinstance(m, ToolMessage)]


class _Page:
    def __init__(self, fail=()):
        self.events, self.url, self.fail = [], None, set(fail)

    async def __call__(self, decision):
        name = decision["tool_action"]
        self.events.append(("start", name))
        try:
            await asyncio.sleep(0.05 if name == "browser_navigate" else 0.01)
            if name in self.fail:
                raise ValueError("boom")
            if name == "browser_navigate":
                self.url = decision["args"]["url"]
                return f"opened {self.url}"
            if name == "browser_read_text":
                return f"text of {self.url}" if self.url else "(empty page)"
            return f"result of {name}"
        finally:
            self.events.append(("end", name))


PAGE = ["browser_navigate", "browser_read_text", "browser_click"]


def test_read_starts_after_navigation_ends_and_sees_the_page():
    page = _Page()
    out = _drive([_call("browser_navigate", {"url": "https://example.com"}), _call("browser_read_text")], page, PAGE)
    assert page.events.index(("end", "browser_navigate")) < page.events.index(("start", "browser_read_text"))
    assert out == ["opened https://example.com", "text of https://example.com"]


def test_non_page_calls_in_one_reply_still_overlap():
    events = []

    async def act(decision):
        events.append(("start", decision["tool_action"]))
        await asyncio.sleep(0.05)
        events.append(("end", decision["tool_action"]))
        return "ok " + decision["tool_action"]

    out = _drive([_call("search_code"), _call("read_repo_file")], act, ["search_code", "read_repo_file"])
    assert [e[0] for e in events[:2]] == ["start", "start"]
    assert out == ["ok search_code", "ok read_repo_file"]


def test_results_keep_the_models_order_with_mixed_calls():
    page = _Page()
    out = _drive([_call("browser_navigate", {"url": "https://a.test"}), _call("search_code"), _call("browser_read_text")], page, PAGE + ["search_code"])
    assert out == ["opened https://a.test", "result of search_code", "text of https://a.test"]


def test_a_failing_page_call_gives_its_error_and_does_not_stop_the_next():
    page = _Page(fail={"browser_click"})
    out = _drive([_call("browser_navigate", {"url": "https://a.test"}), _call("browser_click", {"text": "x"}), _call("browser_read_text")], page, PAGE)
    assert out[0] == "opened https://a.test"
    assert out[1].startswith("ERROR: browser_click failed")
    assert out[2] == "text of https://a.test"

BROWSER_TOOLS = ("browser_navigate", "browser_read_text", "browser_click", "browser_type", "browser_screenshot")
EXPECTED_ARGS = {
    "browser_navigate": {"url"},
    "browser_read_text": set(),
    "browser_click": {"text"},
    "browser_type": {"label", "value", "submit"},
    "browser_screenshot": set(),
}


def test_schemas_and_descriptions_exist_with_menu_argument_names():
    for name in BROWSER_TOOLS:
        assert name in native_tools.REGISTRY
        assert name in native_tools.NATIVE_DESCRIPTIONS
        assert set(native_tools.REGISTRY[name]["properties"]) == EXPECTED_ARGS[name]


def test_declarations_follow_the_menu():
    menu = ["- browser_navigate \u2014 args: url", "- browser_click \u2014 args: text"]
    names = [d["name"] for d in native_tools.declarations_from_menu(menu)]
    assert names == ["browser_navigate", "browser_click"]
    assert native_tools.declarations_from_menu([]) == []


