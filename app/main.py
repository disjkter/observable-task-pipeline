from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="Observable Task Pipeline",
    version="0.1.0",
    description="Observable and recoverable long-running task pipeline MVP",
)
app.include_router(router)
