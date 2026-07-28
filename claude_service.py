"""Claude API wrapper. Builds context, calls Anthropic, parses JSON envelope response."""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional, Tuple

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


# ──────────────────────── CIRCUIT BREAKER ────────────────────────
# Tracks recent Anthropic failures. When >= _CIRCUIT_THRESHOLD consecutive
# failures occur within _CIRCUIT_WINDOW seconds, the circuit "opens" and we
# short-circuit for _CIRCUIT_COOLDOWN seconds (returning a degraded fallback
# instead of hammering a down API). After cooldown, the next call is allowed
# ("half-open"): success closes the circuit; failure re-opens it.
_CIRCUIT_THRESHOLD = 5            # consecutive failures to trip
_CIRCUIT_WINDOW = 60              # seconds within which threshold counts
_CIRCUIT_COOLDOWN = 120           # seconds the circuit stays open
import time as _time

_circuit_failures: list[float] = []  # timestamps of recent failures
_circuit_open_until: float = 0.0     # epoch seconds; 0 = closed


def _circuit_is_open() -> bool:
    return _time.time() < _circuit_open_until


def _circuit_record_failure() -> None:
    now = _time.time()
    _circuit_failures.append(now)
    # Keep only failures within the window
    cutoff = now - _CIRCUIT_WINDOW
    while _circuit_failures and _circuit_failures[0] < cutoff:
        _circuit_failures.pop(0)
    if len(_circuit_failures) >= _CIRCUIT_THRESHOLD:
        global _circuit_open_until
        _circuit_open_until = now + _CIRCUIT_COOLDOWN
        logger.error(
            "Anthropic circuit OPEN — %d failures in %ds, cooling down for %ds",
            len(_circuit_failures), _CIRCUIT_WINDOW, _CIRCUIT_COOLDOWN,
        )
        _circuit_failures.clear()


def _circuit_record_success() -> None:
    """Successful call closes the circuit and resets the failure counter."""
    global _circuit_open_until
    _circuit_open_until = 0.0
    _circuit_failures.clear()


# Internal-directive keywords that signal complex reasoning and benefit from Opus.
# Anything not on this list (and not explicitly forced via complexity="fast"|"complex")
# uses the default CLAUDE_MODEL (Sonnet).
#
# NOTE: check_followups was deliberately removed. It is a routine 4×/day state scan
# with tiny output (~115 tokens) but ran on Opus and drove ~70% of total LLM spend.
# Sonnet handles it at ~1/5 the cost with no quality loss, and two of its three
# checks are already covered by cheaper jobs (_post_meeting_followup_sweep and the
# nightly Haiku _proactive_dependency_check). Don't re-add without a cost review.
# NOTE: the /plan caller now passes complexity="default" to OVERRIDE this and run
# executive_plan on Sonnet (principal's cost choice) — so despite being listed here,
# executive_plan effectively routes to Sonnet in production. risk_analysis is unused
# by any directive (vestigial). This keyword path only fires if a caller omits complexity.
_COMPLEX_DIRECTIVE_KEYWORDS = ("executive_plan", "risk_analysis")


_MAX_HISTORY_TOKENS_APPROX = 2000  # ~4 chars/token heuristic

# Output token ceiling. A single message may ask to create many tasks at once
# (e.g. a pasted list of 14). At ~120 tokens per task object, the old 1500 cap
# truncated the JSON mid-array — the response failed to parse and the user saw
# "Texnik xato". 8000 fits ~40 task objects with headroom and is within both
# Sonnet 4.6 and Opus 4.8 default output limits. Cost is unaffected (billing is
# per token GENERATED, not per ceiling), so a short reply still bills tiny.
_MAX_OUTPUT_TOKENS = 8000


