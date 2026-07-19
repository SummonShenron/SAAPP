# backend/utils/logger.py
import logging
import sys

def setup_logging():
    print("DEBUG: Logger setup is executing!")
    logger = logging.getLogger("SASS Logger")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        # 2. Define your desired format
        formatter = logging.Formatter(
            '%(levelname)s - %(message)s'
        )
        # 3. Add a stream handler to ensure it outputs to your terminal
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        # Avoid adding the handler multiple times if setup is called again
        if not logger.handlers:
            logger.addHandler(handler)
            logger.propagate = False
        # 4. Silence noisy third-party libraries
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