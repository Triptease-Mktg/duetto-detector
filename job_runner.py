"""Background job execution — processes hotel scans and writes results to DB."""
from __future__ import annotations

import asyncio
import logging

from models import DuettoDetectionResult
from config import settings
from detector.browser_session import BrowserSession
from detector.duetto_analyzer import analyze_hotel
import db

logger = logging.getLogger(__name__)

# Track running tasks so they aren't garbage-collected
_tasks: dict[str, asyncio.Task] = {}


def launch_job(job_id: str, hotels: list[dict]) -> None:
    """Fire-and-forget a background scan job."""
    if job_id in _tasks:
        logger.warning("Job %s already running, ignoring duplicate launch", job_id)
        return
    task = asyncio.create_task(_run_job(job_id, hotels))
    _tasks[job_id] = task
    task.add_done_callback(lambda _: _tasks.pop(job_id, None))


async def _run_job(job_id: str, hotels: list[dict]) -> None:
    """Run all hotel scans for a job, writing results to DB as they complete."""
    try:
        await db.mark_job_running(job_id)
        semaphore = asyncio.Semaphore(settings.max_concurrent_scans)

        async with BrowserSession(headless=settings.headless) as browser:

            async def scan_one(index: int, hotel: dict) -> None:
                async with semaphore:
                    name = hotel["name"]
                    website = hotel["website"]
                    city = hotel.get("city", "")

                    await db.update_hotel_status(job_id, index, "scanning")

                    try:
                        result = await analyze_hotel(
                            hotel_name=name,
                            website_url=website,
                            browser_session=browser,
                            city=city,
                        )
                        await db.save_hotel_result(
                            job_id=job_id,
                            hotel_index=index,
                            result_json=result.model_dump_json(),
                            is_duetto=result.duetto_pixel_detected,
                            is_gc=result.gamechanger_detected,
                            has_competitor=bool(result.competitor_rms),
                        )
                    except Exception as e:
                        logger.error("Scan failed for %s: %s", name, e)
                        error_result = DuettoDetectionResult(
                            hotel_name=name,
                            website_url=website,
                            errors=[str(e)],
                        )
                        await db.save_hotel_error(
                            job_id, index, error_result.model_dump_json()
                        )

                    await asyncio.sleep(1.0)

            tasks = [scan_one(i, h) for i, h in enumerate(hotels)]
            await asyncio.gather(*tasks, return_exceptions=True)

        await db.mark_job_done(job_id)
        logger.info("Job %s completed", job_id)

    except Exception as e:
        logger.error("Job %s failed: %s", job_id, e)
        await db.mark_job_failed(job_id, str(e))