def _budget_history(history: list[dict], max_tokens: int = _MAX_HISTORY_TOKENS_APPROX) -> list[dict]:
    """Trim conversation history to fit within an approximate token budget.
    Walks newest-first so the most-recent context is preserved when older
    turns get dropped. Uses a coarse `len(content) // 4` estimate — not
    perfect but enough to prevent surprise context-window overruns when the
    user pastes long meeting notes."""
    if not history:
        return history
    out: list[dict] = []
    total = 0
    for msg in reversed(history):
        content = msg.get("content") or ""
        approx = max(1, len(content) // 4)
        if total + approx > max_tokens and out:
            break
        out.insert(0, msg)
        total += approx
    return out


def _pick_model(complexity: Optional[str], internal_directive: Optional[str]) -> str:
    """Route to the cheapest model that can do the job well.

    Explicit `complexity` (fast/default/complex) wins. Otherwise auto-detect
    from internal_directive keywords. Defaults to CLAUDE_MODEL (Sonnet) so
    behaviour is unchanged for the common case."""
    if complexity == "fast":
        return config.CLAUDE_MODEL_FAST
    if complexity == "complex":
        return config.CLAUDE_MODEL_COMPLEX
    if complexity == "default":
        return config.CLAUDE_MODEL
    if internal_directive and any(k in internal_directive for k in _COMPLEX_DIRECTIVE_KEYWORDS):
        return config.CLAUDE_MODEL_COMPLEX
    return config.CLAUDE_MODEL


# The numbered task list the principal last saw (position → id/title). Lets the LLM
# resolve "10-vazifani tahrirla" — referencing a task by its displayed list number.
# In-memory + single-process (single principal); transient by design.
_last_task_view: list = []


def set_last_task_view(items: list) -> None:
    """Remember the numbered task list just shown, so a later 'N-vazifa' reference
    resolves to the right task. Called by the handler that renders the list."""
    global _last_task_view
    _last_task_view = (items or [])[:25]


# Same idea for meetings: when a meeting card/list is shown, remember it so a
# follow-up like "uchrashuv sarlavhasini o'zgartir" / "shu uchrashuvni ko'chir"
# resolves to that meeting's id instead of the bot asking "which meeting?".
_last_meeting_view: list = []


def set_last_meeting_view(items: list) -> None:
    """Remember the meeting(s) just shown (a single opened card or a numbered list)
    so a later reference ("shu uchrashuv", "N-uchrashuv") resolves to the right id."""
    global _last_meeting_view
    _last_meeting_view = (items or [])[:25]


# Same for notes and reminders, so "shu qaydni vazifa qil" / "shu eslatmani o'chir"
# after opening that card resolves to the right id (whole-system consistency).
_last_note_view: list = []
_last_reminder_view: list = []


def set_last_note_view(items: list) -> None:
    """Remember the note(s) just shown so "shu qayd" / "N-qayd" resolves by id."""
    global _last_note_view
    _last_note_view = (items or [])[:25]


def set_last_reminder_view(items: list) -> None:
    """Remember the reminder(s) just shown so "shu eslatma" / "N-eslatma" resolves."""
    global _last_reminder_view
    _last_reminder_view = (items or [])[:25]


async def _build_state_block() -> str:
    """Dynamic, DB-backed snapshot appended after the cached system prompt.

    Covers ALL four sections (tasks, meetings, reminders, notes) so Claude can
    answer questions about them from REAL data instead of guessing. It is a
    capped snapshot, not the full database — when the principal wants a complete
    list, Claude must emit a show_* action (see output contract) rather than
    enumerate from here. Claude must NEVER invent items beyond what's listed."""
    now = datetime.now(database.TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    active_tasks = await database.list_tasks(status_in=["todo", "in_progress", "blocked"], limit=200)
    overdue = await database.list_overdue_tasks()
    today_tasks = await database.list_today_tasks()

    # Drop any "last shown task" whose id was deleted since it was displayed — else the
    # LLM would be told to update/complete a dead id (a wasted, failing action).
    global _last_task_view
    if _last_task_view:
        _alive = await database.filter_existing_task_ids([it.get("id") for it in _last_task_view])
        _last_task_view = [it for it in _last_task_view if it.get("id") in _alive]
    done_today = await database.list_tasks_done_today()
    today_meetings = await database.list_today_meetings()
    # Upcoming meetings (next 30 days, not just this week) — so a reschedule/rename
    # of a meeting more than a week out still has its id in CURRENT STATE and the
    # model emits update_meeting{id} instead of a duplicate schedule_meeting.
    week_meetings = [
        m for m in await database.list_meetings_in_window(
            today_start.isoformat(), (today_start + timedelta(days=30)).isoformat())
        if not m.get("completed_at")
    ]
    reminders = await database.list_reminders(status_in=["scheduled"], limit=20)
    notes_inbox = await database.count_notes_in_status("inbox")
    recent_notes = await database.list_notes(status="inbox", limit=8)
    contacts = await database.list_contacts()
    corrections = await database.list_recent_corrections(limit=5)
    # Existing categories shown explicitly so Claude REUSES them on create_task and
    # never invents new ones (auto-category sprawl). The app enforces this too.
    _categories = await database.list_categories()
    _cat_names = [c["name"] for c in _categories if c.get("name") and c["name"] != "(boshqa)"]

    def task_line(t: dict) -> str:
        deadline = t.get("deadline") or "no deadline"
        # assignee drives the /plan YUK BALANSI (load/bottleneck) view; "—" = the
        # principal's own task. Never omit it — the planner counts per-owner load.
        assignee = (t.get("assignee") or "").strip() or "—"
        # category shown so Claude REUSES existing category names (consistency)
        # instead of inventing near-duplicates on each new task.
        category = (t.get("category") or "").strip() or "—"
        return (f"  - [{t['priority']}] {t['title']} "
                f"(deadline: {deadline}, status: {t['status']}, assignee: {assignee}, "
                f"category: {category}, id: {t['id']})")

    def meeting_line(m: dict) -> str:
        return f"  - {m['datetime_start']} — {m['title']} (participants: {', '.join(m.get('participants', []))}, id: {m['id']})"

    def reminder_line(r: dict) -> str:
        return f"  - {r.get('remind_at')} — {r.get('title')} (id: {r.get('id')})"

    def note_line(n: dict) -> str:
        return f"  - {n.get('title') or (n.get('content') or '')[:50]} (id: {n.get('id')})"

    def contact_line(c: dict) -> str:
        return f"  - {c['name']} (role: {c.get('role') or 'unknown'}, formality: {c.get('formality_level', 3)})"

    def correction_line(c: dict) -> str:
        return f"  - {c.get('correction', '')[:120]} (reason: {c.get('reason', '')[:80]})"

    blocked = sum(1 for t in active_tasks if t.get("status") == "blocked")

    # Per-assignee load — PRECOMPUTED so /plan's YUK BALANSI uses exact counts
    # instead of the model re-counting task lines (which slips, e.g. 7 vs 8) and
    # undermines a feature whose whole point is "who is overloaded". "—" = the
    # principal's own / unassigned tasks. "soon" = P0 OR due by end of tomorrow.
    soon_cutoff = today_start + timedelta(days=2)
    load: dict[str, dict] = {}
    for t in active_tasks:
        who = (t.get("assignee") or "").strip() or "—"
        entry = load.setdefault(who, {"total": 0, "soon": 0})
        entry["total"] += 1
        is_soon = t.get("priority") == "P0"
        dl = t.get("deadline")
        if dl and not is_soon:
            try:
                is_soon = datetime.fromisoformat(dl) < soon_cutoff
            except (ValueError, TypeError):
                pass
        if is_soon:
            entry["soon"] += 1

    def load_line(item) -> str:
        who, e = item
        return f"  - {who}: {e['total']} active ({e['soon']} urgent/soon)"

    lines = [
        "# CURRENT PRINCIPAL STATE (real DB snapshot — do NOT invent anything beyond this)",
        "",
        f"current_datetime: {now.isoformat()}",
        f"current_weekday: {now.strftime('%A')}",
        "",
        "## COUNTS",
        f"tasks: active={len(active_tasks)}, overdue={len(overdue)}, due_today={len(today_tasks)}, "
        f"blocked={blocked}, done_today={len(done_today)}",
        f"meetings: today={len(today_meetings)}, this_week={len(week_meetings)}",
        f"reminders_scheduled: {len(reminders)}",
        f"notes_inbox: {notes_inbox}",
        "",
        f"## LOAD BY ASSIGNEE ({len(load)} owners — EXACT counts for /plan YUK BALANSI; '—' = principal's own)",
        *(load_line(it) for it in sorted(load.items(), key=lambda kv: -kv[1]["total"])),
        "  (none)" if not load else "",
        f"## ACTIVE TASKS ({len(active_tasks)} total, showing up to 25)",
        *(task_line(t) for t in active_tasks[:25]),
        "  (none)" if not active_tasks else "",
        f"## KATEGORIYALAR ({len(_cat_names)}) — create_task'da FAQAT shulardan birini tanlang; "
        "mos kelmasa category QO'YMANG (yangi kategoriya O'YLAB TOPMANG)",
        ("  " + " · ".join(_cat_names)) if _cat_names else "  (hali kategoriya yo'q)",
        "",
        *(["## OXIRGI KO'RSATILGAN RO'YXAT (raqam → vazifa) — \"N-vazifa\" / \"N-chi\" / "
           "\"o'ninchi vazifa\" yoki ro'yxatdan keyin oddiy \"N\" SHU raqamni bildiradi "
           "(\"N ta vazifa\" = miqdor, boshqacha):",
           *(f"  {it['n']}. «{it['title']}» (id: {it['id']})" for it in _last_task_view), ""]
          if _last_task_view else []),
        *(["## OXIRGI KO'RSATILGAN UCHRASHUV(LAR) (raqam → id) — \"shu uchrashuv\", "
           "\"uchrashuvni/sarlavhasini o'zgartir\", \"N-uchrashuv\" SHU id'ni bildiradi; "
           "bitta bo'lsa \"uchrashuv\" = o'sha. update_meeting{id} ishlat, qayta so'rama:",
           *(f"  {it['n']}. «{it['title']}» (id: {it['id']})" for it in _last_meeting_view), ""]
          if _last_meeting_view else []),
        *(["## OXIRGI KO'RSATILGAN QAYD(LAR) (raqam → id) — \"shu qayd\" / \"N-qayd\" SHU id; "
           "bitta bo'lsa \"qayd\" = o'sha:",
           *(f"  {it['n']}. «{it['title']}» (id: {it['id']})" for it in _last_note_view), ""]
          if _last_note_view else []),
        *(["## OXIRGI KO'RSATILGAN ESLATMA(LAR) (raqam → id) — \"shu eslatma\" / \"N-eslatma\" SHU id; "
           "bitta bo'lsa \"eslatma\" = o'sha:",
           *(f"  {it['n']}. «{it['title']}» (id: {it['id']})" for it in _last_reminder_view), ""]
          if _last_reminder_view else []),
        f"## OVERDUE TASKS ({len(overdue)})",
        *(task_line(t) for t in overdue[:10]),
        "  (none)" if not overdue else "",
        f"## MEETINGS — upcoming, next 30 days ({len(week_meetings)})",
        *(meeting_line(m) for m in week_meetings[:20]),
        "  (none)" if not week_meetings else "",
        f"## SCHEDULED REMINDERS ({len(reminders)})",
        *(reminder_line(r) for r in reminders[:12]),
        "  (none)" if not reminders else "",
        f"## NOTES INBOX ({notes_inbox} unprocessed, showing up to 8)",
        *(note_line(n) for n in recent_notes[:8]),
        "  (none)" if not recent_notes else "",
        f"## CONTACTS ({len(contacts)})",
        *(contact_line(c) for c in contacts[:15]),
        "  (none)" if not contacts else "",
        f"## STYLE CORRECTIONS ({len(corrections)})",
        *(correction_line(c) for c in corrections),
        "  (none)" if not corrections else "",
    ]
    return "\n".join(lines)


_USER_MESSAGE_KEY_RE = re.compile(r'"user_message"\s*:\s*"')


def _extract_partial_user_message(buf: str) -> Optional[str]:
    """Best-effort extraction of the `user_message` value from a partial JSON
    buffer produced by Anthropic's streaming response. Returns None if the
    key hasn't appeared yet; otherwise returns whatever text has accumulated
    inside the string so far (possibly empty, possibly mid-word).

    Handles the common JSON escape sequences. If the buffer ends mid-escape
    (last char is '\\\\'), the escape is held back for the next call."""
    m = _USER_MESSAGE_KEY_RE.search(buf)
    if not m:
        return None
    out: list[str] = []
    i = m.end()
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == "\\":
            if i + 1 >= n:
                break  # incomplete escape — wait for more data
            nxt = buf[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
            out.append(mapping.get(nxt, nxt))
            i += 2
            continue
        if c == '"':
            break  # end of user_message string
        out.append(c)
        i += 1
    return "".join(out)


def _repair_json(text: str) -> str:
    """Best-effort repair of the most common LLM JSON flaw: an UNESCAPED double-quote
    inside a string value — e.g. a task title «"Pulli Gap"» makes json.loads raise
    'Expecting , delimiter'. Walking the text, while inside a string a quote that is
    NOT a structural delimiter (the next non-space char is one of , : } ] or EOF) is
    content → escape it. Structural quotes and existing escapes are left intact."""
    out: list = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            i += 1
            continue
        if c == "\\":                         # keep existing escape pairs intact
            out.append(text[i:i + 2])
            i += 2
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j >= n or text[j] in ",:}]":   # structural close
                out.append('"')
                in_str = False
            else:                             # content quote → escape it
                out.append('\\"')
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _extract_json(text: str) -> Optional[dict]:
    """Robust JSON extraction. Handles code fences AND surrounding/trailing prose
    that some models (notably DeepSeek) add around the envelope: strips the first
    fenced block, tries the whole string, then scans each '{' with raw_decode to
    find the first decodable object, and finally repairs unescaped inner quotes."""
    text = (text or "").strip()
    # First fenced block (non-anchored — a model may append prose AFTER the ``` fence).
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence and fence.group(1).strip():
        text = fence.group(1).strip()
    # Whole string is clean JSON?
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # Scan each '{' and try to decode an object starting there — tolerates leading
    # and trailing prose and stray braces before the real envelope.
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    # Last resort: widest span + unescaped-inner-quote repair.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:                                       # repair unescaped inner quotes, retry once
        return json.loads(_repair_json(candidate))
    except json.JSONDecodeError:
        logger.warning("JSON parse failed even after repair: %s", candidate[:300])
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


def _classify_anthropic_error(e: Exception) -> Tuple[str, str]:
    """Map an Anthropic SDK exception to (audit_label, O'zbek user message).
    Mirrors the inline handling in process_message; used by process_document so
    the document path reports the same root causes (rate limit, credit, etc.)."""
    if isinstance(e, RateLimitError):
        return "rate_limit", "Juda ko'p so'rov. Bir-ikki daqiqadan keyin qayta urinib ko'ring."
    if isinstance(e, AuthenticationError):
        return "auth", "Claude kalitida muammo. Administrator bilan bog'laning."
    if isinstance(e, BadRequestError):
        msg = str(e).lower()
        if "credit" in msg or "balance" in msg or "billing" in msg:
            return ("credit_low",
                    "Claude balansi tugadi. Hisobni to'ldiring: "
                    "https://console.anthropic.com/settings/billing")
        return "bad_request", "Texnik xato (bad request). Iltimos, qaytadan urinib ko'ring."
    if isinstance(e, APITimeoutError):
        return "timeout", "Javob kech keldi. Qaytadan urinib ko'ring."
    if isinstance(e, APIConnectionError):
        return "connection", "Tarmoqqa ulanib bo'lmadi. Bir ozdan keyin qaytadan urinib ko'ring."
    if isinstance(e, APIStatusError):
        code = getattr(e, "status_code", None)
        if code == 529:
            return f"status_{code}", "Claude vaqtincha band. Bir ozdan keyin qaytadan urinib ko'ring."
        return f"status_{code}", "Texnik xato yuz berdi. Iltimos, qaytadan urinib ko'ring."
    return "api_error", "Texnik xato yuz berdi. Iltimos, qaytadan urinib ko'ring."


def _envelope_from_raw(raw: str) -> Optional[dict]:
    """Turn Claude's raw output into a response envelope.

    Normally Claude returns the JSON envelope. But for trivial conversational
    inputs ("Salom", "ha", "rahmat") it sometimes replies in PLAIN TEXT. Rather
    than surfacing a "Texnik xato", treat such a reply as a normal user_message
    so the user just sees Claude's answer. Returns None only when the output is
    genuinely unusable (empty, or a broken JSON attempt) — the caller then uses
    _FALLBACK_RESPONSE.
    """
    parsed = _extract_json(raw)
    if parsed:
        return parsed
    text = (raw or "").strip()
    if text:
        # Not a JSON attempt → chatty prose ("Salom", "ha"), show it verbatim. Detect
        # by the FIRST non-fence char being '{' rather than "no braces at all" — a
        # conversational reply may contain a stray brace (code snippet, ":-}") yet
        # still be prose. Genuine-but-broken JSON (starts with '{') falls through.
        probe = text.lstrip("`json \t\r\n")
        if not probe.startswith("{"):
            return _fallback(text)
    return None


# ──────────────────────── DEEPSEEK (gibrid matn-provayderi) ────────────────────────
# Matnli chaqiruvlar (chat, reja, matn/xlsx'dan vazifa ajratish) arzon DeepSeek'ga
# (OpenAI-mos API) yo'naltiriladi. Vision (rasm/skaner-PDF) HAR DOIM Claude'da —
# bu yo'lga faqat matn keladi. openai voice_service uchun allaqachon o'rnatilgan.
# XAVFSIZ: LLM_TEXT_PROVIDER != "deepseek" YOKI kalit yo'q → hammasi Claude'da qoladi.

_ds_client = None


def _use_deepseek_text() -> bool:
    return config.LLM_TEXT_PROVIDER == "deepseek" and bool(config.DEEPSEEK_API_KEY)


def _deepseek():
    global _ds_client
    if _ds_client is None:
        from openai import AsyncOpenAI
        _ds_client = AsyncOpenAI(api_key=config.DEEPSEEK_API_KEY,
                                 base_url=config.DEEPSEEK_BASE_URL,
                                 timeout=60.0, max_retries=1)
    return _ds_client


def _deepseek_model(complexity: Optional[str], internal_directive: Optional[str]) -> str:
    """Claude model-routerining (_pick_model) DeepSeek ekvivalenti. Aniq `complexity`
    HAR DOIM g'olib — shu sabab /plan (complexity='default' + executive_plan direktivi
    bilan ataylab ARZON modelni majburlaydi) reasoner'ga tushmaydi, xuddi Anthropic
    yo'lidagidek. Faqat complexity berilmaganda kalit-so'zlar reasoner'ni tanlaydi."""
    if complexity == "complex":
        return config.DEEPSEEK_MODEL_COMPLEX
    if complexity in ("fast", "default"):
        return config.DEEPSEEK_MODEL
    if internal_directive and any(k in internal_directive for k in _COMPLEX_DIRECTIVE_KEYWORDS):
        return config.DEEPSEEK_MODEL_COMPLEX
    return config.DEEPSEEK_MODEL


def _blocks_need_vision(content_blocks: list) -> bool:
    """content_blocks ichida rasm yoki PDF-hujjat bormi? Bo'lsa — Claude (vision) shart."""
    return any(isinstance(b, dict) and b.get("type") in ("image", "document")
               for b in (content_blocks or []))


# DeepSeek Claude kabi tool-envelope'ga har doim amal qilmaydi — ba'zan action'ni
# tashlab, faqat user_message'da "qo'shildi" deб yozadi (→ DB'ga hech narsa yozilmaydi,
# false-success). Quyidagi kuchaytirish (qat'iy qoida + few-shot) uni action chiqarishga
# majburlaydi. FAQAT DeepSeek yo'liga qo'shiladi — Claude system-prompt'iga tegmaydi.
_DEEPSEEK_REINFORCE = (
    "━━━ MUHIM — ACTION MAJBURIY (DeepSeek) ━━━\n"
    "Agar foydalanuvchi biror narsa YARATISH / BELGILASH / O'ZGARTIRISH / O'CHIRISH / "
    "KO'RSATISH so'rasa (vazifa, uchrashuv, eslatma, qayd, kontakt, kategoriya, sozlama, "
    "eksport, reja...), mos action `actions` massivIDA BO'LISHI SHART. Hech qachon "
    "`user_message`da \"qo'shildi / belgilandi / bajarildi\" deб yozib, action'ni TASHLAB "
    "KETMA — action bo'lmasa DB'ga HECH NARSA yozilmaydi va foydalanuvchi aldangan bo'ladi. "
    "\"Bajardim\" turidagi HAR BIR javob uchun mos action majburiy. Faqat sof "
    "ma'lumot/hisob/tarjima so'ralganda `actions: []` (intent \"none\") bo'ladi.\n\n"
    "Namuna 1 — vazifa:\n"
    "INPUT: «Ertaga soat 10 da Dilnozaga banner tayyorlashni topshir, muhim»\n"
    "OUTPUT: {\"intent\":\"A\",\"actions\":[{\"type\":\"create_task\",\"data\":{\"title\":"
    "\"Banner tayyorlash\",\"priority\":\"P1\",\"deadline\":\"<ERTA>T10:00:00+05:00\","
    "\"assignee\":\"Dilnoza\"}}],\"user_message\":\"✓ **Vazifa qo'shildi**\\n🗂 Banner "
    "tayyorlash\\n👤 Dilnoza · ⏳ ertaga 10:00 · 🔺 P1\",\"needs_clarification\":false}\n\n"
    "Namuna 2 — uchrashuv:\n"
    "INPUT: «Ertaga soat 15 da Abror aka bilan uchrashuv belgila»\n"
    "OUTPUT: {\"intent\":\"A\",\"actions\":[{\"type\":\"schedule_meeting\",\"data\":{\"title\":"
    "\"Abror aka bilan uchrashuv\",\"datetime_start\":\"<ERTA>T15:00:00+05:00\","
    "\"datetime_end\":\"<ERTA>T16:00:00+05:00\",\"participants\":[\"Abror\"]}}],"
    "\"user_message\":\"✓ **Uchrashuv belgilandi**\\n🤝 Abror aka bilan uchrashuv\\n📅 ertaga "
    "15:00\",\"needs_clarification\":false}\n\n"
    "<ERTA> — HOLAT blokidagi \"bugun\" asosida haqiqiy ISO sanaga almashtiring. Chiqish — "
    "faqat bitta JSON obyekt, kod-panjara (```) va prose'siz."
)


def _ds_system(state_block: str) -> str:
    """DeepSeek uchun to'liq system matni: SYSTEM_PROMPT + holat-bloki + action-kuchaytirish."""
    return config.SYSTEM_PROMPT + "\n\n" + state_block + "\n\n" + _DEEPSEEK_REINFORCE


def _oai_messages(state_block: str, messages: list) -> list:
    """Anthropic-uslub (system massiv + content) → OpenAI xabarlari. SYSTEM_PROMPT +
    holat-bloki + DeepSeek action-kuchaytirishi bitta system xabariga; qolganlar matnga."""
    def _flat(c):
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n\n".join(b.get("text", "") for b in c
                               if isinstance(b, dict) and b.get("type") == "text")
        return str(c or "")
    out = [{"role": "system", "content": _ds_system(state_block)}]
    out += [{"role": m["role"], "content": _flat(m.get("content"))} for m in messages]
    return out


def _finalize_parsed(raw: str) -> Optional[dict]:
    parsed = _envelope_from_raw(raw)
    if not parsed:
        return None
    parsed.setdefault("intent", "none")
    parsed.setdefault("actions", [])
    parsed.setdefault("user_message", "")
    parsed.setdefault("buttons", [])
    parsed.setdefault("needs_clarification", False)
    parsed.setdefault("clarification_question", None)
    return parsed


class _DSError(Exception):
    def __init__(self, label: str, msg: str):
        super().__init__(label)
        self.label, self.msg = label, msg


def _classify_openai_error(e) -> Tuple[str, str]:
    import openai
    if isinstance(e, openai.RateLimitError):
        return "rate_limit", "Juda ko'p so'rov. Bir-ikki daqiqadan keyin qayta urinib ko'ring."
    if isinstance(e, openai.AuthenticationError):
        return "auth", "DeepSeek kalitida muammo. Administrator bilan bog'laning."
    if isinstance(e, openai.APITimeoutError):
        return "timeout", "Javob kech keldi. Qaytadan urinib ko'ring."
    if isinstance(e, openai.APIConnectionError):
        return "connection", "Tarmoqqa ulanib bo'lmadi. Bir ozdan keyin qaytadan urinib ko'ring."
    code = getattr(e, "status_code", None)
    if code == 402:
        return "credit_low", "DeepSeek balansi tugadi. Hisobni to'ldiring: https://platform.deepseek.com"
    if code == 429:
        return "rate_limit", "Juda ko'p so'rov. Bir-ikki daqiqadan keyin qayta urinib ko'ring."
    return "api_error", "Texnik xato yuz berdi. Iltimos, qaytadan urinib ko'ring."


async def _deepseek_call(model: str, oai_msgs: list, timeout: float = 60.0,
                         max_tokens: int = _MAX_OUTPUT_TOKENS):
    """Bitta DeepSeek chat.completions → (raw_text, (in_tok, out_tok)). Xato → _DSError."""
    import openai
    try:
        resp = await _deepseek().with_options(timeout=timeout).chat.completions.create(
            model=model, max_tokens=max_tokens, messages=oai_msgs)
    except openai.OpenAIError as e:
        raise _DSError(*_classify_openai_error(e))
    text = (resp.choices[0].message.content or "") if resp.choices else ""
    u = getattr(resp, "usage", None)
    return text, (getattr(u, "prompt_tokens", 0) if u else 0,
                  getattr(u, "completion_tokens", 0) if u else 0)


async def _ds_log(model, purpose, input_hash, input_chars, in_tok, out_tok, redacted_count, error=None):
    if error:
        await database.log_llm_call("deepseek", model, purpose, input_hash, input_chars,
                                    None, None, redacted_terms_count=redacted_count, error=error)
    else:
        cost = redaction.estimate_cost(model, in_tok, out_tok, 0, 0)
        await database.log_llm_call("deepseek", model, purpose, input_hash, input_chars,
                                    in_tok, out_tok, redacted_terms_count=redacted_count,
                                    estimated_cost_usd=cost)


async def _ds_process_message(user_text, internal_directive, complexity):
    model = _deepseek_model(complexity, internal_directive)
    state_block = await _build_state_block()
    if internal_directive:
        messages = []
        outgoing_content, redacted_count = redaction.redact(internal_directive)
        purpose = "internal:" + internal_directive[:40]
    else:
        history = _budget_history(await database.recent_messages(limit=10))
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in history if (m.get("content") or "").strip()]
        outgoing_content, redacted_count = redaction.redact(user_text)
        purpose = "user_message"
    messages.append({"role": "user", "content": outgoing_content})
    input_hash = redaction.hash_input(outgoing_content)
    input_chars = len(outgoing_content)
    try:
        raw, (in_tok, out_tok) = await _deepseek_call(model, _oai_messages(state_block, messages))
    except _DSError as e:
        await _ds_log(model, purpose, input_hash, input_chars, 0, 0, redacted_count, error=e.label)
        return _fallback(e.msg)
    await _ds_log(model, purpose, input_hash, input_chars, in_tok, out_tok, redacted_count)
    parsed = _finalize_parsed(raw)
    if not parsed:
        logger.error("DeepSeek returned unusable output: %s", (raw or "")[:500])
        return _FALLBACK_RESPONSE
    if not internal_directive:
        await database.append_message("user", user_text)
        await database.append_message("assistant", parsed.get("user_message") or "✅")
        await database.trim_history(keep=30)
    return parsed


async def _ds_process_document(instruction, content_blocks, complexity, file_label):
    model = _deepseek_model(complexity, None)
    state_block = await _build_state_block()
    instruction_red, redacted_count = redaction.redact(instruction or "")
    # Faqat matnli bloklar (rasm/PDF _blocks_need_vision tomonidan bloklangan).
    doc_text = "\n\n".join(b.get("text", "") for b in content_blocks
                           if isinstance(b, dict) and b.get("type") == "text")
    user_content = (doc_text + "\n\n" + instruction_red).strip()
    oai_msgs = [{"role": "system", "content": _ds_system(state_block)},
                {"role": "user", "content": user_content}]
    input_hash = redaction.hash_input(instruction_red)
    input_chars = len(user_content)
    purpose = "document"
    try:
        raw, (in_tok, out_tok) = await _deepseek_call(model, oai_msgs, timeout=140.0)
    except _DSError as e:
        await _ds_log(model, purpose, input_hash, input_chars, 0, 0, redacted_count, error=e.label)
        return _fallback(e.msg)
    await _ds_log(model, purpose, input_hash, input_chars, in_tok, out_tok, redacted_count)
    parsed = _finalize_parsed(raw)
    if not parsed:
        logger.error("DeepSeek (document) returned unusable output: %s", (raw or "")[:500])
        return _FALLBACK_RESPONSE
    try:
        marker = f"[Hujjat yuborildi: {file_label or 'fayl'}] {(instruction or '').strip()[:200]}"
        await database.append_message("user", marker.strip())
        await database.append_message("assistant", parsed.get("user_message") or "✅")
        await database.trim_history(keep=30)
    except Exception:
        logger.debug("Could not persist document turn to history")
    return parsed


async def _ds_process_message_stream(user_text, complexity, internal_directive):
    import openai
    model = _deepseek_model(complexity, internal_directive)
    state_block = await _build_state_block()
    if internal_directive:
        messages = []
        outgoing_content, redacted_count = redaction.redact(internal_directive)
        purpose = "internal_stream:" + internal_directive[:40]
    else:
        history = _budget_history(await database.recent_messages(limit=10))
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in history if (m.get("content") or "").strip()]
        outgoing_content, redacted_count = redaction.redact(user_text)
        purpose = "user_message_stream"
    messages.append({"role": "user", "content": outgoing_content})
    input_hash = redaction.hash_input(outgoing_content)
    input_chars = len(outgoing_content)
    buf, last_emitted, in_tok, out_tok = "", "", 0, 0
    try:
        stream = await _deepseek().chat.completions.create(
            model=model, max_tokens=_MAX_OUTPUT_TOKENS,
            messages=_oai_messages(state_block, messages),
            stream=True, stream_options={"include_usage": True})
        async for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u:
                in_tok = getattr(u, "prompt_tokens", 0) or 0
                out_tok = getattr(u, "completion_tokens", 0) or 0
            if chunk.choices:
                piece = getattr(chunk.choices[0].delta, "content", None)
                if piece:
                    buf += piece
                    partial = _extract_partial_user_message(buf)
                    if partial is not None and partial != last_emitted:
                        last_emitted = partial
                        yield ("partial", partial)
    except openai.OpenAIError as e:
        label, _ = _classify_openai_error(e)
        logger.warning("Streaming DeepSeek failed (%s) — falling back to non-streaming", label)
        yield ("complete", await _ds_process_message(user_text, internal_directive, complexity))
        return
    await _ds_log(model, purpose, input_hash, input_chars, in_tok, out_tok, redacted_count)
    parsed = _finalize_parsed(buf)
    if not parsed:
        logger.error("DeepSeek (stream) returned unusable output: %s", (buf or "")[:500])
        yield ("complete", _FALLBACK_RESPONSE)
        return
    if not internal_directive:
        await database.append_message("user", user_text)
        await database.append_message("assistant", parsed.get("user_message") or "✅")
        await database.trim_history(keep=30)
    yield ("complete", parsed)


