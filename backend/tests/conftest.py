import pytest


class _RealEmbeddingCallBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def block_real_embedding_calls(monkeypatch):
    """Global safety net for embeddings, mirroring block_real_llm_calls below. The fixture this
    replaced patched langchain_google_genai.GoogleGenerativeAIEmbeddings directly, but
    embedding_utils.py already imports that class into its own module namespace at import time
    (`from langchain_google_genai import GoogleGenerativeAIEmbeddings`) — patching the original
    module's attribute afterward never reaches that already-bound local reference, so this suite
    was structurally unprotected against a real, billed embedding call the moment any code path
    called embed_text() without separately mocking it. Confirmed live, not hypothetical: a real
    GOOGLE_API_KEY is present in .env. Patching _get_embeddings_client directly is the actual
    choke point every embed_text() call goes through regardless of which module imported it —
    same reasoning as _ensure_initialized below."""
    def _blocked():
        raise _RealEmbeddingCallBlocked(
            "A test attempted a real, billed Gemini embedding API call instead of mocking it — "
            "this is blocked on purpose. Mock the specific module's own embed_text reference "
            "(e.g. monkeypatch.setattr(my_module, 'embed_text', fake_fn), the pattern every "
            "existing test in this suite already uses) instead of letting it fall through to a "
            "real network call."
        )
    monkeypatch.setattr("backend.utils.embedding_utils._get_embeddings_client", _blocked)


class _RealDBCallBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def block_real_db_calls(monkeypatch):
    """Global safety net for MongoDB, same family as block_real_embedding_calls above. get_db()
    (backend/utils/db_utils.py) constructs a real MongoClient directly whenever USE_DB=true — and
    confirmed live in this repo's own .env, it is. Every consuming module does
    `from backend.utils.db_utils import get_db`, binding its own separate reference at import
    time, so patching db_utils.get_db itself would never reach those already-bound references —
    the same gotcha already fixed above for LLM/embedding calls. The actual choke point is the
    MongoClient name get_db() resolves from db_utils.py's OWN module globals at call time;
    patching that blocks every get_db() call regardless of which module holds a reference to it.
    Also resets the module-level _client cache each test, since one real connection would
    otherwise be cached and silently reused by every later test for the rest of the process."""
    monkeypatch.setattr("backend.utils.db_utils._client", None)

    def _blocked(*args, **kwargs):
        raise _RealDBCallBlocked(
            "A test attempted a real MongoDB connection via get_db() instead of mocking it — "
            "this is blocked on purpose. Mock the specific module's own get_db reference (e.g. "
            "monkeypatch.setattr(my_module, 'get_db', lambda: fake_db_or_None), the pattern most "
            "existing tests in this suite already use) instead of letting it fall through to a "
            "real database."
        )

    monkeypatch.setattr("backend.utils.db_utils.MongoClient", _blocked)


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
