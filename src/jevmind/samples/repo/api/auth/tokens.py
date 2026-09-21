"""Refresh and access tokens."""
import secrets, hashlib

def issue_refresh(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    store(hashlib.sha256(token.encode()).hexdigest(), user_id)
    return token

def rotate_refresh(token: str) -> str:
    row = lookup(hashlib.sha256(token.encode()).hexdigest())
    if row is None or row.used:
        raise InvalidToken("refresh token reuse")
    mark_used(row)
    return issue_refresh(row.user_id)
