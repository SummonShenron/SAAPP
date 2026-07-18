# backend/models/models.py
import aiohttp
import asyncio
from langchain_google_genai import ChatGoogleGenerativeAI
import os

class LazyLLM:
    def __init__(self):
        self._real_llm = None

    def _ensure_initialized(self):
        if self._real_llm is None:
            # This logic now runs only when someone actually calls .invoke()
            # Since that happens inside an async route, a loop is guaranteed to exist.
            connector = aiohttp.TCPConnector(
                ssl=False,
                use_dns_cache=False,
                resolver=aiohttp.resolver.ThreadedResolver()
            )
            session = aiohttp.ClientSession(connector=connector)
            self._real_llm = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                api_key=os.getenv("GOOGLE_API_KEY"),
                temperature=0.7,
                streaming=True,
                http_client=session
            )

    def __getattr__(self, name):
        # When you call .invoke(), .astream(), etc., this triggers
        self._ensure_initialized()
        return getattr(self._real_llm, name)

# This is the global 'llm' that all your existing routes import
llm = LazyLLM()# backend/models/models.py
import aiohttp
import asyncio
from langchain_google_genai import ChatGoogleGenerativeAI
import os

class LazyLLM:
    def __init__(self):
        self._real_llm = None
        # Toggle this to True in your .env file during development
        self.dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"

    def _ensure_initialized(self):
        if self.dev_mode:
            return # Skip initialization if in dev mode

        if self._real_llm is None:
            # ... keep your existing connection logic here ...
            self._real_llm = ChatGoogleGenerativeAI(...)

    def __getattr__(self, name):
        if self.dev_mode:
            # Return a mock object if in dev mode
            return self._mock_call
        
        self._ensure_initialized()
        return getattr(self._real_llm, name)

    async def _mock_call(self, *args, **kwargs):
        """Simulates an LLM response without calling the API."""
        print("DEBUG: Mock LLM called!")
        return "This is a mock response from the LLM during DEV_MODE. The system is working!"

    def _ensure_initialized(self):
        if self._real_llm is None:
            # This logic now runs only when someone actually calls .invoke()
            # Since that happens inside an async route, a loop is guaranteed to exist.
            connector = aiohttp.TCPConnector(
                ssl=False,
                use_dns_cache=False,
                resolver=aiohttp.resolver.ThreadedResolver()
            )
            session = aiohttp.ClientSession(connector=connector)
            self._real_llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash",
                api_key=os.getenv("GOOGLE_API_KEY"),
                temperature=0.7,
                streaming=True,
                http_client=session
            )

    def __getattr__(self, name):
        # When you call .invoke(), .astream(), etc., this triggers
        self._ensure_initialized()
        return getattr(self._real_llm, name)

# This is the global 'llm' that all your existing routes import
llm = LazyLLM()