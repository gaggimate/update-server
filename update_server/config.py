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

UPDATE_STORAGE_DIR = Path(os.getenv("UPDATE_STORAGE_DIR", "storage"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
