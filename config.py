from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    max_concurrent_scans: int = 3
    scan_timeout_ms: int = 30000
    page_load_wait_ms: int = 3000
    booking_engine_wait_ms: int = 8000
    max_hotels_per_batch: int = 50
    headless: bool = True
    firecrawl_api_key: str = ""
    anthropic_api_key: str = ""

    model_config = {"env_prefix": "DUETTO_", "env_file": ".env"}


settings = Settings()
