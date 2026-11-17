from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from packaging.version import Version

from update_server.config import UPDATE_STORAGE_DIR

logger = logging.getLogger(__name__)

GIT_DESCRIBE_VERSION_RE = re.compile(
    r"^v?(?P<base>\d+\.\d+\.\d+)(?:-(?P<distance>\d+)-g(?P<commit>[0-9a-fA-F]+))?$"
)
MAX_VERSION_LENGTH = 128

# Safety net for the in-memory channel index when another process changes storage on a filesystem with
# coarse timestamps. Changes made by this process, or visible through the directory mtime, apply immediately.
INDEX_CACHE_TTL_SECONDS = 15.0
STALE_STAGING_SECONDS = 3600

_index_cache: dict[str, tuple[int, float, list[tuple[str, dict[str, Any]]]]] = {}


class CorruptReleaseError(Exception):
    pass


class ReleaseTargetExistsError(Exception):
    pass


def channel_dir(channel: str) -> Path:
    return UPDATE_STORAGE_DIR / "updates" / channel


def release_dir(channel: str, version: str) -> Path:
    return channel_dir(channel) / version


def release_metadata_path(channel: str, version: str) -> Path:
    return release_dir(channel, version) / "release.json"


def parse_version(raw: str) -> tuple[int, Any]:
    if len(raw) > MAX_VERSION_LENGTH:
        return (0, raw)

    match = GIT_DESCRIBE_VERSION_RE.fullmatch(raw)
    if match:
        base = match.group("base")
        distance = match.group("distance")
        parsed = f"{base}.post{distance}" if distance is not None else base
        return (1, Version(parsed))

    try:
        return (1, Version(raw.lstrip("v")))
    except ValueError:  # InvalidVersion is a ValueError
        return (0, raw)


def read_release(channel: str, version: str) -> dict[str, Any] | None:
    """Return the release metadata, None if there is none, or raise CorruptReleaseError."""
    path = release_metadata_path(channel, version)
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CorruptReleaseError(f"{path} is not readable: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("targets", {}), dict):
        raise CorruptReleaseError(f"{path} does not contain release metadata")
    return payload


def load_release(channel: str, version: str) -> dict[str, Any]:
    try:
        release = read_release(channel, version)
    except CorruptReleaseError as exc:
        logger.error("Corrupt release metadata: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Release metadata is unreadable"
        ) from exc
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release not found")
    return release


def channel_releases(channel: str) -> list[tuple[str, dict[str, Any]]]:
    """All readable releases of a channel, newest first. Cached; callers must not mutate the result."""
    base = channel_dir(channel)
    try:
        mtime = base.stat().st_mtime_ns
    except OSError:
        return []

    key = str(base)
    cached = _index_cache.get(key)
    if cached and cached[0] == mtime and cached[1] > time.monotonic():
        return cached[2]

    releases: list[tuple[str, dict[str, Any]]] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        try:
            release = read_release(channel, entry.name)
        except CorruptReleaseError as exc:
            # One broken release must not take update checks down for the whole channel.
            logger.error("Skipping corrupt release: %s", exc)
            continue
        if release is not None:
            releases.append((entry.name, release))

    releases.sort(key=lambda item: parse_version(item[0]), reverse=True)
    _index_cache[key] = (mtime, time.monotonic() + INDEX_CACHE_TTL_SECONDS, releases)
    return releases


def list_release_versions(channel: str) -> list[str]:
    return [version for version, _ in channel_releases(channel)]


@contextmanager
def _storage_lock() -> Iterator[None]:
    """Serialize release.json read-modify-write cycles across requests and worker processes."""
    UPDATE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(UPDATE_STORAGE_DIR / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def create_staging_dir() -> Path:
    """A private directory on the storage filesystem; uploads are written here and renamed in on success."""
    staging_root = UPDATE_STORAGE_DIR / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    cutoff = time.time() - STALE_STAGING_SECONDS
    for leftover in staging_root.iterdir():  # uploads that died without cleaning up
        try:
            if leftover.stat().st_mtime < cutoff:
                shutil.rmtree(leftover, ignore_errors=True)
        except OSError:
            continue

    staging_dir = staging_root / uuid.uuid4().hex
    staging_dir.mkdir()
    return staging_dir


def commit_target(
    channel: str, version: str, target: str, staged_dir: Path, target_entry: dict[str, Any], created_at: str
) -> None:
    """Publish a fully written and verified target directory and record it in release.json, atomically."""
    with _storage_lock():
        try:
            release = read_release(channel, version)
        except CorruptReleaseError as exc:
            logger.error("Refusing to modify corrupt release: %s", exc)
            raise

        if release is None:
            release = {"channel": channel, "version": version, "createdAt": created_at, "targets": {}}
        targets = release.setdefault("targets", {})
        if target in targets:
            raise ReleaseTargetExistsError(f"{channel}/{version}/{target}")

        destination = release_dir(channel, version) / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            # Not referenced by release.json, so it is debris from an upload that never committed.
            shutil.rmtree(destination)
        os.rename(staged_dir, destination)

        targets[target] = target_entry
        _write_json_atomic(release_metadata_path(channel, version), release)

        os.utime(channel_dir(channel))  # lets other worker processes notice the change
        _index_cache.pop(str(channel_dir(channel)), None)
