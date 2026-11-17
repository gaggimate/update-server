from __future__ import annotations

import hashlib
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Annotated

import aiofiles
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from update_server.config import (
    ALLOWED_CHANNELS,
    DEFAULT_CHIP_FAMILY,
    DEFAULT_PART_OFFSETS,
    MAX_UPLOAD_FILE_SIZE_BYTES,
    MAX_UPLOAD_FILES,
    TARGET_LABELS,
)
from update_server.security import require_admin, validate_filename
from update_server.signing import (
    SignatureError,
    SigningUnavailableError,
    VerifiedManifest,
    signing_key_configured,
    unsigned_uploads_allowed,
    verify_signed_manifest,
)
from update_server.storage import (
    CorruptReleaseError,
    ReleaseTargetExistsError,
    channel_releases,
    commit_target,
    create_staging_dir,
    list_release_versions,
    load_release,
    parse_version,
    read_release,
    release_dir,
)
from update_server.validation import (
    require_valid_channel,
    require_valid_chip_family,
    require_valid_label,
    require_valid_target,
    require_valid_version,
)

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_CHUNK_BYTES = 1024 * 1024


def signed_block(target_data: dict[str, Any]) -> dict[str, Any] | None:
    """The signed release manifest exactly as uploaded, for clients to verify. None for unsigned releases."""
    if not target_data.get("signedManifest"):
        return None
    return {
        "manifest": target_data["signedManifest"],
        "signature": target_data.get("signature"),
        "keyId": target_data.get("signatureKeyId"),
    }


def manifest_for_target(channel: str, version: str, target_data: dict[str, Any]) -> dict[str, Any]:
    artifacts = {item["filename"]: item for item in target_data.get("artifacts", [])}
    return {
        "name": target_data.get("label") or TARGET_LABELS.get(target_data["target"], target_data["target"]),
        "version": target_data.get("componentVersion") or version,
        "new_install_prompt_erase": True,
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
        # Everything above is unauthenticated convenience data for the web flasher. Devices verify
        # signed.signature over the UTF-8 bytes of signed.manifest (Ed25519) and trust only what that
        # manifest says. Extra keys are ignored by esp-web-tools.
        "signed": signed_block(target_data),
    }


