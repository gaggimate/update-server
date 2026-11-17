from __future__ import annotations

import re

from fastapi import HTTPException, status

from update_server.config import ALLOWED_CHANNELS, ALLOWED_TARGETS

SAFE_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
# "latest" is a route keyword (/releases/latest/{target}/manifest), so it can never be a version.
RESERVED_VERSIONS = {"latest"}
CHIP_FAMILY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,31}\Z")
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}\Z")


def require_valid_channel(channel: str) -> None:
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Channel not valid")


def require_valid_target(target: str) -> None:
    if target not in ALLOWED_TARGETS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target not valid")


def require_valid_version(version: str) -> None:
    if not SAFE_PATH_SEGMENT_RE.fullmatch(version) or ".." in version or version.lower() in RESERVED_VERSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Version not valid")


def require_valid_chip_family(chip_family: str) -> None:
    if not CHIP_FAMILY_RE.fullmatch(chip_family):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chip family not valid")


def require_valid_label(label: str) -> None:
    if not LABEL_RE.fullmatch(label):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Label not valid")
