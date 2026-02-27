"""AI-first booking link discovery via Claude Haiku.

Asks Claude Haiku directly for the booking URL based on hotel name and city.
No Firecrawl, no web scraping — just one cheap Haiku API call.

Cost: 1 Haiku call.
"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse

from models import BookingLinkInfo
from config import settings

logger = logging.getLogger(__name__)

OTA_DOMAINS = {
    "booking.com", "expedia.com", "hotels.com", "kayak.com",
    "tripadvisor.com", "agoda.com", "priceline.com", "trivago.com",
    "hotwire.com", "orbitz.com", "travelocity.com", "trip.com",
    "google.com", "momondo.com", "skyscanner.com",
}

PROMPT = """What is the direct booking URL for the hotel "{hotel_name}" in {city}?

I need the URL of the hotel's own booking engine or reservation page where
guests can select dates and book a room directly. This should be:
- The hotel's OWN booking page (not an OTA like Booking.com, Expedia, etc.)
- A URL that starts with http:// or https://
- Often hosted on a booking engine domain like synxis, travelclick, siteminder, etc.
- Or a /booking or /reservations path on the hotel's or brand's website

Return ONLY valid JSON, no other text:
{{"url": "https://...", "confidence": "high"}}
If you don't know: {{"url": "", "confidence": "none"}}"""


def _query_haiku(hotel_name: str, city: str) -> dict:
    """Synchronous Haiku call (run via asyncio.to_thread)."""
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": PROMPT.format(hotel_name=hotel_name, city=city),
        }],
    )

    text = message.content[0].text.strip()
    # Handle code fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("AI booking query: invalid JSON: %s", text[:200])
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


async def find_booking_link_via_ai(
    hotel_name: str, city: str
) -> list[BookingLinkInfo]:
    """Ask Claude Haiku for the direct booking URL."""
    if not settings.anthropic_api_key or not city:
        return []

    try:
        data = await asyncio.to_thread(_query_haiku, hotel_name, city)
    except Exception as e:
        logger.warning("AI booking query failed for %s: %s", hotel_name, e)
        return []

    url = data.get("url", "").strip()
    confidence = data.get("confidence", "none")

    if not _validate_url(url):
        logger.info("AI booking query: no valid URL for %s in %s", hotel_name, city)
        return []

    if confidence == "none":
        logger.info("AI booking query: low confidence for %s, skipping", hotel_name)
        return []

    logger.info("AI booking query: found %s for %s (confidence: %s)", url, hotel_name, confidence)
    return [
        BookingLinkInfo(
            text="AI-suggested booking link",
            href=url,
            link_type="link",
            detection_method="ai_query",
            opens_in="new_tab",
        )
    ]
