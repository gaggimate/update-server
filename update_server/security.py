from __future__ import annotations

import hmac
import re
from typing import Annotated

from fastapi import Header, HTTPException, status

from update_server.config import ADMIN_API_KEY, ALLOW_UNAUTHENTICATED_UPLOADS

# \Z instead of $: "$" also matches before a trailing newline.
SAFE_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def is_admin_authorized(x_api_key: str | None) -> bool:
    if ADMIN_API_KEY:
        # Compare as bytes: compare_digest() raises on non-ASCII str input.
        supplied = (x_api_key or "").encode("utf-8", "surrogateescape")
        return hmac.compare_digest(supplied, ADMIN_API_KEY.encode("utf-8"))
    return ALLOW_UNAUTHENTICATED_UPLOADS


def require_admin_auth_or_404(x_api_key: str | None) -> None:
    if not is_admin_authorized(x_api_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def require_admin(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Route dependency. AdminGuardMiddleware already rejects before the body is read; this is the backstop."""
    require_admin_auth_or_404(x_api_key)


def validate_filename(filename: str) -> str:
    if not SAFE_FILENAME_RE.fullmatch(filename) or ".." in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    return filename
