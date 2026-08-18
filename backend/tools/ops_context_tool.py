import logging
import os

import httpx

logger = logging.getLogger("SAAPP Ops Tool")
ERRAGENT_URL = os.environ["ERRAGENT_URL"].rstrip("/")
ERRAGENT_INGEST_SECRET = os.environ["ERRAGENT_INGEST_SECRET"]


async def ops_context_tool() -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{ERRAGENT_URL}/ops/context",
                headers={"x-ingest-secret": ERRAGENT_INGEST_SECRET},
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        logger.exception("Failed to fetch errAgent ops context")
        return {}