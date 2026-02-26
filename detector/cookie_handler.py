from playwright.async_api import Page

# Common cookie consent button selectors across major CMP providers
CONSENT_SELECTORS = [
    # CookieBot
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    # OneTrust
    "#onetrust-accept-btn-handler",
    ".onetrust-close-btn-handler",
    # Quantcast
    ".qc-cmp2-summary-buttons button[mode='primary']",
    # TrustArc
    "#truste-consent-button",
    # Didomi
    "#didomi-notice-agree-button",
    # Complianz
    ".cmplz-accept",
    # Iubenda
    ".iubenda-cs-accept-btn",
    # Usercentrics
    "[data-testid='uc-accept-all-button']",
    # Generic patterns
    'button:has-text("Accept All")',
    'button:has-text("Accept all")',
    'button:has-text("Accept Cookies")',
    'button:has-text("Accept all cookies")',
    'button:has-text("Allow All")',
    'button:has-text("Allow all cookies")',
    'button:has-text("I Accept")',
    'button:has-text("Agree")',
    'button:has-text("OK")',
    'button:has-text("Akzeptieren")',  # German
    'button:has-text("Alle akzeptieren")',  # German
    'button:has-text("Tout accepter")',  # French
    'button:has-text("Aceptar todo")',  # Spanish
    'a:has-text("Accept All")',
    'a:has-text("Accept all cookies")',
]


async def dismiss_cookie_consent(page: Page, timeout_ms: int = 3000) -> bool:
    """Attempt to dismiss a cookie consent banner. Returns True if successful."""
    for selector in CONSENT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=timeout_ms):
                await locator.click(timeout=2000)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    return False
