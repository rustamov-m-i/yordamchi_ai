"""Phase 1 (Perception) checks — metrics.py aggregation math + database.py read
helpers. Throwaway temp DB; no real data touched; no external APIs.

Run:  venv/bin/python tests/metrics_check.py
"""
import asyncio
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.DATABASE_PATH = "/tmp/yordamchi_metrics_test.db"
config.ICLOUD_ENABLED = False  # tests must NEVER push to the real iCloud calendar
config.APPLE_ID = ""; config.APPLE_APP_SPECIFIC_PASSWORD = ""
if os.path.exists(config.DATABASE_PATH):
    os.remove(config.DATABASE_PATH)

import aiosqlite       # noqa: E402
import database        # noqa: E402
import metrics         # noqa: E402

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ❌ {name}   {detail}")


async def _seed():
    """Insert controlled rows (timestamps relative to now → window-robust)."""
    from datetime import datetime
    now = datetime.now(database.TZ)

    def ts(minutes_ago):
        return (now - timedelta(minutes=minutes_ago)).isoformat()

    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        # llm_audit_log: 8 OK user calls + 2 user errors + 1 internal error = 11 rows
        for i in range(8):
            await db.execute(
                "INSERT INTO llm_audit_log (ts, provider, model, purpose, input_tokens, "
                "output_tokens, estimated_cost_usd, error) VALUES (?,?,?,?,?,?,?,NULL)",
                (ts(60 * (i % 2) + i), "anthropic", "claude-sonnet-4-6",
                 "user_message_stream", 100, 50, 0.05),
            )
        for err in ("timeout", "rate_limit"):
            await db.execute(
                "INSERT INTO llm_audit_log (ts, provider, model, purpose, estimated_cost_usd, error) "
                "VALUES (?,?,?,?,?,?)",
                (ts(30), "anthropic", "claude-sonnet-4-6", "user_message_stream", 0.0, err),
            )
        await db.execute(
            "INSERT INTO llm_audit_log (ts, provider, model, purpose, estimated_cost_usd, error) "
            "VALUES (?,?,?,?,?,?)",
            (ts(20), "anthropic", "claude-opus-4-8", "internal:[INTERNAL] self_diagnose", 0.0, "timeout"),
        )

        # corrections: priority x2, deadline x1, tone x1, other x1 = 5
        corr = [
            ("corr-1", "P2 ni P1 ga ko'tar"),
            ("corr-2", "ustuvorlikni oshir"),
            ("corr-3", "muddatni ertaga qil"),
            ("corr-4", "rasmiyroq ohang kerak"),
            ("corr-5", "umuman tushunarsiz javob"),
        ]
        for cid, reason in corr:
            await db.execute(
                "INSERT INTO corrections (id, context, correction, reason, created_at) "
                "VALUES (?,?,?,?,?)",
                (cid, "ctx", "fix", reason, ts(100)),
            )

        # conversation_history: a clear rephrase pair (2 user turns, ~40s apart, ≥0.5 sim)
        await db.execute("INSERT INTO conversation_history (role, content, created_at) VALUES (?,?,?)",
                         ("user", "hisobotni Azizga yubor", ts(10)))
        await db.execute("INSERT INTO conversation_history (role, content, created_at) VALUES (?,?,?)",
                         ("assistant", "ok", (now - timedelta(minutes=10) + timedelta(seconds=5)).isoformat()))
        await db.execute("INSERT INTO conversation_history (role, content, created_at) VALUES (?,?,?)",
                         ("user", "hisobotni Azizga yubor iltimos",
                          (now - timedelta(minutes=10) + timedelta(seconds=40)).isoformat()))
        await db.commit()


