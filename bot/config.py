# -*- coding: utf-8 -*-
"""Application configuration loaded from .env."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    bot_token: str
    admin_ids: str = ""

    @property
    def admin_id_list(self) -> list[int]:
        if not self.admin_ids:
            return []
        return [int(x.strip()) for x in self.admin_ids.split(",") if x.strip()]

    # LLM (OpenRouter → Gemini)
    openrouter_api_key: str
    llm_model: str = "google/gemini-2.5-flash-preview-04-17"

    # ElevenLabs TTS
    elevenlabs_api_key: str
    elevenlabs_proxy: str = ""

    # Groq (for Whisper transcription)
    groq_api_key: str = ""

    # Fal.ai (FLUX Kontext, reserved)
    fal_key: str = ""

    # Replicate (face swap for illustrations)
    replicate_api_token: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Database
    database_url: str = "postgresql://skazka:skazka@postgres:5432/skazka"

    # Media public URL base (for nginx)
    media_base_url: str = "http://95.216.117.49/media"

    # Media
    media_dir: Path = Path("./media")
    max_concurrent_tts: int = 10
    segment_char_limit: int = 250

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
