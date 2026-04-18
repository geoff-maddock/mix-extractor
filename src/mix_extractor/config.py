"""Configuration — loads settings from .env and environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

# Load .env from the project root (two levels up from this file's package)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


LLMProvider = Literal["openai", "anthropic"]
TranscriptionProvider = Literal["whisper_api", "whisper_local", "assemblyai", "deepgram"]


class Settings(BaseModel):
    # ── LLM ──────────────────────────────────────────────────────────────────
    llm_provider: LLMProvider = Field(default="openai")
    llm_model: str = Field(default="gpt-4o")

    # ── Transcription ─────────────────────────────────────────────────────────
    transcription_provider: TranscriptionProvider = Field(default="whisper_api")

    # ── API Keys ──────────────────────────────────────────────────────────────
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    assemblyai_api_key: str = Field(default="")
    deepgram_api_key: str = Field(default="")
    spotify_client_id: str = Field(default="")
    spotify_client_secret: str = Field(default="")
    discogs_token: str = Field(default="")
    audd_api_key: str = Field(default="")
    buymusic_club_username: str = Field(default="")
    buymusic_club_password: str = Field(default="")

    # ── Paths ─────────────────────────────────────────────────────────────────
    input_dir: Path = Field(default=_PROJECT_ROOT / "content" / "input")
    output_dir: Path = Field(default=_PROJECT_ROOT / "content" / "output")

    @model_validator(mode="before")
    @classmethod
    def _load_from_env(cls, values: dict) -> dict:
        mapping = {
            "llm_provider": "LLM_PROVIDER",
            "llm_model": "LLM_MODEL",
            "transcription_provider": "TRANSCRIPTION_PROVIDER",
            "openai_api_key": "OPENAI_API_KEY",
            "anthropic_api_key": "ANTHROPIC_API_KEY",
            "assemblyai_api_key": "ASSEMBLYAI_API_KEY",
            "deepgram_api_key": "DEEPGRAM_API_KEY",
            "spotify_client_id": "SPOTIFY_CLIENT_ID",
            "spotify_client_secret": "SPOTIFY_CLIENT_SECRET",
            "discogs_token": "DISCOGS_TOKEN",
            "audd_api_key": "AUDD_API_KEY",
            "buymusic_club_username": "BUYMUSIC_CLUB_USERNAME",
            "buymusic_club_password": "BUYMUSIC_CLUB_PASSWORD",
        }
        for field, env_var in mapping.items():
            if env_val := os.getenv(env_var):
                values.setdefault(field, env_val)
        return values

    def require_key(self, name: str) -> str:
        """Return the named API key or raise a clear error."""
        value = getattr(self, name, "")
        if not value:
            env_var = name.upper()
            raise ValueError(
                f"Missing required API key: {env_var}\n"
                f"Set it in your .env file or as an environment variable."
            )
        return value


def get_settings(**overrides) -> Settings:
    """Return a Settings instance, optionally overriding values from CLI flags."""
    return Settings(**overrides)
