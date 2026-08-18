import httpx
import logging

logger = logging.getLogger("SAAPP Ops Tool")

async def ops_context_tool() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://erragent/api/ops/context")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch ops context: {e}")
        return {}