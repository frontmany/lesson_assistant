"""Настройки из переменных окружения. Полный список — в .env.example."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    source_mode: str = os.getenv("SOURCE_MODE", "call")
    demo_lesson_dir: Path = ROOT / os.getenv("DEMO_LESSON_DIR", "data/demo_lesson")
    public_url: str = os.getenv("PUBLIC_URL", "")

    livekit_url: str = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
    livekit_public_url: str = os.getenv("LIVEKIT_PUBLIC_URL", "ws://localhost:7880")
    livekit_api_key: str = os.getenv("LIVEKIT_API_KEY", "devkey")
    livekit_api_secret: str = os.getenv("LIVEKIT_API_SECRET", "secret")

    max_bot_token: str = os.getenv("MAX_BOT_TOKEN", "")
    max_api_url: str = os.getenv("MAX_API_URL", "https://platform-api2.max.ru")
    max_bot_mode: str = os.getenv("MAX_BOT_MODE", "polling")

    whisper_model: str = os.getenv("WHISPER_MODEL", "base")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    whisper_download_root: str = os.getenv("WHISPER_DOWNLOAD_ROOT", str(ROOT / "models"))

    llm_api_url: str = os.getenv("LLM_API_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./lesson_assistant.sqlite3")
    redis_url: str = os.getenv("REDIS_URL", "")


settings = Settings()
