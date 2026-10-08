"""Google SSO for the API.

The Next.js app signs users in with Google (Auth.js). On sign-in it sends the Google ID token to
POST /auth/google; we verify it with Google's public keys, apply the optional allow-list and return
a short-lived app JWT. Every other endpoint requires that JWT as a Bearer token.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel

from shopops.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
_google_request = google_requests.Request()


class GoogleLogin(BaseModel):
    id_token: str


class User(BaseModel):
    email: str
    name: str | None = None
    picture: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    expires_at: int
    user: User


def _csv(value: str) -> set[str]:
    return {v.strip().lower() for v in value.split(",") if v.strip()}


def _allowed(email: str) -> bool:
    s = get_settings()
    emails, domains = _csv(s.allowed_emails), _csv(s.allowed_domains)
    if not emails and not domains:
        return True
    return email.lower() in emails or email.lower().rsplit("@", 1)[-1] in domains


@router.post("/google", response_model=TokenResponse)
def login_with_google(body: GoogleLogin) -> TokenResponse:
    s = get_settings()
    if not s.google_client_id:
        raise HTTPException(500, "GOOGLE_CLIENT_ID is not configured on the API")
    try:
        claims = google_id_token.verify_oauth2_token(body.id_token, _google_request, s.google_client_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid Google token: {exc}") from exc
    if not claims.get("email_verified"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Google account email is not verified")
    if not _allowed(claims["email"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This Google account is not allowed")

    user = User(email=claims["email"], name=claims.get("name"), picture=claims.get("picture"))
    exp = datetime.now(timezone.utc) + timedelta(hours=s.app_jwt_ttl_hours)
    token = jwt.encode({"sub": user.email, "name": user.name, "picture": user.picture, "exp": exp}, s.app_jwt_secret, algorithm="HS256")
    return TokenResponse(access_token=token, expires_at=int(exp.timestamp()), user=user)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(creds.credentials, get_settings().app_jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"}) from exc
    return User(email=claims["sub"], name=claims.get("name"), picture=claims.get("picture"))


@router.get("/me", response_model=User)
def me(user: User = Depends(current_user)) -> User:
    return user
