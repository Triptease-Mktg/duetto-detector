"""Fallback 1: Known hotel chain → booking engine mappings.

Maps well-known hotel brand domains to their booking engine providers.
Returns BookingLinkInfo directly when a match is found, and provides
search hints that Fallback 2 (web search) can use for better queries.

Cost: zero — pure dictionary lookup.
"""
from __future__ import annotations

import logging

from models import BookingLinkInfo
from detector.booking_engine_domains import extract_base_domain

logger = logging.getLogger(__name__)

# brand base-domain → list of provider info dicts
CHAIN_BOOKING_PATTERNS: dict[str, list[dict]] = {
    "hardrock.com": [
        {
            "provider": "SynXis",
            "url_template": "https://be.synxis.com/?chain=28120",
            "search_hint": "{hotel_name} Hard Rock Hotel book room synxis",
        },
    ],
    "marriott.com": [
        {
            "provider": "Marriott IBE",
            "url_template": "https://www.marriott.com/reservation/rateListMenu.mi",
            "search_hint": "{hotel_name} marriott book room reservation",
        },
    ],
    "hilton.com": [
        {
            "provider": "Hilton IBE",
            "url_template": "https://www.hilton.com/en/book/reservation/rooms/",
            "search_hint": "{hotel_name} hilton book room reservation",
        },
    ],
    "ihg.com": [
        {
            "provider": "IHG IBE",
            "url_template": "https://www.ihg.com/redirect",
            "search_hint": "{hotel_name} IHG book room reservation",
        },
    ],
    "hyatt.com": [
        {
            "provider": "Hyatt IBE",
            "url_template": "https://www.hyatt.com/shop/rooms/",
            "search_hint": "{hotel_name} hyatt book room reservation",
        },
    ],
    "accor.com": [
        {
            "provider": "Accor IBE",
            "url_template": "https://all.accor.com/",
            "search_hint": "{hotel_name} accor book room reservation",
        },
    ],
    "wyndhamhotels.com": [
        {
            "provider": "Wyndham IBE",
            "url_template": "https://www.wyndhamhotels.com/",
            "search_hint": "{hotel_name} wyndham book room reservation",
        },
    ],
    "choicehotels.com": [
        {
            "provider": "Choice IBE",
            "url_template": "https://www.choicehotels.com/",
            "search_hint": "{hotel_name} choice hotels book room reservation",
        },
    ],
    "radissonhotels.com": [
        {
            "provider": "Radisson IBE",
            "url_template": "https://www.radissonhotels.com/",
            "search_hint": "{hotel_name} radisson book room reservation",
        },
    ],
    "bestwestern.com": [
        {
            "provider": "Best Western IBE",
            "url_template": "https://www.bestwestern.com/",
            "search_hint": "{hotel_name} best western book room reservation",
        },
    ],
}


def get_chain_info(website_url: str) -> dict | None:
    """Look up chain booking info by the hotel's website domain."""
    base = extract_base_domain(website_url)
    entries = CHAIN_BOOKING_PATTERNS.get(base)
    return entries[0] if entries else None


def get_search_hint(website_url: str, hotel_name: str) -> str | None:
    """Return a targeted search query for the given chain, or None."""
    info = get_chain_info(website_url)
    if info and "search_hint" in info:
        return info["search_hint"].replace("{hotel_name}", hotel_name)
    return None


async def find_booking_links_chain_pattern(
    website_url: str,
    hotel_name: str,
) -> list[BookingLinkInfo]:
    """Fallback 1: Return booking links based on known chain patterns.

    Free and instant — no API calls.
    """
    info = get_chain_info(website_url)
    if not info:
        logger.info("Chain pattern: no match for %s", website_url)
        return []

    logger.info(
        "Chain pattern: %s → %s (%s)",
        website_url, info["provider"], info["url_template"],
    )

    return [
        BookingLinkInfo(
            text=f"Book Now ({info['provider']})",
            href=info["url_template"],
            link_type="link",
            detection_method="chain_pattern",
            opens_in="new_tab",
        )
    ]
