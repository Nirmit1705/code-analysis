"""FastAPI app assembly: CORS, exception handlers, router wiring, health check."""

import logging

from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.router import api_router
from app.api.ws.analysis_progress import router as ws_router
from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger("app")

app = FastAPI(title="AI Codebase Intelligence Platform", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # `jsonable_encoder` (as FastAPI's own default handler does): a
    # `field_validator` that raises `ValueError` puts that exception object in
    # the error's `ctx`, which plain `json.dumps` can't serialise — without
    # this, every such validation failure became a 500 (found in Phase 10.11).
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation error", "errors": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log the full exception server-side; never leak stack traces to the client.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


app.include_router(api_router)
# No `/api/v1` prefix — the WS path is a bare `/ws/analysis-runs/{id}` per
# the plan doc, registered directly on `app` rather than through `api_router`.
app.include_router(ws_router)


@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
