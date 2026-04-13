from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.routers.router import api_router
from core.config import settings
from database.database import get_db_session
from src.interventions.scheduler import WeeklyInterventionScheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler: WeeklyInterventionScheduler | None = None
    if settings.weekly_intervention_scheduler_enabled:
        scheduler = WeeklyInterventionScheduler()
        scheduler.start()
        app.state.weekly_intervention_scheduler = scheduler

    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
settings.upload_dir.mkdir(parents=True, exist_ok=True)
Path("static").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(api_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": message, "detail": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = []
    for error in exc.errors():
        cleaned_error = dict(error)
        if isinstance(cleaned_error.get("ctx"), dict):
            cleaned_error["ctx"] = {
                key: str(value)
                for key, value in cleaned_error["ctx"].items()
            }
        errors.append(cleaned_error)
    first_message = errors[0]["msg"] if errors else "요청 형식이 올바르지 않습니다."
    return JSONResponse(
        status_code=422,
        content={"message": first_message, "detail": errors},
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
async def database_health_check(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str | int]:
    result = await session.execute(text("SELECT 1"))
    return {"status": "ok", "result": result.scalar_one()}
