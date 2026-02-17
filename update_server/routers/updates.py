from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from update_server.config import (
    ALLOWED_CHANNELS,
    DEFAULT_CHIP_FAMILY,
    DEFAULT_PART_OFFSETS,
    MAX_UPLOAD_FILE_SIZE_BYTES,
    TARGET_LABELS,
)
from update_server.security import require_admin_auth_or_404, validate_filename
from update_server.storage import list_release_versions, load_release, parse_version, release_dir, save_release
from update_server.validation import require_valid_channel, require_valid_target, require_valid_version

router = APIRouter()


def manifest_for_target(channel: str, version: str, target_data: dict[str, Any]) -> dict[str, Any]:
    artifacts = {item["filename"]: item for item in target_data.get("artifacts", [])}
    return {
        "name": target_data.get("label") or TARGET_LABELS.get(target_data["target"], target_data["target"]),
        "version": target_data.get("componentVersion") or version,
        "new_install_prompt_erase": False,
        "builds": [
            {
                "chipFamily": target_data.get("chipFamily", DEFAULT_CHIP_FAMILY),
                "parts": [
                    {
                        "path": f"/api/channels/{channel}/releases/{version}/{target_data['target']}/{part['filename']}",
                        "offset": part["offset"],
                        "sha256": artifacts.get(part["filename"], {}).get("sha256"),
                        "size": artifacts.get(part["filename"], {}).get("size"),
                    }
                    for part in target_data["parts"]
                ],
            }
        ],
    }


@router.get("/channels")
def channels() -> list[str]:
    return ALLOWED_CHANNELS


@router.get("/channels/{channel}/releases")
def releases(channel: str) -> dict[str, Any]:
    require_valid_channel(channel)
    return {"channel": channel, "versions": list_release_versions(channel)}


@router.get("/channels/{channel}/check")
def check(channel: str, controller_version: str | None = None, display_version: str | None = None) -> dict[str, Any]:
    require_valid_channel(channel)

    current_versions = {
        "controller": controller_version,
        "display": display_version,
    }

    latest_for_target: dict[str, dict[str, Any]] = {}
    for version in list_release_versions(channel):
        release = load_release(channel, version)
        for target, target_data in release.get("targets", {}).items():
            if target in latest_for_target:
                continue
            target_version = target_data.get("componentVersion") or version
            latest_for_target[target] = {
                "releaseVersion": version,
                "targetVersion": target_version,
                "manifestUrl": f"/api/channels/{channel}/releases/{version}/{target}/manifest",
            }

    updates: dict[str, Any] = {}
    for target, current in current_versions.items():
        if target not in latest_for_target:
            updates[target] = {
                "current": current,
                "latest": None,
                "releaseVersion": None,
                "updateAvailable": False,
                "manifestUrl": None,
            }
            continue

        latest = latest_for_target[target]
        available = current is None or parse_version(latest["targetVersion"]) > parse_version(current)

        updates[target] = {
            "current": current,
            "latest": latest["targetVersion"],
            "releaseVersion": latest["releaseVersion"],
            "updateAvailable": available,
            "manifestUrl": latest["manifestUrl"],
        }

    return {"channel": channel, "updates": updates}


@router.post("/admin/releases/upload")
async def upload_release_target(
    channel: str = Form(...),
    version: str = Form(...),
    target: str = Form(...),
    component_version: str | None = Form(None),
    chip_family: str = Form(DEFAULT_CHIP_FAMILY),
    label: str | None = Form(None),
    parts_json: str | None = Form(None),
    offsets_json: str | None = Form(None),
    files: list[UploadFile] = File(...),
    x_api_key: str | None = Header(None),
) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_version(version)
    require_valid_target(target)
    require_admin_auth_or_404(x_api_key)

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one file is required")

    uploaded_names: set[str] = set()
    ordered_uploaded_names: list[str] = []
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Each upload must have a filename")
        safe_name = validate_filename(file.filename)
        if safe_name not in uploaded_names:
            ordered_uploaded_names.append(safe_name)
        uploaded_names.add(safe_name)

    if parts_json and offsets_json:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either parts_json or offsets_json, not both",
        )

    offsets: dict[str, int] = {}
    if offsets_json:
        try:
            parsed_offsets = json.loads(offsets_json)
            offsets = {validate_filename(name): int(offset) for name, offset in parsed_offsets.items()}
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid offsets_json payload") from exc

    if parts_json:
        try:
            parts_data = json.loads(parts_json)
            parts = [
                {"filename": validate_filename(item["filename"]), "offset": int(item["offset"])} for item in parts_data
            ]
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid parts_json payload") from exc
    else:
        parts = []
        for name in ordered_uploaded_names:
            if name in offsets:
                offset = offsets[name]
            elif name in DEFAULT_PART_OFFSETS:
                offset = DEFAULT_PART_OFFSETS[name]
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Offset missing for part {name}; include it via offsets_json or parts_json",
                )

            parts.append({"filename": name, "offset": offset})

    if not parts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No manifest parts could be generated")

    for part in parts:
        if part["filename"] not in uploaded_names:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing upload for part {part['filename']}",
            )
        if part["offset"] < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Offsets must be non-negative")

    try:
        release = load_release(channel, version)
    except HTTPException:
        release = {
            "channel": channel,
            "version": version,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "targets": {},
        }

    if target in release.get("targets", {}):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Release {channel}/{version}/{target} already exists and cannot be overwritten",
        )

    target_directory = release_dir(channel, version) / target
    target_directory.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, dict[str, Any]] = {}
    for file in files:
        filename = validate_filename(file.filename or "")
        destination = target_directory / filename

        digest = hashlib.sha256()
        bytes_written = 0
        with Path(destination).open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_FILE_SIZE_BYTES:
                    out.close()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File {filename} exceeds max upload size of {MAX_UPLOAD_FILE_SIZE_BYTES} bytes",
                    )
                out.write(chunk)
                digest.update(chunk)

        artifacts[filename] = {
            "filename": filename,
            "size": bytes_written,
            "sha256": digest.hexdigest(),
        }

    release.setdefault("targets", {})[target] = {
        "target": target,
        "label": label or TARGET_LABELS.get(target, target),
        "chipFamily": chip_family,
        "componentVersion": component_version or version,
        "parts": parts,
        "artifacts": [artifacts[part["filename"]] for part in parts],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    save_release(channel, version, release)

    return {
        "message": "Upload successful",
        "channel": channel,
        "version": version,
        "target": target,
        "manifestUrl": f"/api/channels/{channel}/releases/{version}/{target}/manifest",
    }


@router.get("/channels/{channel}/releases/{version}/{target}/manifest")
def manifest(channel: str, version: str, target: str) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_version(version)
    require_valid_target(target)

    release = load_release(channel, version)
    target_data = release.get("targets", {}).get(target)
    if not target_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target build not found")

    return manifest_for_target(channel, version, target_data)


@router.get("/channels/{channel}/releases/latest/{target}/manifest")
def latest_manifest(channel: str, target: str) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_target(target)

    for version in list_release_versions(channel):
        release = load_release(channel, version)
        target_data = release.get("targets", {}).get(target)
        if target_data:
            return manifest_for_target(channel, version, target_data)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No release found for target")


@router.get("/channels/{channel}/releases/{version}/{target}/{filename}")
def binary(channel: str, version: str, target: str, filename: str) -> FileResponse:
    require_valid_channel(channel)
    require_valid_version(version)
    require_valid_target(target)
    safe_filename = validate_filename(filename)

    file_path = release_dir(channel, version) / target / safe_filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binary not found")
    return FileResponse(file_path)
