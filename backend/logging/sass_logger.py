import os
import logging
import sys

def setup_logging():
    logger = logging.getLogger("SASS Logger")
    env_log_level = os.getenv("LOG_LEVEL")

    if env_log_level:
        level = getattr(logging, env_log_level.upper(), logging.INFO)
    else:
        is_local = (
            os.getenv("LOCAL_DEV", "false").lower() == "true" or 
            os.getenv("DEV_MODE", "false").lower() == "true"
        )
        level = logging.DEBUG if is_local else logging.INFO

    logger.setLevel(level)

    if not logger.handlers:
        formatter = logging.Formatter('%(levelname)s - %(message)s')
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False

    # Ensure logging still works even if other libraries attach handlers later.
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        fallback = logging.StreamHandler(sys.stderr)
        fallback.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
        logger.addHandler(fallback)

    noisy_loggers = [
        "uvicorn.access",
        "httpx", "httpcore", "h11", "anyio", "asyncio",
        "transformers", "huggingface_hub", "sentence_transformers", "chromadb"
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)

    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
    logging.getLogger("uvicorn").setLevel(logging.ERROR)

    return logger