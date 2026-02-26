from __future__ import annotations

import time
from datetime import date, timedelta
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from playwright.async_api import Page, BrowserContext

from models import DuettoDetectionResult, DuettoProduct, BookingLinkInfo
from config import settings
from detector.network_monitor import NetworkMonitor
from detector.booking_link_finder import (
    find_booking_links_with_fallback,
    rank_booking_links,
)
from detector.cookie_handler import dismiss_cookie_consent
from detector.browser_session import BrowserSession


def _inject_dates_into_url(url: str) -> str:
    """Add default check-in/check-out dates to a booking engine URL.

    The Duetto pixel typically only fires when the booking engine displays
    room rates, which requires dates to be present in the URL.
    """
    if not url or not url.startswith("http"):
        return url

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    host = parsed.netloc.lower()

    checkin = (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
    checkout = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")
    checkin_slash = (date.today() + timedelta(days=14)).strftime("%m/%d/%Y")
    checkout_slash = (date.today() + timedelta(days=15)).strftime("%m/%d/%Y")

    # Detect booking engine type and add appropriate date params
    url_lower = url.lower()

    # SynXis (Sabre) — arrive/depart
    if "synxis" in host or "synxis" in url_lower:
        params.setdefault("arrive", [checkin])
        params.setdefault("depart", [checkout])
        params.setdefault("adult", ["2"])
        params.setdefault("rooms", ["1"])

    # TravelClick / Amadeus — datein/dateout
    elif "travelclick" in host or "travelclick" in url_lower:
        params.setdefault("datein", [checkin_slash])
        params.setdefault("dateout", [checkout_slash])
        params.setdefault("adults", ["2"])

    # Generic reservations subdomains (often TravelClick-based)
    elif "reservations." in host:
        params.setdefault("datein", [checkin_slash])
        params.setdefault("dateout", [checkout_slash])
        params.setdefault("adults", ["2"])

    # SiteMinder / Little Hotelier
    elif "siteminder" in host or "littlehotelier" in host:
        params.setdefault("checkin", [checkin])
        params.setdefault("checkout", [checkout])

    # Cloudbeds
    elif "cloudbeds" in host:
        params.setdefault("checkin", [checkin])
        params.setdefault("checkout", [checkout])

    # BookAssist
    elif "bookassist" in host:
        params.setdefault("arrive", [checkin])
        params.setdefault("depart", [checkout])

    # Profitroom
    elif "profitroom" in host:
        params.setdefault("dateFrom", [checkin])
        params.setdefault("dateTo", [checkout])

    # Mews
    elif "mews" in host:
        params.setdefault("startDate", [checkin])
        params.setdefault("endDate", [checkout])

    # D-EDGE
    elif "d-edge" in host or "availpro" in host:
        params.setdefault("arrivalDate", [checkin])
        params.setdefault("departureDate", [checkout])

    # Roiback
    elif "rfrb" in host or "roiback" in host:
        params.setdefault("checkin", [checkin])
        params.setdefault("checkout", [checkout])

    # Mirai
    elif "mirai" in host:
        params.setdefault("checkin", [checkin])
        params.setdefault("checkout", [checkout])

    # Generic fallback — try common param names
    else:
        has_dates = any(
            k.lower() in (
                "arrive", "depart", "checkin", "checkout",
                "check_in", "check_out", "datein", "dateout",
                "startdate", "enddate", "arrivaldate", "departuredate",
                "start_date", "end_date", "arrival", "departure",
            )
            for k in params
        )
        if not has_dates:
            params.setdefault("checkin", [checkin])
            params.setdefault("checkout", [checkout])
            params.setdefault("adults", ["2"])

    # Rebuild URL
    flat_params = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}
    new_query = urlencode(flat_params)
    new_parsed = parsed._replace(query=new_query)
    return urlunparse(new_parsed)


