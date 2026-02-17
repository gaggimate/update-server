from __future__ import annotations

import re

from fastapi import HTTPException, status

from update_server.config import ALLOWED_CHANNELS, ALLOWED_TARGETS

SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def require_valid_channel(channel: str) -> None:
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Channel not valid")


def require_valid_target(target: str) -> None:
    if target not in ALLOWED_TARGETS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target not valid")


def require_valid_version(version: str) -> None:
    if not SAFE_PATH_SEGMENT_RE.fullmatch(version):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Version not valid")
    if ".." in version or "/" in version or "\\" in version:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Version not valid")
