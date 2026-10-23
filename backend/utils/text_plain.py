"""Small text clean-ups shared by the "is this a question about Sonic itself" checks."""

_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2026": "..."})


def plain_quotes(text: str) -> str:
    """Curly apostrophes and quotes (what a phone or Mac keyboard types: "you’ve", "what’s") as the straight ones the question patterns are written with. Without this
    "what you’ve been up to" slipped past every one of them."""
    return (text or "").translate(_QUOTES)
