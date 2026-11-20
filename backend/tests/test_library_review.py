"""How the person's own work has gone over time (backend/utils/library_questions.py, library_search.py, agent_workflow.py): such a question goes to the tool agent, which lists the files and keeps to the one
library that holds them, instead of the one-pass retrieval's three nearest passages across every library (a real answer about one job came back mixed with a cover letter and work from another, 2026-11-18)."""
import pytest
from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw
from backend.services import library_search as ls
from backend.services import native_tools
from backend.tests.test_tool_agent_node import GROUNDING_CHECK_MARKER, _llm_response, _setup_github_repo, _state, run_async
from backend.utils import library_questions as lq
from backend.utils import llm_turns

OWN_RECORD = [
    "how do you think my work has progressed since ive been at Principal/PFG?",          # the real question, typos and all
    "How has my work progressed since I started at the new job?",
    "review my progress at work over the last seven weeks",
    "how have I grown over the last few months?",
    "what have I accomplished since I joined the team",
    "walk me through my last seven weeks",
    "summarize my time at Principal so far",
    "recap my progress this year",
    "what did I get done over the last six weeks?",
    "how has my performance changed since January",
    "what's my track record at this job looking like",
]
NOT_OWN_RECORD = [
    "add a progress bar to the dashboard", "how do I make progress on a hard problem", "fix the progress indicator in Chat.tsx", "review the repo for the progress function",
    "how has the weather changed since January", "what is the progress of the pull request", "hello there", "tell me about the history of Rome", "what does my cover letter say",
    "write a poem about growth", "", "   ", "how do I improve my squat", "run the tests and tell me how my branch is doing",
    # these WOULD read as "how has my work gone over time" but for the software words, which keep a coding message on the code path
    "how has my work on the repo progressed since Monday", "review my progress on Chat.tsx over the last few weeks", "what have I accomplished on this branch since last week",
]


@pytest.mark.parametrize("text", OWN_RECORD)
def test_a_question_about_how_my_own_work_has_gone_reads_as_one(text):
    assert lq.asks_about_own_record(text) is True, text


@pytest.mark.parametrize("text", NOT_OWN_RECORD)
def test_everything_else_does_not_and_software_words_keep_a_coding_message_out(text):
    assert lq.asks_about_own_record(text) is False, text
    assert lq.asks_about_own_record(None) is False


# ---- the library argument -----------------------------------------------------------------------------------------------------------------------

def test_a_named_library_narrows_the_scope_and_one_outside_it_is_an_error_that_never_widens():
    assert ls.narrow_to_library(["weekly", "letters", "All"], "weekly") == (["weekly"], None)
    assert ls.narrow_to_library(["weekly", "letters"], None) == (["weekly", "letters"], None)
    assert ls.narrow_to_library(["weekly", "letters"], "All") == (["weekly", "letters"], None)
    assert ls.narrow_to_library(["weekly", "letters"], {"$ne": None}) == (["weekly", "letters"], None)          # a name is a string, never a query operator
    scope, error = ls.narrow_to_library(["weekly"], "someone_elses")
    assert scope == ["weekly"] and error.startswith("ERROR") and "weekly" in error and "someone_elses" in error
    assert ls.narrow_to_library([], "weekly")[0] == []


class _Rows(list):
    def sort(self, *args, **kwargs):
        return self


class _FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.scopes = []

    def aggregate(self, pipeline):
        scope = pipeline[0]["$match"]["affiliate"]["$in"]
        self.scopes.append(list(scope))
        counts = {}
        for r in self.rows:
            if r["affiliate"] in scope:
                key = (r["source"], r["affiliate"])
                counts[key] = counts.get(key, 0) + 1
        return [{"_id": {"source": s, "library": a}, "passages": n} for (s, a), n in sorted(counts.items())]


ROWS = [
    {"source": "week-2026-09-14.json", "affiliate": "kb_weekly"},
    {"source": "week-2026-09-21.json", "affiliate": "kb_weekly"},
    {"source": "Cover Letter.pdf", "affiliate": "Affiliate_C"},
    {"source": "Beths Letter of Recommendation.pdf", "affiliate": "Affiliate_C"},
]


def test_the_listing_can_be_kept_to_one_library(monkeypatch):
    fake = _FakeCollection(ROWS)
    monkeypatch.setattr(ls, "_collection", lambda: fake)
    everything = ls.list_for_agent(["kb_weekly", "Affiliate_C"])
    assert "Cover Letter.pdf" in everything and "week-2026-09-14.json" in everything
    only = ls.list_for_agent(["kb_weekly", "Affiliate_C"], library="kb_weekly")
    assert "week-2026-09-14.json (library kb_weekly" in only and "Cover Letter.pdf" not in only and "in library kb_weekly" in only
    assert fake.scopes[-1] == ["kb_weekly"]
    refused = ls.list_for_agent(["kb_weekly"], library="Affiliate_C")
    assert refused.startswith("ERROR") and "Cover Letter" not in refused


