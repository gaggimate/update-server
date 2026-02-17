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
