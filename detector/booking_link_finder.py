import logging
import re
from playwright.async_api import Page
from models import BookingLinkInfo
from config import settings

logger = logging.getLogger(__name__)


# Text patterns that indicate a booking CTA (multilingual)
BOOKING_TEXT_PATTERNS = [
    re.compile(r"book\s*now", re.IGNORECASE),
    re.compile(r"book\s*(?:a\s*)?room", re.IGNORECASE),
    re.compile(r"book\s*(?:your\s*)?stay", re.IGNORECASE),
    re.compile(r"book\s*direct", re.IGNORECASE),
    re.compile(r"reserve\s*(?:now|a\s*room)?", re.IGNORECASE),
    re.compile(r"check\s*availab", re.IGNORECASE),
    re.compile(r"make\s*(?:a\s*)?reservat", re.IGNORECASE),
    re.compile(r"plan\s*your\s*stay", re.IGNORECASE),
    re.compile(r"view\s*(?:rooms?|rates?|availab)", re.IGNORECASE),
    re.compile(r"buchen", re.IGNORECASE),  # German
    re.compile(r"jetzt\s*buchen", re.IGNORECASE),  # German
    re.compile(r"zimmer\s*buchen", re.IGNORECASE),  # German
    re.compile(r"r[eé]server", re.IGNORECASE),  # French
    re.compile(r"reservar", re.IGNORECASE),  # Spanish
    re.compile(r"prenota", re.IGNORECASE),  # Italian
    re.compile(r"boek\s*nu", re.IGNORECASE),  # Dutch
]

# Href substrings that indicate a booking engine
BOOKING_HREF_PATTERNS = [
    "reservations.", "bookings.", "book.", "reserve.",
    "synxis", "travelclick", "siteminder", "cloudbeds",
    "mews.", "guestcentric", "bookdirect", "bookassist",
    "profitroom", "duettoresearch", "duettocloud",
    "/booking", "/reservation", "/reserve", "/book-now",
    "be.synxis.com", "gc.synxis.com",
    "booking-engine", "ibe.", "wbe.",
    "rfrb.net",  # Roiback
    "omnibees.com",
    "d-edge.com",
    "seekda.com",
]

# Iframe src patterns for embedded booking engines
IFRAME_BOOKING_PATTERNS = [
    "booking", "reserv", "synxis", "travelclick",
    "siteminder", "cloudbeds", "duetto", "mews",
    "bookassist", "profitroom",
]


async def find_booking_links(page: Page) -> list[BookingLinkInfo]:
    """Find all booking-related links on the page using multiple strategies."""
    results: list[BookingLinkInfo] = []
    seen_hrefs: set[str] = set()

    # Strategy 1: Text-based matching on visible <a> and <button> elements
    await _find_by_text(page, results, seen_hrefs)

    # Strategy 2: Href pattern matching on all links
    await _find_by_href(page, results, seen_hrefs)

    # Strategy 3: Iframe detection
    await _find_by_iframe(page, results, seen_hrefs)

    return results


async def _find_by_text(
    page: Page, results: list[BookingLinkInfo], seen: set[str]
):
    """Find booking links by matching visible text content."""
    for pattern in BOOKING_TEXT_PATTERNS:
        for tag in ["a", "button"]:
            locator = page.locator(f"{tag}:visible").filter(
                has_text=pattern
            )
            try:
                count = await locator.count()
            except Exception:
                continue

            for i in range(min(count, 5)):  # Cap per pattern
                try:
                    el = locator.nth(i)
                    text = (await el.text_content(timeout=2000) or "").strip()
                    if len(text) > 100:
                        text = text[:100]
                    href = await el.get_attribute("href") or ""
                    target = await el.get_attribute("target") or ""
                    opens_in = "new_tab" if target == "_blank" else "same_window"

                    key = href or text
                    if key in seen:
                        continue
                    seen.add(key)

                    results.append(BookingLinkInfo(
                        text=text,
                        href=href,
                        link_type="button" if tag == "button" else "link",
                        detection_method="text_match",
                        opens_in=opens_in,
                    ))
                except Exception:
                    continue


async def _find_by_href(
    page: Page, results: list[BookingLinkInfo], seen: set[str]
):
    """Find booking links by matching href URL patterns."""
    all_links = page.locator("a[href]:visible")
    try:
        count = await all_links.count()
    except Exception:
        return

    for i in range(min(count, 200)):  # Scan up to 200 links
        try:
            el = all_links.nth(i)
            href = (await el.get_attribute("href") or "").strip()
            href_lower = href.lower()

            matched = any(p in href_lower for p in BOOKING_HREF_PATTERNS)
            if not matched:
                continue

            if href in seen:
                continue
            seen.add(href)

            text = (await el.text_content(timeout=2000) or "").strip()
            if len(text) > 100:
                text = text[:100]
            target = await el.get_attribute("target") or ""
            opens_in = "new_tab" if target == "_blank" else "same_window"

            results.append(BookingLinkInfo(
                text=text or "Booking Link",
                href=href,
                link_type="link",
                detection_method="href_pattern",
                opens_in=opens_in,
            ))
        except Exception:
            continue


