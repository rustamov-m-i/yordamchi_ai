"""Phase 2 — Diagnosis (Channel A) of the self-improvement subsystem.

A nightly job feeds the Phase-1 perception signals to Claude (complex tier) under
an [INTERNAL] self_diagnose directive and stores the returned proposals in the
improvement_proposals table. Large/architectural proposals are stored with
status='requires_manual' — never auto-implemented (spec §3, limit 1).

The pure functions (build_directive / parse_proposals) are unit-tested; run_and_store
takes an injectable `process_fn`, so tests never make a real (paid) LLM call.
"""

import json
import logging
import re
from typing import Callable, Optional

import database

logger = logging.getLogger(__name__)

_MAX_PROPOSALS = 10
_FIX_KINDS = ("prompt", "code", "config", "data", "feature")

_SCHEMA_HINT = (
    'Each proposal object:\n'
    '  {"title": str (≤120 chars, imperative),\n'
    '   "problem": str (what is wrong, 1-2 sentences),\n'
    '   "evidence": str (cite the specific signal/number that justifies it),\n'
    '   "root_cause": str,\n'
    '   "fix_kind": one of ["prompt","code","config","data","feature"],\n'
    '   "proposed_change": str (the smallest concrete change that fixes it),\n'
    '   "impact_estimate": str (expected effect, e.g. "~1.5 fewer corrections/day"),\n'
    '   "requires_manual": bool (true if large/architectural — multi-module, a new\n'
    '                      subsystem, multi-user, or anything needing a written spec)}'
)


def build_directive(signals: dict) -> str:
    """Build the [INTERNAL] self_diagnose prompt from Phase-1 signals. Pure — no I/O."""
    payload = json.dumps(signals, ensure_ascii=False, default=str, indent=2)
    return (
        "[INTERNAL] self_diagnose\n\n"
        "You are auditing your OWN behaviour as the Yordamchi bot from real usage "
        "telemetry. Propose concrete, evidence-backed improvements — mostly small "
        "fixes. Propose a NEW feature only when the data clearly shows a recurring "
        "unmet need. Do NOT invent problems the signals do not support; if nothing "
        "is actionable, return an empty array [].\n\n"
        "Flag any large/architectural item (multi-module, multi-user, a new "
        'subsystem) with "requires_manual": true — it needs a written spec, not '
        "auto-build.\n\n"
        f"{_SCHEMA_HINT}\n\n"
        "OUTPUT CONTRACT OVERRIDE: ignore Telegram formatting. Your `user_message` "
        f"MUST be ONLY a JSON array of 0–{_MAX_PROPOSALS} proposal objects (no prose, "
        "no code fences). Set actions=[].\n\n"
        f"USAGE SIGNALS (last {signals.get('window_days', '?')} days):\n{payload}"
    )


def _extract_json_array(text: str):
    """Best-effort: pull a JSON array out of a possibly fenced / prose-wrapped string."""
    if not text:
        return None
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    try:
        v = json.loads(t)
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        pass
    start, end = t.find("["), t.rfind("]")
    if start != -1 and end > start:
        try:
            v = json.loads(t[start:end + 1])
            return v if isinstance(v, list) else None
        except json.JSONDecodeError:
            return None
    return None


def parse_proposals(user_message: str) -> list[dict]:
    """Parse + normalize the proposals JSON array from the LLM's user_message into
    clean proposal dicts (source=auto; status=new, or requires_manual when flagged).
    Malformed / non-array input → []."""
    arr = _extract_json_array(user_message)
    if not arr:
        return []
    out: list[dict] = []
    for item in arr[:_MAX_PROPOSALS]:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        fix_kind = str(item.get("fix_kind") or "").strip().lower()
        if fix_kind not in _FIX_KINDS:
            fix_kind = "code"
        out.append({
            "source": "auto",
            "title": title[:200],
            "problem": (item.get("problem") or "")[:1000],
            "evidence": (item.get("evidence") or "")[:1000],
            "root_cause": (item.get("root_cause") or "")[:1000],
            "fix_kind": fix_kind,
            "proposed_change": (item.get("proposed_change") or "")[:2000],
            "impact_estimate": (item.get("impact_estimate") or "")[:500],
            "status": "requires_manual" if item.get("requires_manual") else "new",
        })
    return out


async def run_and_store(days: int = 7, process_fn: Optional[Callable] = None) -> list[str]:
    """Collect Phase-1 signals → ask Claude (complex tier) → store proposals.
    Returns the created proposal ids. `process_fn` is injectable for tests; it
    defaults to the real claude_service.process_message."""
    import metrics
    if process_fn is None:
        import claude_service
        process_fn = claude_service.process_message

    signals = await metrics.collect_signals(days=days)
    directive = build_directive(signals)
    resp = await process_fn("", internal_directive=directive, complexity="complex")
    proposals = parse_proposals((resp or {}).get("user_message", ""))

    ids: list[str] = []
    for p in proposals:
        try:
            ids.append(await database.create_improvement_proposal(p))
        except Exception:
            logger.exception("Failed to store improvement proposal")
    logger.info("self_diagnose: %d proposal(s) stored", len(ids))
    return ids
