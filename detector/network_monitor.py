import time
from models import NetworkRequest


class NetworkMonitor:
    """Captures all network requests and identifies Duetto-related traffic."""

    DUETTO_PIXEL_PATTERNS = [
        "capture.duettoresearch.com",
        "duettoresearch.com/capture",
        "duettoresearch.com/pixel",
        "duettoresearch.com/track",
    ]

    DUETTO_DOMAIN_PATTERNS = [
        "duettoresearch.com",
        "duettocloud.com",
    ]

    GAMECHANGER_PATTERNS = [
        "gamechanger.duetto",
        "gc.duettoresearch.com",
        "app.duettoresearch.com",
        "duettocloud.com/gamechanger",
    ]

    def __init__(self):
        self.all_requests: list[dict] = []
        self.duetto_requests: list[dict] = []
        self.console_logs: list[str] = []

    def attach(self, page):
        """Attach listeners to a Playwright page."""
        page.on("request", self._on_request)
        page.on("console", self._on_console)

    def _on_request(self, request):
        entry = {
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "timestamp": time.time(),
        }
        self.all_requests.append(entry)

        url_lower = request.url.lower()
        if any(p in url_lower for p in self.DUETTO_DOMAIN_PATTERNS):
            self.duetto_requests.append(entry)

    def _on_console(self, msg):
        self.console_logs.append(msg.text)

    @property
    def duetto_pixel_detected(self) -> bool:
        return any(
            any(p in r["url"].lower() for p in self.DUETTO_PIXEL_PATTERNS)
            for r in self.duetto_requests
        )

    @property
    def gamechanger_in_network(self) -> bool:
        return any(
            any(p in r["url"].lower() for p in self.GAMECHANGER_PATTERNS)
            for r in self.duetto_requests
        )

    @property
    def pixel_requests(self) -> list[NetworkRequest]:
        return [
            NetworkRequest(
                url=r["url"],
                method=r["method"],
                resource_type=r.get("resource_type", ""),
                timestamp=r.get("timestamp", 0),
            )
            for r in self.duetto_requests
            if any(p in r["url"].lower() for p in self.DUETTO_PIXEL_PATTERNS)
        ]

    @property
    def captured_domains(self) -> list[str]:
        """Return unique domains from all captured requests."""
        from urllib.parse import urlparse

        domains = set()
        for r in self.all_requests:
            try:
                domains.add(urlparse(r["url"]).netloc)
            except Exception:
                pass
        return sorted(domains)
