from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from packaging.version import InvalidVersion, Version

from update_server.config import UPDATE_STORAGE_DIR


GIT_DESCRIBE_VERSION_RE = re.compile(
    r"^v?(?P<base>\d+\.\d+\.\d+)(?:-(?P<distance>\d+)-g(?P<commit>[0-9a-fA-F]+))?$"
)


def channel_dir(channel: str) -> Path:
    return UPDATE_STORAGE_DIR / "updates" / channel


def release_dir(channel: str, version: str) -> Path:
    return channel_dir(channel) / version


def release_metadata_path(channel: str, version: str) -> Path:
    return release_dir(channel, version) / "release.json"


def parse_version(raw: str) -> tuple[int, Any]:
    match = GIT_DESCRIBE_VERSION_RE.fullmatch(raw)
    if match:
        base = match.group("base")
        distance = match.group("distance")
        parsed = f"{base}.post{distance}" if distance is not None else base
        return (1, Version(parsed))

    try:
        return (1, Version(raw.lstrip("v")))
    except InvalidVersion:
        return (0, raw)


def list_release_versions(channel: str) -> list[str]:
    base = channel_dir(channel)
    if not base.exists():
        return []

    versions = [entry.name for entry in base.iterdir() if entry.is_dir() and (entry / "release.json").exists()]
    return sorted(versions, key=parse_version, reverse=True)


def load_release(channel: str, version: str) -> dict[str, Any]:
    path = release_metadata_path(channel, version)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release not found")
    return json.loads(path.read_text())


def save_release(channel: str, version: str, payload: dict[str, Any]) -> None:
    dst = release_dir(channel, version)
    dst.mkdir(parents=True, exist_ok=True)
    release_metadata_path(channel, version).write_text(json.dumps(payload, indent=2, sort_keys=True))
