# backend/models/models.py
import aiohttp
import asyncio
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage

class LazyLLM:
    def __init__(self, model_name="gemini-3.5-flash", fallback_model=None, temperature=0.7, max_retries=1):
        self.model_name = model_name
        self.fallback_model = fallback_model
        self.temperature = temperature
        self.max_retries = max_retries
        self._real_llm = None
        self.dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"

    def _ensure_initialized(self):
        if self._real_llm is None and not self.dev_mode:
            connector = aiohttp.TCPConnector(
                ssl=False,
                use_dns_cache=False,
                resolver=aiohttp.resolver.ThreadedResolver()
            )
            session = aiohttp.ClientSession(connector=connector)
            
            primary = ChatGoogleGenerativeAI(
                model=self.model_name,
                api_key=os.getenv("GOOGLE_API_KEY"),
                temperature=self.temperature,
                max_retries=self.max_retries,
                streaming=True,
                http_client=session
            )

            # Enable auto-fallback if specified
            if self.fallback_model:
                fallback = ChatGoogleGenerativeAI(
                    model=self.fallback_model,
                    api_key=os.getenv("GOOGLE_API_KEY"),
                    temperature=self.temperature,
                    max_retries=1,
                    streaming=True,
                    http_client=session
                )
                self._real_llm = primary.with_fallbacks([fallback])
            else:
                self._real_llm = primary

    def invoke(self, *args, **kwargs):
        if self.dev_mode:
            return AIMessage(content="[DEV_MODE] Mock LLM Response. System is working!")
        self._ensure_initialized()
        try:
            return self._real_llm.invoke(*args, **kwargs)
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                print(f"CRITICAL: {self.model_name} API overloaded (503). Returning fallback note.")
                return AIMessage(content="[System Note: The AI model is temporarily experiencing high traffic. Please try again in a moment.]")
            raise e

    async def astream(self, *args, **kwargs):
        if self.dev_mode:
            yield AIMessage(content="[DEV_MODE] Mock streaming response. System is working!")
            return

        self._ensure_initialized()
        try:
            async for chunk in self._real_llm.astream(*args, **kwargs):
                yield chunk
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                print(f"CRITICAL: {self.model_name} API overloaded (503) during streaming.")
                yield AIMessage(content="[System Note: The AI model is temporarily experiencing high traffic. Please try again in a moment.]")
            else:
                raise e

    def __getattr__(self, name):
        if self.dev_mode:
            return lambda *args, **kwargs: AIMessage(content="Mocked method call.")
        self._ensure_initialized()
        return getattr(self._real_llm, name)

# Primary reasoning LLM (gemini-3.5-flash with auto-fallback to gemini-3.1-flash-lite on 503 errors)
llm = LazyLLM(model_name="gemini-3.5-flash", fallback_model="gemini-3.1-flash-lite", temperature=0.7)

# Fast utility LLM for grading, query rewriting, classification, and JSON parsing
lite_llm = LazyLLM(model_name="gemini-3.1-flash-lite", temperature=0.2, max_retries=1)