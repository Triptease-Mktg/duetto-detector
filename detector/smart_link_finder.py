"""Firecrawl + Claude Haiku booking link discovery."""
from __future__ import annotations

import asyncio
import json
import logging

from models import BookingLinkInfo
from config import settings

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are analyzing a hotel website to find the booking engine link.
The hotel website URL is: {url}

Here is the page content in markdown:
{markdown}

Identify the link(s) that lead to the hotel's EXTERNAL booking engine where
guests can search rooms and make reservations. Look for:
- "Book Now", "Reserve", "Check Availability" buttons/links that go to a DIFFERENT domain
- Links to known booking engine domains (synxis, travelclick, siteminder, cloudbeds, mews, profitroom, bookassist, d-edge, roiback, mirai, etc.)
- Embedded booking widgets or iframes from external domains

IMPORTANT: Do NOT return links that point back to the hotel's own website.
Only return links to external booking engine domains.

Return ONLY valid JSON, no other text:
{{"links": [{{"url": "https://...", "text": "Book Now", "confidence": "high"}}]}}
If no booking link found: {{"links": []}}"""


def _scrape_url(url: str) -> str | None:
    """Scrape a URL with Firecrawl and return markdown content."""
    from firecrawl import Firecrawl

    app = Firecrawl(api_key=settings.firecrawl_api_key)
    doc = app.scrape(url, formats=["markdown"])

    if not doc:
        return None

    markdown = doc.markdown or ""
    if not markdown:
        return None

    # Truncate to keep Haiku costs low
    if len(markdown) > 8000:
        markdown = markdown[:8000] + "\n\n[content truncated]"

    return markdown


def _ask_llm(markdown: str, url: str) -> list[dict]:
    """Send page markdown to Claude Haiku to identify booking links."""
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": PROMPT_TEMPLATE.format(markdown=markdown, url=url),
            }
        ],
    )

    text = message.content[0].text.strip()

    # Parse JSON from response (handle markdown code fences)
    if "```" in text:
        # Extract content between code fences
        parts = text.split("```")
        for part in parts:
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
        logger.warning("LLM returned invalid JSON: %s", text[:200])
        return []


async def find_booking_links_smart(url: str) -> list[BookingLinkInfo]:
    """Use Firecrawl + Claude Haiku to find booking links on a hotel website."""
    from urllib.parse import urlparse

    # Run sync Firecrawl call in a thread
    markdown = await asyncio.to_thread(_scrape_url, url)
    if not markdown:
        logger.info("Firecrawl returned no content for %s", url)
        return []

    # Run sync Anthropic call in a thread
    links_data = await asyncio.to_thread(_ask_llm, markdown, url)
    if not links_data:
        return []

    # Filter out links pointing back to the hotel's own domain
    hotel_domain = urlparse(url).netloc.lower().lstrip("www.")

    confidence_order = {"high": 0, "medium": 1, "low": 2}
    links_data.sort(key=lambda x: confidence_order.get(x.get("confidence", "low"), 2))

    results = []
    for item in links_data:
        link_url = item.get("url", "").strip()
        if not link_url or not link_url.startswith("http"):
            continue

        # Skip same-domain links (not a booking engine)
        link_domain = urlparse(link_url).netloc.lower().lstrip("www.")
        if link_domain == hotel_domain:
            logger.info("Skipping same-domain link: %s", link_url[:80])
            continue

        results.append(
            BookingLinkInfo(
                text=item.get("text", "Book Now"),
                href=link_url,
                link_type="link",
                detection_method="firecrawl_llm",
                opens_in="new_tab",
            )
        )

    return results