def test_a_search_can_be_kept_to_one_library_and_cannot_reach_another(monkeypatch):
    seen = []

    def fake_vector(query, scope, k=3):
        seen.append(list(scope))
        return []

    monkeypatch.setattr(ls, "vector_docs", fake_vector)
    monkeypatch.setattr(ls, "graph_facts", lambda *a, **k: seen.append(("graph", list(a[1]))) or [])
    ls.search_for_agent("my progress", ["kb_weekly", "Affiliate_C"], library="kb_weekly")
    assert seen == [["kb_weekly"]]
    seen.clear()
    out = ls.search_for_agent("my progress", ["kb_weekly"], library="Affiliate_C")
    assert out.startswith("ERROR") and seen == [], "a library the person may not read is refused before any search"


def test_the_graph_facts_stay_inside_the_chosen_library_too(monkeypatch):
    graph_scopes = []
    monkeypatch.setattr(ls, "vector_docs", lambda q, scope, k=3: [ls.Document(page_content="text", metadata={"source": "week.json", "affiliate": "kb_weekly"})])
    monkeypatch.setattr(ls.graph_db, "scoped_context", lambda q, scope, hops=2: graph_scopes.append(list(scope)) or [])
    ls.search_for_agent("my progress", ["kb_weekly", "Affiliate_C"], library="kb_weekly")
    assert graph_scopes == [["kb_weekly"]]


def test_the_tools_declare_the_library_argument_and_it_is_optional():
    for name in ("search_library", "list_library_files"):
        props = native_tools.REGISTRY[name]["parameters"]["properties"] if "parameters" in native_tools.REGISTRY[name] else native_tools.REGISTRY[name]["properties"]
        assert "library" in props, name
    assert native_tools.REGISTRY["search_library"]["required"] == ["query"]


# ---- the route ----------------------------------------------------------------------------------------------------------------------------------

def plan_for(question, *, scope=("kb_weekly", "Affiliate_C"), username="jack", flags=None, **extra):
    state = {"username": username, "messages": [HumanMessage(content=question)], "target_scope": list(scope), "reasoner_flags": flags if flags is not None else {"needs_retrieval": True}, **extra}
    return aw.build_agent_plan("conversational", state)


def test_the_real_question_goes_to_the_tool_agent_and_not_to_the_one_pass_retriever():
    plan = plan_for("how do you think my work has progressed since ive been at Principal/PFG?")
    assert "tool_agent" in plan["agents"] and "retriever" not in plan["agents"], plan
    control = plan_for("what is the process for requesting access to the shared drive?")
    assert "retriever" in control["agents"] and "tool_agent" not in control["agents"], "an ordinary retrieval question keeps the cheap one-pass path"


@pytest.mark.parametrize("extra", [
    {"scope": ()},                                                  # no library to read
    {"scope": ("All",)},                                            # "All" is never a library
    {"username": "guest-recruiter@example.com"},                    # guests do not take the dear path for this
    {"attachment_summaries": ["a file attached to this turn"]},     # answered from the attachment
    {"rag_enabled": False},                                         # retrieval switched off
])
def test_the_route_is_only_taken_when_there_is_a_library_a_signed_in_person_and_nothing_attached(extra):
    plan = plan_for("how has my work progressed since I started at Principal?", **extra)
    assert "tool_agent" not in plan["agents"], extra


def test_a_coding_question_that_mentions_progress_is_not_taken_for_it():
    plan = plan_for("how is my progress on the repo going", flags={"needs_retrieval": True})
    assert "tool_agent" not in plan["agents"]


def test_the_reason_is_counted_like_every_other_route_reason():
    assert "library_review" in llm_turns.ROUTE_REASONS
    assert "route_library_review_turns" in llm_turns.FIELDS and "route_alone_library_review_turns" in llm_turns.FIELDS
    assert "route_notool_library_review_turns" in llm_turns.FIELDS


# ---- through the tool agent ---------------------------------------------------------------------------------------------------------------------

def _scoped_state(question, scope):
    state = _state(question)
    state["target_scope"] = scope
    return state


@run_async
async def test_the_agent_is_told_to_keep_to_the_job_and_leave_letters_out_and_a_library_call_is_kept_inside_that_library(monkeypatch):
    _setup_github_repo(monkeypatch)
    seen = []

    def fake_vector(query, scope, k=3):
        seen.append(list(scope))
        return [ls.Document(page_content="shipped the export", metadata={"source": "week-2026-09-14.json", "affiliate": "kb_weekly"})]

    monkeypatch.setattr(ls, "vector_docs", fake_vector)
    monkeypatch.setattr(ls, "graph_facts", lambda *a, **k: [])
    prompts = []
    responses = [
        _llm_response(action="query", purpose="Weekly reports only", tool_action="search_library", args={"query": "progress", "library": "kb_weekly"}),
        _llm_response(action="query", purpose="Wrong library", tool_action="search_library", args={"query": "progress", "library": "not_mine"}),
        _llm_response(action="final", answer="Over the weeks you shipped the export.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        prompts.append(prompt)
        return responses[min(len(prompts) - 1, len(responses) - 1)]

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)
    result = await aw.tool_agent_node(_scoped_state("how has my work progressed since I started at Principal?", ["kb_weekly", "Affiliate_C"]))
    assert "shipped the export" in result["content_to_format"]
    assert seen == [["kb_weekly"]], "the search was kept inside the library the model named; the one that is not theirs never reached the search layer"
    first = prompts[0]
    assert "KEEP TO WHAT THE QUESTION IS ABOUT" in first and "cover letter" in first and "never mix in work from another employer" in first
    assert "no library called" in prompts[2] and "not_mine" in prompts[2], "the model is told which libraries are its own"
