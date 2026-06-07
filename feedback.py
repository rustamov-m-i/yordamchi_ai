"""Phase 6 — Feedback (suggest-only) of the self-improvement subsystem.

After a deploy, compare the aggregate Phase-1 health signals (error rate, fallback
count) from a baseline captured at deploy time vs the current window. If they
regressed, create a NEW "Consider reverting #X" proposal — SUGGEST ONLY. It NEVER
reverts anything: the revert is itself a proposal that must pass the human gates.
(The only autonomous action anywhere stays the backward rollback in Phase 5.)

Pure functions (compact_signals / assess_regression / build_revert_proposal) are
unit-tested; run_feedback takes injected before/after so tests need no real metrics.
"""
import json
import logging
import os

import config
import database

logger = logging.getLogger(__name__)

# Regression thresholds — a deploy is flagged if EITHER worsens beyond these.
_ERROR_RATE_DELTA = 0.10     # error_rate rose by ≥ 10 percentage points
_FALLBACK_DELTA = 3          # total fallbacks rose by ≥ 3

_BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(config.DATABASE_PATH)), "si_baselines.json")


def _fallback_total(signals: dict) -> int:
    return sum(x.get("calls", 0) for x in (signals.get("fallback_frequency") or []))


def compact_signals(signals: dict) -> dict:
    """Reduce a full Phase-1 signal bundle to the two metrics feedback compares."""
    return {
        "error_rate": (signals.get("error_rates") or {}).get("error_rate", 0.0),
        "fallbacks": _fallback_total(signals),
    }


def assess_regression(before: dict, after: dict) -> "dict | None":
    """Compare two COMPACT metric snapshots ({error_rate, fallbacks}). Returns a
    verdict dict if `after` regressed vs `before`, else None. Pure / deterministic."""
    if not before or not after:
        return None
    be, ae = before.get("error_rate", 0.0), after.get("error_rate", 0.0)
    bf, af = before.get("fallbacks", 0), after.get("fallbacks", 0)
    reasons = []
    if ae - be >= _ERROR_RATE_DELTA:
        reasons.append(f"xato darajasi {be:.0%} → {ae:.0%}")
    if af - bf >= _FALLBACK_DELTA:
        reasons.append(f"fallback {bf} → {af}")
    if not reasons:
        return None
    return {"regressed": True, "reasons": reasons,
            "before": {"error_rate": be, "fallbacks": bf},
            "after": {"error_rate": ae, "fallbacks": af}}


def build_revert_proposal(orig: dict, verdict: dict) -> dict:
    """Build a suggest-only 'Consider reverting #X' proposal from a regression
    verdict. Pure. fix_kind='config'; status='new' (goes through the same gates)."""
    oid = orig.get("id", "?")
    return {
        "source": "auto",
        "title": f"Reverting'ni ko'rib chiqing: «{orig.get('title', '?')}»"[:200],
        "problem": f"Deploy #{oid} dan keyin ko'rsatkichlar yomonlashdi.",
        "evidence": "; ".join(verdict.get("reasons", [])),
        "root_cause": f"Ehtimol #{oid} o'zgarishi sabab.",
        "fix_kind": "config",
        "proposed_change": f"#{oid} o'zgarishini qaytarishni (revert) ko'rib chiqing.",
        "impact_estimate": "Regressiyani bartaraf etish",
        "status": "new",
    }


def record_baseline(proposal_id: str, signals: dict) -> None:
    """Persist the health baseline captured at deploy time (keyed by proposal)."""
    try:
        data = {}
        if os.path.exists(_BASELINE_PATH):
            with open(_BASELINE_PATH) as f:
                data = json.load(f)
        comp = compact_signals(signals)
        comp["at"] = signals.get("generated_at")
        data[proposal_id] = comp
        os.makedirs(os.path.dirname(_BASELINE_PATH), exist_ok=True)
        with open(_BASELINE_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        logger.exception("record_baseline failed")


def load_baseline(proposal_id: str) -> "dict | None":
    try:
        with open(_BASELINE_PATH) as f:
            return json.load(f).get(proposal_id)
    except Exception:
        return None


async def run_feedback(orig_proposal: dict, before: dict, after: dict) -> "str | None":
    """If `after` (compact) regressed vs `before` (compact), create a SUGGEST-ONLY
    revert proposal and return its id; else None. NEVER reverts anything itself."""
    pid = orig_proposal.get("id", "?")
    verdict = assess_regression(before, after)
    if not verdict:
        await database.log_si_audit("feedback_ok", pid, "no regression")
        return None
    new_pid = await database.create_improvement_proposal(
        build_revert_proposal(orig_proposal, verdict))
    await database.log_si_audit(
        "feedback_regression", pid, f"→ revert proposal {new_pid}: {'; '.join(verdict['reasons'])}")
    logger.info("deploy feedback: regression after #%s → suggested revert %s", pid, new_pid)
    return new_pid
