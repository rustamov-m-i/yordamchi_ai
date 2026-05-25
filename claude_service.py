"""Claude API wrapper. Builds context, calls Anthropic, parses JSON envelope response."""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

import config
import database
import redaction

logger = logging.getLogger(__name__)

_client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0, max_retries=2)


async def _build_state_block() -> str:
    """Dynamic state snapshot — appended after the cached system prompt."""
    now = datetime.now(database.TZ)
    today_tasks = await database.list_today_tasks()
    today_meetings = await database.list_today_meetings()
    overdue = await database.list_overdue_tasks()
    contacts = await database.list_contacts()
    corrections = await database.list_recent_corrections(limit=5)

    def task_line(t: dict) -> str:
        deadline = t.get("deadline") or "no deadline"
        return f"  - [{t['priority']}] {t['title']} (deadline: {deadline}, status: {t['status']}, id: {t['id']})"

    def meeting_line(m: dict) -> str:
        return f"  - {m['datetime_start']} — {m['title']} (participants: {', '.join(m.get('participants', []))}, id: {m['id']})"

    def contact_line(c: dict) -> str:
        return f"  - {c['name']} (role: {c.get('role') or 'unknown'}, formality: {c.get('formality_level', 3)})"

    def correction_line(c: dict) -> str:
        return f"  - {c.get('correction', '')[:120]} (reason: {c.get('reason', '')[:80]})"

    lines = [
        "# CURRENT PRINCIPAL STATE",
        "",
        f"current_datetime: {now.isoformat()}",
        f"current_weekday: {now.strftime('%A')}",
        "",
        f"today_tasks ({len(today_tasks)}):",
        *(task_line(t) for t in today_tasks[:15]),
        "" if today_tasks else "  (none)",
        "",
        f"today_meetings ({len(today_meetings)}):",
        *(meeting_line(m) for m in today_meetings[:10]),
        "" if today_meetings else "  (none)",
        "",
        f"overdue_tasks ({len(overdue)}):",
        *(task_line(t) for t in overdue[:10]),
        "" if overdue else "  (none)",
        "",
        f"recent_contacts ({len(contacts)}):",
        *(contact_line(c) for c in contacts[:15]),
        "" if contacts else "  (none)",
        "",
        f"recent_style_corrections ({len(corrections)}):",
        *(correction_line(c) for c in corrections),
        "" if corrections else "  (none)",
    ]
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    """Robust JSON extraction. Strips code fences and finds the first valid JSON object."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.exception("Failed to parse JSON from Claude response: %s", text[:500])
        return None


def _fallback(user_message: str) -> dict:
    return {
        "intent": "none",
        "actions": [],
        "user_message": user_message,
        "buttons": [],
        "needs_clarification": False,
        "clarification_question": None,
    }


_FALLBACK_RESPONSE = _fallback("Texnik xato yuz berdi. Iltimos, qaytadan urinib ko'ring.")


async def process_message(user_text: str, internal_directive: Optional[str] = None) -> dict:
    """Send user input to Claude, return parsed JSON envelope.

    internal_directive: when invoked by scheduler (briefings, follow-ups) instead of by the user,
                        pass a system-style directive like "[INTERNAL] generate_morning_briefing".
    """

    state_block = await _build_state_block()

    history = await database.recent_messages(limit=10)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    redacted_count = 0
    if internal_directive:
        outgoing_content = internal_directive
        purpose = "internal:" + internal_directive[:40]
    else:
        outgoing_content, redacted_count = redaction.redact(user_text)
        purpose = "user_message"
    messages.append({"role": "user", "content": outgoing_content})

    input_hash = redaction.hash_input(outgoing_content)
    input_chars = len(outgoing_content)

    try:
        max_tokens = 4000 if internal_directive and "executive_plan" in internal_directive else 1500
        response = await _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": config.SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": state_block,
                },
            ],
            messages=messages,
        )
    except RateLimitError:
        logger.warning("Anthropic rate limited")
        await database.log_llm_call("anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="rate_limit")
        return _fallback("Juda ko'p so'rov. Bir-ikki daqiqadan keyin qayta urinib ko'ring.")
    except AuthenticationError:
        logger.exception("Anthropic auth failed — check ANTHROPIC_API_KEY")
        await database.log_llm_call("anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="auth")
        return _fallback("Claude kalit kalitida muammo. Administrator bilan bog'laning.")
    except BadRequestError as e:
        msg = str(e).lower()
        err_label = "credit_low" if ("credit" in msg or "balance" in msg or "billing" in msg) else "bad_request"
        await database.log_llm_call("anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error=err_label)
        if err_label == "credit_low":
            return _fallback("Claude balansi tugadi. Hisobni to'ldiring: https://console.anthropic.com/settings/billing")
        logger.exception("Anthropic bad request")
        return _fallback("Texnik xato (bad request). Iltimos, qaytadan urinib ko'ring.")
    except APITimeoutError:
        logger.warning("Anthropic timeout")
        await database.log_llm_call("anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="timeout")
        return _fallback("Javob kech keldi. Qaytadan urinib ko'ring.")
    except APIConnectionError:
        logger.warning("Anthropic connection error")
        await database.log_llm_call("anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="connection")
        return _fallback("Tarmoqqa ulanib bo'lmadi. Bir ozdan keyin qaytadan urinib ko'ring.")
    except APIStatusError as e:
        await database.log_llm_call("anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error=f"status_{e.status_code}")
        if getattr(e, "status_code", None) == 529:
            return _fallback("Claude vaqtincha band. Bir ozdan keyin qaytadan urinib ko'ring.")
        logger.exception("Anthropic API status error: %s", e.status_code)
        return _FALLBACK_RESPONSE
    except APIError:
        logger.exception("Anthropic API error")
        await database.log_llm_call("anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="api_error")
        return _FALLBACK_RESPONSE

    raw = response.content[0].text if response.content else ""

    # Audit log — success path
    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    out_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
    cost = redaction.estimate_cost(config.CLAUDE_MODEL, in_tokens, out_tokens, cache_read, cache_creation)
    await database.log_llm_call(
        "anthropic", config.CLAUDE_MODEL, purpose, input_hash, input_chars,
        in_tokens, out_tokens, cache_read, cache_creation,
        redacted_terms_count=redacted_count, estimated_cost_usd=cost,
    )

    parsed = _extract_json(raw)
    if not parsed:
        logger.error("Claude returned non-JSON output: %s", raw[:500])
        return _FALLBACK_RESPONSE

    parsed.setdefault("intent", "none")
    parsed.setdefault("actions", [])
    parsed.setdefault("user_message", "")
    parsed.setdefault("buttons", [])
    parsed.setdefault("needs_clarification", False)
    parsed.setdefault("clarification_question", None)

    if not internal_directive:
        await database.append_message("user", user_text)
        await database.append_message("assistant", parsed.get("user_message", ""))
        await database.trim_history(keep=30)

    return parsed
