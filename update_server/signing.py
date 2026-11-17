"""Verification of Ed25519-signed release manifests.

A release manifest is a small JSON document that names the channel, version and target of one upload and
lists every file with its size and SHA-256. The signature is a detached Ed25519 signature over the exact
manifest bytes, so the manifest is stored and served verbatim and never re-serialized.

Devices verify the same bytes with the same public key (libsodium ``crypto_sign_verify_detached``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from update_server.config import ALLOW_UNSIGNED_UPLOADS, SIGNING_PUBLIC_KEY_FILE
from update_server.security import SAFE_FILENAME_RE

MANIFEST_SCHEMA = 1
MAX_MANIFEST_BYTES = 64 * 1024
ED25519_SIGNATURE_BYTES = 64

_PEM_PUBLIC_KEY_RE = re.compile(rb"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----", re.DOTALL)
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")


class SignatureError(ValueError):
    """The submitted manifest or signature is not acceptable (client error)."""


class SigningUnavailableError(RuntimeError):
    """Verification cannot run on this server (missing or unusable key file)."""


@dataclass(frozen=True)
class ManifestFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class VerifiedManifest:
    channel: str
    version: str
    target: str
    component_version: str | None
    files: dict[str, ManifestFile]
    key_id: str
    """Identifies which trusted key signed (see key_id())."""
    manifest: str
    """The exact signed text, to be stored and served verbatim."""
    signature: str
    """Base64 of the 64-byte Ed25519 signature over ``manifest`` encoded as UTF-8."""


def signing_key_configured() -> bool:
    return bool(SIGNING_PUBLIC_KEY_FILE)


def unsigned_uploads_allowed() -> bool:
    return ALLOW_UNSIGNED_UPLOADS


def key_id(public_key: Ed25519PublicKey) -> str:
    """First 8 bytes of the SHA-256 of the raw 32-byte public key, as hex. Lets clients pick the key."""
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def load_trusted_keys() -> dict[str, Ed25519PublicKey]:
    """Read the PEM public key(s) from SIGNING_PUBLIC_KEY_FILE. Several keys allow rotation."""
    if not SIGNING_PUBLIC_KEY_FILE:
        raise SigningUnavailableError("No release signing key is configured")
    try:
        data = Path(SIGNING_PUBLIC_KEY_FILE).read_bytes()
    except OSError as exc:
        raise SigningUnavailableError(f"Signing key file is not readable: {exc.strerror}") from exc

    keys: dict[str, Ed25519PublicKey] = {}
    for block in _PEM_PUBLIC_KEY_RE.findall(data):
        try:
            public_key = serialization.load_pem_public_key(block)
        except (ValueError, TypeError) as exc:
            raise SigningUnavailableError("Signing key file contains an invalid PEM public key") from exc
        if not isinstance(public_key, Ed25519PublicKey):
            raise SigningUnavailableError("Signing key file contains a key that is not Ed25519")
        keys[key_id(public_key)] = public_key
    if not keys:
        raise SigningUnavailableError("Signing key file contains no PEM public key")
    return keys


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # JSON parsers disagree on which duplicate wins; a signed document must mean one thing everywhere.
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SignatureError(f"Manifest contains duplicate key {key!r}")
        result[key] = value
    return result


def _require_str(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise SignatureError(f"Manifest field {field!r} must be a non-empty string")
    return value


def parse_manifest(text: str) -> tuple[str, str, str, str | None, dict[str, ManifestFile]]:
    """Strictly parse manifest JSON into (channel, version, target, componentVersion, files)."""
    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except SignatureError:
        raise
    except (ValueError, RecursionError) as exc:
        raise SignatureError("Manifest is not valid JSON") from exc
    if not isinstance(document, dict):
        raise SignatureError("Manifest must be a JSON object")
    if document.get("schema") != MANIFEST_SCHEMA or isinstance(document.get("schema"), bool):
        raise SignatureError(f"Manifest field 'schema' must be {MANIFEST_SCHEMA}")

    channel = _require_str(document, "channel")
    version = _require_str(document, "version")
    target = _require_str(document, "target")
    component_version = document.get("componentVersion")
    if component_version is not None and (not isinstance(component_version, str) or not component_version):
        raise SignatureError("Manifest field 'componentVersion' must be a non-empty string")

    entries = document.get("files")
    if not isinstance(entries, list) or not entries:
        raise SignatureError("Manifest field 'files' must be a non-empty list")

    files: dict[str, ManifestFile] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SignatureError("Manifest file entries must be objects")
        name, size, sha256 = entry.get("name"), entry.get("size"), entry.get("sha256")
        if not isinstance(name, str) or not SAFE_FILENAME_RE.fullmatch(name) or ".." in name:
            raise SignatureError("Manifest lists an invalid filename")
        if name in files:
            raise SignatureError(f"Manifest lists {name} more than once")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SignatureError(f"Manifest size for {name} must be a non-negative integer")
        if not isinstance(sha256, str) or not _SHA256_HEX_RE.fullmatch(sha256):
            raise SignatureError(f"Manifest sha256 for {name} must be 64 lowercase hex characters")
        files[name] = ManifestFile(name=name, size=size, sha256=sha256)

    return channel, version, target, component_version, files


def verify_signed_manifest(manifest: str, signature: str) -> VerifiedManifest:
    """Verify the detached signature over the manifest text, then parse what was signed."""
    trusted_keys = load_trusted_keys()  # first: a misconfigured server must say so whatever the payload is

    try:
        manifest_bytes = manifest.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SignatureError("Manifest is not valid UTF-8") from exc
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise SignatureError("Manifest is too large")

    try:
        signature_bytes = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError("Signature must be base64") from exc
    if len(signature_bytes) != ED25519_SIGNATURE_BYTES:
        raise SignatureError("Signature must be a 64-byte Ed25519 signature")

    signer = None
    for candidate_id, public_key in trusted_keys.items():
        try:
            public_key.verify(signature_bytes, manifest_bytes)
        except InvalidSignature:
            continue
        signer = candidate_id
        break
    if signer is None:
        raise SignatureError("Manifest is not validly signed by a trusted release signing key")

    # Only parsed after the signature checked out: nothing unauthenticated reaches the JSON handling above.
    channel, version, target, component_version, files = parse_manifest(manifest)
    return VerifiedManifest(
        channel=channel,
        version=version,
        target=target,
        component_version=component_version,
        files=files,
        key_id=signer,
        manifest=manifest,
        signature=base64.b64encode(signature_bytes).decode("ascii"),
    )
