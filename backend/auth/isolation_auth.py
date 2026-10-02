import jwt
import requests
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer
import os
import asyncio
import time
from clerk_backend_api import Clerk
from datetime import datetime, timezone
import logging
from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")
security = HTTPBearer()
# clerk_client = Clerk(bearer_auth=os.environ.get("CLERK_SECRET_KEY"))
_cached_jwks = None

class MockUser:
    def __init__(self, email: str):
        self.sub = email
        self.email = email

def get_clerk_public_key():
    global _cached_jwks
    if _cached_jwks is None:
        # Replace <YOUR_CLERK_FRONTEND_API> with your Clerk Issuer URL
        # You can find this in your Clerk Dashboard under "JWT Templates" 
        # or "API Keys" -> "Issuer"
        jwks_url = f"{os.environ.get('CLERK_ISSUER')}/.well-known/jwks.json"
        _cached_jwks = requests.get(jwks_url, timeout=10).json()
    return _cached_jwks

# How long a Clerk-user-id -> verified-email lookup is reused before re-asking Clerk's API.
_EMAIL_CACHE_TTL_SECONDS = 3600
_email_cache: dict = {}  # clerk user id -> (expires_at_monotonic, verified email or None)


def _lookup_clerk_email(user_id: str):
    """Ask Clerk's backend API for the user's primary *verified* email. Returns None if the
    secret key isn't configured, the call fails, or the user has no verified primary email.
    Clerk's default session token carries no email claim, so this is how the backend learns an
    email it can actually trust instead of believing a request header."""
    secret = os.environ.get("CLERK_SECRET_KEY")
    if not secret:
        logger.error("CLERK_SECRET_KEY is not set and the JWT has no email claim; cannot verify the claimed email principal.")
        return None
    now = time.monotonic()
    cached = _email_cache.get(user_id)
    if cached and cached[0] > now:
        return cached[1]
    try:
        user = Clerk(bearer_auth=secret).users.get(user_id=user_id)
        primary_id = getattr(user, "primary_email_address_id", None)
        email = None
        for addr in (getattr(user, "email_addresses", None) or []):
            if primary_id and addr.id != primary_id:
                continue
            status = getattr(getattr(addr, "verification", None), "status", None)
            status = getattr(status, "value", status)
            if status == "verified":
                email = addr.email_address
                break
    except Exception as e:
        logger.error(f"Clerk user lookup failed for {user_id}: {e}")
        return None  # not cached: a transient failure shouldn't stick for an hour
    _email_cache[user_id] = (now + _EMAIL_CACHE_TTL_SECONDS, email)
    return email


def _verify_clerk_jwt(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        jwks = get_clerk_public_key()
        key_data = next(k for k in jwks['keys'] if k['kid'] == header['kid'])
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        return jwt.decode(token, public_key, algorithms=["RS256"])
    except Exception as e:
        logger.error(f"Manual JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")


async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    principal_hint = request.headers.get("x-principal") or request.headers.get("X-Principal") or request.headers.get("x-user-id")
    normalized_principal = (principal_hint or "").strip()

    # Guest paths (intentional, unchanged): the sandbox and the embedded BTY widget have no Clerk
    # session, and the browser tool seeds the sandbox identity (GUEST_SEED_INIT_SCRIPT).
    if normalized_principal == "guest":
        logger.info("Guest principal override detected. Bypassing JWT verification.")
        return {"sub": "guest-recruiter@example.com", "email": "guest@example.com"}

    if normalized_principal == "guest_bty":
        logger.info("BTY embedded guest principal override detected. Bypassing JWT verification.")
        return {"sub": "guest_bty", "email": "guest_bty@bty.local"}

    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header.split(" ", 1)[1]

    # 1. GUEST BYPASS: Check for your sandbox token first
    if token == "guest-sandbox-token":
        logger.info("Guest session detected. Bypassing JWT verification.")
        return {"sub": "guest-recruiter@example.com", "email": "guest@example.com"}

    if token == "guest-bty-token":
        logger.info("BTY embedded guest session detected. Bypassing JWT verification.")
        return {"sub": "guest_bty", "email": "guest_bty@bty.local"}

    # 2. Clerk JWT verification
    payload = _verify_clerk_jwt(token)

    # 3. An email principal header is only a *claim*: it's honored when it matches the email Clerk
    # vouches for (the JWT's email claim, else Clerk's API), never on its own and never to fill in
    # a missing claim. The email stays the identity key (sub) so existing per-user data keeps resolving.
    if "@" in normalized_principal:
        verified_email = payload.get("email")
        if not verified_email and payload.get("sub"):
            verified_email = await asyncio.to_thread(_lookup_clerk_email, payload["sub"])
        if not verified_email:
            raise HTTPException(status_code=401, detail="Authentication failed")
        if verified_email.strip().lower() != normalized_principal.lower():
            logger.warning(f"Principal header {normalized_principal!r} does not match the verified identity of {payload.get('sub')}; rejecting.")
            raise HTTPException(status_code=403, detail="Principal does not match authenticated user")
        return {**payload, "sub": verified_email, "email": verified_email}

    return payload

def record_login_event(user_id: str, email: str, is_guest: bool = False, ip_address: str = None):
    """
    Writes a single login document to MongoDB.
    """
    try:
        db = get_db()
        if db is None:
            return

        db["login_logs"].insert_one({
            "user_id": user_id,
            "email": email,
            "is_guest": is_guest,
            "ip_address": ip_address,
            "logged_at": datetime.now(timezone.utc)
        })
        logger.info(f"Recorded login for: {email} (Guest={is_guest})")
    except Exception as e:
        logger.error(f"Failed to record login in MongoDB: {e}")