from __future__ import annotations

import csv
import io

from models import BatchResult


NAME_ALIASES = [
    "name", "hotel_name", "hotel name", "account name", "property",
    "property name", "hotel",
]
WEBSITE_ALIASES = [
    "website", "url", "website url", "site", "hotel url", "web",
    "homepage", "link",
]


def _find_column(fieldnames: list[str], aliases: list[str]) -> str | None:
    """Find the first matching column name from a list of aliases."""
    for alias in aliases:
        if alias in fieldnames:
            return alias
    return None


def parse_csv(content: str | bytes) -> list[dict]:
    """Parse CSV with flexible column names. Returns list of dicts."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")  # Handle BOM

    reader = csv.DictReader(io.StringIO(content))

    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]

    name_col = _find_column(reader.fieldnames or [], NAME_ALIASES)
    website_col = _find_column(reader.fieldnames or [], WEBSITE_ALIASES)

    hotels = []
    for row in reader:
        name = row.get(name_col or "name", "").strip()
        website = row.get(website_col or "website", "").strip()
        if not name or not website:
            continue
        if not website.startswith(("http://", "https://")):
            website = f"https://{website}"
        hotels.append({"name": name, "website": website})

    return hotels


def results_to_csv(batch: BatchResult) -> str:
    """Convert batch results to CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "hotel_name",
        "website_url",
        "duetto_pixel_detected",
        "gamechanger_detected",
        "duetto_products",
        "confidence",
        "booking_engine_url",
        "booking_links_count",
        "pixel_request_urls",
        "proof_snippets",
        "scan_duration_seconds",
        "errors",
    ])

    for r in batch.results:
        writer.writerow([
            r.hotel_name,
            r.website_url,
            r.duetto_pixel_detected,
            r.gamechanger_detected,
            "; ".join(p.value for p in r.duetto_products),
            r.confidence,
            r.booking_engine_url,
            len(r.booking_links_found),
            "; ".join(pr.url for pr in r.pixel_requests),
            " | ".join(r.proof_snippets) if r.proof_snippets else "",
            f"{r.scan_duration_seconds:.1f}",
            "; ".join(r.errors) if r.errors else "",
        ])

    return output.getvalue()
