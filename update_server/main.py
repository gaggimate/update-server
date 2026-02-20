from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from update_server.config import ALLOWED_CHANNELS, ALLOWED_TARGETS, DEFAULT_CORS_ALLOW_ORIGIN_REGEX, TARGET_LABELS
from update_server.routers.updates import router as updates_router
from update_server.storage import list_release_versions

app = FastAPI(title="Update Server")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=DEFAULT_CORS_ALLOW_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(updates_router, prefix="/api")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
def flash_page(request: Request, channel: str = "stable") -> HTMLResponse:
    selected_channel = channel if channel in ALLOWED_CHANNELS else ALLOWED_CHANNELS[0]

    releases = list_release_versions(selected_channel)
    latest_release = releases[0] if releases else None
    previous_releases = releases[1:] if len(releases) > 1 else []

    targets = [
        {
            "name": target,
            "label": TARGET_LABELS.get(target, target),
            "manifest_url": f"/api/channels/{selected_channel}/releases/latest/{target}/manifest",
        }
        for target in ALLOWED_TARGETS
    ]

    previous_release_targets = [
        {
            "version": release,
            "targets": [
                {
                    "name": target,
                    "label": TARGET_LABELS.get(target, target),
                    "manifest_url": f"/api/channels/{selected_channel}/releases/{release}/{target}/manifest",
                }
                for target in ALLOWED_TARGETS
            ],
        }
        for release in previous_releases
    ]

    return templates.TemplateResponse(
        request,
        "flash.html",
        {
            "channels": ALLOWED_CHANNELS,
            "selected_channel": selected_channel,
            "targets": targets,
            "latest_release": latest_release,
            "previous_releases": previous_releases,
            "previous_release_targets": previous_release_targets,
        },
    )


@app.get("/api")
def api_root() -> dict[str, str]:
    return {"status": "ok", "service": "update-server"}
