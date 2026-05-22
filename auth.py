"""Shared admin-auth dependency for FastAPI routers.

Round-2 finding A2 surfaced a regression: four routers each rolled their
own admin-auth pattern that captured `LOCALLYAI_ADMIN_KEY` at module
import. That made admin-key rotation invisible to those routers and
blocked the startup-fail-closed gate (the routers RuntimeError'd during
api.py's import chain before the gate could ever run).

Phase-1 fixed each router in-place. This module exists so future
admin-guarded endpoints inherit the correct per-request env read by
default — adding a new admin endpoint is `Depends(admin_auth_dep())`,
not a 15-line copy of the same auth scaffolding.
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_security = HTTPBearer()


def admin_auth_dep():
    """Return a FastAPI dependency that reads LOCALLYAI_ADMIN_KEY per
    request and validates the bearer credential with compare_digest.

    Usage:
        from auth import admin_auth_dep
        from fastapi import Depends

        @router.get("/protected")
        def handler(key: str = Depends(admin_auth_dep())):
            ...
    """
    def _auth(creds: HTTPAuthorizationCredentials = Security(_security)) -> str:
        admin_key = os.environ.get("LOCALLYAI_ADMIN_KEY", "")
        if not admin_key or not hmac.compare_digest(creds.credentials, admin_key):
            raise HTTPException(status_code=403, detail="Invalid admin key")
        return creds.credentials
    return _auth
