"""Voice transcription.

Primary backend: **Muxlisa.uz** — Uzbek-native STT, significantly more accurate for Uzbek
than OpenAI Whisper. Data stays inside Uzbekistan (banking-compliance win).

Fallback backend: OpenAI Whisper (auto-detect + Uzbek primer). Used only if Muxlisa is
unreachable or returns an error.

API contract (Muxlisa v2):
  POST https://service.muxlisa.uz/api/v2/stt
  Header: x-api-key: <key>
  Body (multipart/form-data):
    audio: <file bytes>
    language: "uz"
  Response: {"text": "<transcript>"}
"""

import hashlib
import io
import logging
import socket
import ssl
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import certifi
import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

import config
import database

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 5 * 1024 * 1024  # 5 MB hard limit
MUXLISA_TIMEOUT = 45.0
WHISPER_TIMEOUT = 45.0

# Heuristic silence cutoff in bytes. An Ogg/Opus voice message of <2KB is
# almost always silence/click — Telegram's minimum recordable audio is
# ~3KB. Below this we skip the (paid) STT round-trip entirely.
_SILENCE_BYTES_THRESHOLD = 2 * 1024

_CA_BUNDLE = certifi.where()

# Muxlisa's TLS endpoint is signed by a private CA ("bBakh") absent from every public
# trust store, so verification against certifi always fails. We pin the leaf cert
# instead: fetched fresh on bot startup, saved to data/muxlisa_pin.pem, and used as
# the verify bundle for Muxlisa requests only. Auto-refresh handles the ~90-day
# cert rotation; if the refresh ever fails, the Whisper fallback still covers voice.
_MUXLISA_PIN_PATH = Path(config.DATABASE_PATH).parent / "muxlisa_pin.pem"


def refresh_muxlisa_pin() -> Optional[str]:
    """Fetch service.muxlisa.uz's served chain and save it as the verify bundle.
    Returns the pin file path on success, None on failure (caller falls back to certifi).
    """
    host = urlparse(config.MUXLISA_STT_URL).hostname or "service.muxlisa.uz"
    try:
        if not hasattr(ssl.SSLSocket, "get_unverified_chain"):
            # Python <3.13: no chain API. A leaf-only pin breaks verification
            # (OpenSSL needs the issuer too), so skip pinning and let
            # _muxlisa_verify() fall back to certifi. This works as long as the
            # server uses a public CA (currently Let's Encrypt).
            logger.info("Muxlisa pin skipped (Python <3.13); using certifi")
            return None
        ctx = ssl._create_unverified_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                chain = tls.get_unverified_chain()
        if not chain:
            logger.warning("Muxlisa pin refresh: empty chain from %s", host)
            return None
        _MUXLISA_PIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MUXLISA_PIN_PATH, "w") as f:
            for cert in chain:
                der = cert if isinstance(cert, (bytes, bytearray)) else cert.public_bytes(ssl.Purpose.CLIENT_AUTH)
                f.write(ssl.DER_cert_to_PEM_cert(der))
        logger.info("Muxlisa cert pinned: %s (chain len=%d)", _MUXLISA_PIN_PATH, len(chain))
        return str(_MUXLISA_PIN_PATH)
    except Exception:
        logger.exception("Muxlisa pin refresh failed — Whisper fallback will be used")
        return None


def _muxlisa_verify() -> str:
    """Verify target for Muxlisa: pinned leaf if present, else certifi (will fail
    on private-CA certs, but at least is well-defined)."""
    if _MUXLISA_PIN_PATH.exists():
        return str(_MUXLISA_PIN_PATH)
    return _CA_BUNDLE


# Refresh the pin on import so the first request after startup uses a current cert.
refresh_muxlisa_pin()

_openai_client = AsyncOpenAI(
    api_key=config.OPENAI_API_KEY,
    timeout=WHISPER_TIMEOUT,
    max_retries=2,
    http_client=httpx.AsyncClient(verify=_CA_BUNDLE, timeout=WHISPER_TIMEOUT),
)

# Uzbek primer for Whisper fallback only — biases auto-detect toward Uzbek vs Turkish/Azeri.
_UZBEK_PRIMER = (
    "Bu o'zbek tilida yozilgan audio. Salom, hisobot, uchrashuv, Toshkent, "
    "Agrobank, ertaga, vazifa, deadline, prioritet, marketing, byudjet."
)

