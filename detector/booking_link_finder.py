import re
from playwright.async_api import Page
from models import BookingLinkInfo


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
        # Text matches are highest confidence
        if link.detection_method == "text_match":
            s += 100
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
