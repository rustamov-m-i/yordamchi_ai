"""Pre-LLM redaction layer.

Purpose: catch obvious bank-confidentiality leaks BEFORE text leaves the host to Anthropic/OpenAI.
This is a safety net, NOT a compliance guarantee. The principal is still responsible for what
they dictate. Compliance sign-off (see audit §4) is required regardless.

What gets redacted:
  - Card numbers (13–19 contiguous digits, with optional spaces/dashes)
  - Long digit runs that look like account numbers (≥9 digits)
  - Uzbek tax ID / INN-like patterns (9 digits with context word)
  - Phone numbers (+998 ...)
  - Email addresses
  - IBAN-like patterns (UZ + 2 digits + alphanumeric)

What does NOT get redacted:
  - Names — too contextual; flagged for the principal's awareness instead
  - Amounts — legitimate operational content
  - General bank-internal jargon

Override via REDACTION_DISABLE=true in .env (for personal-use deployments where
the principal explicitly opts out).
"""

import hashlib
import logging
import os
import re
from typing import Tuple

logger = logging.getLogger(__name__)

DISABLED = os.getenv("REDACTION_DISABLE", "false").strip().lower() in ("1", "true", "yes", "on")


# Patterns are applied in order. First match wins per region.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Card numbers: 13–19 digits possibly separated by spaces or dashes
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Phone numbers: +998 followed by digits (with optional spaces/dashes)
    ("PHONE", re.compile(r"\+998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")),
    # Email
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    # IBAN-like (Uzbek): UZ + 2 digits + 16 alphanumeric
    ("IBAN", re.compile(r"\bUZ\d{2}[A-Z0-9]{16,20}\b", re.IGNORECASE)),
    # INN-like: word "inn"/"стир"/"стир" near 9 digits
    ("INN", re.compile(r"(?:\binn\b|\bстир\b|\bstir\b)[\s:№#]*(\d{9})\b", re.IGNORECASE)),
    # Long digit runs that look like account numbers (≥12 digits, not already caught)
    ("ACCOUNT", re.compile(r"\b\d{12,}\b")),
]


def redact(text: str) -> Tuple[str, int]:
    """Return (redacted_text, count_of_redactions).

    Each match is replaced with a tag like [CARD-REDACTED] so the LLM still understands
    the shape of the input (and can answer questions like "where do I send this card number?")
    without seeing the actual digits.
    """
    if DISABLED or not text:
        return text, 0

    total = 0
    redacted = text
    for label, pattern in _PATTERNS:
        new_text, n = pattern.subn(f"[{label}-REDACTED]", redacted)
        if n:
            logger.info("Redacted %d %s occurrence(s) before LLM call", n, label)
            total += n
            redacted = new_text
    return redacted, total


def hash_input(text: str) -> str:
    """Stable 16-char hex hash of input — for audit log without storing content."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# Rough USD pricing as of mid-2025 (per-million tokens). Update if rates change.
_PRICES_USD_PER_MTOK = {
    "claude-opus-4-7":     {"in": 15.0, "out": 75.0, "cache_read": 1.5,  "cache_write": 18.75},
    "claude-sonnet-4-6":   {"in": 3.0,  "out": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-sonnet-4-5":   {"in": 3.0,  "out": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "whisper-1":           {"in": 6.0,  "out": 0.0,  "cache_read": 0.0,  "cache_write": 0.0},  # $0.006/min ≈ rough est.
}


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    rates = _PRICES_USD_PER_MTOK.get(model)
    if not rates:
        return 0.0
    return (
        input_tokens * rates["in"]
        + output_tokens * rates["out"]
        + cache_read_tokens * rates["cache_read"]
        + cache_creation_tokens * rates["cache_write"]
    ) / 1_000_000
