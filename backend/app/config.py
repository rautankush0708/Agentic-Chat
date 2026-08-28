import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    """All secrets/tunables come from environment variables — nothing hardcoded."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'agentic_chat.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")

    # ---- LLM provider (used for intent detection, NL->SQL, result explanation) ----
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "openai")  # "openai" | "anthropic"
    AI_API_KEY = os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get(
        "ANTHROPIC_API_KEY"
    )
    AI_MODEL = os.environ.get("AI_MODEL", "gpt-4.1-mini")
    AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
    AI_TIMEOUT_SECONDS = int(os.environ.get("AI_TIMEOUT_SECONDS", "60"))

    # ---- Text-to-speech provider ----
    TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "gtts")  # "gtts" | "sarvam"
    SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")
    SARVAM_TTS_SPEAKER = os.environ.get("SARVAM_TTS_SPEAKER", "meera")
    SARVAM_TTS_LANGUAGE = os.environ.get("SARVAM_TTS_LANGUAGE", "en-IN")

    SQL_ROW_LIMIT = int(os.environ.get("SQL_ROW_LIMIT", "200"))
