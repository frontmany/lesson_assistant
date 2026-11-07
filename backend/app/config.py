"""Настройки из переменных окружения. Полный список — в .env.example."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ROOT — папка backend/ (данные, промпты, сертификаты); мини-приложение лежит рядом, в webapp/
ROOT = Path(__file__).resolve().parent.parent
WEBAPP = ROOT.parent / "webapp"


@dataclass(frozen=True)
class Settings:
    source_mode: str = os.getenv("SOURCE_MODE", "call")
    demo_lesson_dir: Path = ROOT / os.getenv("DEMO_LESSON_DIR", "data/demo_lesson")
    # Длина урока для шкалы: от неё зависят смягчение нормы и «давно не отвечал»
    lesson_minutes: float = float(os.getenv("LESSON_MINUTES", "40"))
    public_url: str = os.getenv("PUBLIC_URL", "")

    livekit_url: str = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
    livekit_public_url: str = os.getenv("LIVEKIT_PUBLIC_URL", "ws://localhost:7880")
    livekit_api_key: str = os.getenv("LIVEKIT_API_KEY", "devkey")
    livekit_api_secret: str = os.getenv("LIVEKIT_API_SECRET", "secret")

    max_bot_token: str = os.getenv("MAX_BOT_TOKEN", "")
    max_api_url: str = os.getenv("MAX_API_URL", "https://platform-api2.max.ru")
    max_bot_mode: str = os.getenv("MAX_BOT_MODE", "polling")
    # имя бота нужно кнопке, которая открывает его мини-приложение
    max_bot_name: str = os.getenv("MAX_BOT_NAME", "")
    # подпись сессий мини-приложения (#24); без неё — производная от токена бота
    session_secret: str = os.getenv("SESSION_SECRET", "")
    # разработка: участника можно назвать в адресе (?id=), без данных запуска MAX
    dev_auth: bool = os.getenv("DEV_AUTH", "0") == "1"

    # GigaAM у всех: на живых фразах меньше ошибок и почти все «?», в разы быстрее Whisper,
    # а на демо-уроке и у учителя точнее small, имена без подсказок слышит (analytics/README.md)
    whisper_model_teacher: str = os.getenv("WHISPER_MODEL_TEACHER", "gigaam-v3-e2e-ctc")
    whisper_model_student: str = os.getenv("WHISPER_MODEL_STUDENT", "gigaam-v3-e2e-ctc")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    whisper_download_root: str = os.getenv("WHISPER_DOWNLOAD_ROOT", str(ROOT.parent / "models"))

    # Сохранять найденные фразы (wav и текст) для набора замеров. Только для
    # тестовых уроков: голоса детей не сохраняем
    save_phrases_dir: str = os.getenv("SAVE_PHRASES_DIR", "")

    llm_api_url: str = os.getenv("LLM_API_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./lesson_assistant.sqlite3")
    redis_url: str = os.getenv("REDIS_URL", "")


settings = Settings()
