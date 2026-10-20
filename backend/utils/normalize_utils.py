def chunk_text(chunk) -> str:
    """The text of one streamed model chunk: a string as is, a message's content as text, and "" for a chunk with no content. Never the message object itself:
    the last chunk of a stream often has `content=[]`, and `content or chunk` once printed the whole object ("content=[] additional_kwargs={} ...") into a reply."""
    if isinstance(chunk, str):
        return chunk
    content = getattr(chunk, "content", None)
    if not content:
        return ""
    return ensure_str(content)


def ensure_str(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        # Common LLM content-block shape: {"type": "text", "text": "..."}
        if "text" in x:
            return str(x.get("text", ""))
        return str(x)
    if isinstance(x, (list, tuple)):
        parts = []
        for item in x:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(x)
