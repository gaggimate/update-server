from __future__ import annotations

from fastapi import HTTPException, status

from update_server.config import ALLOWED_CHANNELS, ALLOWED_TARGETS


def require_valid_channel(channel: str) -> None:
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Channel not valid")


def require_valid_target(target: str) -> None:
    if target not in ALLOWED_TARGETS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target not valid")