async def process_message(
    user_text: str,
    internal_directive: Optional[str] = None,
    complexity: Optional[str] = None,
) -> dict:
    """Send user input to Claude, return parsed JSON envelope.

    internal_directive: when invoked by scheduler (briefings, follow-ups) instead of by the user,
                        pass a system-style directive like "[INTERNAL] generate_morning_briefing".
    complexity: optional override for the model router — "fast" (Haiku),
                "default" (Sonnet), or "complex" (Opus). When omitted, the
                router auto-picks based on internal_directive keywords.
    """

    # Gibrid: matnli chaqiruvlar DeepSeek'ga (kalit sozlangan bo'lsa).
    if _use_deepseek_text():
        return await _ds_process_message(user_text, internal_directive, complexity)

    if _circuit_is_open():
        logger.warning("Anthropic circuit open — short-circuiting (cooldown remaining: %.0fs)",
                        _circuit_open_until - _time.time())
        return _fallback(
            "Claude vaqtinchalik mavjud emas. Bir necha daqiqadan keyin qayta urinib ko'ring. "
            "Bot boshqa funksiyalari (ro'yxatlar, qidiruv) ishlayapti."
        )

    model = _pick_model(complexity, internal_directive)
    state_block = await _build_state_block()

    # Conversation history is included ONLY for the interactive user path.
    # Internal directives (briefings, summaries, proactive checks) must rely
    # SOLELY on the structured state block — otherwise Claude treats things the
    # user merely MENTIONED in chat (but never saved) as real tasks/meetings and
    # fabricates them into briefings (reported bug: phantom "overdue tasks").
    if internal_directive:
        messages = []
    else:
        history = await database.recent_messages(limit=10)
        history = _budget_history(history)
        # Drop any empty-content rows — the Anthropic API rejects empty messages
        # ("messages.N: ... must have non-empty content"), which would 400 the
        # whole call. Defense-in-depth alongside not persisting internal directives.
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in history if (m.get("content") or "").strip()]

    # Redact PII for BOTH user messages and internal directives. Internal directives
    # are generated server-side but may interpolate state (task titles, meeting
    # agendas) that could contain redactable values (card numbers, IBANs) the
    # principal entered earlier.
    if internal_directive:
        outgoing_content, redacted_count = redaction.redact(internal_directive)
        purpose = "internal:" + internal_directive[:40]
    else:
        outgoing_content, redacted_count = redaction.redact(user_text)
        purpose = "user_message"
    messages.append({"role": "user", "content": outgoing_content})

    input_hash = redaction.hash_input(outgoing_content)
    input_chars = len(outgoing_content)

    try:
        max_tokens = _MAX_OUTPUT_TOKENS
        response = await _client.messages.create(
            model=model,
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
        logger.warning("Anthropic rate limited (model=%s)", model)
        await database.log_llm_call("anthropic", model, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="rate_limit")
        _circuit_record_failure()
        return _fallback("Juda ko'p so'rov. Bir-ikki daqiqadan keyin qayta urinib ko'ring.")
    except AuthenticationError:
        logger.exception("Anthropic auth failed — check ANTHROPIC_API_KEY")
        await database.log_llm_call("anthropic", model, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="auth")
        _circuit_record_failure()
        return _fallback("Claude kalit kalitida muammo. Administrator bilan bog'laning.")
    except BadRequestError as e:
        msg = str(e).lower()
        err_label = "credit_low" if ("credit" in msg or "balance" in msg or "billing" in msg) else "bad_request"
        await database.log_llm_call("anthropic", model, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error=err_label)
        # credit_low is "permanent" until billing is fixed — keep circuit closed
        # so we don't waste calls on a known-broken state during the cooldown.
        _circuit_record_failure()
        if err_label == "credit_low":
            return _fallback("Claude balansi tugadi. Hisobni to'ldiring: https://console.anthropic.com/settings/billing")
        logger.exception("Anthropic bad request")
        return _fallback("Texnik xato (bad request). Iltimos, qaytadan urinib ko'ring.")
    except APITimeoutError:
        logger.warning("Anthropic timeout (model=%s)", model)
        await database.log_llm_call("anthropic", model, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="timeout")
        _circuit_record_failure()
        return _fallback("Javob kech keldi. Qaytadan urinib ko'ring.")
    except APIConnectionError:
        logger.warning("Anthropic connection error")
        await database.log_llm_call("anthropic", model, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="connection")
        _circuit_record_failure()
        return _fallback("Tarmoqqa ulanib bo'lmadi. Bir ozdan keyin qaytadan urinib ko'ring.")
    except APIStatusError as e:
        await database.log_llm_call("anthropic", model, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error=f"status_{e.status_code}")
        _circuit_record_failure()
        if getattr(e, "status_code", None) == 529:
            return _fallback("Claude vaqtincha band. Bir ozdan keyin qaytadan urinib ko'ring.")
        logger.exception("Anthropic API status error: %s", e.status_code)
        return _FALLBACK_RESPONSE
    except APIError:
        logger.exception("Anthropic API error")
        await database.log_llm_call("anthropic", model, purpose, input_hash, input_chars, None, None, redacted_terms_count=redacted_count, error="api_error")
        _circuit_record_failure()
        return _FALLBACK_RESPONSE

    # Successful call — reset the circuit so any cooldown clears immediately.
    _circuit_record_success()
    raw = response.content[0].text if response.content else ""

    # Audit log — success path. Log the model that was actually used (after
    # router decision), not config.CLAUDE_MODEL, so cost analytics stay accurate.
    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    out_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
    cost = redaction.estimate_cost(model, in_tokens, out_tokens, cache_read, cache_creation)
    await database.log_llm_call(
        "anthropic", model, purpose, input_hash, input_chars,
        in_tokens, out_tokens, cache_read, cache_creation,
        redacted_terms_count=redacted_count, estimated_cost_usd=cost,
    )

    parsed = _envelope_from_raw(raw)
    if not parsed:
        logger.error("Claude returned unusable output: %s", raw[:500])
        return _FALLBACK_RESPONSE

    parsed.setdefault("intent", "none")
    parsed.setdefault("actions", [])
    parsed.setdefault("user_message", "")
    parsed.setdefault("buttons", [])
    parsed.setdefault("needs_clarification", False)
    parsed.setdefault("clarification_question", None)

    if not internal_directive:
        await database.append_message("user", user_text)
        await database.append_message("assistant", parsed.get("user_message") or "✅")
        await database.trim_history(keep=30)

    return parsed


async def process_document(
    instruction: str,
    content_blocks: list,
    complexity: Optional[str] = None,
    file_label: Optional[str] = None,
) -> dict:
    """Analyse an uploaded document/image and return the standard JSON envelope.

    `content_blocks` are pre-built Anthropic content blocks (text/image/document)
    from document_service — prepended to the user turn, followed by `instruction`.
    Because the return shape matches process_message, handlers can route any
    create_task / create_reminder actions through the normal confirm pipeline.

    Not streamed: analysis isn't latency-critical and handlers shows a working
    indicator. A short marker plus the summary are written to conversation history
    so the principal can ask follow-up questions about the document.
    """
    # Gibrid: matnli hujjat (xlsx/csv/docx/txt/matnli PDF) DeepSeek'ga. Rasm/skaner-PDF
    # (vision) — HAR DOIM Claude'da (DeepSeek ko'rmaydi).
    if _use_deepseek_text() and not _blocks_need_vision(content_blocks):
        return await _ds_process_document(instruction, content_blocks, complexity, file_label)

    if _circuit_is_open():
        logger.warning("Anthropic circuit open — short-circuiting document analysis "
                        "(cooldown remaining: %.0fs)", _circuit_open_until - _time.time())
        return _fallback(
            "Claude vaqtinchalik mavjud emas. Bir necha daqiqadan keyin qayta urinib ko'ring. "
            "Bot boshqa funksiyalari (ro'yxatlar, qidiruv) ishlayapti."
        )

    model = _pick_model(complexity, None)
    state_block = await _build_state_block()

    # The instruction is small and server-shaped, but redact it anyway — the
    # principal's caption may quote a card number / IBAN. (Text extracted from
    # the file is already redacted upstream in document_service.)
    instruction_red, redacted_count = redaction.redact(instruction or "")
    content = list(content_blocks) + [{"type": "text", "text": instruction_red}]

    input_hash = redaction.hash_input(instruction_red)
    # Only the textual portion is "chars"; binary image/PDF blocks aren't counted.
    input_chars = sum(len(b.get("text", "")) for b in content if b.get("type") == "text")
    purpose = "document"

    try:
        # Hujjatdan ko'p vazifa ajratish katta chiqish beradi (8000 tokengacha) va
        # 60s standart timeout'dan oshib ketishi mumkin — o'shanda SDK 60s'da uzib
        # 2 marta qayta urinardi (~180s), natijada nginx 504 qaytarardi. Shu chaqiruv
        # uchun bitta uzoqroq (140s) urinish beramiz, retry-ko'paytirishsiz — chegara
        # aniq va nginx proxy_read_timeout ostida qoladi.
        response = await _client.with_options(timeout=140.0, max_retries=0).messages.create(
            model=model,
            max_tokens=_MAX_OUTPUT_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": config.SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": state_block},
            ],
            messages=[{"role": "user", "content": content}],
        )
    except (RateLimitError, AuthenticationError, BadRequestError, APITimeoutError,
            APIConnectionError, APIStatusError, APIError) as e:
        label, msg = _classify_anthropic_error(e)
        if label not in ("rate_limit", "credit_low"):
            logger.exception("Anthropic document call failed (%s, model=%s)", label, model)
        await database.log_llm_call(
            "anthropic", model, purpose, input_hash, input_chars, None, None,
            redacted_terms_count=redacted_count, error=label,
        )
        _circuit_record_failure()
        return _fallback(msg)

    _circuit_record_success()
    raw = response.content[0].text if response.content else ""

    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    out_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
    cost = redaction.estimate_cost(model, in_tokens, out_tokens, cache_read, cache_creation)
    await database.log_llm_call(
        "anthropic", model, purpose, input_hash, input_chars,
        in_tokens, out_tokens, cache_read, cache_creation,
        redacted_terms_count=redacted_count, estimated_cost_usd=cost,
    )

    parsed = _envelope_from_raw(raw)
    if not parsed:
        logger.error("Claude (document) returned unusable output: %s", raw[:500])
        return _FALLBACK_RESPONSE

    parsed.setdefault("intent", "none")
    parsed.setdefault("actions", [])
    parsed.setdefault("user_message", "")
    parsed.setdefault("buttons", [])
    parsed.setdefault("needs_clarification", False)
    parsed.setdefault("clarification_question", None)

    # Persist a SHORT marker (not the file contents) + the summary so the
    # principal can follow up ("3-banddagi muddat qachon?") with context.
    try:
        marker = f"[Hujjat yuborildi: {file_label or 'fayl'}] {(instruction or '').strip()[:200]}"
        await database.append_message("user", marker.strip())
        await database.append_message("assistant", parsed.get("user_message") or "✅")
        await database.trim_history(keep=30)
    except Exception:
        logger.debug("Could not persist document turn to history")

    return parsed