async def analyze_hotel(
    hotel_name: str,
    website_url: str,
    browser_session: BrowserSession,
    screenshot_dir: str | None = None,
) -> DuettoDetectionResult:
    """Run complete Duetto detection for one hotel."""
    start_time = time.time()
    result = DuettoDetectionResult(
        hotel_name=hotel_name,
        website_url=website_url,
    )

    context = await browser_session.new_context()
    monitor = NetworkMonitor()

    try:
        page = await context.new_page()
        monitor.attach(page)

        # Monitor any new pages/popups that open
        context.on("page", lambda new_page: monitor.attach(new_page))

        # Step 1: Navigate to hotel homepage
        try:
            await page.goto(
                website_url,
                wait_until="networkidle",
                timeout=settings.scan_timeout_ms,
            )
        except Exception as e:
            result.errors.append(f"Homepage load failed: {e}")
            try:
                await page.goto(
                    website_url,
                    wait_until="domcontentloaded",
                    timeout=settings.scan_timeout_ms,
                )
            except Exception as e2:
                result.errors.append(f"Homepage fallback also failed: {e2}")
                return result

        await page.wait_for_timeout(settings.page_load_wait_ms)

        # Step 2: Dismiss cookie consent
        await dismiss_cookie_consent(page)

        # Step 3: Find booking links (Firecrawl+LLM if configured, else selectors)
        booking_links = await find_booking_links_with_fallback(page, website_url)
        result.booking_links_found = booking_links

        if not booking_links:
            result.errors.append("No booking links found on homepage")
        else:
            # Step 4: Follow the best booking link, with dates injected
            ranked = rank_booking_links(booking_links)
            best_link = ranked[0]

            # Inject dates into the booking engine URL
            if best_link.href and best_link.href.startswith("http"):
                best_link = BookingLinkInfo(
                    text=best_link.text,
                    href=_inject_dates_into_url(best_link.href),
                    link_type=best_link.link_type,
                    detection_method=best_link.detection_method,
                    opens_in=best_link.opens_in,
                )

            result.booking_link_followed = best_link
            await _follow_booking_link(page, context, best_link, monitor)

        # Step 5: Wait for booking engine to fully load & pixel to fire
        # The Duetto pixel fires after rooms/rates are displayed, which can
        # take time after the initial page load
        await page.wait_for_timeout(settings.booking_engine_wait_ms)

        # Try to trigger rate display by interacting with the page
        active_page = await _get_active_page(context, page)

        # Dismiss cookie consent on the booking engine page too — tag managers
        # like Tealium won't fire tracking pixels (including Duetto) until the
        # user accepts cookies on the booking engine domain
        await dismiss_cookie_consent(active_page)
        await active_page.wait_for_timeout(2000)

        await _try_trigger_rate_search(active_page)
        await active_page.wait_for_timeout(settings.booking_engine_wait_ms)

        # Step 6: Check network traffic for Duetto signals
        result.duetto_pixel_detected = monitor.duetto_pixel_detected
        result.pixel_requests = monitor.pixel_requests
        result.gamechanger_detected = monitor.gamechanger_in_network

        # Step 7: Deep DOM inspection for GameChanger
        try:
            gamechanger_evidence = await _check_gamechanger_dom(active_page)
            if gamechanger_evidence:
                result.gamechanger_detected = True
                result.gamechanger_evidence = gamechanger_evidence
        except Exception:
            pass

        # Step 8: Check CSP headers and page source for Duetto references
        # Some booking engines whitelist Duetto in CSP or embed config
        # referencing Duetto even when the pixel doesn't fire in headless
        try:
            source_evidence = await _check_duetto_in_source(active_page)
            if source_evidence:
                result.gamechanger_evidence.extend(source_evidence)
        except Exception:
            pass

        if monitor.duetto_in_csp:
            result.gamechanger_evidence.append(
                "CSP header allows *.duettoresearch.com"
            )
            # If CSP references Duetto but pixel didn't fire, still flag it
            if not result.duetto_pixel_detected:
                result.duetto_pixel_detected = True
                result.errors.append(
                    "Pixel detected via CSP allowlist (pixel did not fire "
                    "in headless mode)"
                )

        # Check console logs for direct Duetto references (not CSP violations)
        duetto_console = [
            log for log in monitor.console_logs
            if "duetto" in log.lower()
            and "content security policy" not in log.lower()
            and "violates" not in log.lower()
        ]
        if duetto_console:
            result.gamechanger_evidence.extend(
                [f"console: {log}" for log in duetto_console[:5]]
            )

        # Build product list
        if result.duetto_pixel_detected:
            result.duetto_products.append(DuettoProduct.PIXEL)
        if result.gamechanger_detected:
            result.duetto_products.append(DuettoProduct.GAMECHANGER)
        if not result.duetto_products:
            result.duetto_products = [DuettoProduct.NONE]

        result.confidence = _calculate_confidence(result)
        result.all_captured_domains = monitor.captured_domains
        result.console_logs = monitor.console_logs[:50]
        result.booking_engine_url = active_page.url

        # Collect raw proof snippets — actual URLs, headers, DOM excerpts
        proof: list[str] = []
        for pr in result.pixel_requests:
            proof.append(f"pixel_request: {pr.url}")
        for csp in monitor.csp_headers:
            if any(p in csp.lower() for p in NetworkMonitor.DUETTO_DOMAIN_PATTERNS):
                snippet = csp[:500] + "..." if len(csp) > 500 else csp
                proof.append(f"csp_header: {snippet}")
        for ev in result.gamechanger_evidence:
            if ev not in proof and "CSP header allows" not in ev:
                proof.append(ev)
        result.proof_snippets = proof

        # Optional screenshot
        if screenshot_dir:
            slug = "".join(
                c if c.isalnum() else "_" for c in hotel_name
            ).strip("_")
            screenshot_path = f"{screenshot_dir}/{slug}_booking.png"
            try:
                await active_page.screenshot(path=screenshot_path)
                result.screenshot_path = screenshot_path
            except Exception:
                pass

    except Exception as e:
        result.errors.append(f"Scan error: {e}")
    finally:
        await context.close()
        result.scan_duration_seconds = round(time.time() - start_time, 1)

    return result


