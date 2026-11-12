from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def configure_cors(app: FastAPI, origins: list[str]) -> None:
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Cache", "Retry-After"],
        max_age=600,
    )
