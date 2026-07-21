# backend/models/models.py
import aiohttp
import asyncio
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage

class LazyLLM:
    def __init__(self):
        self._real_llm = None
        # Toggle this to True in your .env file during development
        self.dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"

    def _ensure_initialized(self):
        if self._real_llm is None and not self.dev_mode:
            # Initialize connection only when first requested
            connector = aiohttp.TCPConnector(
                ssl=False,
                use_dns_cache=False,
                resolver=aiohttp.resolver.ThreadedResolver()
            )
            session = aiohttp.ClientSession(connector=connector)
            self._real_llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash",  # Active production model
                api_key=os.getenv("GOOGLE_API_KEY"),
                temperature=0.7,
                streaming=True,
                http_client=session
            )

    def invoke(self, *args, **kwargs):
        if self.dev_mode:
            return AIMessage(content="[DEV_MODE] Mock LLM Response. System is working!")
        
        self._ensure_initialized()
        try:
            return self._real_llm.invoke(*args, **kwargs)
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                print("CRITICAL: Gemini API overloaded (503). Returning fallback text.")
                return AIMessage(content="[System Note: The AI model is temporarily experiencing high traffic. Please try your request again in a moment.]")
            raise e

    async def astream(self, *args, **kwargs):
        if self.dev_mode:
            yield AIMessage(content="[DEV_MODE] Mock streaming response. System is working!")
            return

        self._ensure_initialized()
        try:
            # Yield chunks directly from the actual LangChain generator
            async for chunk in self._real_llm.astream(*args, **kwargs):
                yield chunk
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                print("CRITICAL: Gemini API overloaded (503) during streaming. Returning fallback text.")
                yield AIMessage(content="[System Note: The AI model is temporarily experiencing high traffic. Please try your request again in a moment.]")
            else:
                raise e

    def __getattr__(self, name):
        if self.dev_mode:
            return lambda *args, **kwargs: AIMessage(content="Mocked method call.")
        
        self._ensure_initialized()
        return getattr(self._real_llm, name)

# This is the global 'llm' that all your existing routes import
llm = LazyLLM()