async def _get_active_page(context: BrowserContext, fallback: Page) -> Page:
    """Return the most recently opened page in the context."""
    pages = context.pages
    if len(pages) > 1:
        return pages[-1]
    return fallback


async def _follow_booking_link(
    page: Page,
    context: BrowserContext,
    link: BookingLinkInfo,
    monitor: NetworkMonitor,
):
    """Follow a booking link, handling new tabs, popups, iframes, and modals."""

    if link.opens_in == "iframe":
        await page.wait_for_timeout(3000)
        return

    # For links with full URLs, navigate directly (more reliable than
    # clicking, and lets us use the date-injected URL)
    if link.href and link.href.startswith("http"):
        if link.opens_in == "new_tab":
            new_page = await context.new_page()
            monitor.attach(new_page)
            try:
                await new_page.goto(
                    link.href,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await new_page.wait_for_timeout(5000)
            except Exception:
                await new_page.wait_for_timeout(5000)
            return

        # Same-window navigation
        try:
            await page.goto(
                link.href,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await page.wait_for_timeout(3000)
        except Exception:
            await page.wait_for_timeout(5000)
        return

    # No direct URL — the button likely opens a modal or triggers JS
    url_before = page.url
    try:
        await _click_booking_element(page, link)
    except Exception:
        return

    await page.wait_for_timeout(2000)

    # Check if we navigated away
    if page.url != url_before:
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        return

    # We didn't navigate — likely a modal opened. Try to fill and submit it.
    submitted = await _try_submit_modal_booking_form(page, context, monitor)
    if submitted:
        return

    # If no modal form found, try clicking any new booking links that appeared
    if link.opens_in == "new_tab":
        try:
            async with context.expect_page(timeout=10000) as new_page_info:
                await _click_booking_element(page, link)
            new_page = await new_page_info.value
            monitor.attach(new_page)
            try:
                await new_page.wait_for_load_state(
                    "networkidle", timeout=30000
                )
            except Exception:
                await new_page.wait_for_timeout(5000)
        except Exception:
            pass


async def _try_submit_modal_booking_form(
    page: Page,
    context: BrowserContext,
    monitor: NetworkMonitor,
) -> bool:
    """Try to fill in dates and submit a modal booking form.

    Many hotel brand sites open a modal with a property picker, date fields,
    and a submit button instead of navigating directly to the booking engine.
    """
    checkin = (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
    checkout = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")

    # Step 1: If there's a property/destination dropdown, pick the first one.
    # Brand-level sites (Hard Rock, Marriott, etc.) require selecting a property
    # before the booking form can be submitted.
    await _select_first_property(page)

    # Step 2: Fill date inputs
    date_input_selectors = [
        'input[name="arrive"]',
        'input[name="depart"]',
        'input[name="checkin"]',
        'input[name="checkout"]',
        'input[name="datein"]',
        'input[name="dateout"]',
        'input[name="check_in"]',
        'input[name="check_out"]',
        'input[name="arrivalDate"]',
        'input[name="departureDate"]',
        'input[name="startDate"]',
        'input[name="endDate"]',
    ]

    filled_dates = False
    for selector in date_input_selectors:
        try:
            inputs = page.locator(selector)
            count = await inputs.count()
            if count > 0:
                for i in range(count):
                    el = inputs.nth(i)
                    name = (await el.get_attribute("name") or "").lower()
                    if any(k in name for k in ("arrive", "checkin", "check_in", "datein", "arrival", "start")):
                        await el.evaluate("(el, val) => el.value = val", checkin)
                        filled_dates = True
                    elif any(k in name for k in ("depart", "checkout", "check_out", "dateout", "departure", "end")):
                        await el.evaluate("(el, val) => el.value = val", checkout)
                        filled_dates = True
        except Exception:
            continue

    if not filled_dates:
        return False

    # Step 3: Submit the form. First try building the URL from form data
    # (most reliable), then fall back to clicking submit buttons.
    form_url = await page.evaluate("""() => {
        var forms = document.querySelectorAll('form');
        for (var i = 0; i < forms.length; i++) {
            var f = forms[i];
            var data = {};
            new FormData(f).forEach(function(v, k) { data[k] = v; });
            // Check if this form has booking-related fields
            var keys = Object.keys(data).join(' ').toLowerCase();
            if (keys.indexOf('arrive') !== -1 || keys.indexOf('checkin') !== -1 ||
                keys.indexOf('datein') !== -1 || keys.indexOf('check_in') !== -1) {
                if (f.action) {
                    var url = new URL(f.action);
                    Object.entries(data).forEach(function(pair) {
                        url.searchParams.set(pair[0], pair[1]);
                    });
                    return url.toString();
                }
            }
        }
        return null;
    }""")

    if form_url:
        # Inject dates into the constructed URL too
        form_url = _inject_dates_into_url(form_url)
        new_page = await context.new_page()
        monitor.attach(new_page)
        try:
            await new_page.goto(
                form_url, wait_until="domcontentloaded", timeout=30000
            )
        except Exception:
            pass
        await new_page.wait_for_timeout(5000)
        return True

    # Fallback: try clicking visible submit buttons
    submit_selectors = [
        'button:has-text("Book Now"):visible',
        'button:has-text("Search"):visible',
        'button:has-text("Check Availability"):visible',
        'button:has-text("Find Rooms"):visible',
        'button[type="submit"]:visible',
        'input[type="submit"]:visible',
    ]

    url_before = page.url
    for selector in submit_selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=1000):
                try:
                    async with context.expect_page(timeout=10000) as new_page_info:
                        await locator.click(timeout=3000)
                    new_page = await new_page_info.value
                    monitor.attach(new_page)
                    try:
                        await new_page.wait_for_load_state(
                            "domcontentloaded", timeout=30000
                        )
                    except Exception:
                        pass
                    await new_page.wait_for_timeout(5000)
                    return True
                except Exception:
                    await page.wait_for_timeout(3000)
                    if page.url != url_before:
                        try:
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=15000
                            )
                        except Exception:
                            pass
                        return True
        except Exception:
            continue

    return False


async def _select_first_property(page: Page):
    """Select the first non-empty option in a property/destination dropdown.

    Brand-level hotel sites have a dropdown to pick a specific property
    before the booking form can be submitted.
    """
    # Find select elements that look like property/destination pickers
    property_select_keywords = [
        "location", "hotel", "property", "destination", "resort",
    ]

    selected = await page.evaluate("""(keywords) => {
        var selects = document.querySelectorAll('select');
        for (var i = 0; i < selects.length; i++) {
            var sel = selects[i];
            var idName = ((sel.id || '') + ' ' + (sel.name || '') + ' ' + (sel.className || '')).toLowerCase();
            var isProperty = keywords.some(function(k) { return idName.indexOf(k) !== -1; });
            if (!isProperty) continue;

            // Count non-empty options — if >3, it's likely a property list
            var options = sel.querySelectorAll('option');
            var nonEmpty = [];
            options.forEach(function(o) {
                if (o.value && o.value.trim()) nonEmpty.push(o.value);
            });
            if (nonEmpty.length < 2) continue;

            // Pick the first non-empty option
            sel.value = nonEmpty[0];
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return nonEmpty[0];
        }
        return null;
    }""", property_select_keywords)

    if selected:
        await page.wait_for_timeout(1000)  # Let any dependent fields update


async def _click_booking_element(page: Page, link: BookingLinkInfo):
    """Click a booking element by reconstructing the best selector."""
    if link.href and link.link_type == "link":
        try:
            safe_href = link.href.replace('"', '\\"')
            locator = page.locator(f'a[href="{safe_href}"]').first
            if await locator.is_visible(timeout=3000):
                await locator.click()
                return
        except Exception:
            pass

    tag = "button" if link.link_type == "button" else "a"
    text = link.text.split("\n")[0].strip().replace('"', '\\"')
    try:
        locator = page.locator(f'{tag}:has-text("{text}")').first
        await locator.click(timeout=5000)
    except Exception:
        locator = page.locator(f':has-text("{text}")').first
        await locator.click(timeout=5000)


async def _try_trigger_rate_search(page: Page):
    """Try to trigger a room/rate search on the booking engine page.

    Many booking engines show a date picker and search button. If we can
    fill in dates and click search, the Duetto pixel will fire when rates
    are displayed.
    """
    # Common search/submit button selectors on booking engines
    search_selectors = [
        'button:has-text("Search")',
        'button:has-text("Check Availability")',
        'button:has-text("Find Rooms")',
        'button:has-text("View Rates")',
        'button:has-text("Check Rates")',
        'button:has-text("Submit")',
        'button:has-text("Buscar")',
        'button:has-text("Suchen")',
        'button:has-text("Rechercher")',
        'input[type="submit"]',
        'button[type="submit"]',
        '#submitButton',
        '.search-button',
        '.btn-search',
    ]

    for selector in search_selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=1000):
                await locator.click(timeout=3000)
                await page.wait_for_timeout(3000)
                return
        except Exception:
            continue


