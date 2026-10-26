from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

app = FastAPI(
    title="Observable Task Pipeline",
    version="0.2.0",
    description="Observable and recoverable long-running task pipeline MVP",
)
app.include_router(router)

STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/", include_in_schema=False)
def dashboard_page() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/tasks/{task_id}", include_in_schema=False)
def task_detail_page(task_id: str) -> FileResponse:
    return FileResponse(STATIC_ROOT / "task-detail.html")
