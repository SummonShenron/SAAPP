import jwt
import requests
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer
import os
from clerk_backend_api import Clerk

import os
import logging

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
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = auth_header.split(" ")[1]
    
    try:
        # 1. Get header to find the 'kid' (Key ID)
        header = jwt.get_unverified_header(token)
        jwks = get_clerk_public_key()
        
        # 2. Find matching key
        key_data = next(k for k in jwks['keys'] if k['kid'] == header['kid'])
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        
        # 3. Verify
        # Adjust 'audience' if you have one configured, otherwise remove it
        payload = jwt.decode(token, public_key, algorithms=["RS256"])
        return payload 
        
    except Exception as e:
        logger.error(f"Manual JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")