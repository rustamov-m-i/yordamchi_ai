"""Phase 1 — Perception (self-improvement subsystem).

Read-only aggregation of the bot's own telemetry into queryable improvement
signals. NO Telegram, NO Claude, NO writes — pure reads via database.py helpers.
Consumed by the Phase-2 nightly self-diagnosis.

Honest limits (verified against the live schema, not assumed):
  • Latency is NOT recorded in llm_audit_log → trends are cost/token based.
  • `intent` is NOT persisted → the 'unmet request' signal uses a rephrase
    heuristic over conversation_history (consecutive, near-in-time, highly
    similar user turns), not a real intent=none count.
  • `corrections` has no `theme` column → themes are derived deterministically
    from the `reason`/`context` text via keyword buckets.
"""

import re
from datetime import datetime

import database

# ── Unmet-request rephrase heuristic ──────────────────────────────────────────
_REPHRASE_MAX_GAP_SEC = 180       # two user turns within 3 minutes …
_REPHRASE_MIN_SIMILARITY = 0.5    # … sharing ≥50% of their words (Jaccard) = a rephrase

# ── Correction theme buckets: (theme, keywords). First match wins; order matters.
_THEME_RULES = [
    ("priority",   ("priorit", "ustuvor", "p0", "p1", "p2", "p3", "shoshilinch", "muhim")),
    ("deadline",   ("deadline", "muddat", "sana", "qachon", "ertaga", "bugun")),
    ("assignee",   ("ijrochi", "assignee", "deleg", "topshir", "mas'ul", "masul")),
    ("tone",       ("ohang", "tone", "rasmiy", "formal", "iliq", "qo'pol", "qopol", "uslub", "style")),
    ("length",     ("qisqa", "uzun", "short", "long", "qisqartir", "kengaytir")),
    ("formatting", ("format", "emoji", "sarlavha", "bo'lim", "bolim", "markdown")),
    ("language",   ("tarjima", "ruscha", "inglizcha", "lotin", "kiril", "language")),
]


def _word_set(text: str) -> set:
    return set(re.findall(r"\w+", (text or "").lower()))


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity over word sets (0..1). Deterministic, language-agnostic."""
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def theme_of(reason: str, context: str = "") -> str:
    """Deterministic theme bucket for a correction, from its `reason` (+ `context`
    fallback). Returns a known theme or 'other'.

    Keywords are matched as a **prefix of a whole word**, not a raw substring — so
    stems like 'priorit'/'deleg' still catch 'prioritetni'/'delegatsiya', but
    'qachon' does NOT mis-match inside 'allaqachon'."""
    words = re.findall(r"\w+", f"{reason or ''} {context or ''}".lower())
    for theme, keywords in _THEME_RULES:
        if any(w.startswith(k) for w in words for k in keywords):
            return theme
    return "other"


def _theme_corrections(items: list[dict]) -> dict:
    """Bucket corrections by derived theme → {theme: {count, sample}}, count-desc."""
    buckets: dict = {}
    for it in items:
        theme = theme_of(it.get("reason", ""), it.get("context", ""))
        b = buckets.setdefault(theme, {"count": 0, "sample": ""})
        b["count"] += 1
        if not b["sample"]:
            b["sample"] = (it.get("reason") or it.get("correction") or "")[:120]
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1]["count"]))


def _parse_ts(s):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def detect_unmet_requests(conversation: list[dict]) -> dict:
    """Rephrase heuristic over chronological conversation turns. A 'rephrase' = two
    consecutive USER turns that are close in time (≤ _REPHRASE_MAX_GAP_SEC) AND
    highly similar (Jaccard ≥ _REPHRASE_MIN_SIMILARITY) — a signal the previous answer
    didn't land. Returns counts + rate + a few samples. Deterministic / testable."""
    users = [t for t in conversation if t.get("role") == "user"]
    rephrases = []
    for prev, cur in zip(users, users[1:]):
        ta, tb = _parse_ts(prev.get("created_at")), _parse_ts(cur.get("created_at"))
        gap = abs((tb - ta).total_seconds()) if (ta and tb) else None
        sim = _similarity(prev.get("content", ""), cur.get("content", ""))
        if sim >= _REPHRASE_MIN_SIMILARITY and (gap is None or gap <= _REPHRASE_MAX_GAP_SEC):
            rephrases.append({
                "prev": (prev.get("content") or "")[:80],
                "repeat": (cur.get("content") or "")[:80],
                "similarity": round(sim, 2),
                "gap_sec": round(gap) if gap is not None else None,
            })
    n_users = len(users)
    return {
        "user_turns": n_users,
        "rephrases": len(rephrases),
        "rephrase_rate": round(len(rephrases) / n_users, 4) if n_users else 0.0,
        "samples": rephrases[:5],
    }


async def collect_signals(days: int = 7) -> dict:
    """Top-level Phase-1 signal bundle. Read-only. The Phase-2 nightly diagnosis
    feeds this (plus recent correction/fallback samples) to Claude.

    `days` is the primary window; corrections and the cost trend use a slightly
    wider window since they're sparser."""
    err = await database.llm_error_breakdown(days)
    corr = await database.correction_frequency(days=max(days, 30))
    trend = await database.cost_trend_by_day(days=max(days, 14))
    convo = await database.recent_conversation(days=days, limit=500)

    return {
        "window_days": days,
        "generated_at": datetime.now(database.TZ).isoformat(),
        "error_rates": {
            "total_calls": err["total_calls"],
            "error_calls": err["error_calls"],
            "error_rate": err["error_rate"],
            "by_family": err["by_family"],
        },
        "fallback_frequency": err["by_label"],   # error labels == fallbacks
        "correction_themes": {
            "total": corr["total"],
            "window_days": corr["window_days"],
            "by_theme": _theme_corrections(corr["items"]),
        },
        "cost_trend": trend["by_day"],
        "unmet_requests": detect_unmet_requests(convo),
    }
