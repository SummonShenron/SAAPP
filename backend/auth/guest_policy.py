"""Which write endpoints the shared guest may use, and which write endpoints need no sign-in at all. Everything else must take `get_member_user` (backend/auth/isolation_auth.py).

WHY A LIST. The guest identity is claimed with one header (`X-Principal: guest`), so every endpoint that uses `get_current_user` is reachable by anyone on the internet. The launch probe (2026-11-19) found 70 of 84 signed-in
write endpoints that never mentioned guests: settings including the GitHub token, memory deletion, runner pairing, billing. The default is now "members only"; THIS file is the exception list, written out in full, and
backend/tests/test_guest_policy.py reads the app's real routes and fails for any write endpoint that is in neither place, so a new endpoint cannot be added without deciding.

A write endpoint is in GUEST_WRITES for one of two reasons: the public demo calls it (chat and what hangs off it), or it turns guests away itself (GUEST_HANDLED: a check inside the handler, kept as it was).
"""
from typing import FrozenSet, Tuple

Route = Tuple[str, str]       # (HTTP method, path as the route is declared)

# Endpoints that look at the guest themselves (a guest check inside the handler). Allowed at the door; they decide.
GUEST_HANDLED: FrozenSet[Route] = frozenset({
    ("POST", "/api/log-login"),
    ("POST", "/api/chat"),
    ("POST", "/api/chat/opening"),
    ("POST", "/api/memory/name-aliases"),
    ("POST", "/api/local-edits/render-docx"),
    ("POST", "/api/local-edits/edit-docx"),
    ("PATCH", "/api/open-loops/{loop_id}"),
    ("POST", "/api/local-workspace/sync"),
    ("PATCH", "/api/code-plan"),
    ("POST", "/api/guest/signup-click"),
    ("PUT", "/api/settings/profile"),
    ("PUT", "/api/settings/proactive-opening"),
    ("POST", "/api/local-git/bundle"),
    ("POST", "/api/conversation-summary/compact"),
})

# What the public demo calls besides chat itself: steering, clearing and answering within a conversation, the thumbs, an attachment, managing the guest's own conversations, the welcome card, the demo's Apply / Decline,
# and the toggles on the composer (the chat path ignores a guest's stored values and uses safe defaults, so these write nothing that matters).
GUEST_DEMO_WRITES: FrozenSet[Route] = frozenset({
    ("POST", "/api/chat/steer"),
    ("POST", "/api/chat/clear"),
    ("POST", "/api/chat/choice/{choice_id}"),
    ("POST", "/api/chat/feedback"),
    ("POST", "/api/upload-attachment"),
    ("DELETE", "/api/conversations/{session_id}"),
    ("PATCH", "/api/conversations/{session_id}"),
    ("POST", "/api/welcome-click"),
    ("POST", "/api/local-edits/{proposal_id}/decision"),
    ("PUT", "/api/settings/has-seen-help"),
    ("PUT", "/api/settings/timezone"),
    ("PUT", "/api/settings/rag-mode"),
    ("PUT", "/api/settings/deep-thinking"),
    ("PUT", "/api/settings/plan-mode"),
})

GUEST_WRITES: FrozenSet[Route] = GUEST_HANDLED | GUEST_DEMO_WRITES

# Write endpoints with no sign-in at all, each proving the caller another way: a one-time pairing code, a shared secret, a signature, or (login) nothing worth protecting.
PUBLIC_WRITES: FrozenSet[Route] = frozenset({
    ("POST", "/api/login"),
    ("POST", "/api/runner/pair"),
    ("POST", "/api/v1/webhooks/ingest"),
    ("POST", "/webhooks/github"),
    ("POST", "/api/billing/webhook"),
})
