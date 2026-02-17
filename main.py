from fastapi import Body, FastAPI, status
from fastapi.responses import JSONResponse

from config import ALLOWED_CHANNELS, ALLOWED_TARGETS, TARGET_LABELS

app = FastAPI()


@app.get("/")
def read_root():
    return {"Hello": "World"}

@app.get('/channels')
def channels():
    return ALLOWED_CHANNELS

@app.get('/check/{channel}')
def check(channel: str):
    if channel not in ALLOWED_CHANNELS:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={'msg': 'Channel not valid'})
    return {}

@app.get("/updates/{channel}/{version}/{target}/manifest")
def manifest(channel: str, version: str, target: str):
    if channel not in ALLOWED_CHANNELS:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={'msg': 'Channel not valid'})
    if target not in ALLOWED_TARGETS:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={'msg': 'Target not valid'})
    path = f"/updates/{channel}/{version}/{target}"

    return {
        "name": TARGET_LABELS[target],
        "version": version,
        "new_install_prompt_erase": False,
        "builds": [
            {
                "chipFamily": "ESP32-S3",
                "parts": [
                    { "path": f"{path}/bootloader.bin", "offset": 0 },
                    { "path": f"{path}/partitions.bin", "offset": 32768 },
                    { "path": f"{path}/boot_app0.bin", "offset": 57344 },
                    { "path": f"{path}/firmware.bin", "offset": 65536 }
                ]
            }
        ]
    }
