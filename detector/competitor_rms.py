"""Competitor RMS and hotel tech vendor detection.

Scans network traffic, DOM, and cookies captured during the booking engine
visit to identify non-Duetto hotel technology vendors.  Zero additional
page loads or API calls — pure pattern matching on already-captured data.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from playwright.async_api import Page
from detector.network_monitor import NetworkMonitor

logger = logging.getLogger(__name__)

# Vendor registry: name → detection patterns
# "domains" are checked against captured network request URLs
# "dom_signals" are window-level JS objects checked via page.evaluate()
# "cookie_patterns" are substring matches against cookie names/domains
VENDOR_PATTERNS: dict[str, dict] = {
    "Triptease": {
        "category": "Direct Booking Platform",
        "domains": ["triptease.io", "triptease.com"],
        "dom_signals": ["triptease", "Triptease"],
        "cookie_patterns": ["triptease"],
    },
    "RateGain": {
        "category": "Revenue Intelligence",
        "domains": ["uno.rategain.com", "adara.com", "rategain.com"],
        "dom_signals": [],
        "cookie_patterns": ["rategain", "adara"],
    },
    "The Hotels Network": {
        "category": "Direct Booking Platform",
        "domains": ["thehotelsnetwork.com", "thn.com"],
        "dom_signals": ["THN", "theHotelsNetwork"],
        "cookie_patterns": ["thn_"],
    },
    "OTA Insight": {
        "category": "Rate Intelligence",
        "domains": ["otainsight.com", "lighthouse.com"],
        "dom_signals": [],
        "cookie_patterns": ["otainsight"],
    },
    "Fornova": {
        "category": "Rate Intelligence",
        "domains": ["fornova.com", "fornova.net"],
        "dom_signals": [],
        "cookie_patterns": ["fornova"],
    },
    "Cendyn": {
        "category": "CRM / Marketing Automation",
        "domains": ["cendyn.com", "nextguest.com"],
        "dom_signals": ["cendyn", "Cendyn"],
        "cookie_patterns": ["cendyn", "nextguest"],
    },
    "Revinate": {
        "category": "Guest Marketing",
        "domains": ["revinate.com"],
        "dom_signals": ["revinate", "Revinate"],
        "cookie_patterns": ["revinate"],
    },
    "TravelClick": {
        "category": "Booking Engine / Analytics",
        "domains": ["travelclick.com", "amadeus-hospitality.com"],
        "dom_signals": [],
        "cookie_patterns": ["travelclick"],
    },
    "SynXis": {
        "category": "Booking Engine (Sabre)",
        "domains": ["synxis.com"],
        "dom_signals": [],
        "cookie_patterns": ["synxis"],
    },
    "SiteMinder": {
        "category": "Booking Engine / Distribution",
        "domains": ["siteminder.com", "littlehotelier.com"],
        "dom_signals": ["siteminder"],
        "cookie_patterns": ["siteminder"],
    },
    "Cloudbeds": {
        "category": "PMS / Booking Engine",
        "domains": ["cloudbeds.com"],
        "dom_signals": ["cloudbeds"],
        "cookie_patterns": ["cloudbeds"],
    },
    "Mews": {
        "category": "PMS / Booking Engine",
        "domains": ["mews.com"],
        "dom_signals": [],
        "cookie_patterns": ["mews"],
    },
    "Profitroom": {
        "category": "Booking Engine / CRM",
        "domains": ["profitroom.com"],
        "dom_signals": ["profitroom"],
        "cookie_patterns": ["profitroom"],
    },
    "BookAssist": {
        "category": "Booking Engine / Marketing",
        "domains": ["bookassist.com", "bookassist.org"],
        "dom_signals": [],
        "cookie_patterns": ["bookassist"],
    },
    "D-EDGE": {
        "category": "Booking Engine / Distribution",
        "domains": ["d-edge.com", "availpro.com"],
        "dom_signals": [],
        "cookie_patterns": ["d-edge", "availpro"],
    },
    "Roiback": {
        "category": "Booking Engine",
        "domains": ["roiback.com", "rfrb.net"],
        "dom_signals": [],
        "cookie_patterns": ["roiback"],
    },
    "Mirai": {
        "category": "Booking Engine / Distribution",
        "domains": ["mirai.com"],
        "dom_signals": [],
        "cookie_patterns": ["mirai"],
    },
    "Seekda": {
        "category": "Booking Engine",
        "domains": ["seekda.com"],
        "dom_signals": [],
        "cookie_patterns": ["seekda"],
    },
    "Net Affinity": {
        "category": "Booking Engine",
        "domains": ["netaffinity.com"],
        "dom_signals": [],
        "cookie_patterns": ["netaffinity"],
    },
    "Omnibees": {
        "category": "Booking Engine / Distribution",
        "domains": ["omnibees.com"],
        "dom_signals": [],
        "cookie_patterns": ["omnibees"],
    },
}


def _check_network(monitor: NetworkMonitor) -> dict[str, list[str]]:
    """Check all captured requests against vendor domain patterns.

    Returns {vendor_name: [matching_urls, ...]}.
    """
    hits: dict[str, list[str]] = {}
    for req in monitor.all_requests:
        url_lower = req["url"].lower()
        try:
            host = urlparse(req["url"]).netloc.lower()
        except Exception:
            host = ""
        for vendor, info in VENDOR_PATTERNS.items():
            for domain in info["domains"]:
                if domain in host or domain in url_lower:
                    hits.setdefault(vendor, []).append(req["url"])
                    break
    return hits


async def _check_dom(page: Page) -> dict[str, list[str]]:
    """Check window-level objects and script src for vendor signals.

    Runs a single page.evaluate() that checks all vendors at once.
    Returns {vendor_name: [signal_descriptions, ...]}.
    """
    # Build the list of signals to check
    all_signals = []
    for vendor, info in VENDOR_PATTERNS.items():
        for signal in info.get("dom_signals", []):
            all_signals.append({"vendor": vendor, "signal": signal})

    if not all_signals:
        return {}

    try:
        results = await page.evaluate("""(signals) => {
            var hits = [];
            for (var i = 0; i < signals.length; i++) {
                var s = signals[i];
                if (typeof window[s.signal] !== 'undefined') {
                    hits.push({vendor: s.vendor, evidence: 'window.' + s.signal});
                }
            }
            // Also check script src attributes for vendor domains
            var scripts = document.querySelectorAll('script[src]');
            var vendorDomains = {};
            for (var j = 0; j < signals.length; j++) {
                var sig = signals[j].signal.toLowerCase();
                vendorDomains[sig] = signals[j].vendor;
            }
            scripts.forEach(function(script) {
                var src = script.src.toLowerCase();
                for (var key in vendorDomains) {
                    if (src.indexOf(key) !== -1) {
                        hits.push({
                            vendor: vendorDomains[key],
                            evidence: 'script_src: ' + script.src
                        });
                    }
                }
            });
            return hits;
        }""", all_signals)
    except Exception as e:
        logger.debug("DOM vendor check failed: %s", e)
        return {}

    hits: dict[str, list[str]] = {}
    for item in results:
        hits.setdefault(item["vendor"], []).append(item["evidence"])
    return hits


async def _check_cookies(page: Page) -> dict[str, list[str]]:
    """Check cookies for vendor-specific patterns.

    Returns {vendor_name: [cookie_descriptions, ...]}.
    """
    try:
        cookies = await page.context.cookies()
    except Exception:
        return {}

    hits: dict[str, list[str]] = {}
    for cookie in cookies:
        name_lower = cookie["name"].lower()
        domain_lower = cookie.get("domain", "").lower()
        combined = f"{name_lower} {domain_lower}"
        for vendor, info in VENDOR_PATTERNS.items():
            for pattern in info.get("cookie_patterns", []):
                if pattern in combined:
                    hits.setdefault(vendor, []).append(
                        f"cookie: {cookie['name']} (domain: {cookie.get('domain', '')})"
                    )
                    break
    return hits


async def detect_competitor_rms(
    monitor: NetworkMonitor,
    page: Page,
) -> list:
    """Detect competitor RMS/tech vendors from captured data.

    Returns list of CompetitorRMSDetection (imported lazily to avoid
    circular imports at module level).
    """
    from models import CompetitorRMSDetection

    # Run all three checks
    network_hits = _check_network(monitor)
    dom_hits = await _check_dom(page)
    cookie_hits = await _check_cookies(page)

    # Merge all evidence per vendor
    all_vendors = set(network_hits) | set(dom_hits) | set(cookie_hits)
    results: list[CompetitorRMSDetection] = []

    for vendor in sorted(all_vendors):
        evidence: list[str] = []

        for url in (network_hits.get(vendor, []))[:5]:
            evidence.append(f"network: {url}")
        for sig in dom_hits.get(vendor, []):
            evidence.append(sig)
        for ck in cookie_hits.get(vendor, []):
            evidence.append(ck)

        category = VENDOR_PATTERNS.get(vendor, {}).get("category", "Hotel Technology")

        results.append(CompetitorRMSDetection(
            vendor=vendor,
            category=category,
            evidence=evidence,
        ))

    if results:
        logger.info(
            "Competitor RMS: detected %d vendor(s): %s",
            len(results),
            ", ".join(r.vendor for r in results),
        )

    return results
