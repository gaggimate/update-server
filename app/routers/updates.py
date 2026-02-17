from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.config import (
    ALLOWED_CHANNELS,
    DEFAULT_CHIP_FAMILY,
    DEFAULT_PART_OFFSETS,
    TARGET_LABELS,
)
from app.security import require_admin_auth_or_404, validate_filename
from app.storage import list_release_versions, load_release, parse_version, release_dir, save_release
from app.validation import require_valid_channel, require_valid_target

router = APIRouter()


def manifest_for_target(channel: str, version: str, target_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": target_data.get("label") or TARGET_LABELS.get(target_data["target"], target_data["target"]),
        "version": target_data.get("componentVersion") or version,
        "new_install_prompt_erase": False,
        "builds": [
            {
                "chipFamily": target_data.get("chipFamily", DEFAULT_CHIP_FAMILY),
                "parts": [
                    {
                        "path": f"/updates/{channel}/{version}/{target_data['target']}/{part['filename']}",
                        "offset": part["offset"],
                    }
                    for part in target_data["parts"]
                ],
            }
        ],
    }


@router.get("/channels")
def channels() -> list[str]:
    return ALLOWED_CHANNELS


@router.get("/releases/{channel}")
def releases(channel: str) -> dict[str, Any]:
    require_valid_channel(channel)
    return {"channel": channel, "versions": list_release_versions(channel)}


@router.get("/check/{channel}")
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
                "manifestUrl": f"/updates/{channel}/{version}/{target}/manifest",
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
    files: list[UploadFile] = File(...),
    x_api_key: str | None = Header(None),
) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_target(target)
    require_admin_auth_or_404(x_api_key)

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one file is required")

    uploaded_names = set()
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Each upload must have a filename")
        uploaded_names.add(validate_filename(file.filename))

    if parts_json:
        try:
            parts_data = json.loads(parts_json)
            parts = [
                {"filename": validate_filename(item["filename"]), "offset": int(item["offset"])} for item in parts_data
            ]
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid parts_json payload") from exc
    else:
        parts = [
            {"filename": name, "offset": offset}
            for name, offset in DEFAULT_PART_OFFSETS.items()
            if name in uploaded_names
        ]

    if not parts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No manifest parts could be generated")

    for part in parts:
        if part["filename"] not in uploaded_names:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing upload for part {part['filename']}",
            )

    target_directory = release_dir(channel, version) / target
    target_directory.mkdir(parents=True, exist_ok=True)

    for file in files:
        filename = validate_filename(file.filename or "")
        destination = target_directory / filename
        content = await file.read()
        destination.write_bytes(content)

    try:
        release = load_release(channel, version)
    except HTTPException:
        release = {
            "channel": channel,
            "version": version,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "targets": {},
        }

    release.setdefault("targets", {})[target] = {
        "target": target,
        "label": label or TARGET_LABELS.get(target, target),
        "chipFamily": chip_family,
        "componentVersion": component_version or version,
        "parts": parts,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    save_release(channel, version, release)

    return {
        "message": "Upload successful",
        "channel": channel,
        "version": version,
        "target": target,
        "manifestUrl": f"/updates/{channel}/{version}/{target}/manifest",
    }


@router.get("/updates/{channel}/{version}/{target}/manifest")
def manifest(channel: str, version: str, target: str) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_target(target)

    release = load_release(channel, version)
    target_data = release.get("targets", {}).get(target)
    if not target_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target build not found")

    return manifest_for_target(channel, version, target_data)


@router.get("/updates/{channel}/latest/{target}/manifest")
def latest_manifest(channel: str, target: str) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_target(target)

    for version in list_release_versions(channel):
        release = load_release(channel, version)
        target_data = release.get("targets", {}).get(target)
        if target_data:
            return manifest_for_target(channel, version, target_data)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No release found for target")


@router.get("/updates/{channel}/{version}/{target}/{filename}")
def binary(channel: str, version: str, target: str, filename: str) -> FileResponse:
    require_valid_channel(channel)
    require_valid_target(target)
    safe_filename = validate_filename(filename)

    file_path = release_dir(channel, version) / target / safe_filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binary not found")
    return FileResponse(file_path)
