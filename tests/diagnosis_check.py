"""Phase 2 (Diagnosis) checks — diagnosis.py pure functions + improvement_proposals
CRUD + run_and_store end-to-end with a MOCKED Claude (no real LLM call, $0).

Run:  venv/bin/python tests/diagnosis_check.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.DATABASE_PATH = "/tmp/yordamchi_diagnosis_test.db"
if os.path.exists(config.DATABASE_PATH):
    os.remove(config.DATABASE_PATH)

import database      # noqa: E402
import diagnosis     # noqa: E402

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

    print("\n[ A. build_directive (pure) ]")
    d = diagnosis.build_directive({"window_days": 7, "error_rates": {"error_rate": 0.1},
                                   "fallback_frequency": [{"label": "timeout", "calls": 3}]})
    check("directive: self_diagnose", "self_diagnose" in d)
    check("directive: requires_manual qoidasi", "requires_manual" in d)
    check("directive: JSON array talab", "JSON array" in d)
    check("directive: fix_kind sxema", '"fix_kind"' in d)
    check("directive: signal payload bor", "error_rates" in d and "timeout" in d)

    print("\n[ B. parse_proposals (pure) ]")
    p = diagnosis.parse_proposals('[{"title":"Hisobotni P1 qil","fix_kind":"prompt","problem":"x"}]')
    check("parse: 1 proposal", len(p) == 1)
    check("parse: source=auto", p[0]["source"] == "auto")
    check("parse: status=new", p[0]["status"] == "new")
    check("parse: fix_kind=prompt", p[0]["fix_kind"] == "prompt")

    pm = diagnosis.parse_proposals('[{"title":"Multi-user","fix_kind":"feature","requires_manual":true}]')
    check("parse: requires_manual → status", pm[0]["status"] == "requires_manual")

    pc = diagnosis.parse_proposals('[{"title":"Y","fix_kind":"bogus"}]')
    check("parse: noma'lum fix_kind → 'code'", pc[0]["fix_kind"] == "code")

    check("parse: title yo'q → skip", diagnosis.parse_proposals('[{"problem":"no title"}]') == [])
    check("parse: buzuq JSON → []", diagnosis.parse_proposals("salom, bu JSON emas") == [])
    check("parse: bo'sh massiv → []", diagnosis.parse_proposals("[]") == [])
    fenced = diagnosis.parse_proposals('```json\n[{"title":"Z","fix_kind":"code"}]\n```')
    check("parse: code-fence ichidan", len(fenced) == 1 and fenced[0]["title"] == "Z")

    print("\n[ C. improvement_proposals CRUD ]")
    pid = await database.create_improvement_proposal(
        {"title": "Test taklif", "fix_kind": "prompt", "problem": "p", "source": "auto"})
    got = await database.get_improvement_proposal(pid)
    check("CRUD: create+get", got is not None and got["title"] == "Test taklif")
    check("CRUD: default status=new", got["status"] == "new")
    check("CRUD: source=auto", got["source"] == "auto")
    pid2 = await database.create_improvement_proposal(
        {"title": "Bogus kind", "fix_kind": "nonsense", "status": "requires_manual"})
    got2 = await database.get_improvement_proposal(pid2)
    check("CRUD: bad fix_kind → code", got2["fix_kind"] == "code")
    check("CRUD: requires_manual saqlandi", got2["status"] == "requires_manual")
    lst = await database.list_improvement_proposals()
    check("CRUD: list 2 ta", len(lst) == 2)
    new_only = await database.list_improvement_proposals(status_in=["new"])
    check("CRUD: status filtri", len(new_only) == 1 and new_only[0]["id"] == pid)
    check("CRUD: update status ok", await database.update_proposal_status(pid, "approved"))
    check("CRUD: update reflected", (await database.get_improvement_proposal(pid))["status"] == "approved")
    check("CRUD: bad status → False", not await database.update_proposal_status(pid, "bogus"))
    counts = await database.count_proposals_by_status()
    check("CRUD: count_by_status", counts.get("approved") == 1 and counts.get("requires_manual") == 1)

    print("\n[ D. run_and_store (mock Claude — real chaqiruvsiz) ]")
    canned = (
        '[{"title":"Default hisobot → P1","problem":"often re-prioritised","evidence":"11/wk",'
        '"root_cause":"no rule","fix_kind":"prompt","proposed_change":"add a rule","impact_estimate":"~1.5/day"},'
        '{"title":"Multi-user mode","problem":"...","evidence":"...","fix_kind":"feature","requires_manual":true}]'
    )
    seen = {}

    async def fake_process(user_text, internal_directive=None, complexity=None):
        seen["directive"] = internal_directive
        seen["complexity"] = complexity
        return {"user_message": canned, "actions": []}

    ids = await diagnosis.run_and_store(days=7, process_fn=fake_process)
    check("run_and_store: 2 ta saqlandi", len(ids) == 2, f"{ids}")
    check("run_and_store: complex tier", seen.get("complexity") == "complex")
    check("run_and_store: self_diagnose directive", "self_diagnose" in (seen.get("directive") or ""))
    counts2 = await database.count_proposals_by_status()
    check("run_and_store: new +1", counts2.get("new", 0) >= 1)
    check("run_and_store: requires_manual +1", counts2.get("requires_manual", 0) >= 2)  # 1 from CRUD + 1 here

    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