def target_details_for_version(channel: str, version: str, target_data: dict[str, Any]) -> dict[str, Any]:
    part_offsets = {part["filename"]: part["offset"] for part in target_data.get("parts", [])}

    return {
        "target": target_data["target"],
        "name": target_data.get("label") or TARGET_LABELS.get(target_data["target"], target_data["target"]),
        "version": target_data.get("componentVersion") or version,
        "chipFamily": target_data.get("chipFamily", DEFAULT_CHIP_FAMILY),
        "manifestUrl": f"/api/channels/{channel}/releases/{version}/{target_data['target']}/manifest",
        "files": [
            {
                "filename": artifact["filename"],
                "path": f"/api/channels/{channel}/releases/{version}/{target_data['target']}/{artifact['filename']}",
                "offset": part_offsets.get(artifact["filename"]),
                "sha256": artifact.get("sha256"),
                "size": artifact.get("size"),
            }
            for artifact in target_data.get("artifacts", [])
        ],
        "signed": signed_block(target_data),
        "updatedAt": target_data.get("updatedAt"),
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
    for version, release in channel_releases(channel):
        for target, target_data in release.get("targets", {}).items():
            if target in latest_for_target:
                continue
            target_version = target_data.get("componentVersion") or version
            latest_for_target[target] = {
                "releaseVersion": version,
                "targetVersion": target_version,
                "manifestUrl": f"/api/channels/{channel}/releases/{version}/{target}/manifest",
            }
        if all(target in latest_for_target for target in current_versions):
            break

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


def _target_exists(channel: str, version: str, target: str) -> bool:
    try:
        release = read_release(channel, version)
    except CorruptReleaseError as exc:
        logger.error("Refusing upload into corrupt release: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Release metadata is unreadable"
        ) from exc
    return release is not None and target in release.get("targets", {})


def _conflict(channel: str, version: str, target: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Release {channel}/{version}/{target} already exists and cannot be overwritten",
    )


async def _verified_manifest(
    manifest: str | None,
    signature: str | None,
    channel: str,
    version: str,
    target: str,
    component_version: str | None,
    artifacts: dict[str, dict[str, Any]],
) -> VerifiedManifest | None:
    """Check the signed manifest against this upload. Returns None only for an allowed unsigned upload."""
    if not manifest and not signature:
        if unsigned_uploads_allowed():
            return None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="manifest and signature are required: an Ed25519-signed release manifest covering every uploaded file",
        )
    if not manifest or not signature:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="manifest and signature must be sent together")

    if not signing_key_configured():
        if unsigned_uploads_allowed():
            # Never store a signature nobody checked: clients must be able to rely on what the server hands out.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The manifest signature cannot be verified: no release signing key is configured on this server",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Uploads are disabled: no release signing key is configured on this server",
        )

    try:
        verified = await run_in_threadpool(verify_signed_manifest, manifest, signature)
    except SignatureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SigningUnavailableError as exc:
        logger.error("Release signature verification unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Uploads are disabled: release signature verification is not available on this server",
        ) from exc

    # The signature binds the files to one channel/version/target: a signed build can not be republished
    # under another name, as another target, or as a newer version than it is.
    for field, signed_value, requested in (
        ("channel", verified.channel, channel),
        ("version", verified.version, version),
        ("target", verified.target, target),
    ):
        if signed_value != requested:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Signed manifest is for {field} {signed_value!r}, not {requested!r}",
            )
    signed_component_version = verified.component_version or verified.version
    if component_version and component_version != signed_component_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"component_version does not match the signed manifest ({signed_component_version!r})",
        )

    # The manifest must account for exactly the uploaded set: no unsigned extras, nothing missing.
    unsigned = sorted(set(artifacts) - set(verified.files))
    if unsigned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Signed manifest does not cover uploaded file(s): {', '.join(unsigned)}",
        )
    missing = sorted(set(verified.files) - set(artifacts))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Signed manifest references missing upload(s): {', '.join(missing)}",
        )
    for filename, signed_file in verified.files.items():
        artifact = artifacts[filename]
        if artifact["sha256"] != signed_file.sha256 or artifact["size"] != signed_file.size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Uploaded {filename} does not match the signed manifest",
            )

    return verified