# ──────────────────────── STREAMING (user-facing path only) ────────────────────────


async def process_message_stream(
    user_text: str,
    complexity: Optional[str] = None,
    internal_directive: Optional[str] = None,
) -> AsyncIterator[Tuple[str, object]]:
    """Streaming variant of process_message for the interactive user path.

    Yields tuples (kind, payload):
        ("partial", str)   — current best-effort user_message text. Callers
                              should treat each yield as the FULL accumulated
                              text so far (not a delta) and replace/edit any
                              progress message with it.
        ("complete", dict) — the final, fully-parsed JSON envelope. Same shape
                              as process_message() returns.

    `internal_directive` streams a server-generated directive (e.g. /plan's
    executive_plan) the SAME way — there IS a user waiting, and a long Opus
    plan that appears 33s later with zero feedback reads as "broken". Streaming
    shows the plan building live. As in process_message, an internal directive
    skips conversation history (the directive carries its own context via the
    state block) to avoid hallucinating chat-only items.

    Falls back to the non-streaming path on any unrecoverable error (yields
    a single ("complete", fallback_dict))."""

    # Gibrid: matnli oqim DeepSeek'ga (kalit sozlangan bo'lsa).
    if _use_deepseek_text():
        async for _ev in _ds_process_message_stream(user_text, complexity, internal_directive):
            yield _ev
        return

    model = _pick_model(complexity, internal_directive)
    state_block = await _build_state_block()
    if internal_directive:
        messages = []
        outgoing_content, redacted_count = redaction.redact(internal_directive)
        purpose = "internal_stream:" + internal_directive[:40]
    else:
        history = await database.recent_messages(limit=10)
        history = _budget_history(history)
        # Drop any empty-content rows — the Anthropic API rejects empty messages
        # ("messages.N: ... must have non-empty content"), which would 400 the
        # whole call. Defense-in-depth alongside not persisting internal directives.
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in history if (m.get("content") or "").strip()]
        outgoing_content, redacted_count = redaction.redact(user_text)
        purpose = "user_message_stream"
    messages.append({"role": "user", "content": outgoing_content})

    input_hash = redaction.hash_input(outgoing_content)
    input_chars = len(outgoing_content)

    buf = ""
    last_emitted = ""
    final_usage = None
    try:
        async with _client.messages.stream(
            model=model,
            max_tokens=_MAX_OUTPUT_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": config.SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": state_block},
            ],
            messages=messages,
        ) as stream:
            async for chunk in stream.text_stream:
                if not chunk:
                    continue
                buf += chunk
                partial = _extract_partial_user_message(buf)
                if partial is None or partial == last_emitted:
                    continue
                last_emitted = partial
                yield ("partial", partial)
            final_message = await stream.get_final_message()
            final_usage = getattr(final_message, "usage", None)
    except (RateLimitError, AuthenticationError, BadRequestError,
             APITimeoutError, APIConnectionError, APIStatusError, APIError) as e:
        logger.warning("Streaming Claude failed (%s) — falling back to non-streaming", type(e).__name__)
        fallback = await process_message(user_text, complexity=complexity, internal_directive=internal_directive)
        yield ("complete", fallback)
        return

    raw = buf
    # Audit log mirrors process_message (success path) so cost analytics still work.
    in_tokens = getattr(final_usage, "input_tokens", 0) if final_usage else 0
    out_tokens = getattr(final_usage, "output_tokens", 0) if final_usage else 0
    cache_read = getattr(final_usage, "cache_read_input_tokens", 0) if final_usage else 0
    cache_creation = getattr(final_usage, "cache_creation_input_tokens", 0) if final_usage else 0
    cost = redaction.estimate_cost(model, in_tokens, out_tokens, cache_read, cache_creation)
    await database.log_llm_call(
        "anthropic", model, purpose, input_hash, input_chars,
        in_tokens, out_tokens, cache_read, cache_creation,
        redacted_terms_count=redacted_count, estimated_cost_usd=cost,
    )

    parsed = _envelope_from_raw(raw)
    if not parsed:
        logger.error("Claude (stream) returned unusable output: %s", raw[:500])
        yield ("complete", _FALLBACK_RESPONSE)
        return

    parsed.setdefault("intent", "none")
    parsed.setdefault("actions", [])
    parsed.setdefault("user_message", "")
    parsed.setdefault("buttons", [])
    parsed.setdefault("needs_clarification", False)
    parsed.setdefault("clarification_question", None)

    # Never persist internal directives (plans/briefings) — their user_text is ""
    # which would poison history with an empty message and 400 the next API call.
    if not internal_directive:
        await database.append_message("user", user_text)
        await database.append_message("assistant", parsed.get("user_message") or "✅")
        await database.trim_history(keep=30)

    yield ("complete", parsed)
