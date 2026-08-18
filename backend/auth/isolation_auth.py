import jwt
import requests
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer
import os
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
        _cached_jwks = requests.get(jwks_url).json()
    return _cached_jwks

async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    principal_hint = request.headers.get("x-principal") or request.headers.get("X-Principal") or request.headers.get("x-user-id")
    normalized_principal = (principal_hint or "").strip()

    if normalized_principal == "guest":
        logger.info("Guest principal override detected. Bypassing JWT verification.")
        return {"sub": "guest-recruiter@example.com", "email": "guest@example.com"}

    if normalized_principal == "guest_bty":
        logger.info("BTY embedded guest principal override detected. Bypassing JWT verification.")
        return {"sub": "guest_bty", "email": "guest_bty@bty.local"}

    if normalized_principal in {"guest_erragent", "guest-erragent"}:
        logger.info("Erragent embedded guest principal override detected. Bypassing JWT verification.")
        return {"sub": "guest_erragent", "email": "guest_erragent@erragent.local"}

    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = auth_header.split(" ")[1]
    
    # 1. GUEST BYPASS: Check for your sandbox token first
    if token == "guest-sandbox-token":
        logger.info("Guest session detected. Bypassing JWT verification.")
        return {"sub": "guest-recruiter@example.com", "email": "guest@example.com"}

    if token == "guest-bty-token":
        logger.info("BTY embedded guest session detected. Bypassing JWT verification.")
        return {"sub": "guest_bty", "email": "guest_bty@bty.local"}

    if token == "guest-erragent-token":
        logger.info("Erragent embedded guest session detected. Bypassing JWT verification.")
        return {"sub": "guest_erragent", "email": "guest_erragent@erragent.local"}

    # 2. Existing JWT verification logic
    try:
        # Get header to find the 'kid' (Key ID)
        header = jwt.get_unverified_header(token)
        jwks = get_clerk_public_key()
        
        # Find matching key
        key_data = next(k for k in jwks['keys'] if k['kid'] == header['kid'])
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        
        # Verify
        payload = jwt.decode(token, public_key, algorithms=["RS256"])
        return payload 
        
    except Exception as e:
        logger.error(f"Manual JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")
    
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