# Post-correction config — Muxlisa often misrecognizes proper nouns, banking terms,
# and Russian/English loanwords. We fix these with a cheap Claude pass after the
# Muxlisa response. If the correction fails for any reason we return the original
# transcript unchanged — voice messages must never be lost to this enhancement.
_CORRECTION_MODEL = "claude-haiku-4-5"
_CORRECTION_TIMEOUT = 15.0
_CORRECTION_MIN_LEN = 5  # skip for "ha", "yo'q", etc.

_FIXED_GLOSSARY = [
    # Workplace nouns Muxlisa often mangles
    "Agrobank", "Yordamchi", "Telegram", "Toshkent",
    # English business loanwords frequent in Uzbek speech
    "deadline", "meeting", "call", "stand-up", "follow-up", "report",
    "presentation", "demo", "feedback", "KPI", "OKR",
    # Uzbek business terms often misheard
    "kredit", "byudjet", "marketing", "biznes", "menejer", "prioritet",
    "loyiha", "vazifa", "uchrashuv", "hisobot", "muhokama",
    # Russian loanwords common in mixed speech
    "встреча", "проект", "задача", "сегодня", "завтра", "отчёт",
]

_anthropic_client = AsyncAnthropic(
    api_key=config.ANTHROPIC_API_KEY,
    timeout=_CORRECTION_TIMEOUT,
    max_retries=1,
)


