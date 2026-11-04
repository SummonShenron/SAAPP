"""A tripwire for the consent audit (docs/consent-audit.md). Nothing here proves the Privacy page is complete; it makes two kinds of drift fail loudly instead of silently:
1. a service that the code really talks to must be named on the Privacy page (so adding one forces someone to update the page);
2. a new place that creates a model client must be reviewed (so a new route for a person's words to a provider cannot appear unnoticed).
When one of these fails: decide who chooses the new thing, what it receives, update local/src/pages/Privacy.tsx and docs/consent-audit.md, then update the list below."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRIVACY = (ROOT / "local" / "src" / "pages" / "Privacy.tsx").read_text(encoding="utf-8")
TERMS = (ROOT / "local" / "src" / "pages" / "Terms.tsx").read_text(encoding="utf-8")


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


# (file, what shows the code talks to the service, what the Privacy page must say)
DISCLOSED = [
    ("backend/models/claude_llm.py", "anthropic", "Anthropic"),
    ("backend/models/openai_llm.py", "openai", "OpenAI"),
    ("backend/models/models.py", "ChatGoogleGenerativeAI", "Gemini"),
    ("backend/services/web_search.py", "api.tavily.com", "Tavily"),
    ("backend/services/browser_tool.py", "browserless", "Browserless"),
    ("backend/services/maps_tools.py", "maps", "Google Maps"),
    ("backend/services/image_gen.py", "genai.Client", "image"),
    ("backend/utils/embedding_utils.py", "gemini-embedding", "embedding"),
    ("backend/services/github_service.py", "api.github.com", "GitHub"),
    ("backend/utils/runner_jobs.py", "runner", "runner"),
    ("backend/auth/isolation_auth.py", "jwks", "Clerk"),
]


def test_every_service_the_code_talks_to_is_named_on_the_privacy_page():
    for path, evidence, phrase in DISCLOSED:
        assert evidence.lower() in read(path).lower(), f"{path} no longer mentions {evidence!r}: update this list"
        assert phrase in PRIVACY, f"{path} talks to a service the Privacy page does not name ({phrase!r})"


# Every file that builds a client for a language model or an embedding service. A new entry means a new route for a person's words to a provider.
MODEL_CLIENT_FILES = {
    "backend/models/models.py",
    "backend/models/openai_llm.py",
    "backend/services/image_gen.py",
    "backend/services/orchestrator.py",
    "backend/services/search.py",
    "backend/utils/embedding_utils.py",
}
_CLIENT = re.compile(r"\b(ClaudeLLM\(|ChatOpenAI\(|ChatGoogleGenerativeAI\(|GoogleGenerativeAIEmbeddings\(|genai\.Client\(|anthropic\.(?:Async)?Anthropic\(|openai\.(?:Async)?OpenAI\()")


def test_a_new_place_that_builds_a_model_client_has_to_be_reviewed_first():
    found = set()
    for path in list((ROOT / "backend").rglob("*.py")) + [ROOT / "app.py"]:
        relative = path.relative_to(ROOT).as_posix()
        if "/tests/" in relative or "__pycache__" in relative or relative.startswith("backend/evals/"):
            continue          # the evals call models with made-up and public text, never a person's
        if _CLIENT.search(path.read_text(encoding="utf-8", errors="ignore")):
            found.add(relative)
    found.discard("backend/models/claude_llm.py")          # defines the class itself
    assert found <= MODEL_CLIENT_FILES, f"new model client in {sorted(found - MODEL_CLIENT_FILES)}: review who chooses it and what it receives (docs/consent-audit.md), then add it here"


def test_the_privacy_page_says_the_claude_readings_need_a_claude_choice_as_the_code_does():
    assert "only if you have chosen Claude for your Tool model or your Execute model" in PRIVACY
    assert "If you have\n            not chosen Claude, no second reading is made".replace("\n            ", " ") in PRIVACY.replace("\r\n", "\n").replace("\n            ", " ")
    models = read("backend/models/models.py")
    assert "def plan_review_llm" in models and "def review_llm" in models
    agent = read("backend/services/agent_workflow.py")
    assert 'claude_may_read = state.get("tool_model") in code_plan.ANTHROPIC_CHOICES or tool_model_choice in code_plan.ANTHROPIC_CHOICES' in agent
    summary = read("backend/utils/conversation_summary.py")
    assert 'and _chose_claude(username)' in summary


def test_the_terms_say_what_sonic_is_not_and_that_a_paid_option_is_only_planned():
    flat = re.sub(r"\s+", " ", TERMS)
    assert "not a doctor, therapist, lawyer or financial adviser" in flat and "not a crisis service" in flat
    assert "have not yet been reviewed by a clinician" in flat
    assert "planned but is not available" in flat and "nothing in these Terms promises one, a price or a date" in flat
    assert "only when you press Apply" in flat and "approve that specific one" in flat


def test_a_guest_cannot_list_the_conversations_every_guest_shares():
    """All guests share one identity and one public token (backend/auth/isolation_auth.py), and guest conversations are saved under it, so listing "their" conversations would list everyone's."""
    source = read("app.py")
    block = source[source.index('@app.get("/api/conversations")'):source.index('@app.get("/api/conversations/{session_id}")')]
    assert "if is_guest_username(username):" in block and "return []" in block
    assert block.index("is_guest_username(username)") < block.index("list_user_conversation_summaries")      # decided before anything is read
