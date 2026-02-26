import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from models import BatchResult
from config import settings
from pipeline.csv_processor import parse_csv, results_to_csv
from detector.browser_session import BrowserSession
from detector.duetto_analyzer import analyze_hotel


_jobs: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app):
    yield


app = FastAPI(title="Duetto Detector", version="1.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/scan")
async def start_scan(csv_file: UploadFile = File(...)):
    """Upload CSV and start a scan batch."""
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
    _jobs[job_id] = {"hotels": hotels, "status": "pending"}
    return {"job_id": job_id, "hotel_count": len(hotels)}


@app.post("/scan-url")
async def start_scan_url(name: str = Form(...), website: str = Form(...)):
    """Start a scan for a single hotel URL."""
    name = name.strip()
    website = website.strip()
    if not name or not website:
        raise HTTPException(400, "Hotel name and website URL are required")
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"

    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "hotels": [{"name": name, "website": website}],
        "status": "pending",
    }
    return {"job_id": job_id, "hotel_count": 1}


@app.get("/stream/{job_id}")
async def stream_progress(job_id: str):
    """SSE endpoint for scan progress."""

    async def event_generator():
        job = _jobs.get(job_id)
        if not job:
            yield _sse({"type": "error", "message": "Job not found"})
            return

        hotels = job["hotels"]
        job["status"] = "running"

        yield _sse({"type": "started", "total": len(hotels)})

        results = []
        async with BrowserSession(headless=settings.headless) as browser:
            for i, hotel in enumerate(hotels):
                yield _sse({
                    "type": "scanning",
                    "index": i,
                    "hotel": hotel["name"],
                })

                result = await analyze_hotel(
                    hotel["name"], hotel["website"], browser
                )
                results.append(result)

                yield _sse({
                    "type": "result",
                    "index": i,
                    "hotel": hotel["name"],
                    "duetto_pixel": result.duetto_pixel_detected,
                    "gamechanger": result.gamechanger_detected,
                    "products": [p.value for p in result.duetto_products],
                    "confidence": result.confidence,
                    "booking_engine_url": result.booking_engine_url,
                    "booking_links_count": len(result.booking_links_found),
                    "scan_duration": result.scan_duration_seconds,
                    "errors": result.errors,
                })

        batch = BatchResult(
            total_hotels=len(hotels),
            scanned=len(results),
            duetto_pixel_count=sum(
                1 for r in results if r.duetto_pixel_detected
            ),
            gamechanger_count=sum(
                1 for r in results if r.gamechanger_detected
            ),
            results=results,
        )

        job["result"] = batch
        job["status"] = "done"

        yield _sse({
            "type": "done",
            "summary": {
                "total": batch.total_hotels,
                "scanned": batch.scanned,
                "pixel_count": batch.duetto_pixel_count,
                "gamechanger_count": batch.gamechanger_count,
            },
        })

    return StreamingResponse(
        event_generator(), media_type="text/event-stream"
    )


@app.get("/download/{job_id}")
async def download_csv(job_id: str):
    """Download results as CSV."""
    job = _jobs.get(job_id)
    if not job or "result" not in job:
        raise HTTPException(404, "Results not found")

    csv_content = results_to_csv(job["result"])
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=duetto_scan_{job_id}.csv"
        },
    )


@app.get("/api/scan")
async def api_scan_single(name: str, website: str):
    """Scan a single hotel and return JSON."""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"

    async with BrowserSession(headless=settings.headless) as browser:
        result = await analyze_hotel(name, website, browser)
    return result.model_dump()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
