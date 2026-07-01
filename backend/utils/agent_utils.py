import json
from datetime import datetime

def format_error_payload(error_message: str) -> str:
    """Serializes an error message into an SSE-compatible JSON format."""
    error_data = {
        "status": "error",
        "message": error_message,
        "timestamp": str(import_datetime().now()) # or your preferred timestamp
    }
    return f"data: {json.dumps(error_data)}\n\n"

# Helper for the timestamp inside the utility
def import_datetime():
    return datetime

def format_final_payload(data: dict) -> str:
    """Serializes data to a server-sent event (SSE) format."""
    import json
    return f"data: {json.dumps(data)}\n\n"

def update_chat_history(history: str, role: str, message: str) -> str:
    """Appends new messages to the history transcript string."""
    entry = f"{role.upper()}: {message}\n"
    return (history or "") + entry