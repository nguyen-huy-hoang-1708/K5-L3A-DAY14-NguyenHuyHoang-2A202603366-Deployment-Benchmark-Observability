"""
Production AI Agent — Part 6 Final Project

    uvicorn app.main:app --workers 2
    python check_production_ready.py      # → PASS 12/12

Tiêu chí đáp ứng:
  /health (liveness), /ready (readiness, 503 khi mất Redis), X-API-Key → 401,
  rate limit Redis → 429, cost guard → 402, config từ env, log JSON, graceful shutdown.
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown
# ─────────────────────────────────────────────────────────
STATE = {"ready": False, "in_flight": 0}


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              redis_ok=redis_healthy(), rate_limit_per_minute=settings.rate_limit_per_minute)
    STATE["ready"] = True
    yield
    # Uvicorn nhận SIGTERM → ngừng nhận kết nối mới, chờ request đang chạy xong,
    # rồi mới chạy tới đây. Không tự đăng ký signal handler để không đè handler của uvicorn.
    STATE["ready"] = False
    log_event("shutdown", in_flight=STATE["in_flight"])


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs")


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    STATE["in_flight"] += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        STATE["in_flight"] -= 1
        log_event("request", request_id=request_id, method=request.method,
                  path=request.url.path, status=status,
                  ms=round((time.perf_counter() - started) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling — mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "msg": e.get("msg")}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request body", "errors": errors})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, path=request.url.path, exc_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: process còn sống là 200, KHÔNG phụ thuộc Redis."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được."""
    if not STATE["ready"]:
        return JSONResponse(status_code=503, content={"status": "shutting_down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "down"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)          # 429
    check_budget()                     # 402
    answer = llm_ask(body.question)
    cost = record_cost(estimate_tokens(body.question), estimate_tokens(answer))
    log_event("llm_call", user_id=body.user_id, q_len=len(body.question),
              cost_usd=round(cost, 8))
    return {"question": body.question, "answer": answer, "user_id": body.user_id}
