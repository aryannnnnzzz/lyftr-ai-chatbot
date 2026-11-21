"""FastAPI app: GET /health, POST /chat, GET /sessions/{session_id}/messages."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import Settings, load_settings
from .db import Database
from .llm import AllProvidersUnavailable, LLMGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("lyftr.api")


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., max_length=100_000)
    system_prompt: Optional[str] = Field(default=None, max_length=8000)

    @field_validator("session_id")
    @classmethod
    def session_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id must not be blank")
        return value

    @field_validator("message")
    @classmethod
    def message_length(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be empty")
        if len(value) > 4000:
            raise ValueError("message must be at most 4000 characters")
        return value

    @field_validator("system_prompt")
    @classmethod
    def blank_prompt_is_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        return value.strip()


class ChatMetadata(BaseModel):
    resolution_source: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.database_path)
    gateway = LLMGateway(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init()
        logger.info(
            "started: %d key(s), simulation=%s, db=%s",
            len(settings.keys),
            settings.enable_failure_simulation,
            settings.database_path,
        )
        yield

    app = FastAPI(title="Lyftr AI Chatbot", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.gateway = gateway

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    async def health() -> Any:
        db_ok = db.ping()
        keys = len(gateway.keys)
        body = {"status": "ok", "keys_configured": keys, "cache_enabled": gateway.cache_enabled}
        if not db_ok or keys == 0:
            body["status"] = "degraded"
            body["detail"] = "database unreachable" if not db_ok else "no API keys configured"
            return JSONResponse(status_code=503, content=body)
        return body

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        payload: ChatRequest,
        x_simulate_failure: Optional[str] = Header(default=None),
    ) -> ChatResponse:
        started = time.perf_counter()

        system_prompt = (
            payload.system_prompt
            or db.get_system_prompt(payload.session_id)
            or settings.default_system_prompt
        )
        history = db.get_messages(payload.session_id)
        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": payload.message})

        try:
            result = await gateway.complete(messages, simulate_header=x_simulate_failure)
        except AllProvidersUnavailable:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        metadata = ChatMetadata(
            resolution_source=result.resolution_source,
            model=result.model,
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            key_id=result.key_id,
            fallback_attempts=result.fallback_attempts,
        )
        db.save_exchange(
            session_id=payload.session_id,
            user_message=payload.message,
            assistant_reply=result.reply,
            metadata=metadata.model_dump(),
            system_prompt=payload.system_prompt,
        )
        return ChatResponse(session_id=payload.session_id, reply=result.reply, metadata=metadata)

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str) -> Any:
        if not db.session_exists(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"session_id": session_id, "messages": db.get_messages(session_id)}

    return app


app = create_app()
