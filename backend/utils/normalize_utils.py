def ensure_str(x):
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        try:
            return "".join(map(str, x))
        except Exception:
            return " ".join(map(str, x))
    return str(x)
