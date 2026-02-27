import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from models import BatchResult, DuettoDetectionResult
from config import settings
from pipeline.csv_processor import parse_csv, results_to_csv
from detector.browser_session import BrowserSession
from detector.duetto_analyzer import analyze_hotel
import db
from job_runner import launch_job


@asynccontextmanager
async def lifespan(app):
    await db.init_db()
    await db._recover_orphaned_jobs()
    yield


app = FastAPI(title="Duetto Detector", version="2.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.post("/scan")
async def start_scan(csv_file: UploadFile = File(...)):
    """Upload CSV and start a background scan batch."""
    content = await csv_file.read()
    hotels = parse_csv(content)

    if not hotels:
        raise HTTPException(400, "No valid hotels found in CSV")
    if len(hotels) > settings.max_hotels_per_batch:
        raise HTTPException(
            400,
            f"Maximum {settings.max_hotels_per_batch} hotels per batch",
        )

    job_id = uuid.uuid4().hex[:12]
    await db.create_job(job_id, hotels)
    launch_job(job_id, hotels)
    return {"job_id": job_id, "hotel_count": len(hotels)}


@app.post("/scan-url")
async def start_scan_url(
    name: str = Form(...),
    website: str = Form(...),
    city: str = Form(""),
):
    """Start a background scan for a single hotel URL."""
    name = name.strip()
    website = website.strip()
    city = city.strip()
    if not name or not website:
        raise HTTPException(400, "Hotel name and website URL are required")
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"

    hotels = [{"name": name, "website": website, "city": city}]
    job_id = uuid.uuid4().hex[:12]
    await db.create_job(job_id, hotels)
    launch_job(job_id, hotels)
    return {"job_id": job_id, "hotel_count": 1}


@app.get("/stream/{job_id}")
async def stream_progress(job_id: str):
    """SSE endpoint that polls DB for scan progress."""

    async def event_generator():
        job = await db.get_job(job_id)
        if not job:
            yield _sse({"type": "error", "message": "Job not found"})
            return

        yield _sse({"type": "started", "total": job["total_hotels"]})

        last_seen_done = 0
        sent_scanning: set[int] = set()

        while True:
            job = await db.get_job(job_id)
            hotels = await db.get_job_hotels(job_id)

            # Yield events for in-progress hotels (only once per hotel)
            for h in hotels:
                if h["status"] == "scanning" and h["hotel_index"] not in sent_scanning:
                    sent_scanning.add(h["hotel_index"])
                    yield _sse({
                        "type": "scanning",
                        "index": h["hotel_index"],
                        "hotel": h["hotel_name"],
                    })

            # Find completed hotels and yield results for new ones
            done_hotels = [
                h for h in hotels
                if h["status"] in ("done", "error") and h["result_json"]
            ]
            done_hotels.sort(key=lambda h: h["hotel_index"])

            for h in done_hotels[last_seen_done:]:
                try:
                    result = DuettoDetectionResult.model_validate_json(
                        h["result_json"]
                    )
                    yield _sse({
                        "type": "result",
                        "index": h["hotel_index"],
                        "hotel": h["hotel_name"],
                        "duetto_pixel": result.duetto_pixel_detected,
                        "gamechanger": result.gamechanger_detected,
                        "products": [p.value for p in result.duetto_products],
                        "confidence": result.confidence,
                        "booking_engine_url": result.booking_engine_url,
                        "booking_links_count": len(result.booking_links_found),
                        "competitor_rms": [
                            {"vendor": c.vendor, "category": c.category}
                            for c in result.competitor_rms
                        ],
                        "scan_duration": result.scan_duration_seconds,
                        "errors": result.errors,
                    })
                except Exception:
                    yield _sse({
                        "type": "result",
                        "index": h["hotel_index"],
                        "hotel": h["hotel_name"],
                        "duetto_pixel": False,
                        "gamechanger": False,
                        "products": [],
                        "confidence": "none",
                        "booking_engine_url": "",
                        "booking_links_count": 0,
                        "competitor_rms": [],
                        "scan_duration": 0,
                        "errors": ["Failed to parse result"],
                    })

            last_seen_done = len(done_hotels)

            # Check if job is complete
            if job and job["status"] in ("done", "failed"):
                yield _sse({
                    "type": "done",
                    "summary": {
                        "total": job["total_hotels"],
                        "scanned": job["scanned_count"],
                        "pixel_count": job["duetto_pixel_count"],
                        "gamechanger_count": job["gamechanger_count"],
                        "competitor_rms_count": job["competitor_rms_count"],
                    },
                })
                return

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream"
    )


@app.get("/download/{job_id}")
async def download_csv(job_id: str):
    """Download results as CSV."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    result_jsons = await db.get_job_results_json(job_id)
    results = [
        DuettoDetectionResult.model_validate_json(rj) for rj in result_jsons
    ]

    batch = BatchResult(
        total_hotels=job["total_hotels"],
        scanned=job["scanned_count"],
        duetto_pixel_count=job["duetto_pixel_count"],
        gamechanger_count=job["gamechanger_count"],
        competitor_rms_count=job["competitor_rms_count"],
        results=results,
    )

    csv_content = results_to_csv(batch)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=duetto_scan_{job_id}.csv"
        },
    )


@app.get("/api/jobs")
async def api_list_jobs():
    """List all jobs for the dashboard."""
    jobs = await db.list_jobs()
    return jobs


@app.get("/api/jobs/{job_id}")
async def api_get_job(job_id: str):
    """Get job details with hotel results."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    hotels = await db.get_job_hotels(job_id)
    return {"job": job, "hotels": hotels}


@app.get("/api/scan")
async def api_scan_single(name: str, website: str, city: str = ""):
    """Scan a single hotel and return JSON (synchronous for API use)."""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"

    async with BrowserSession(headless=settings.headless) as browser:
        result = await analyze_hotel(name, website, browser, city=city)
    return result.model_dump()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
