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


def _bool(key: str, default: bool = True) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
PRINCIPAL_USER_ID: int = _int("PRINCIPAL_USER_ID")

ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")
# Default model used for normal user-facing turns and most internal calls.
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip()
# Fast/cheap model used for short, low-stakes calls (voice transcript polish,
# simple intent classification). ~70% cheaper than Sonnet.
CLAUDE_MODEL_FAST: str = os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5").strip()
# Premium model reserved for long-form planning and high-judgement directives
# (executive_plan). Used sparingly; ~5x cost of Sonnet.
CLAUDE_MODEL_COMPLEX: str = os.getenv("CLAUDE_MODEL_COMPLEX", "claude-opus-4-8").strip()

# OpenAI Whisper — STT fallback ONLY (used when both Uzbek-native providers fail).
# Optional: leave unset to run without OpenAI entirely — the bot just loses the
# last-resort transcription fallback. NOT used for the bot's brain (that's Claude).
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-1").strip()
VOICE_AUTO_CONFIRM: bool = _bool("VOICE_AUTO_CONFIRM", True)
CONFIRM_CREATE_ACTIONS: bool = _bool("CONFIRM_CREATE_ACTIONS", True)

# Aisha AI STT — primary Uzbek-native voice provider (pay-per-minute, ~425 UZS/min).
# Data stays in Uzbekistan (banking-compliance win). Supports uz/ru/en.
# Endpoint: POST /api/v1/stt/post/ (sync, suits short Telegram voice messages).
# Auth: X-Api-Key header.
AISHA_API_KEY: str = os.getenv("AISHA_API_KEY", "").strip()
AISHA_STT_URL: str = os.getenv("AISHA_STT_URL", "https://back.aisha.group/api/v1/stt/post/").strip()

# Muxlisa.uz STT — legacy provider, retained as secondary fallback for the
# transition period. Remove MUXLISA_API_KEY from .env to fully decommission.
MUXLISA_API_KEY: str = os.getenv("MUXLISA_API_KEY", "").strip()
MUXLISA_STT_URL: str = os.getenv("MUXLISA_STT_URL", "https://service.muxlisa.uz/api/v2/stt").strip()

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/yordamchi.db").strip()
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Tashkent").strip()
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()

ERROR_NOTIFY_USER_ID: int = _int("ERROR_NOTIFY_USER_ID", required=False, default=PRINCIPAL_USER_ID)

# ── Telegram Mini App (Web App) ──
# Off by default: the bot behaves exactly as before unless WEBAPP_ENABLED=1.
# WEBAPP_URL is the PUBLIC https URL (nginx → 127.0.0.1:WEBAPP_PORT) used for the
# menu button; Telegram only opens https. Auth reuses TELEGRAM_BOT_TOKEN (initData
# HMAC) and restricts access to PRINCIPAL_USER_ID.
WEBAPP_ENABLED: bool = _bool("WEBAPP_ENABLED", False)
WEBAPP_PORT: int = _int("WEBAPP_PORT", required=False, default=8081)
WEBAPP_URL: str = os.getenv("WEBAPP_URL", "").strip()
# Bind host — keep 127.0.0.1 so only the local nginx reverse proxy can reach it.
WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "127.0.0.1").strip()
# Bot @username — set at startup from get_me(); the browser Login Widget needs it.
# Overridable via env for the web layer when the bot process isn't the one serving.
BOT_USERNAME: str = os.getenv("WEBAPP_BOT_USERNAME", "").strip()

# Data retention — banking-compliance friendly defaults. Override per
# deployment if your jurisdiction requires longer/shorter windows.
CONVERSATION_TTL_DAYS: int = _int("CONVERSATION_TTL_DAYS", required=False, default=365)
LLM_AUDIT_TTL_DAYS: int = _int("LLM_AUDIT_TTL_DAYS", required=False, default=365)
# Self-improvement circuit-breaker: max SI LLM operations (nightly diagnosis +
# implementation runs) per calendar day. Beyond this the autonomous loop pauses and
# notifies the principal — a hard ceiling so a runaway loop can't burn unbounded spend.
SI_DAILY_OP_CAP: int = _int("SI_DAILY_OP_CAP", required=False, default=10)

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
    sys.stderr.write("FATAL: no system_prompts/ directory and no legacy system_prompt.md\n")
    sys.exit(1)


SYSTEM_PROMPT: str = _load_system_prompt()


def ensure_paths() -> None:
    """Create runtime directories (DB folder). Called explicitly from bot startup."""
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
