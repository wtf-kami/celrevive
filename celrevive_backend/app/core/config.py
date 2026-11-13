"""
Central configuration for image-validation thresholds and constraints.

All values are overridable via environment variables / a .env file so they
can be tuned per work item 1.1 ("Define/configure ... threshold") without
touching code.
"""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/celrevive"
    SKIN_IMAGE_STORAGE_ROOT: str = "./data/skin_images"
    VISUAL_AI_MODEL: str = "gemini-2.5-flash"
    QUESTIONNAIRE_AI_MODEL: str = "gemini-2.5-flash"
    FORMULATION_AI_MODEL: str = "gemini-2.5-flash"
    GEMINI_API_KEY: str | None = None
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    SUPABASE_STORAGE_BUCKET: str = "skin-images"

    # --- File constraints ---
    # NOTE: kept as a plain str field (not List[str]) because pydantic-settings
    # requires JSON syntax in .env for list-typed fields (e.g. ["a","b"]).
    # A plain str avoids that and lets .env stay a simple comma-separated value;
    # use the ALLOWED_CONTENT_TYPES property below to get the parsed list.
    ALLOWED_CONTENT_TYPES_RAW: str = "image/jpeg,image/png,image/webp"

    @property
    def ALLOWED_CONTENT_TYPES(self) -> List[str]:
        return [item.strip() for item in self.ALLOWED_CONTENT_TYPES_RAW.split(",") if item.strip()]
    MAX_FILE_SIZE_MB: float = 10.0

    # --- Resolution ---
    MIN_WIDTH_PX: int = 480
    MIN_HEIGHT_PX: int = 480

    # --- Sharpness (Laplacian variance) ---
    # Below this, image is considered blurry. Tune against real Sprint-1 test images.
    BLUR_THRESHOLD: float = 100.0

    # --- Brightness (mean pixel intensity, 0-255) ---
    LOW_LIGHT_THRESHOLD: float = 40.0
    OVEREXPOSED_THRESHOLD: float = 235.0

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.MAX_FILE_SIZE_MB * 1024 * 1024)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import and call this, don't instantiate Settings directly."""
    return Settings()
