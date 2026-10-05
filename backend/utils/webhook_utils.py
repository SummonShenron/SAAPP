import hashlib
import hmac


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Checks GitHub's X-Hub-Signature-256 header ("sha256=<hex hmac of the raw body>") against the
    shared webhook secret, in constant time. Without this, anyone who can reach the endpoint can post
    a made-up pull_request event and make the server fetch diffs, run the LLM and comment on a PR
    using a user's token."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
