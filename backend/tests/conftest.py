import pytest
from unittest.mock import MagicMock

@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    monkeypatch.setattr(
        "langchain_google_genai.GoogleGenerativeAIEmbeddings",
        MagicMock()
    )


class _RealLLMCallBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def block_real_llm_calls(monkeypatch):
    """Global safety net: makes it structurally impossible for a test to make a real, billed
    call to the Gemini API, regardless of whether that specific test remembers to mock its own
    LLM call. Every real call path in this app (llm, lite_llm, lite_llm_deep, stream_llm,
    embedded_llm, and everything get_chat_llm/get_stream_llm return) is a models.LazyLLM
    instance, and every one of them funnels through _ensure_initialized() before ever
    constructing a real client — patching it here at the class level is one choke point that
    covers all of them. A test that already replaces an instance's own .ainvoke/.invoke (the
    normal pattern throughout this suite) never reaches this at all, since that replacement
    shadows the class method entirely — this only fires for a call this suite forgot to mock."""
    def _blocked(self):
        # Mirrors the real method's own guard: a test that pre-sets ._real_llm to a fake client
        # directly (test_models.py's own LazyLLM unit tests do exactly this, to exercise
        # invoke/ainvoke/astream's graceful-degradation logic without a real client) must still
        # work unchanged — only an attempt to build a REAL client for the first time is blocked.
        if self._real_llm is None and not self.dev_mode:
            raise _RealLLMCallBlocked(
                "A test attempted a real, billed Gemini API call instead of mocking its LLM — "
                "this is blocked on purpose. Mock the specific .ainvoke()/.invoke() call this "
                "test actually exercises (see existing tests in this suite for the pattern) "
                "instead of letting it fall through to a real network call."
            )

    monkeypatch.setattr("backend.models.models.LazyLLM._ensure_initialized", _blocked)
