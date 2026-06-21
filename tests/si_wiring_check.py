"""Phase 4-5 WIRING checks — the Gate-2/3 chain connecting approve → implement →
deploy → result. FULLY simulated: dev_agent.prepare/push/merge/cleanup, metrics, and
the bot are all faked, so NO real git / gh / Claude SDK / Telegram / VM is touched.
Throwaway temp DB + temp data dir.

Run:  venv/bin/python tests/si_wiring_check.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
_TMP = "/tmp/yd_si_wiring"
os.makedirs(_TMP, exist_ok=True)
config.DATABASE_PATH = os.path.join(_TMP, "yordamchi.db")
config.PRINCIPAL_USER_ID = 424242
if os.path.exists(config.DATABASE_PATH):
    os.remove(config.DATABASE_PATH)

import database     # noqa: E402
import dev_agent    # noqa: E402
import feedback     # noqa: E402
import metrics      # noqa: E402
import handlers     # noqa: E402
import scheduler as scheduler_module  # noqa: E402

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


class FakeBot:
    def __init__(self):
        self.sent = []   # (chat_id, text, kwargs)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text, kw))

    def texts(self):
        return "\n".join(t for _, t, _ in self.sent)


class FakeMsg:
    def __init__(self, text=""):
        self.text = text
        self.answers = []   # (text, reply_markup, parse_mode)

    async def answer(self, t, **k):
        self.answers.append((t, k.get("reply_markup"), k.get("parse_mode")))

    async def edit_text(self, t, **k):
        self.answers.append(("EDIT:" + t, k.get("reply_markup"), k.get("parse_mode")))


class FakeQuery:
    def __init__(self, data, bot=None):
        self.data = data
        self.message = FakeMsg()
        self.bot = bot or FakeBot()
        self.answered = []

    async def answer(self, *a, **k):
        self.answered.append((a, k))


def kb_cbs(markup):
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def _mk(status="approved", title="check_followups Opus'da", fix="code"):
    pid = await database.create_improvement_proposal(
        {"title": title, "fix_kind": fix, "proposed_change": "Sonnet tier", "status": "new"})
    if status != "new":
        await database.update_proposal_status(pid, status)
    return await database.get_improvement_proposal(pid)


def _signal_path():
    return handlers._si_data_path("deploy_request.json")


def _result_path():
    return os.path.join(_TMP, "deploy_result.json")


async def main():
    await database.init()

    print("\n[ A. helperlar ]")
    check("A: signal yo'li data papkada",
          _signal_path() == os.path.join(_TMP, "deploy_request.json"), _signal_path())
    check("A: diffstat sanaydi", "fayl" in handlers._format_diffstat("+++ b/x.py\n+a\n+b\n-c"))
    check("A: bo'sh diff → '—'", handlers._format_diffstat("") == "—")
    cbs = kb_cbs(handlers._si_gate2_keyboard("imp-1"))
    check("A: gate2 kb = deploy+diff+cancel",
          any(c.startswith("sideploy:") for c in cbs) and any(c.startswith("sidiff:") for c in cbs)
          and any(c.startswith("sicancel:") for c in cbs), f"{cbs}")

    print("\n[ B. approve → 🛠 Implement tugmasi (avtomatik emas) ]")
    p = await _mk(status="new")
    pid = p["id"]
    q = FakeQuery(f"impapprove:{pid}")
    await handlers.cb_improvement_approve(q)
    check("B: status=approved", (await database.get_improvement_proposal(pid))["status"] == "approved")
    bcbs = kb_cbs(q.message.answers[-1][1])
    check("B: 🛠 Implement (siimpl) tugmasi bor", any(c.startswith("siimpl:") for c in bcbs), f"{bcbs}")
    check("B: approve hali kod yozmaydi (status approved, in_progress emas)",
          (await database.get_improvement_proposal(pid))["status"] == "approved")

    print("\n[ C. cb_si_implement — status guard + background ishga tushirish ]")
    np = await _mk(status="new")
    qn = FakeQuery(f"siimpl:{np['id']}")
    await handlers.cb_si_implement(qn)
    check("C: approved bo'lmasa alert", bool(qn.answered) and qn.answered[-1][1].get("show_alert"))
    check("C: guard'da background ishga tushmadi (status hali new)",
          (await database.get_improvement_proposal(np["id"]))["status"] == "new")

    rec = []
    orig_impl = handlers._si_run_implementation

    async def fake_impl(bot, proposal):
        rec.append(proposal["id"])
    handlers._si_run_implementation = fake_impl
    try:
        ap = await _mk()
        qa = FakeQuery(f"siimpl:{ap['id']}")
        await handlers.cb_si_implement(qa)
        await asyncio.sleep(0.05)   # let create_task run
    finally:
        handlers._si_run_implementation = orig_impl
    check("C: approved → worker shu proposal bilan chaqirildi", rec == [ap["id"]], f"{rec}")
    check("C: '⏳ boshlandi' edit qilindi",
          any("boshlandi" in a[0] for a in qa.message.answers))

    print("\n[ D. _si_run_implementation — uchta natija ]")
    orig_prep = dev_agent.prepare

    # D1 — success → 'Kod tayyor' + Deploy tugmasi
    okp = await _mk()

    async def prep_ok(proposal, implementer=None):
        await database.update_proposal_status(proposal["id"], "in_progress")
        return dev_agent.PrepareResult(True, proposal["id"], f"si/{proposal['id']}",
                                       worktree="/tmp/wt", diff="+++ b/x.py\n+a\n+b",
                                       tests_passed=True, test_summary="✅ all")
    dev_agent.prepare = prep_ok
    fb = FakeBot()
    try:
        await handlers._si_run_implementation(fb, okp)
    finally:
        dev_agent.prepare = orig_prep
    check("D1: 'Kod tayyor' xabari", "Kod tayyor" in fb.texts())
    check("D1: Deploy (sideploy) tugmasi yuborildi",
          any(c.startswith("sideploy:") for c in kb_cbs(fb.sent[-1][2].get("reply_markup"))))

    # D2 — protected hit → no deploy button
    pp = await _mk()

    async def prep_protected(proposal, implementer=None):
        return dev_agent.PrepareResult(False, proposal["id"], f"si/{proposal['id']}",
                                       protected_hits=[".env"], reason="himoyalangan")
    dev_agent.prepare = prep_protected
    fb2 = FakeBot()
    try:
        await handlers._si_run_implementation(fb2, pp)
    finally:
        dev_agent.prepare = orig_prep
    check("D2: 'Himoyalangan' + .env xabari", "Himoyalangan" in fb2.texts() and ".env" in fb2.texts())
    check("D2: Deploy tugmasi YO'Q",
          not any("sideploy:" in str(s[2].get("reply_markup")) for s in fb2.sent))

    # D3 — tests failed → blocked, no deploy
    tp = await _mk()

    async def prep_tests(proposal, implementer=None):
        return dev_agent.PrepareResult(False, proposal["id"], f"si/{proposal['id']}",
                                       diff="x", tests_passed=False,
                                       test_summary="❌ tests/full_test.py", reason="testlar yiqildi")
    dev_agent.prepare = prep_tests
    fb3 = FakeBot()
    try:
        await handlers._si_run_implementation(fb3, tp)
    finally:
        dev_agent.prepare = orig_prep
    check("D3: 'Testlar yiqildi' + Deploy yo'q", "Testlar yiqildi" in fb3.texts()
          and not any("sideploy:" in str(s[2].get("reply_markup")) for s in fb3.sent))

    print("\n[ E. cb_si_deploy — status guard ]")
    ep = await _mk()   # status 'approved' (not in_progress)
    qe = FakeQuery(f"sideploy:{ep['id']}")
    await handlers.cb_si_deploy(qe)
    check("E: in_progress bo'lmasa alert", bool(qe.answered) and qe.answered[-1][1].get("show_alert"))

    print("\n[ F. _si_run_deploy — happy: signal + merged + baseline ]")
    orig_push, orig_pr = dev_agent.push_branch, dev_agent.open_and_merge_pr
    orig_clean, orig_sig = dev_agent.cleanup_worktree, metrics.collect_signals

    dp = await _mk()
    dpid = dp["id"]
    await database.update_proposal_status(dpid, "in_progress")
    wt = dev_agent._worktree_path(f"si/{dpid}")
    os.makedirs(wt, exist_ok=True)
    if os.path.exists(_signal_path()):
        os.remove(_signal_path())

    pushed, cleaned = [], []

    async def fake_push(branch, worktree, msg, runner=None):
        pushed.append(branch)
        return True, ""

    async def fake_pr(branch, title="", body="", runner=None, auto_merge=False):
        return True, "http://pr/1"

    async def fake_clean(path):
        cleaned.append(path)

    async def fake_signals(days=7):
        return {"error_rates": {"error_rate": 0.1}, "generated_at": "2026-06-07"}

    dev_agent.push_branch = fake_push
    dev_agent.open_and_merge_pr = fake_pr
    dev_agent.cleanup_worktree = fake_clean
    metrics.collect_signals = fake_signals
    fbd = FakeBot()
    try:
        await handlers._si_run_deploy(fbd, await database.get_improvement_proposal(dpid))
    finally:
        dev_agent.push_branch, dev_agent.open_and_merge_pr = orig_push, orig_pr
        dev_agent.cleanup_worktree, metrics.collect_signals = orig_clean, orig_sig

    check("F: signal fayl yozildi", os.path.exists(_signal_path()))
    sig = json.load(open(_signal_path())) if os.path.exists(_signal_path()) else {}
    check("F: signal proposal_id to'g'ri", sig.get("proposal_id") == dpid, f"{sig}")
    check("F: signal target=None (main'ni pull qiladi)", sig.get("target") is None)
    check("F: status=merged", (await database.get_improvement_proposal(dpid))["status"] == "merged")
    check("F: baseline yozildi (Phase 6)", feedback.load_baseline(dpid) is not None)
    check("F: worktree tozalandi", wt in cleaned)
    check("F: 'deploy signali' xabari", "deploy signali" in fbd.texts())

    print("\n[ F2. _si_run_deploy — push fail → signal YO'Q ]")
    dp2 = await _mk()
    dpid2 = dp2["id"]
    await database.update_proposal_status(dpid2, "in_progress")
    os.makedirs(dev_agent._worktree_path(f"si/{dpid2}"), exist_ok=True)
    if os.path.exists(_signal_path()):
        os.remove(_signal_path())

    async def fake_push_fail(branch, worktree, msg, runner=None):
        return False, "remote rejected"
    dev_agent.push_branch = fake_push_fail
    fbf = FakeBot()
    try:
        await handlers._si_run_deploy(fbf, await database.get_improvement_proposal(dpid2))
    finally:
        dev_agent.push_branch = orig_push
    check("F2: push fail → signal yozilmadi", not os.path.exists(_signal_path()))
    check("F2: status merged EMAS",
          (await database.get_improvement_proposal(dpid2))["status"] != "merged")
    check("F2: 'Push muvaffaqiyatsiz' xabari", "Push muvaffaqiyatsiz" in fbf.texts())

    print("\n[ G. cb_si_cancel — cleanup + rejected ]")
    cp = await _mk()
    cpid = cp["id"]
    await database.update_proposal_status(cpid, "in_progress")
    wtc = dev_agent._worktree_path(f"si/{cpid}")
    os.makedirs(wtc, exist_ok=True)
    cln = []

    async def fake_clean2(path):
        cln.append(path)
    dev_agent.cleanup_worktree = fake_clean2
    qc = FakeQuery(f"sicancel:{cpid}")
    try:
        await handlers.cb_si_cancel(qc)
    finally:
        dev_agent.cleanup_worktree = orig_clean
    check("G: cleanup chaqirildi", wtc in cln)
    check("G: status=rejected", (await database.get_improvement_proposal(cpid))["status"] == "rejected")
    check("G: 'Bekor qilindi' edit", any("Bekor qilindi" in a[0] for a in qc.message.answers))

    print("\n[ H. _deploy_result_sweep — natijani o'qib, status + xabar ]")
    # H1 — deployed
    hp = await _mk()
    hpid = hp["id"]
    await database.update_proposal_status(hpid, "merged")
    with open(_result_path(), "w") as f:
        json.dump({"status": "deployed", "proposal_id": hpid, "healthy": True}, f)
    sched = scheduler_module.YordamchiScheduler(FakeBot())
    await sched._deploy_result_sweep()
    check("H1: status=deployed", (await database.get_improvement_proposal(hpid))["status"] == "deployed")
    check("H1: result fayl iste'mol qilindi (o'chdi)", not os.path.exists(_result_path()))
    check("H1: 'muvaffaqiyatli' xabari", "muvaffaqiyatli" in sched.bot.texts())

    # H2 — rolled_back
    rp = await _mk()
    rpid = rp["id"]
    await database.update_proposal_status(rpid, "merged")
    with open(_result_path(), "w") as f:
        json.dump({"status": "rolled_back", "proposal_id": rpid, "healthy": True}, f)
    sched2 = scheduler_module.YordamchiScheduler(FakeBot())
    await sched2._deploy_result_sweep()
    check("H2: status=reverted", (await database.get_improvement_proposal(rpid))["status"] == "reverted")
    check("H2: 'orqaga qaytarildi' xabari", "qaytarildi" in sched2.bot.texts())

    # H3 — no file → no-op (no spurious notification)
    if os.path.exists(_result_path()):
        os.remove(_result_path())
    sched3 = scheduler_module.YordamchiScheduler(FakeBot())
    await sched3._deploy_result_sweep()
    check("H3: fayl yo'q → xabar yo'q", not sched3.bot.sent)

    print("\n[ I. /silog — holat + audit ko'rinishi (read-only) ]")
    check("I: bo'sh → 'hech qanday taklif'", "hech qanday" in handlers._format_silog_text([]))
    props = await database.list_improvement_proposals(limit=20)
    txt = handlers._format_silog_text(props)
    check("I: sarlavha bor", "Self-improvement — holat" in txt)
    check("I: 'Tugaganlar' bo'limi bor", "Tugaganlar" in txt, txt[:200])
    check("I: deployed badge (✅) bor", "✅" in txt)
    check("I: silog matni Markdown-belgisiz (parse-safe)",
          "**" not in txt and "`" not in txt, txt[:120])
    kcbs = kb_cbs(handlers._silog_keyboard(props))
    check("I: siaudit tugmalari bor", any(c.startswith("siaudit:") for c in kcbs), f"{kcbs}")
    check("I: 'silog:refresh' tugmasi bor", "silog:refresh" in kcbs)
    ms = FakeMsg("/silog")
    await handlers.cmd_silog(ms)
    check("I: /silog xabar yubordi", bool(ms.answers))
    # REGRESSIYA: bot default'i ParseMode.MARKDOWN — /silog aniq parse_mode=None
    # bermasa, '_' li status/audit nomlari Markdown deb o'qilib xato beradi.
    check("I: /silog parse_mode=None (default Markdown bekor)",
          bool(ms.answers) and all(a[2] is None for a in ms.answers),
          str([a[2] for a in ms.answers]))
    qr = FakeQuery("silog:refresh")
    await handlers.cb_silog_refresh(qr)
    check("I: refresh javob + edit", bool(qr.answered)
          and any(a[0].startswith("EDIT:") for a in qr.message.answers))
    # hpid (H1) deployed → audit zanjirida 'deploy_succeeded' bo'lishi kerak
    qa2 = FakeQuery(f"siaudit:{hpid}")
    await handlers.cb_si_audit(qa2)
    audit_txt = qa2.message.answers[-1][0] if qa2.message.answers else ""
    check("I: audit ko'rinishi pid + sarlavha", hpid in audit_txt and "audit zanjiri" in audit_txt)
    check("I: audit 'deploy_succeeded' ko'rsatadi", "deploy_succeeded" in audit_txt, audit_txt[:150])
    check("I: audit matni Markdown-belgisiz (parse-safe, '_' li nomlarga qaramay)",
          "**" not in audit_txt and "`" not in audit_txt, audit_txt[:120])
    check("I: audit parse_mode=None (default Markdown bekor)",
          bool(qa2.message.answers) and qa2.message.answers[-1][2] is None)
    qbad = FakeQuery("siaudit:imp-yoqnarsa")
    await handlers.cb_si_audit(qbad)
    check("I: noma'lum pid → alert", bool(qbad.answered) and qbad.answered[-1][1].get("show_alert"))

    print("\n[ J. /deploy — qo'lda supervised deploy (signal yozish) ]")
    md = FakeMsg("/deploy")
    await handlers.cmd_deploy(md)
    dcbs = kb_cbs(md.answers[-1][1]) if md.answers else []
    check("J: /deploy tasdiq tugmalari (mdeploy:yes/no)",
          "mdeploy:yes" in dcbs and "mdeploy:no" in dcbs, f"{dcbs}")
    sig_path = handlers._si_data_path("deploy_request.json")
    if os.path.exists(sig_path):
        os.remove(sig_path)
    qyes = FakeQuery("mdeploy:yes")
    await handlers.cb_manual_deploy(qyes)
    check("J: tasdiqlanganda signal yozildi", os.path.exists(sig_path))
    sig = json.load(open(sig_path)) if os.path.exists(sig_path) else {}
    check("J: signal target=None (main'ni pull qiladi)", sig.get("target") is None, f"{sig}")
    check("J: signal proposal_id=manual", sig.get("proposal_id") == "manual")
    qno = FakeQuery("mdeploy:no")
    await handlers.cb_manual_deploy_cancel(qno)
    check("J: 'Yo'q' → bekor", bool(qno.answered)
          and any("bekor" in str(a[0]).lower() for a in qno.message.answers))

    print("\n[ K. /freeze kill-switch — avtonom loop'ni to'xtatadi (divergensiya) ]")
    await handlers.cmd_freeze(FakeMsg("/freeze"))
    check("K: /freeze → si_frozen=True", (await database.get_settings()).get("si_frozen") is True)
    check("K: _si_is_frozen() True", await handlers._si_is_frozen())
    # frozen → implement bloklanadi (status o'zgarmaydi, real prepare ishga tushmaydi)
    fp = await _mk()
    qfi = FakeQuery(f"siimpl:{fp['id']}")
    await handlers.cb_si_implement(qfi)
    check("K: frozen → implement bloklandi (alert)",
          bool(qfi.answered) and qfi.answered[-1][1].get("show_alert"))
    check("K: frozen → status approved qoldi", (await database.get_improvement_proposal(fp["id"]))["status"] == "approved")
    # frozen → /deploy signal yozilmaydi
    if os.path.exists(_signal_path()):
        os.remove(_signal_path())
    qfd = FakeQuery("mdeploy:yes")
    await handlers.cb_manual_deploy(qfd)
    check("K: frozen → /deploy signal yozilmadi", not os.path.exists(_signal_path()))
    check("K: /silog frozen banner", "MUZLATILGAN" in handlers._format_silog_text([], frozen=True))
    await handlers.cmd_unfreeze(FakeMsg("/unfreeze"))
    check("K: /unfreeze → si_frozen=False", not (await database.get_settings()).get("si_frozen", False))

    print("\n[ L. Deploy signal ATOMIK yoziladi (temp + os.replace) ]")
    import inspect as _insp_l
    handlers._write_deploy_signal({"target": None, "proposal_id": "atomic-test"})
    _sig = _signal_path()
    check("L: signal fayl yozildi", os.path.exists(_sig))
    check("L: kontent to'g'ri", json.load(open(_sig)).get("proposal_id") == "atomic-test")
    check("L: .tmp qoldiq yo'q (atomik almashtirildi)", not os.path.exists(_sig + ".tmp"))
    check("L: _write_deploy_signal os.replace ishlatadi (atomik)",
          "os.replace" in _insp_l.getsource(handlers._write_deploy_signal))
    try:
        os.remove(_sig)
    except OSError:
        pass

    print("\n[ M. /freeze in-flight SI ishni to'xtatadi (2b) ]")
    import asyncio as _aio_m
    import inspect as _insp_m

    async def _never_m():
        await _aio_m.sleep(30)
    await database.set_setting("si_frozen", False)
    _tm = handlers._si_spawn(_never_m())
    check("M: _si_spawn in-flight set'ga qo'shadi", _tm in handlers._si_inflight_tasks)
    await handlers.cmd_freeze(FakeMsg("/freeze"))
    await _aio_m.sleep(0.05)  # let the cancellation propagate
    check("M: /freeze in-flight ishni cancel qildi", _tm.cancelled() or _tm.done())
    await handlers.cmd_unfreeze(FakeMsg("/unfreeze"))
    check("M: _si_run_implementation frozen re-check qiladi",
          "_si_frozen_abort" in _insp_m.getsource(handlers._si_run_implementation))
    check("M: _si_run_deploy push/merge/signal oldidan re-check (≥3)",
          _insp_m.getsource(handlers._si_run_deploy).count("_si_frozen_abort") >= 3)
    _hsrc = open(handlers.__file__, encoding="utf-8").read()
    check("M: SI ishlar _si_spawn bilan (xom create_task emas)",
          "_si_spawn(_si_run_implementation" in _hsrc and "_si_spawn(_si_run_deploy" in _hsrc
          and "asyncio.create_task(_si_run_" not in _hsrc)

    print("\n[ N. SI kunlik xarajat cheklovi / circuit-breaker (2c) ]")
    import config as _cfg_n
    import inspect as _insp_n
    _since0 = "1970-01-01T00:00:00+05:00"
    _before = await database.si_daily_op_count(_since0)
    await database.log_si_audit("implement_started", "n-pid")
    check("N: si_daily_op_count implement_started'ni sanaydi",
          await database.si_daily_op_count(_since0) == _before + 1)
    _notified = []

    async def _fake_notify(t):
        _notified.append(t)
    _orig_cap = _cfg_n.SI_DAILY_OP_CAP
    try:
        _cfg_n.SI_DAILY_OP_CAP = 0
        _over = await handlers._si_budget_exceeded(_fake_notify, "implement_started")
        check("N: limit=0 → budget exceeded (True) + xabar berildi",
              _over is True and len(_notified) == 1)
        _cfg_n.SI_DAILY_OP_CAP = 100000
        _ok = await handlers._si_budget_exceeded(_fake_notify, "implement_started")
        check("N: limit yuqori → davom etadi (False)", _ok is False)
    finally:
        _cfg_n.SI_DAILY_OP_CAP = _orig_cap
    check("N: _self_diagnose budget gate ishlatadi",
          "_si_budget_exceeded" in _insp_n.getsource(scheduler_module.YordamchiScheduler._self_diagnose))
    check("N: _si_run_implementation budget gate ishlatadi",
          "_si_budget_exceeded" in _insp_n.getsource(handlers._si_run_implementation))

    print("\n[ O. Tungi 02:30 dependency-check o'chirilgan ]")
    _sched_src = _insp_n.getsource(scheduler_module)
    check("O: 02:30 dependency-check rejaga QO'YILMAGAN (add_job yo'q)",
          'id="proactive_dependency_check"' not in _sched_src)
    check("O: 02:30 CronTrigger(hour=2, minute=30) yo'q",
          "hour=2, minute=30" not in _sched_src)
    check("O: _proactive_dependency_check metodi saqlangan (keyin qayta yoqsa bo'ladi)",
          hasattr(scheduler_module.YordamchiScheduler, "_proactive_dependency_check"))

    print("\n[ P. Proaktiv digestlar faqat Dushanba–Juma (mon-fri) ]")
    check("P: morning briefing mon-fri (08:00)",
          'day_of_week="mon-fri", hour=8, minute=0' in _sched_src)
    check("P: evening summary mon-fri (18:00)",
          'day_of_week="mon-fri", hour=18, minute=0' in _sched_src)
    check("P: delegation digest mon-fri (09:30)",
          'day_of_week="mon-fri", hour=9, minute=30' in _sched_src)
    check("P: apply_briefing reschedule ham mon-fri (≥5 marta)",
          _sched_src.count('day_of_week="mon-fri"') >= 5)
    check("P: follow-up (interval) dam-olishni o'tkazadi (weekday>=5)",
          "weekday() >= 5" in _sched_src
          and "weekday() >= 5" in _insp_n.getsource(scheduler_module.YordamchiScheduler._followup_check))

    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
