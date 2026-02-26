from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    max_concurrent_scans: int = 3
    scan_timeout_ms: int = 60000
    page_load_wait_ms: int = 2000
    booking_engine_wait_ms: int = 5000
    max_hotels_per_batch: int = 50
    headless: bool = True

    model_config = {"env_prefix": "DUETTO_"}


settings = Settings()