async def _check_gamechanger_dom(page: Page) -> list[str]:
    """Check the page DOM for GameChanger-related signals."""
    evidence = []

    duetto_signals = await page.evaluate("""
        () => {
            const signals = [];
            for (const key of Object.keys(window)) {
                const lower = key.toLowerCase();
                if (lower.includes('duetto') || lower.includes('gamechanger')) {
                    signals.push('window.' + key);
                }
            }
            document.querySelectorAll('script[src]').forEach(s => {
                if (s.src.toLowerCase().includes('duetto')) {
                    signals.push('script: ' + s.src);
                }
            });
            document.querySelectorAll('meta').forEach(m => {
                const content = (m.content || '').toLowerCase();
                const name = (m.name || '').toLowerCase();
                if (content.includes('duetto') || name.includes('duetto') ||
                    content.includes('gamechanger') || name.includes('gamechanger')) {
                    signals.push('meta[' + m.name + ']: ' + m.content);
                }
            });
            if (document.title.toLowerCase().includes('gamechanger')) {
                signals.push('title: ' + document.title);
            }
            return signals;
        }
    """)

    if duetto_signals:
        evidence.extend(duetto_signals)

    try:
        cookies = await page.context.cookies()
        for cookie in cookies:
            name_lower = cookie["name"].lower()
            domain_lower = cookie.get("domain", "").lower()
            if "duetto" in name_lower or "duetto" in domain_lower:
                evidence.append(
                    f"cookie: {cookie['name']} (domain: {cookie.get('domain', '')})"
                )
    except Exception:
        pass

    return evidence


