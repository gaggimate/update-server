from fastapi import FastAPI

from app.routers.updates import router as updates_router

app = FastAPI(title="Update Server")
app.include_router(updates_router)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"status": "ok", "service": "update-server"}