async def _find_by_iframe(
    page: Page, results: list[BookingLinkInfo], seen: set[str]
):
    """Find booking engines embedded in iframes."""
    iframes = page.locator("iframe")
    try:
        count = await iframes.count()
    except Exception:
        return

    for i in range(count):
        try:
            src = (await iframes.nth(i).get_attribute("src") or "").strip()
            src_lower = src.lower()

            matched = any(p in src_lower for p in IFRAME_BOOKING_PATTERNS)
            if not matched:
                continue

            if src in seen:
                continue
            seen.add(src)

            results.append(BookingLinkInfo(
                text="Embedded Booking Widget",
                href=src,
                link_type="iframe",
                detection_method="iframe_src",
                opens_in="iframe",
            ))
        except Exception:
            continue


def rank_booking_links(links: list[BookingLinkInfo]) -> list[BookingLinkInfo]:
    """Rank booking links by confidence. Best candidate first."""

    def score(link: BookingLinkInfo) -> int:
        s = 0
        # Detection method base scores
        if link.detection_method in ("text_match", "firecrawl_llm"):
            s += 100
        elif link.detection_method == "ai_query":
            s += 95
        elif link.detection_method == "web_search":
            s += 90
        elif link.detection_method == "brand_crawl":
            s += 85
        elif link.detection_method == "chain_pattern":
            s += 70
        elif link.detection_method == "href_pattern":
            s += 50
        elif link.detection_method == "iframe_src":
            s += 25

        # Explicit "Book Now" text is best
        text_lower = link.text.lower()
        if "book now" in text_lower:
            s += 50
        elif "book" in text_lower:
            s += 30
        elif "reserve" in text_lower:
            s += 30
        elif "check avail" in text_lower:
            s += 20

        # Links that open in new tab likely go to booking engine
        if link.opens_in == "new_tab":
            s += 10

        # Links with actual hrefs are better than buttons without
        if link.href and link.href != "#":
            s += 10

        return s

    return sorted(links, key=score, reverse=True)


async def find_booking_links_with_fallback(
    page: Page, url: str, hotel_name: str = "", city: str = ""
) -> list[BookingLinkInfo]:
    """Find booking links using cascading strategies.

    Order (short-circuits on first success):
      0. AI direct query via Claude Haiku (cheapest, 1 call)
      1. Firecrawl+LLM smart scrape
      2. Web search via Firecrawl search API (property-specific)
      3. Brand site deep crawl via Firecrawl map + scrape
      4. Known chain patterns (generic chain URL, last resort)
      5. CSS selector fallback on loaded page
    """
    has_apis = bool(settings.firecrawl_api_key and settings.anthropic_api_key)

    # 0. AI-first: Ask Claude Haiku directly (cheapest: 1 call, no Firecrawl)
    if city and settings.anthropic_api_key:
        try:
            from detector.ai_booking_query import find_booking_link_via_ai

            ai_links = await find_booking_link_via_ai(hotel_name, city)
            if ai_links:
                logger.info("AI query: %d link(s) for %s", len(ai_links), url)
                return ai_links
            logger.info("AI query: no links for %s", url)
        except Exception as e:
            logger.warning("AI query failed for %s: %s", url, e)

    # 1. Firecrawl+LLM smart scrape
    if has_apis:
        try:
            from detector.smart_link_finder import find_booking_links_smart

            smart_links = await find_booking_links_smart(url)
            if smart_links:
                logger.info("Smart finder: %d link(s) for %s", len(smart_links), url)
                return smart_links
            logger.info("Smart finder: no links for %s", url)
        except Exception as e:
            logger.warning("Smart finder failed for %s: %s", url, e)

    # 2. Web search (Firecrawl search API — finds property-specific URLs)
    if has_apis and hotel_name:
        try:
            from detector.fallback_web_search import find_booking_links_web_search

            search_links = await find_booking_links_web_search(hotel_name, url)
            if search_links:
                logger.info("Web search: %d link(s) for %s", len(search_links), url)
                return search_links
        except Exception as e:
            logger.warning("Web search failed for %s: %s", url, e)

    # 3. Brand site deep crawl (Firecrawl map + scrape)
    if has_apis and hotel_name:
        try:
            from detector.fallback_brand_crawl import find_booking_links_brand_crawl

            crawl_links = await find_booking_links_brand_crawl(hotel_name, url)
            if crawl_links:
                logger.info("Brand crawl: %d link(s) for %s", len(crawl_links), url)
                return crawl_links
        except Exception as e:
            logger.warning("Brand crawl failed for %s: %s", url, e)

    # 4. Known chain patterns (generic chain URL — last resort before selectors)
    if hotel_name:
        try:
            from detector.fallback_chain_patterns import find_booking_links_chain_pattern

            chain_links = await find_booking_links_chain_pattern(url, hotel_name)
            if chain_links:
                logger.info("Chain pattern: %d link(s) for %s", len(chain_links), url)
                return chain_links
        except Exception as e:
            logger.warning("Chain pattern failed for %s: %s", url, e)

    # 5. CSS selector fallback
    logger.info("All API strategies exhausted for %s, using CSS selectors", url)
    return await find_booking_links(page)
