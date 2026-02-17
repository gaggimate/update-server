from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from update_server.config import ALLOWED_CHANNELS, ALLOWED_TARGETS, TARGET_LABELS
from update_server.routers.updates import router as updates_router
from update_server.storage import list_release_versions

app = FastAPI(title="Update Server")
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
            "latest_manifest_url": (
                f"/api/channels/{selected_channel}/releases/{latest_release}/{target}/manifest" if latest_release else None
            ),
        }
        for target in ALLOWED_TARGETS
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
        },
    )


@app.get("/api")
def api_root() -> dict[str, str]:
    return {"status": "ok", "service": "update-server"}
