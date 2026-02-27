"""Fallback 2: Web search for hotel booking engine URLs.

Uses Firecrawl search() to find booking-related pages for a hotel,
filters results through the known booking engine domain registry,
and optionally uses Claude Haiku to pick the best match.

Cost: 1-2 Firecrawl search calls + 0-1 Haiku calls.
"""
from __future__ import annotations

import asyncio
import json
import logging

from models import BookingLinkInfo
from config import settings

logger = logging.getLogger(__name__)

# OTAs and meta-search sites — NOT the hotel's own booking engine.
OTA_DOMAINS = {
    "booking.com", "expedia.com", "hotels.com", "kayak.com",
    "tripadvisor.com", "agoda.com", "priceline.com", "trivago.com",
    "hotwire.com", "orbitz.com", "travelocity.com", "trip.com",
    "momondo.com", "skyscanner.com", "cheaptickets.com", "lastminute.com",
    "hostelworld.com", "google.com",
}


def _build_search_queries(hotel_name: str, website_url: str) -> list[str]:
    """Build a prioritised list of search queries (max 2)."""
    from detector.fallback_chain_patterns import get_search_hint

    safe_name = hotel_name.replace('"', "").strip()
    queries: list[str] = []

    # Chain-specific hint first (if applicable)
    hint = get_search_hint(website_url, safe_name)
    if hint:
        queries.append(hint)

    # Generic query
    queries.append(f'"{safe_name}" hotel booking reservations book room')
    return queries


def _search_firecrawl(query: str, limit: int = 5) -> list[dict]:
    """Run a Firecrawl search (sync — called via asyncio.to_thread)."""
    from firecrawl import Firecrawl

    app = Firecrawl(api_key=settings.firecrawl_api_key)
    result = app.search(query, limit=limit)

    if not result or not result.web:
        return []

    return [
        {
            "url": item.url,
            "title": getattr(item, "title", None) or "",
            "description": getattr(item, "description", None) or "",
        }
        for item in result.web
        if hasattr(item, "url") and item.url
    ]


def _pick_best_with_llm(candidates: list[dict], hotel_name: str) -> dict | None:
    """Use Claude Haiku to pick the best booking engine URL from candidates."""
    from anthropic import Anthropic

    if not candidates:
        return None

    client = Anthropic(api_key=settings.anthropic_api_key)
    candidates_text = "\n".join(
        f'{i+1}. URL: {c["url"]}\n   Title: {c["title"]}\n   Description: {c["description"]}'
        for i, c in enumerate(candidates)
    )

    prompt = f"""You are selecting the best booking engine URL for "{hotel_name}".

Here are the search results:
{candidates_text}

Pick the result that is most likely to be the direct booking/reservation page
where a guest can select dates and book a room at this specific hotel.
Prefer URLs from known booking engine providers (SynXis, TravelClick,
SiteMinder, etc.) over marketing pages.

Return ONLY valid JSON: {{"index": 1, "reason": "..."}}
If none are relevant: {{"index": 0, "reason": "..."}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    try:
        data = json.loads(text)
        idx = data.get("index", 0)
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM returned invalid JSON for search pick: %s", text[:200])

    return None


async def find_booking_links_web_search(
    hotel_name: str,
    website_url: str,
) -> list[BookingLinkInfo]:
    """Fallback 2: Search the web for the hotel's booking engine URL."""
    from detector.booking_engine_domains import (
        url_matches_booking_engine,
        extract_base_domain,
    )

    hotel_base = extract_base_domain(website_url)
    queries = _build_search_queries(hotel_name, website_url)

    for query in queries:
        logger.info("Web search: trying query '%s'", query)

        try:
            raw_results = await asyncio.to_thread(_search_firecrawl, query)
        except Exception as e:
            logger.warning("Web search failed for '%s': %s", query, e)
            continue

        if not raw_results:
            logger.info("Web search: no results for '%s'", query)
            continue

        # Tier 1: known booking engine domains, excluding hotel's own and OTAs
        booking_candidates: list[dict] = []
        for r in raw_results:
            r_base = extract_base_domain(r["url"])
            if r_base == hotel_base or r_base in OTA_DOMAINS:
                continue
            if url_matches_booking_engine(r["url"]):
                booking_candidates.append(r)

        # Tier 2: any external link with booking-related title/description
        if not booking_candidates:
            for r in raw_results:
                r_base = extract_base_domain(r["url"])
                if r_base == hotel_base or r_base in OTA_DOMAINS:
                    continue
                combined = f"{r['title']} {r['description']}".lower()
                if any(w in combined for w in ("book", "reserv", "room", "rate")):
                    booking_candidates.append(r)

        if not booking_candidates:
            logger.info("Web search: no booking candidates for '%s'", query)
            continue

        # Single candidate → use directly; multiple → LLM disambiguates
        if len(booking_candidates) == 1:
            best = booking_candidates[0]
        elif settings.anthropic_api_key:
            try:
                best = await asyncio.to_thread(
                    _pick_best_with_llm, booking_candidates, hotel_name
                )
            except Exception as e:
                logger.warning("LLM pick failed: %s", e)
                best = booking_candidates[0]
        else:
            best = booking_candidates[0]

        if best:
            logger.info("Web search: found %s for %s", best["url"], hotel_name)
            return [
                BookingLinkInfo(
                    text=best.get("title") or "Booking Page",
                    href=best["url"],
                    link_type="link",
                    detection_method="web_search",
                    opens_in="new_tab",
                )
            ]

    logger.info("Web search: all queries exhausted for %s", hotel_name)
    return []