@router.post("/admin/releases/upload", dependencies=[Depends(require_admin)])
async def upload_release_target(
    channel: Annotated[str, Form()],
    version: Annotated[str, Form()],
    target: Annotated[str, Form()],
    chip_family: Annotated[str, Form()] = DEFAULT_CHIP_FAMILY,
    component_version: Annotated[str | None, Form()] = None,
    label: Annotated[str | None, Form()] = None,
    manifest: Annotated[str | None, Form()] = None,
    signature: Annotated[str | None, Form()] = None,
    files: list[UploadFile] = None,
) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_version(version)
    require_valid_target(target)
    require_valid_chip_family(chip_family)
    if component_version:
        require_valid_version(component_version)
    if label:
        require_valid_label(label)

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one file is required")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"At most {MAX_UPLOAD_FILES} files per upload")

    filenames: list[str] = []
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Each upload must have a filename")
        safe_name = validate_filename(file.filename)
        if safe_name in filenames:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Duplicate filename: {safe_name}")
        filenames.append(safe_name)

    # Fail fast; commit_target() repeats this check under the storage lock.
    if _target_exists(channel, version, target):
        raise _conflict(channel, version, target)

    # Nothing becomes visible to clients until every file is written and verified: uploads land in a
    # private staging directory that is renamed into place together with the release.json update.
    staging_dir = create_staging_dir()
    try:
        artifacts: dict[str, dict[str, Any]] = {}
        for file, filename in zip(files, filenames):
            digest = hashlib.sha256()
            bytes_written = 0
            async with aiofiles.open(staging_dir / filename, "wb") as out:
                while content := await file.read(UPLOAD_CHUNK_BYTES):
                    bytes_written += len(content)
                    if bytes_written > MAX_UPLOAD_FILE_SIZE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File {filename} exceeds max upload size of {MAX_UPLOAD_FILE_SIZE_BYTES} bytes",
                        )
                    await out.write(content)
                    digest.update(content)
                await out.flush()
                await run_in_threadpool(os.fsync, out.fileno())

            artifacts[filename] = {
                "filename": filename,
                "size": bytes_written,
                "sha256": digest.hexdigest(),
            }

        verified = await _verified_manifest(manifest, signature, channel, version, target, component_version, artifacts)
        if verified and verified.component_version:
            require_valid_version(verified.component_version)
            component_version = verified.component_version

        now = datetime.now(timezone.utc).isoformat()
        target_entry = {
            "target": target,
            "label": label or TARGET_LABELS.get(target, target),
            "chipFamily": chip_family,
            "componentVersion": component_version or version,
            "parts": [
                {"filename": name, "offset": DEFAULT_PART_OFFSETS[name]} for name in filenames if name in DEFAULT_PART_OFFSETS
            ],
            "artifacts": [artifacts[name] for name in filenames],
            "signedManifest": verified.manifest if verified else None,
            "signature": verified.signature if verified else None,
            "signatureKeyId": verified.key_id if verified else None,
            "updatedAt": now,
        }

        try:
            commit_target(channel, version, target, staging_dir, target_entry, created_at=now)
        except ReleaseTargetExistsError as exc:
            raise _conflict(channel, version, target) from exc
        except CorruptReleaseError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Release metadata is unreadable"
            ) from exc
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)  # already renamed away on success

    return {
        "message": "Upload successful",
        "channel": channel,
        "version": version,
        "target": target,
        "signatureKeyId": verified.key_id if verified else None,
        "manifestUrl": f"/api/channels/{channel}/releases/{version}/{target}/manifest",
        "versionDetailsUrl": f"/api/channels/{channel}/releases/{version}",
    }


@router.get("/channels/{channel}/releases/{version}")
def version_details(channel: str, version: str) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_version(version)

    release = load_release(channel, version)

    return {
        "channel": channel,
        "version": version,
        "createdAt": release.get("createdAt"),
        "targets": {
            target: target_details_for_version(channel, version, target_data)
            for target, target_data in release.get("targets", {}).items()
        },
    }


@router.get("/channels/{channel}/releases/latest/{target}/manifest")
def latest_manifest(channel: str, target: str) -> dict[str, Any]:
    require_valid_channel(channel)
    require_valid_target(target)

    for version, release in channel_releases(channel):
        target_data = release.get("targets", {}).get(target)
        if target_data:
            return manifest_for_target(channel, version, target_data)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No release found for target")


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


@router.get("/channels/{channel}/releases/{version}/{target}/{filename}")
def binary(channel: str, version: str, target: str, filename: str) -> FileResponse:
    require_valid_channel(channel)
    require_valid_version(version)
    require_valid_target(target)
    safe_filename = validate_filename(filename)

    # Only files recorded in release.json are served, never whatever happens to be on disk.
    release = load_release(channel, version)
    target_data = release.get("targets", {}).get(target) or {}
    published = {artifact.get("filename") for artifact in target_data.get("artifacts", [])}
    file_path = release_dir(channel, version) / target / safe_filename
    if safe_filename not in published or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binary not found")

    # Always a download, never rendered: uploads must not be able to plant HTML/SVG on this origin.
    return FileResponse(file_path, media_type="application/octet-stream", filename=safe_filename)
