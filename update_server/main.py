from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from update_server.config import (
    ALLOWED_CHANNELS,
    ALLOWED_TARGETS,
    CHANNEL_DESCRIPTIONS,
    DEFAULT_CORS_ALLOW_ORIGIN_REGEX,
    TARGET_DESCRIPTIONS,
    TARGET_LABELS,
)
from update_server.middleware import AdminGuardMiddleware, SecurityHeadersMiddleware
from update_server.routers.updates import router as updates_router
from update_server.storage import channel_releases

PACKAGE_DIR = Path(__file__).parent

# No interactive docs / OpenAPI schema on a public OTA host.
app = FastAPI(title="Update Server", docs_url=None, redoc_url=None, openapi_url=None)

# Middleware added last runs first: security headers wrap everything, then CORS, then the admin guard.
app.add_middleware(AdminGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=DEFAULT_CORS_ALLOW_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(updates_router, prefix="/api")
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(size // 1024, 1)} kB"


def _format_date(timestamp: Any) -> str | None:
    try:
        return datetime.fromisoformat(str(timestamp)).strftime("%-d %B %Y")
    except ValueError:
        return None


def _target_card(channel: str, version: str | None, release: dict[str, Any] | None, target: str) -> dict[str, Any]:
    data = (release or {}).get("targets", {}).get(target) or {}
    return {
        "name": target,
        "label": TARGET_LABELS.get(target, target),
        "description": TARGET_DESCRIPTIONS.get(target, ""),
        # Only builds with flashable parts can be installed from the browser.
        "available": bool(data.get("parts")),
        "version": data.get("componentVersion") or version,
        "size": _format_size(sum(int(artifact.get("size") or 0) for artifact in data.get("artifacts", []))),
        "manifest_url": f"/api/channels/{channel}/releases/{version}/{target}/manifest",
    }


@app.get("/", response_class=HTMLResponse)
def flash_page(request: Request, channel: str = "stable", version: str | None = None) -> HTMLResponse:
    selected_channel = channel if channel in ALLOWED_CHANNELS else ALLOWED_CHANNELS[0]

    releases = dict(channel_releases(selected_channel))  # newest first
    versions = list(releases)
    latest_version = versions[0] if versions else None
    # Anything that is not a published version of this channel falls back to the latest one.
    selected_version = version if version in releases else latest_version
    release = releases.get(selected_version) if selected_version else None

    return templates.TemplateResponse(
        request,
        "flash.html",
        {
            "channels": [
                {"name": name, "description": CHANNEL_DESCRIPTIONS.get(name, ""), "selected": name == selected_channel}
                for name in ALLOWED_CHANNELS
            ],
            "selected_channel": selected_channel,
            "versions": versions,
            "latest_version": latest_version,
            "selected_version": selected_version,
            "release_date": _format_date(release.get("createdAt")) if release else None,
            "targets": [_target_card(selected_channel, selected_version, release, target) for target in ALLOWED_TARGETS],
        },
    )


@app.get("/api")
def api_root() -> dict[str, str]:
    return {"status": "ok", "service": "update-server"}
