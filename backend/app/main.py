from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health


def create_app() -> FastAPI:
    app = FastAPI(
        title="SleekFlow TODO API",
        version="0.1.0",
        description="Shared TODO list with dependencies, recurrence, and optimistic concurrency.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )
    app.include_router(health.router)
    return app


app = create_app()
