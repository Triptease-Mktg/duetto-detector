"""Fallback 3: Brand site deep crawl to find property booking links.

For brand sites (marriott.com, hilton.com, etc.), uses Firecrawl map()
to discover property-specific pages, LLM to pick the right property,
then scrape + LLM to extract booking links from that page.

Cost: 1 Firecrawl map + 1 Haiku + 1 Firecrawl scrape + 1 Haiku ≈ 4 API calls.
"""
from __future__ import annotations

import asyncio
import json
import logging

from models import BookingLinkInfo
from config import settings

logger = logging.getLogger(__name__)


def _map_brand_site(brand_url: str, hotel_name: str, limit: int = 20) -> list[dict]:
    """Use Firecrawl map() to discover pages on a brand site (sync)."""
    from firecrawl import Firecrawl

    app = Firecrawl(api_key=settings.firecrawl_api_key)
    result = app.map(brand_url, search=hotel_name, limit=limit)

    if not result or not result.links:
        return []

    return [
        {
            "url": link.url,
            "title": getattr(link, "title", None) or "",
            "description": getattr(link, "description", None) or "",
        }
        for link in result.links
        if link.url
    ]


def _pick_property_page(
    links: list[dict],
    hotel_name: str,
    brand_url: str,
) -> str | None:
    """Use Claude Haiku to select the property page from map results (sync)."""
    from anthropic import Anthropic

    if not links:
        return None

    client = Anthropic(api_key=settings.anthropic_api_key)

    links_text = "\n".join(
        f'{i+1}. URL: {l["url"]}\n   Title: {l["title"]}\n   Description: {l["description"]}'
        for i, l in enumerate(links[:15])
    )

    prompt = f"""You are looking for the specific property page for "{hotel_name}" on the brand website {brand_url}.

Here are pages found on the brand site:
{links_text}

Which URL is the specific hotel/property page for "{hotel_name}"?
This should be the property's detail/overview page, NOT a search results
page, NOT the brand homepage, and NOT a generic listing.

Return ONLY valid JSON: {{"index": 1, "reason": "..."}}
If no page matches this specific property: {{"index": 0, "reason": "..."}}"""

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
        if 1 <= idx <= len(links):
            return links[idx - 1]["url"]
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM property pick invalid JSON: %s", text[:200])

    return None


def _scrape_and_extract(page_url: str, hotel_name: str) -> list[dict]:
    """Scrape a property page and extract booking links via LLM (sync)."""
    from firecrawl import Firecrawl
    from anthropic import Anthropic

    app = Firecrawl(api_key=settings.firecrawl_api_key)
    doc = app.scrape(page_url, formats=["markdown"])

    if not doc or not doc.markdown:
        return []

    markdown = doc.markdown
    if len(markdown) > 8000:
        markdown = markdown[:8000] + "\n\n[content truncated]"

    client = Anthropic(api_key=settings.anthropic_api_key)

    prompt = f"""You are analyzing the property page for "{hotel_name}" to find booking engine links.

Page URL: {page_url}
Page content:
{markdown}

Find links that lead to the booking/reservation engine where a guest can
select dates and book a room. These may be:
- "Book Now" / "Reserve" buttons linking to a booking engine
- Links to external booking domains (synxis, travelclick, etc.)
- Links to internal reservation paths (/reservation/...)

Return ONLY valid JSON:
{{"links": [{{"url": "https://...", "text": "Book Now"}}]}}
If no booking link found: {{"links": []}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
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
        return data.get("links", [])
    except json.JSONDecodeError:
        logger.warning("LLM brand crawl invalid JSON: %s", text[:200])
        return []


async def find_booking_links_brand_crawl(
    hotel_name: str,
    website_url: str,
) -> list[BookingLinkInfo]:
    """Fallback 3: Deep crawl a brand site to find property booking links."""
    logger.info("Brand crawl: starting for '%s' on %s", hotel_name, website_url)

    # Step 1: Map the brand site for property pages
    try:
        links = await asyncio.to_thread(_map_brand_site, website_url, hotel_name)
    except Exception as e:
        logger.warning("Brand crawl map failed for %s: %s", website_url, e)
        return []

    if not links:
        logger.info("Brand crawl: map returned no links for %s", website_url)
        return []

    logger.info("Brand crawl: map found %d links for %s", len(links), website_url)

    # Step 2: LLM picks the right property page
    try:
        property_url = await asyncio.to_thread(
            _pick_property_page, links, hotel_name, website_url
        )
    except Exception as e:
        logger.warning("Brand crawl property pick failed: %s", e)
        return []

    if not property_url:
        logger.info("Brand crawl: could not identify property page for '%s'", hotel_name)
        return []

    logger.info("Brand crawl: picked property page %s", property_url)

    # Step 3: Scrape property page and extract booking links
    try:
        raw_links = await asyncio.to_thread(
            _scrape_and_extract, property_url, hotel_name
        )
    except Exception as e:
        logger.warning("Brand crawl scrape failed for %s: %s", property_url, e)
        return []

    if not raw_links:
        logger.info("Brand crawl: no booking links on %s", property_url)
        return []

    results = []
    for item in raw_links:
        link_url = item.get("url", "").strip()
        if not link_url or not link_url.startswith("http"):
            continue
        results.append(
            BookingLinkInfo(
                text=item.get("text", "Book Now"),
                href=link_url,
                link_type="link",
                detection_method="brand_crawl",
                opens_in="new_tab",
            )
        )

    logger.info("Brand crawl: found %d link(s) for '%s'", len(results), hotel_name)
    return results
