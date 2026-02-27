"""Shared registry of known booking engine domains and chain patterns."""
from __future__ import annotations

from urllib.parse import urlparse

# Domains / substrings that indicate a booking engine URL.
KNOWN_BOOKING_ENGINE_DOMAINS: list[str] = [
    # SynXis (Sabre)
    "be.synxis.com",
    "gc.synxis.com",
    "booking.synxis.com",
    # TravelClick (Amadeus)
    "travelclick.com",
    "reservations.travelclick.com",
    # SiteMinder
    "siteminder.com",
    "littlehotelier.com",
    # Cloudbeds
    "cloudbeds.com",
    # Mews
    "mews.com",
    "app.mews.com",
    # GuestCentric
    "guestcentric.com",
    # BookAssist
    "bookassist.com",
    # Profitroom
    "profitroom.com",
    # D-EDGE
    "d-edge.com",
    "availpro.com",
    # Roiback
    "rfrb.net",
    "roiback.com",
    # Mirai
    "mirai.com",
    # Omnibees
    "omnibees.com",
    # Seekda
    "seekda.com",
    # Generic booking subdomains
    "reservations.",
    "bookings.",
    "book.",
    "reserve.",
]

# Looser keywords for URL matching (title/description filtering).
BOOKING_URL_KEYWORDS: list[str] = [
    "synxis", "travelclick", "siteminder", "cloudbeds",
    "mews", "guestcentric", "bookdirect", "bookassist",
    "profitroom", "d-edge", "roiback", "rfrb.net",
    "omnibees", "seekda", "mirai",
    "/booking", "/reservation", "/reserve", "/book-now",
    "booking-engine", "ibe.", "wbe.",
]


def url_matches_booking_engine(url: str) -> bool:
    """Return True if *url* contains any known booking engine domain or keyword."""
    url_lower = url.lower()
    return (
        any(d in url_lower for d in KNOWN_BOOKING_ENGINE_DOMAINS)
        or any(k in url_lower for k in BOOKING_URL_KEYWORDS)
    )


def extract_base_domain(url_or_host: str) -> str:
    """Extract the registrable domain from a URL or hostname.

    "hotel.hardrock.com"            → "hardrock.com"
    "https://www.marriott.com/foo"  → "marriott.com"
    """
    if "://" in url_or_host:
        host = urlparse(url_or_host).netloc
    else:
        host = url_or_host
    host = host.lower().lstrip("www.")
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host
