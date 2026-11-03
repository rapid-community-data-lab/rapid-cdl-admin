import time

import jwt
from fastapi import HTTPException, status

from .config import Settings


class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings

    def verify(self, authorization: str | None) -> dict:
        if not self.settings.jwt_secret:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication is not configured on the ingester.")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing authentication token.")
        token = authorization[len("Bearer "):].strip()
        try:
            payload = jwt.decode(token, self.settings.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired authentication token.")
        if payload.get("role") not in self.settings.allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not allowed to ingest data.")
        return payload

    def service_token(self, role: str) -> str:
        now = int(time.time())
        claims = {"id": "data-ingester", "email": "data-ingester", "role": role, "iat": now, "exp": now + 600}
        return jwt.encode(claims, self.settings.jwt_secret, algorithm="HS256")
