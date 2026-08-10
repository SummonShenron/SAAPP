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
