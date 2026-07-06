"""Phase 6 (Feedback, suggest-only) checks — compact_signals, assess_regression,
build_revert_proposal, baseline round-trip, and run_feedback (creates a SUGGEST-ONLY
revert proposal on regression; never reverts). Temp DB; no LLM.

Run:  venv/bin/python tests/feedback_check.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.DATABASE_PATH = "/tmp/yd_feedback_test/yordamchi.db"
config.ICLOUD_ENABLED = False  # tests must NEVER push to the real iCloud calendar
config.APPLE_ID = ""; config.APPLE_APP_SPECIFIC_PASSWORD = ""
os.makedirs("/tmp/yd_feedback_test", exist_ok=True)
for _f in ("/tmp/yd_feedback_test/yordamchi.db", "/tmp/yd_feedback_test/si_baselines.json"):
    if os.path.exists(_f):
        os.remove(_f)

import database     # noqa: E402
import feedback     # noqa: E402

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


async def main():
    await database.init()

    print("\n[ A. compact_signals ]")
    full = {"error_rates": {"error_rate": 0.12},
            "fallback_frequency": [{"label": "timeout", "calls": 2}, {"label": "rate_limit", "calls": 1}]}
    c = feedback.compact_signals(full)
    check("compact: error_rate", c["error_rate"] == 0.12)
    check("compact: fallbacks summed", c["fallbacks"] == 3)

    print("\n[ B. assess_regression ]")
    check("regression: error_rate ↑", feedback.assess_regression(
        {"error_rate": 0.05, "fallbacks": 1}, {"error_rate": 0.20, "fallbacks": 1}) is not None)
    check("regression: fallbacks ↑", feedback.assess_regression(
        {"error_rate": 0.05, "fallbacks": 1}, {"error_rate": 0.05, "fallbacks": 5}) is not None)
    check("no regression: stable", feedback.assess_regression(
        {"error_rate": 0.05, "fallbacks": 1}, {"error_rate": 0.06, "fallbacks": 2}) is None)
    check("no regression: improved", feedback.assess_regression(
        {"error_rate": 0.20, "fallbacks": 5}, {"error_rate": 0.05, "fallbacks": 0}) is None)
    check("empty → None", feedback.assess_regression({}, {}) is None)

    print("\n[ C. build_revert_proposal ]")
    v = feedback.assess_regression({"error_rate": 0.05, "fallbacks": 1}, {"error_rate": 0.30, "fallbacks": 1})
    rp = feedback.build_revert_proposal({"id": "imp-9", "title": "Default hisobot → P1"}, v)
    check("revert: source=auto", rp["source"] == "auto")
    check("revert: status=new", rp["status"] == "new")
    check("revert: title 'Reverting'", "Reverting" in rp["title"])
    check("revert: evidence raqamlar bilan", "xato darajasi" in rp["evidence"])
    check("revert: #imp-9 ga ishora", "imp-9" in rp["proposed_change"])

    print("\n[ D. baseline round-trip ]")
    feedback.record_baseline("imp-9", full)
    bl = feedback.load_baseline("imp-9")
    check("baseline saqlandi+o'qildi", bl is not None and bl["error_rate"] == 0.12 and bl["fallbacks"] == 3)
    check("baseline yo'q → None", feedback.load_baseline("imp-yoq") is None)

    print("\n[ E. run_feedback — regressiya → SUGGEST-ONLY revert proposal ]")
    orig = {"id": "imp-9", "title": "Default hisobot → P1"}
    before = {"error_rate": 0.05, "fallbacks": 1}
    after_bad = {"error_rate": 0.40, "fallbacks": 1}
    n0 = len(await database.list_improvement_proposals(limit=100))
    new_pid = await feedback.run_feedback(orig, before, after_bad)
    check("regression: yangi revert pid qaytdi", bool(new_pid))
    props = await database.list_improvement_proposals(limit=100)
    check("regression: aynan +1 proposal (faqat taklif)", len(props) == n0 + 1)
    newp = await database.get_improvement_proposal(new_pid)
    check("regression: status=new (avtomatik bajarilmaydi)", newp["status"] == "new")
    check("regression: 'Reverting' taklifi", "Reverting" in newp["title"])
    audit = await database.list_si_audit(limit=20)
    check("regression: audit 'feedback_regression'", any(a["action"] == "feedback_regression" for a in audit))

    print("\n[ F. run_feedback — regressiyasiz → None, hech narsa yaratilmaydi ]")
    n1 = len(await database.list_improvement_proposals(limit=100))
    none = await feedback.run_feedback(orig, before, {"error_rate": 0.06, "fallbacks": 1})
    check("no regression: None", none is None)
    check("no regression: yangi proposal yo'q", len(await database.list_improvement_proposals(limit=100)) == n1)
    check("no regression: audit 'feedback_ok'",
          any(a["action"] == "feedback_ok" for a in await database.list_si_audit(limit=20)))

    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
