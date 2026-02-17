from __future__ import annotations

import hmac
import re

from fastapi import HTTPException, status

from update_server.config import ADMIN_API_KEY, ALLOW_UNAUTHENTICATED_UPLOADS

SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def require_admin_auth_or_404(x_api_key: str | None) -> None:
    if ADMIN_API_KEY:
        if not hmac.compare_digest(x_api_key or "", ADMIN_API_KEY):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return

    if not ALLOW_UNAUTHENTICATED_UPLOADS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload endpoint disabled: ADMIN_API_KEY is not configured",
        )


def validate_filename(filename: str) -> str:
    if not SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid filename: {filename}")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid filename: {filename}")
    return filename
