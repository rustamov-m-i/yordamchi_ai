"""Phase 3 (Proposal & Request Gate) checks — /improve, /improvements, /autopilot
commands + approve/reject/details callbacks + the briefing line. Mock Message /
CallbackQuery (handlers called directly → auth middleware bypassed); no LLM, no real
Telegram. Throwaway temp DB.

Run:  venv/bin/python tests/improvements_check.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.DATABASE_PATH = "/tmp/yordamchi_improve_test.db"
config.ICLOUD_ENABLED = False  # tests must NEVER push to the real iCloud calendar
config.APPLE_ID = ""; config.APPLE_APP_SPECIFIC_PASSWORD = ""
if os.path.exists(config.DATABASE_PATH):
    os.remove(config.DATABASE_PATH)

import database     # noqa: E402
import handlers     # noqa: E402

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


class FakeMsg:
    def __init__(self, text=""):
        self.text = text
        self.answers = []  # (text, reply_markup)

    async def answer(self, t, **k):
        self.answers.append((t, k.get("reply_markup")))

    async def edit_text(self, t, **k):
        self.answers.append(("EDIT:" + t, k.get("reply_markup")))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMsg()
        self.answered = []

    async def answer(self, *a, **k):
        self.answered.append((a, k))


def kb_cbs(markup):
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def main():
    await database.init()

    print("\n[ A. /improve → manual proposal + scope confirm ]")
    m = FakeMsg("/improve eslatmalarga snooze tugmasi qo'sh")
    await handlers.cmd_improve(m)
    manual = await database.list_improvement_proposals(status_in=["new"])
    check("/improve: 1 manual proposal yaratildi", len(manual) == 1, f"{len(manual)}")
    check("/improve: source=manual", manual and manual[0]["source"] == "manual")
    check("/improve: confirm kartochka + tugmalar", bool(m.answers) and m.answers[-1][1] is not None)
    pid_manual = manual[0]["id"]

    print("\n[ B. approve → 'approved', boshqa hech narsa ishga tushmaydi ]")
    q = FakeQuery(f"impapprove:{pid_manual}")
    await handlers.cb_improvement_approve(q)
    p = await database.get_improvement_proposal(pid_manual)
    check("approve: status=approved", p["status"] == "approved", f"{p['status']}")
    check("approve: callback javob berdi", bool(q.answered))
    # "triggers nothing" — Phase 4 yo'q; faqat status o'zgardi (boshqa proposal yaratilmadi)
    check("approve: yangi proposal yaratilmadi", len(await database.list_improvement_proposals()) == 1)

    print("\n[ C. reject → 'rejected' ]")
    rid = await database.create_improvement_proposal({"title": "Reject me", "fix_kind": "code"})
    qr = FakeQuery(f"impreject:{rid}")
    await handlers.cb_improvement_reject(qr)
    check("reject: status=rejected", (await database.get_improvement_proposal(rid))["status"] == "rejected")

    print("\n[ D. requires_manual → Approve tugmasi YO'Q + auto-approve bloklangan ]")
    mid = await database.create_improvement_proposal(
        {"title": "Multi-user", "fix_kind": "feature", "status": "requires_manual"})
    mp = await database.get_improvement_proposal(mid)
    cbs = kb_cbs(handlers._proposal_keyboard(mp))
    check("requires_manual: Approve tugmasi yo'q", not any(c.startswith("impapprove:") for c in cbs), f"{cbs}")
    check("requires_manual: Reject + Details bor",
          any(c.startswith("impreject:") for c in cbs) and any(c.startswith("impdetails:") for c in cbs))
    qm = FakeQuery(f"impapprove:{mid}")
    await handlers.cb_improvement_approve(qm)
    check("requires_manual: approve bloklandi (status o'zgarmadi)",
          (await database.get_improvement_proposal(mid))["status"] == "requires_manual")

    print("\n[ E. /autopilot on|off → setting toggle ]")
    await handlers.cmd_autopilot(FakeMsg("/autopilot on"))
    check("autopilot on → True", (await database.get_settings()).get("autopilot_enabled") is True)
    await handlers.cmd_autopilot(FakeMsg("/autopilot off"))
    check("autopilot off → False", (await database.get_settings()).get("autopilot_enabled") is False)
    mstat = FakeMsg("/autopilot")
    await handlers.cmd_autopilot(mstat)
    check("autopilot (argsiz) → holat ko'rsatadi", any("holati" in (t or "") for t, _ in mstat.answers))

    print("\n[ F. /improvements ro'yxati ]")
    ml = FakeMsg("/improvements")
    await handlers.cmd_improvements(ml)
    # new(0 after approve/reject) + requires_manual(1) → 1 ko'rsatiladi
    check("/improvements: kartochka(lar) chiqdi", len(ml.answers) >= 1)

    print("\n[ G. briefing satri — pending proposal bo'lganda ]")
    await database.create_improvement_proposal({"title": "Pending one", "fix_kind": "prompt"})
    brief = await handlers._build_briefing_text()
    check("briefing: 'yaxshilanish taklifi' satri bor", "yaxshilanish taklifi" in brief)

    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
