"""Configuration loader. Reads .env, validates required fields, exposes typed constants."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.resolve()
load_dotenv(ROOT / ".env", override=True)


def _require(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        sys.stderr.write(f"FATAL: missing required env var {key}. See .env.example.\n")
        sys.exit(1)
    return value


def _int(key: str, required: bool = True, default: int = 0) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        if required:
            sys.stderr.write(f"FATAL: missing required env var {key}.\n")
            sys.exit(1)
        return default
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(f"FATAL: env var {key} must be an integer, got '{raw}'.\n")
        sys.exit(1)


TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
PRINCIPAL_USER_ID: int = _int("PRINCIPAL_USER_ID")

ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip()

OPENAI_API_KEY: str = _require("OPENAI_API_KEY")
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-1").strip()

# Muxlisa.uz STT — Uzbek-native, primary voice provider
MUXLISA_API_KEY: str = os.getenv("MUXLISA_API_KEY", "").strip()
MUXLISA_STT_URL: str = os.getenv("MUXLISA_STT_URL", "https://service.muxlisa.uz/api/v2/stt").strip()

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/yordamchi.db").strip()
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Tashkent").strip()
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()

ERROR_NOTIFY_USER_ID: int = _int("ERROR_NOTIFY_USER_ID", required=False, default=PRINCIPAL_USER_ID)

# iCloud Calendar integration (optional)
APPLE_ID: str = os.getenv("APPLE_ID", "").strip()
APPLE_APP_SPECIFIC_PASSWORD: str = os.getenv("APPLE_APP_SPECIFIC_PASSWORD", "").strip()
ICLOUD_CALENDAR_NAME: str = os.getenv("ICLOUD_CALENDAR_NAME", "").strip()
ICLOUD_SYNC_INTERVAL_MIN: int = _int("ICLOUD_SYNC_INTERVAL_MIN", required=False, default=15)
ICLOUD_ENABLED: bool = bool(APPLE_ID and APPLE_APP_SPECIFIC_PASSWORD)

def _load_system_prompt() -> str:
    """Load the system prompt from system_prompts/*.md (modular).
    Falls back to legacy system_prompt.md if the directory is missing or empty.
    Files are concatenated in alphanumeric order (00_, 10_, 20_, ...).
    """
    modules_dir = ROOT / "system_prompts"
    if modules_dir.is_dir():
        parts = []
        for p in sorted(modules_dir.glob("*.md")):
            parts.append(p.read_text(encoding="utf-8").rstrip())
        if parts:
            return "\n\n---\n\n".join(parts)
    legacy = ROOT / "system_prompt.md"
    if legacy.exists():
        return legacy.read_text(encoding="utf-8")
    sys.stderr.write(f"FATAL: no system_prompts/ directory and no legacy system_prompt.md\n")
    sys.exit(1)


SYSTEM_PROMPT: str = _load_system_prompt()


def ensure_paths() -> None:
    """Create runtime directories (DB folder). Called explicitly from bot startup."""
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