async def _check_duetto_in_source(page: Page) -> list[str]:
    """Check the page HTML source and app state for Duetto references.

    Booking engines like SynXis embed Duetto configuration in their React
    state or inline scripts even when the pixel doesn't fire in headless mode.
    Returns raw evidence snippets suitable for proof.
    """
    return await page.evaluate("""
        (function() {
            var evidence = [];
            var patterns = ["duettoresearch", "duettocloud"];

            // Check __INITIAL_STATE__ for Duetto references (SynXis React app)
            if (window.__INITIAL_STATE__) {
                var stateStr = JSON.stringify(window.__INITIAL_STATE__);
                var lower = stateStr.toLowerCase();
                for (var p = 0; p < patterns.length; p++) {
                    var idx = lower.indexOf(patterns[p]);
                    var found = 0;
                    while (idx !== -1 && found < 3) {
                        var start = Math.max(0, idx - 50);
                        var end = Math.min(stateStr.length, idx + patterns[p].length + 50);
                        evidence.push("__INITIAL_STATE__: ..." + stateStr.substring(start, end) + "...");
                        found++;
                        idx = lower.indexOf(patterns[p], idx + 1);
                    }
                }
            }

            // Check inline scripts for Duetto pixel code
            var scripts = document.querySelectorAll("script:not([src])");
            for (var i = 0; i < scripts.length; i++) {
                var text = scripts[i].textContent || "";
                var textLower = text.toLowerCase();
                for (var p2 = 0; p2 < patterns.length; p2++) {
                    var idx2 = textLower.indexOf(patterns[p2]);
                    if (idx2 !== -1) {
                        var start2 = Math.max(0, idx2 - 80);
                        var end2 = Math.min(text.length, idx2 + patterns[p2].length + 80);
                        evidence.push("inline_script: ..." + text.substring(start2, end2).trim() + "...");
                    }
                }
            }

            // Check meta tags for CSP that includes Duetto
            var metas = document.querySelectorAll("meta[http-equiv]");
            for (var j = 0; j < metas.length; j++) {
                var content = metas[j].content || "";
                if (content.toLowerCase().indexOf("duettoresearch") !== -1) {
                    var snippet = content.length > 500 ? content.substring(0, 500) + "..." : content;
                    evidence.push("meta_csp: " + snippet);
                }
            }

            return evidence;
        })()
    """)


def _calculate_confidence(result: DuettoDetectionResult) -> str:
    """Calculate confidence level based on detection signals."""
    # Check if pixel was only detected via CSP (not actual network traffic)
    csp_only = any(
        "CSP allowlist" in e for e in result.errors
    )

    score = 0

    if result.duetto_pixel_detected:
        score += 1 if csp_only else 3  # CSP-only is weaker signal
    if result.gamechanger_detected:
        score += 3
    if result.gamechanger_evidence:
        score += len(result.gamechanger_evidence)
    if result.booking_link_followed:
        score += 1
    if result.errors:
        score -= 1

    if csp_only:
        # CSP/source-based detection is indirect — cap at medium
        return "medium" if score >= 2 else "low"

    if score >= 4:
        return "high"
    elif score >= 2:
        return "medium"
    elif score >= 1:
        return "low"
    return "none"
