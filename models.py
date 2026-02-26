from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class DuettoProduct(str, Enum):
    PIXEL = "Duetto Pixel"
    GAMECHANGER = "GameChanger Booking Engine"
    NONE = "None Detected"


class BookingLinkInfo(BaseModel):
    text: str
    href: str
    link_type: str  # "button", "link", "iframe"
    detection_method: str  # "text_match", "href_pattern", "iframe_src"
    opens_in: str = "same_window"  # "same_window", "new_tab", "iframe"


class NetworkRequest(BaseModel):
    url: str
    method: str = "GET"
    resource_type: str = ""
    timestamp: float = 0.0


class DuettoDetectionResult(BaseModel):
    hotel_name: str
    website_url: str
    scan_timestamp: datetime = Field(default_factory=datetime.utcnow)

    booking_links_found: list[BookingLinkInfo] = []
    booking_link_followed: Optional[BookingLinkInfo] = None
    booking_engine_url: str = ""

    duetto_pixel_detected: bool = False
    pixel_requests: list[NetworkRequest] = []

    gamechanger_detected: bool = False
    gamechanger_evidence: list[str] = []

    duetto_products: list[DuettoProduct] = []
    confidence: str = "none"  # "none", "low", "medium", "high"

    all_captured_domains: list[str] = []
    console_logs: list[str] = []
    errors: list[str] = []
    scan_duration_seconds: float = 0.0
    screenshot_path: str = ""


class BatchResult(BaseModel):
    total_hotels: int
    scanned: int
    duetto_pixel_count: int
    gamechanger_count: int
    results: list[DuettoDetectionResult]
    scan_date: datetime = Field(default_factory=datetime.utcnow)
