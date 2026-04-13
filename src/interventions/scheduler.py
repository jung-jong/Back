import asyncio
from datetime import date, timedelta
import logging

from sqlalchemy import select

from core.config import settings
from database.database import get_sessionmaker
from src.models import Course
from src.models.enums import CourseStatus
from src.interventions.service import generate_weekly_interventions

logger = logging.getLogger(__name__)


def current_week_range(today: date | None = None) -> tuple[date, date]:
    base_day = today or date.today()
    week_start = base_day - timedelta(days=base_day.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


class WeeklyInterventionScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info("Weekly intervention scheduler started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        logger.info("Weekly intervention scheduler stopped")

    async def run_once(self) -> int:
        week_start_date, week_end_date = current_week_range()
        generated_count = 0
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(Course).where(Course.status == CourseStatus.ACTIVE),
            )
            courses = list(result.scalars().all())
            for course in courses:
                interventions = await generate_weekly_interventions(
                    session=session,
                    course=course,
                    week_start_date=week_start_date,
                    week_end_date=week_end_date,
                )
                generated_count += len(interventions)

        return generated_count

    async def _loop(self) -> None:
        if settings.weekly_intervention_run_on_startup:
            await self._run_safely()

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=settings.weekly_intervention_interval_seconds,
                )
            except asyncio.TimeoutError:
                await self._run_safely()

    async def _run_safely(self) -> None:
        try:
            generated_count = await self.run_once()
            logger.info("Weekly intervention scheduler generated %s suggestions", generated_count)
        except Exception:
            logger.exception("Weekly intervention scheduler failed")
