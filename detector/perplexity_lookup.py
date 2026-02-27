"""Perplexity API-based hotel URL discovery.

Uses Perplexity's sonar model (web-search-backed LLM) to find:
1. The hotel's official website
2. The hotel's direct booking/reservation URL

Cost: 1 Perplexity API call per hotel.
"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse

import requests

from config import settings

logger = logging.getLogger(__name__)

OTA_DOMAINS = {
    "booking.com", "expedia.com", "hotels.com", "kayak.com",
    "tripadvisor.com", "agoda.com", "priceline.com", "trivago.com",
    "hotwire.com", "orbitz.com", "travelocity.com", "trip.com",
    "google.com", "momondo.com", "skyscanner.com",
}

PROMPT = """What is the official website and official direct booking URL for the hotel "{hotel_name}" in {city}?

I need two URLs:
1. **Official website**: The hotel's own website homepage (e.g. https://www.theplazany.com)
2. **Booking URL**: The hotel's own booking/reservation page where guests select dates and book directly.
   - Often hosted on a booking engine domain (synxis, travelclick, siteminder, cloudbeds, etc.)
   - Or a /booking, /reservations, /book-now path on the hotel's or brand's website
   - Must NOT be an OTA (Booking.com, Expedia, Hotels.com, Agoda, etc.)

Return ONLY valid JSON, no other text:
{{"official_website": "https://...", "booking_url": "https://...", "confidence": "high"}}
If you only know the website: {{"official_website": "https://...", "booking_url": "", "confidence": "medium"}}
If you don't know: {{"official_website": "", "booking_url": "", "confidence": "none"}}"""


def _query_perplexity(hotel_name: str, city: str) -> dict:
    """Synchronous Perplexity API call (run via asyncio.to_thread)."""
    response = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.perplexity_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "sonar",
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT.format(hotel_name=hotel_name, city=city),
                }
            ],
            "max_tokens": 300,
            "temperature": 0.1,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    text = data["choices"][0]["message"]["content"].strip()

    # Handle code fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    # Handle text before/after JSON
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Perplexity lookup: invalid JSON: %s", text[:200])
        return {}


def _validate_url(url: str) -> bool:
    """Check that URL is valid and not an OTA."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return False
        base = parsed.netloc.lower().lstrip("www.")
        parts = base.split(".")
        if len(parts) >= 2:
            registrable = ".".join(parts[-2:])
            if registrable in OTA_DOMAINS:
                return False
        return True
    except Exception:
        return False


async def lookup_hotel_urls(
    hotel_name: str, city: str
) -> dict:
    """Ask Perplexity for the hotel's official website and booking URL.

    Returns dict with keys: official_website, booking_url, confidence
    """
    if not settings.perplexity_api_key or not city:
        return {"official_website": "", "booking_url": "", "confidence": "none"}

    try:
        data = await asyncio.to_thread(_query_perplexity, hotel_name, city)
    except Exception as e:
        logger.warning("Perplexity lookup failed for %s: %s", hotel_name, e)
        return {"official_website": "", "booking_url": "", "confidence": "none"}

    official = data.get("official_website", "").strip()
    booking = data.get("booking_url", "").strip()
    confidence = data.get("confidence", "none")

    if not _validate_url(official):
        official = ""
    if not _validate_url(booking):
        booking = ""

    if confidence == "none" and not official:
        logger.info("Perplexity: no results for %s in %s", hotel_name, city)
        return {"official_website": "", "booking_url": "", "confidence": "none"}

    logger.info(
        "Perplexity: %s in %s → website=%s, booking=%s (confidence: %s)",
        hotel_name, city, official or "?", booking or "?", confidence,
    )
    return {
        "official_website": official,
        "booking_url": booking,
        "confidence": confidence,
    }
