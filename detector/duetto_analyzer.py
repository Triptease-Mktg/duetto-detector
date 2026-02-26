from __future__ import annotations

import time
from playwright.async_api import Page, BrowserContext

from models import DuettoDetectionResult, DuettoProduct, BookingLinkInfo
from config import settings
from detector.network_monitor import NetworkMonitor
from detector.booking_link_finder import find_booking_links, rank_booking_links
from detector.cookie_handler import dismiss_cookie_consent
from detector.browser_session import BrowserSession


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
            # Try with just domcontentloaded as fallback
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

        # Step 3: Find booking links
        booking_links = await find_booking_links(page)
        result.booking_links_found = booking_links

        if not booking_links:
            result.errors.append("No booking links found on homepage")
        else:
            # Step 4: Click the best booking link
            ranked = rank_booking_links(booking_links)
            best_link = ranked[0]
            result.booking_link_followed = best_link

            await _follow_booking_link(page, context, best_link, monitor)

        # Step 5: Wait for booking engine to fully load
        await page.wait_for_timeout(settings.booking_engine_wait_ms)

        # Step 6: Check network traffic for Duetto signals
        result.duetto_pixel_detected = monitor.duetto_pixel_detected
        result.pixel_requests = monitor.pixel_requests
        result.gamechanger_detected = monitor.gamechanger_in_network

        # Step 7: Deep DOM inspection for GameChanger
        try:
            gamechanger_evidence = await _check_gamechanger_dom(page)
            if gamechanger_evidence:
                result.gamechanger_detected = True
                result.gamechanger_evidence = gamechanger_evidence
        except Exception:
            pass

        # Also check console logs for Duetto references
        duetto_console = [
            log for log in monitor.console_logs
            if "duetto" in log.lower()
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

        # Set confidence
        result.confidence = _calculate_confidence(result)

        # Capture diagnostic data
        result.all_captured_domains = monitor.captured_domains
        result.console_logs = monitor.console_logs[:50]
        result.booking_engine_url = page.url

        # Optional screenshot
        if screenshot_dir:
            slug = "".join(
                c if c.isalnum() else "_" for c in hotel_name
            ).strip("_")
            screenshot_path = f"{screenshot_dir}/{slug}_booking.png"
            try:
                await page.screenshot(path=screenshot_path)
                result.screenshot_path = screenshot_path
            except Exception:
                pass

    except Exception as e:
        result.errors.append(f"Scan error: {e}")
    finally:
        await context.close()
        result.scan_duration_seconds = round(time.time() - start_time, 1)

    return result


async def _follow_booking_link(
    page: Page,
    context: BrowserContext,
    link: BookingLinkInfo,
    monitor: NetworkMonitor,
):
    """Follow a booking link, handling new tabs, popups, and iframes."""

    if link.opens_in == "iframe":
        # For iframes, the network monitor already captures requests
        await page.wait_for_timeout(3000)
        return

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
            return
        except Exception:
            # Fallback: try direct navigation if popup capture failed
            if link.href and link.href.startswith("http"):
                try:
                    await page.goto(
                        link.href,
                        wait_until="networkidle",
                        timeout=30000,
                    )
                except Exception:
                    await page.wait_for_timeout(5000)
            return

    # Same-window navigation
    try:
        await _click_booking_element(page, link)
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            await page.wait_for_timeout(5000)
    except Exception:
        # Fallback: navigate directly to href
        if link.href and link.href.startswith("http"):
            try:
                await page.goto(
                    link.href, wait_until="networkidle", timeout=30000
                )
            except Exception:
                await page.wait_for_timeout(5000)


async def _click_booking_element(page: Page, link: BookingLinkInfo):
    """Click a booking element by reconstructing the best selector."""
    # Try clicking by href first (most reliable for links)
    if link.href and link.link_type == "link":
        try:
            # Escape quotes in href for CSS selector
            safe_href = link.href.replace('"', '\\"')
            locator = page.locator(f'a[href="{safe_href}"]').first
            if await locator.is_visible(timeout=3000):
                await locator.click()
                return
        except Exception:
            pass

    # Fall back to text matching
    tag = "button" if link.link_type == "button" else "a"
    text = link.text.replace('"', '\\"')
    try:
        locator = page.locator(f'{tag}:has-text("{text}")').first
        await locator.click(timeout=5000)
    except Exception:
        # Last resort: try any clickable element with this text
        locator = page.locator(f':has-text("{text}")').first
        await locator.click(timeout=5000)


async def _check_gamechanger_dom(page: Page) -> list[str]:
    """Check the page DOM for GameChanger-related signals."""
    evidence = []

    duetto_signals = await page.evaluate("""
        () => {
            const signals = [];
            // Check window-level variables
            for (const key of Object.keys(window)) {
                const lower = key.toLowerCase();
                if (lower.includes('duetto') || lower.includes('gamechanger')) {
                    signals.push('window.' + key);
                }
            }
            // Check for Duetto script tags
            document.querySelectorAll('script[src]').forEach(s => {
                if (s.src.toLowerCase().includes('duetto')) {
                    signals.push('script: ' + s.src);
                }
            });
            // Check meta tags
            document.querySelectorAll('meta').forEach(m => {
                const content = (m.content || '').toLowerCase();
                const name = (m.name || '').toLowerCase();
                if (content.includes('duetto') || name.includes('duetto') ||
                    content.includes('gamechanger') || name.includes('gamechanger')) {
                    signals.push('meta[' + m.name + ']: ' + m.content);
                }
            });
            // Check page title and body text for GameChanger
            if (document.title.toLowerCase().includes('gamechanger')) {
                signals.push('title: ' + document.title);
            }
            return signals;
        }
    """)

    if duetto_signals:
        evidence.extend(duetto_signals)

    # Check cookies
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


def _calculate_confidence(result: DuettoDetectionResult) -> str:
    """Calculate confidence level based on detection signals."""
    score = 0

    if result.duetto_pixel_detected:
        score += 3
    if result.gamechanger_detected:
        score += 3
    if result.gamechanger_evidence:
        score += len(result.gamechanger_evidence)
    if result.booking_link_followed:
        score += 1
    if result.errors:
        score -= 1

    if score >= 4:
        return "high"
    elif score >= 2:
        return "medium"
    elif score >= 1:
        return "low"
    return "none"