async def _correct_transcript(text: str) -> str:
    """Post-correct a Muxlisa transcript using Claude + contacts/glossary.

    Returns the original text unchanged on any failure, so a broken correction
    pass never causes a voice message to be lost.
    """
    if not text or len(text.strip()) < _CORRECTION_MIN_LEN:
        return text

    try:
        contacts = await database.list_contacts()
        names = [c["name"] for c in contacts if c.get("name")]
        names_block = ", ".join(names[:40]) if names else "(yo'q)"
        glossary_block = ", ".join(_FIXED_GLOSSARY)

        prompt = (
            "Sen o'zbek tilidagi STT (speech-to-text) chiqishini post-korrektor'sisan.\n"
            "Muxlisa STT ayrim ismlarni, bank atamalarini va rus/ingliz so'zlarini xato eshitishi mumkin.\n\n"
            "VAZIFA: matnda glossary bilan mos keladigan ANIQ xato eshitilgan so'zlarni tuzating.\n"
            "QOIDALAR:\n"
            "- Boshqa so'zlarga tegma. Mazmunni saqla.\n"
            "- Tinish belgilarini o'zgartirma.\n"
            "- Glossary so'zi matnda yo'q bo'lsa — uni zo'rlab kiritma.\n"
            "- Faqat tuzatilgan matnni qaytar (izoh, prefiks, qo'shtirnoq yo'q).\n\n"
            f"GLOSSARY:\n"
            f"Ismlar: {names_block}\n"
            f"Atamalar: {glossary_block}\n\n"
            f"INPUT:\n{text}"
        )

        resp = await _anthropic_client.messages.create(
            model=_CORRECTION_MODEL,
            max_tokens=min(len(text) * 2 + 200, 2000),
            messages=[{"role": "user", "content": prompt}],
        )
        corrected = (resp.content[0].text if resp.content else "").strip()
        if not corrected:
            return text
        # Safety: if Claude went off-script (truncated or expanded massively), keep original
        if len(corrected) > len(text) * 3 or len(corrected) < len(text) // 3:
            logger.warning(
                "Transcript correction shape off (orig=%d, corr=%d) — using original",
                len(text), len(corrected),
            )
            return text

        usage = getattr(resp, "usage", None)
        await database.log_llm_call(
            "anthropic", _CORRECTION_MODEL, "voice_transcript_correction",
            None, len(text),
            getattr(usage, "input_tokens", 0) if usage else 0,
            getattr(usage, "output_tokens", 0) if usage else 0,
        )
        if corrected != text:
            logger.info("Transcript fixed: %r -> %r", text[:80], corrected[:80])
        return corrected
    except Exception as e:
        logger.warning("Transcript correction failed (%s: %s) — using original", type(e).__name__, e)
        return text


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg", language: Optional[str] = "uz") -> Optional[str]:
    """Transcribe audio. Returns transcript string or None on failure.

    Order of attempts:
      1. Muxlisa.uz (if MUXLISA_API_KEY is set) — Uzbek-native, best accuracy.
      2. OpenAI Whisper with Uzbek primer (fallback).
    """
    if not audio_bytes:
        return None
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        logger.warning("Audio rejected: %d bytes > %d MB limit", len(audio_bytes), MAX_AUDIO_BYTES // (1024 * 1024))
        await database.log_llm_call(
            "muxlisa", "stt-v2", "voice_transcribe",
            None, len(audio_bytes), None, None, error="size_limit",
        )
        return None
    # Skip silent/empty clips before paying for an STT round-trip. A real
    # Telegram voice message is at least a few KB; anything below that is
    # almost certainly an accidental tap or empty recording.
    if len(audio_bytes) < _SILENCE_BYTES_THRESHOLD:
        logger.info("Audio skipped: %d bytes below silence threshold", len(audio_bytes))
        await database.log_llm_call(
            "muxlisa", "stt-v2", "voice_transcribe",
            None, len(audio_bytes), None, None, error="silence_skip",
        )
        return None

    audio_hash = hashlib.sha256(audio_bytes).hexdigest()[:16]

    # ── Attempt 1: Muxlisa ──
    if config.MUXLISA_API_KEY:
        try:
            transcript = await _transcribe_muxlisa(audio_bytes, filename, language or "uz")
            if transcript is not None:
                await database.log_llm_call(
                    "muxlisa", "stt-v2", "voice_transcribe",
                    audio_hash, len(audio_bytes), None, len(transcript),
                )
                if transcript:
                    transcript = await _correct_transcript(transcript)
                return transcript or None
        except Exception as e:
            logger.warning("Muxlisa STT failed (%s: %s) — falling back to Whisper", type(e).__name__, e)
            await database.log_llm_call(
                "muxlisa", "stt-v2", "voice_transcribe",
                audio_hash, len(audio_bytes), None, None, error=type(e).__name__,
            )

    # ── Attempt 2: OpenAI Whisper fallback ──
    transcript = await _transcribe_whisper(audio_bytes, filename, audio_hash)
    return transcript


async def _transcribe_muxlisa(audio_bytes: bytes, filename: str, language: str) -> Optional[str]:
    """Single Muxlisa call. Raises on network/HTTP error, returns text on success."""
    async with httpx.AsyncClient(timeout=MUXLISA_TIMEOUT, verify=_muxlisa_verify()) as client:
        files = {"audio": (filename, audio_bytes, "audio/ogg")}
        data = {"language": language}
        headers = {"x-api-key": config.MUXLISA_API_KEY}
        resp = await client.post(config.MUXLISA_STT_URL, files=files, data=data, headers=headers)

    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"Muxlisa returned {resp.status_code}: {resp.text[:200]}",
            request=resp.request,
            response=resp,
        )

    try:
        payload = resp.json()
    except Exception:
        raise ValueError(f"Muxlisa non-JSON response: {resp.text[:200]}")

    text = (payload.get("text") or "").strip()
    logger.info("Muxlisa transcript: %d chars", len(text))
    return text


async def _transcribe_whisper(audio_bytes: bytes, filename: str, audio_hash: str) -> Optional[str]:
    """OpenAI Whisper fallback with Uzbek primer."""
    buf = io.BytesIO(audio_bytes)
    buf.name = filename
    try:
        result = await _openai_client.audio.transcriptions.create(
            model=config.WHISPER_MODEL,
            file=buf,
            response_format="text",
            prompt=_UZBEK_PRIMER,
        )
        transcript = (result if isinstance(result, str) else getattr(result, "text", "")).strip()
        await database.log_llm_call(
            "openai", config.WHISPER_MODEL, "voice_transcribe",
            audio_hash, len(audio_bytes), None, len(transcript) if transcript else 0,
        )
        return transcript or None
    except Exception as e:
        logger.exception("Whisper fallback also failed")
        await database.log_llm_call(
            "openai", config.WHISPER_MODEL, "voice_transcribe",
            audio_hash, len(audio_bytes), None, None, error=type(e).__name__,
        )
        return None
