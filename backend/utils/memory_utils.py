
import os
import json
from backend.utils.app_utils import load_saved_conversations

# ========== TBD =============
    
def flatten_saved_conversations(username: str):
    saved = load_saved_conversations()
    if username not in saved:
        return []

    flattened = []
    for convo in saved[username]:
        text = "\n".join(
            f"{msg['type']}: {msg['content']}"
            for msg in convo["messages"]
        )
        flattened.append({
            "title": convo["title"],
            "timestamp": convo["timestamp"],
            "text": text
        })
    return flattened    

def grade_memory_docs(memory_docs, question):
    """Simple keyword-based grading for saved conversation memory."""
    question_lower = question.lower()
    graded = []

    for m in memory_docs:
        if any(word in m["text"].lower() for word in question_lower.split()):
            graded.append(m)

    return graded