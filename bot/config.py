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
    llm_model: str = "google/gemini-2.5-pro-preview-03-25"

    # ElevenLabs TTS
    elevenlabs_api_key: str
    elevenlabs_proxy: str = ""

    # Groq (for Whisper transcription)
    groq_api_key: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Database
    database_url: str = "sqlite+aiosqlite:///./skazka.db"

    # Media
    media_dir: Path = Path("./media")
    max_concurrent_tts: int = 10
    segment_char_limit: int = 250

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
