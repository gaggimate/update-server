from __future__ import annotations

import os
from pathlib import Path

ALLOWED_TARGETS = ["display", "controller", "headless"]
ALLOWED_CHANNELS = ["stable", "nightly"]

TARGET_LABELS = {
    "display": "GaggiMate Display",
    "controller": "GaggiMate Controller",
    "headless": "GaggiMate Headless",
}

DEFAULT_CHIP_FAMILY = "ESP32-S3"
DEFAULT_PART_OFFSETS = {
    "bootloader.bin": 0,
    "partitions.bin": 32768,
    "boot_app0.bin": 57344,
    "firmware.bin": 65536,
    "filesystem.bin": 13172736
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


UPDATE_STORAGE_DIR = Path(os.getenv("UPDATE_STORAGE_DIR", "storage"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
ALLOW_UNAUTHENTICATED_UPLOADS = _env_bool("ALLOW_UNAUTHENTICATED_UPLOADS", default=False)
MAX_UPLOAD_FILE_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_FILE_SIZE_BYTES", str(32 * 1024 * 1024)))

# Allow local development origins by default: *.local domains, single-label hostnames,
# and private RFC1918 IPv4 addresses.
DEFAULT_CORS_ALLOW_ORIGIN_REGEX = (
    r"^https?://(?:"
    r"(?:[A-Za-z0-9-]+\.)+local|"
    r"[A-Za-z0-9-]+|"
    r"10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}"
    r")(?:\:\d+)?$"
)
