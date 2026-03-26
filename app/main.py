import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database.connection import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    os.makedirs(settings.REPOS_DIR, exist_ok=True)
    create_tables
    print(f"{settings.APP_NAME} v{settings.APP_VERSION} started")
    yield

    print("Shutting down")


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.mount("/static", StaticFiles(directory="frontend/assets"), name="static")