async def main():
    await database.init()

    print("\n[ A. Sof funksiyalar — theming ]")
    check("theme_of priority (P1)", metrics.theme_of("P2 ni P1 qil") == "priority")
    check("theme_of priority (ustuvor)", metrics.theme_of("ustuvorlikni oshir") == "priority")
    check("theme_of deadline", metrics.theme_of("muddatni o'zgartir") == "deadline")
    check("theme_of tone", metrics.theme_of("rasmiyroq ohang") == "tone")
    check("theme_of other", metrics.theme_of("allaqachon bor") == "other")

    print("\n[ B. Sof funksiyalar — similarity + unmet ]")
    check("similarity identical = 1.0", metrics._similarity("hisobot tayyorla", "hisobot tayyorla") == 1.0)
    check("similarity disjoint = 0.0", metrics._similarity("aaa", "bbb") == 0.0)
    convo = [
        {"role": "user", "content": "hisobotni Azizga yubor", "created_at": "2026-06-07T10:00:00+05:00"},
        {"role": "assistant", "content": "ok", "created_at": "2026-06-07T10:00:05+05:00"},
        {"role": "user", "content": "hisobotni Azizga yubor iltimos", "created_at": "2026-06-07T10:00:40+05:00"},
    ]
    um = metrics.detect_unmet_requests(convo)
    check("unmet: 2 user turns", um["user_turns"] == 2)
    check("unmet: 1 rephrase topildi", um["rephrases"] == 1, f"{um}")
    convo2 = [
        {"role": "user", "content": "bugun nima ish bor", "created_at": "2026-06-07T10:00:00+05:00"},
        {"role": "user", "content": "ertaga uchrashuv qo'sh", "created_at": "2026-06-07T12:00:00+05:00"},
    ]
    check("unmet: bog'liqmas turlar e'tiborsiz", metrics.detect_unmet_requests(convo2)["rephrases"] == 0)

    print("\n[ C. DB helperlari — seed'langan ]")
    await _seed()
    err = await database.llm_error_breakdown(days=90)
    check("error_breakdown total=11", err["total_calls"] == 11, f"{err['total_calls']}")
    check("error_breakdown error_calls=3", err["error_calls"] == 3, f"{err['error_calls']}")
    check("error_rate = 3/11", err["error_rate"] == round(3 / 11, 4))
    labels = {x["label"]: x["calls"] for x in err["by_label"]}
    check("by_label timeout=2", labels.get("timeout") == 2, f"{labels}")
    check("by_label rate_limit=1", labels.get("rate_limit") == 1)
    check("by_family user errors=2", err["by_family"]["user"]["errors"] == 2, f"{err['by_family']}")
    check("by_family internal errors=1", err["by_family"]["internal"]["errors"] == 1)

    corr = await database.correction_frequency(days=120)
    check("correction_frequency total=5", corr["total"] == 5, f"{corr['total']}")

    trend = await database.cost_trend_by_day(days=90)
    check("cost_trend kunlar bor", len(trend["by_day"]) >= 1)
    check("cost_trend cost > 0", round(sum(d["cost_usd"] for d in trend["by_day"]), 2) > 0)
    check("cost_trend errorlar sanaladi", sum(d["errors"] for d in trend["by_day"]) == 3)

    cv = await database.recent_conversation(days=90)
    check("recent_conversation 3 satr", len(cv) == 3, f"{len(cv)}")
    check("recent_conversation xronologik", [r["created_at"] for r in cv] == sorted(r["created_at"] for r in cv))

    print("\n[ D. collect_signals — end-to-end (seed'langan) ]")
    sig = await metrics.collect_signals(days=90)
    check("signals kalitlari",
          {"error_rates", "fallback_frequency", "correction_themes", "cost_trend", "unmet_requests"} <= set(sig))
    check("signals priority theme ≥2",
          sig["correction_themes"]["by_theme"].get("priority", {}).get("count", 0) >= 2)
    check("signals fallback timeout bor",
          any(x["label"] == "timeout" for x in sig["fallback_frequency"]))
    check("signals unmet rephrase ≥1", sig["unmet_requests"]["rephrases"] >= 1, f"{sig['unmet_requests']}")

    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
