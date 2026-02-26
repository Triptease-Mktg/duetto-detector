from __future__ import annotations

import asyncio
from typing import Callable

from models import BatchResult, DuettoDetectionResult
from config import settings
from detector.browser_session import BrowserSession
from detector.duetto_analyzer import analyze_hotel


async def run_batch(
    hotels: list[dict],
    max_concurrent: int | None = None,
    screenshot_dir: str | None = None,
    on_progress: Callable | None = None,
) -> BatchResult:
    """Process a batch of hotels with controlled concurrency."""
    max_concurrent = max_concurrent or settings.max_concurrent_scans
    semaphore = asyncio.Semaphore(max_concurrent)

    async with BrowserSession(headless=settings.headless) as browser:

        async def scan_one(index: int, hotel: dict) -> DuettoDetectionResult:
            async with semaphore:
                if on_progress:
                    on_progress(index, hotel["name"], "scanning")

                result = await analyze_hotel(
                    hotel_name=hotel["name"],
                    website_url=hotel["website"],
                    browser_session=browser,
                    screenshot_dir=screenshot_dir,
                )

                if on_progress:
                    on_progress(index, hotel["name"], "done")

                # Respectful delay between scans
                await asyncio.sleep(1.0)
                return result

        tasks = [scan_one(i, h) for i, h in enumerate(hotels)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    final_results = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            final_results.append(DuettoDetectionResult(
                hotel_name=hotels[i]["name"],
                website_url=hotels[i]["website"],
                errors=[str(r)],
            ))
        else:
            final_results.append(r)

    return BatchResult(
        total_hotels=len(hotels),
        scanned=len(final_results),
        duetto_pixel_count=sum(
            1 for r in final_results if r.duetto_pixel_detected
        ),
        gamechanger_count=sum(
            1 for r in final_results if r.gamechanger_detected
        ),
        results=final_results,
    )
