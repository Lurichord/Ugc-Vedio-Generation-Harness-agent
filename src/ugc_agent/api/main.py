from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langchain.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ..agent import build_agent, make_sqlite_checkpointer
from ..store import ProjectStore
from .projects import router

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_env() -> None:
    for path in (Path(".env"), REPO_ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        break


@dataclass
class Resources:
    store: ProjectStore
    agent: Any


def build_chat_model() -> BaseChatModel:
    _load_env()
    return ChatOpenAI(
        model=os.environ["UGC_LLM_MODEL"],
        api_key=os.environ["UGC_LLM_API_KEY"],
        base_url=os.environ["UGC_LLM_BASE_URL"],
    )


def create_app(
    *,
    model: BaseChatModel | None = None,
    store_root: Path | None = None,
    checkpoints_path: Path | None = None,
) -> FastAPI:
    store_root = store_root or Path("var/projects")
    checkpoints_path = checkpoints_path or Path("var/checkpoints.sqlite")
    model = model or build_chat_model()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store_root.mkdir(parents=True, exist_ok=True)
        connection, checkpointer = make_sqlite_checkpointer(checkpoints_path)
        app.state.resources = Resources(
            store=ProjectStore(store_root),
            agent=build_agent(model, checkpointer=checkpointer),
        )
        try:
            yield
        finally:
            connection.close()

    app = FastAPI(title="UGC Video Agent", lifespan=lifespan)
    app.include_router(router)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    return app
