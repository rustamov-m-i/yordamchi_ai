"""Phase 4 (Implementation engine, scaffolding) checks — protected-path guard,
StubImplementer, test gate, push/PR command construction (injected runner → no real
git/GitHub), prepare() orchestration (git helpers monkeypatched), and a real-git
worktree smoke on a THROWAWAY /tmp repo (never the live repo). No LLM, no real push.

Run:  venv/bin/python tests/dev_agent_check.py
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.DATABASE_PATH = "/tmp/yordamchi_devagent_test.db"
config.ICLOUD_ENABLED = False  # tests must NEVER push to the real iCloud calendar
config.APPLE_ID = ""; config.APPLE_APP_SPECIFIC_PASSWORD = ""
if os.path.exists(config.DATABASE_PATH):
    os.remove(config.DATABASE_PATH)

import database     # noqa: E402
import dev_agent    # noqa: E402

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


def test_protected():
    print("\n[ A. protected-path guard (pure) ]")
    for p in (".env", "config.py", "redaction.py", "dev_agent.py", "deployer.py",
              "deploy/yordamchi.service", ".github/workflows/test.yml",
              "requirements.txt", "requirements-dev.txt"):
        check(f"protected: {p}", dev_agent.is_protected(p))
    for p in ("handlers.py", "database.py", "metrics.py", "system_prompts/20_task_capture.md",
              "tests/full_test.py"):
        check(f"NOT protected: {p}", not dev_agent.is_protected(p))
    hits = dev_agent.diff_touches_protected(["handlers.py", ".env", "tests/x.py", "config.py"])
    check("diff_touches_protected → only .env+config.py", set(hits) == {".env", "config.py"}, f"{hits}")


def test_gate_runner():
    print("\n[ B. run_test_gate (real subprocess, throwaway dir) ]")
    d = "/tmp/yd_wtgate"
    os.makedirs(os.path.join(d, "tests"), exist_ok=True)
    open(os.path.join(d, "tests", "pass.py"), "w").write("import sys; sys.exit(0)\n")
    open(os.path.join(d, "tests", "fail.py"), "w").write("import sys; sys.exit(1)\n")
    ok, _ = asyncio.run(dev_agent.run_test_gate(d, suites=("tests/pass.py",), python=sys.executable))
    check("gate: passing suite → True", ok is True)
    ok2, _ = asyncio.run(dev_agent.run_test_gate(d, suites=("tests/fail.py",), python=sys.executable))
    check("gate: failing suite → False", ok2 is False)
    ok3, _ = asyncio.run(dev_agent.run_test_gate(
        d, suites=("tests/pass.py", "tests/fail.py"), python=sys.executable))
    check("gate: any-fail → False", ok3 is False)


def test_push_pr_commands():
    print("\n[ C. push / PR command construction (fake runner — no real remote) ]")
    calls = []

    class R:
        returncode = 0
        stdout = "https://github.com/x/y/pull/1"
        stderr = ""

    def fake(args, **kw):
        calls.append(list(args))
        return R()

    ok, out = asyncio.run(dev_agent.push_branch("si/imp-1", "/tmp/wt", "msg", runner=fake))
    check("push: ok", ok is True)
    check("push: git add", ["git", "add", "-A"] in calls)
    check("push: commit uses -c identity (global git config'ga bog'liq emas)",
          any(a[0] == "git" and "user.email=si-bot@yordamchi.local" in a and "commit" in a
              for a in calls), f"{calls}")
    check("push: git push -u origin branch",
          ["git", "push", "-u", "origin", "si/imp-1"] in calls)

    # commit fails (no identity / nothing to commit) → ok=False AND no push (no empty branch)
    cfail = []

    class RC:
        def __init__(self, rc):
            self.returncode, self.stdout, self.stderr = rc, "", "nothing to commit"

    def fake_commit_fail(args, **kw):
        cfail.append(list(args))
        return RC(1 if "commit" in args else 0)
    okf, _ = asyncio.run(dev_agent.push_branch("si/imp-2", "/tmp/wt", "msg", runner=fake_commit_fail))
    check("push: commit yiqilsa → ok=False", okf is False)
    check("push: commit yiqilsa → push YO'Q (bo'sh branch oldini oladi)",
          not any(a[:2] == ["git", "push"] for a in cfail))

    calls.clear()
    okp, url = asyncio.run(dev_agent.open_and_merge_pr("si/imp-1", "Title", runner=fake, auto_merge=False))
    check("pr: gh pr create chaqirildi", any(a[:3] == ["gh", "pr", "create"] for a in calls))
    check("pr: auto_merge=False → merge YO'Q", not any(a[:3] == ["gh", "pr", "merge"] for a in calls))
    calls.clear()
    okm, _ = asyncio.run(dev_agent.open_and_merge_pr("si/imp-1", "Title", runner=fake, auto_merge=True))
    check("pr: auto_merge=True → merge chaqirildi", any(a[:3] == ["gh", "pr", "merge"] for a in calls))
    check("pr: merge ok → ok=True", okm is True)
    check("pr: --delete-branch ISHLATILMAYDI (worktree checkout konflikti oldini oladi)",
          not any("--delete-branch" in a for a in calls), f"{calls}")


async def _prep_scenarios():
    print("\n[ D. prepare() orkestratsiyasi (git helperlari monkeypatch) ]")
    await database.init()

    cleaned = []

    async def fake_wt(branch, base="HEAD"):
        return "/tmp/fake_wt_" + branch.replace("/", "-")

    async def fake_cleanup(path):
        cleaned.append(path)

    async def fake_diff(wt):
        return "FAKE DIFF"

    dev_agent.prepare_worktree = fake_wt
    dev_agent.cleanup_worktree = fake_cleanup
    dev_agent.get_diff = fake_diff

    class FakeImpl:
        def __init__(self, ok=True):
            self.ok = ok
        async def implement(self, proposal, worktree):
            return dev_agent.ImplementResult(ok=self.ok, summary="done")

    # 1) happy path: safe files + tests green → ok, status in_progress
    async def cf_safe(wt):
        return ["handlers.py", "tests/new_test.py"]

    async def gate_green(wt, suites=None, python=None):
        return True, "✅ all"
    dev_agent.changed_files = cf_safe
    dev_agent.run_test_gate = gate_green
    pid = await database.create_improvement_proposal({"title": "Safe fix", "fix_kind": "code", "status": "approved"})
    r = await dev_agent.prepare({"id": pid}, implementer=FakeImpl(ok=True))
    check("prepare: happy → ok=True", r.ok is True, r.reason)
    check("prepare: status in_progress", (await database.get_improvement_proposal(pid))["status"] == "in_progress")
    check("prepare: diff qaytdi", r.diff == "FAKE DIFF")

    # 2) protected file touched → reject + requires_manual
    async def cf_protected(wt):
        return ["handlers.py", ".env"]
    dev_agent.changed_files = cf_protected
    pid2 = await database.create_improvement_proposal({"title": "Bad", "fix_kind": "code", "status": "approved"})
    r2 = await dev_agent.prepare({"id": pid2}, implementer=FakeImpl(ok=True))
    check("prepare: protected → ok=False", r2.ok is False)
    check("prepare: protected_hits=['.env']", r2.protected_hits == [".env"], f"{r2.protected_hits}")
    check("prepare: status requires_manual",
          (await database.get_improvement_proposal(pid2))["status"] == "requires_manual")

    # 3) tests fail → ok=False
    dev_agent.changed_files = cf_safe

    async def gate_red(wt, suites=None, python=None):
        return False, "❌ tests/x.py"
    dev_agent.run_test_gate = gate_red
    pid3 = await database.create_improvement_proposal({"title": "Breaks tests", "fix_kind": "code", "status": "approved"})
    r3 = await dev_agent.prepare({"id": pid3}, implementer=FakeImpl(ok=True))
    check("prepare: tests fail → ok=False", r3.ok is False and "test" in r3.reason.lower())

    # 4) StubImplementer (no SDK) → ok=False, never fakes success
    r4 = await dev_agent.prepare({"id": "imp-x"}, implementer=dev_agent.StubImplementer())
    check("prepare: stub (no SDK) → ok=False", r4.ok is False and "SDK" in r4.reason)

    # 5) implementer ran but changed NO files → ok=False (no phantom 'ready'/empty branch)
    async def cf_empty(wt):
        return []
    dev_agent.changed_files = cf_empty
    dev_agent.run_test_gate = gate_green
    pid5 = await database.create_improvement_proposal({"title": "No-op", "fix_kind": "code", "status": "approved"})
    r5 = await dev_agent.prepare({"id": pid5}, implementer=FakeImpl(ok=True))
    check("prepare: o'zgarish yo'q → ok=False", r5.ok is False and "o'zgartirmadi" in r5.reason, r5.reason)
    check("prepare: o'zgarish yo'q → status approved (in_progress EMAS)",
          (await database.get_improvement_proposal(pid5))["status"] == "approved")

    # audit trail populated
    audit = await database.list_si_audit(limit=80)
    actions = {a["action"] for a in audit}
    check("audit: prepare_ok yozildi", "prepare_ok" in actions)
    check("audit: prepare_rejected_protected yozildi", "prepare_rejected_protected" in actions)
    check("audit: prepare_failed (stub) yozildi", "prepare_failed" in actions)
    check("audit: implement_done yozildi (fayl soni diagnostikasi)", "implement_done" in actions)
    check("audit: prepare_no_changes yozildi", "prepare_no_changes" in actions)


def test_real_worktree_smoke():
    print("\n[ E. real-git worktree smoke (throwaway /tmp repo, live repo'ga tegmaydi) ]")
    repo = "/tmp/yd_throwaway_repo"
    branch = f"smoke{os.getpid()}"                 # unique → no collision with /tmp leftovers
    subprocess.run(["rm", "-rf", repo])
    subprocess.run("rm -rf /tmp/yordamchi-wt-smoke*", shell=True)
    os.makedirs(repo)
    for args in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                 ["git", "config", "user.name", "t"]):
        subprocess.run(args, cwd=repo)
    open(os.path.join(repo, "a.txt"), "w").write("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo)

    orig_root = dev_agent.ROOT
    dev_agent.ROOT = repo
    try:
        wt = asyncio.run(dev_agent.prepare_worktree(branch))
        check("worktree yaratildi", os.path.isdir(wt))
        open(os.path.join(wt, "b.txt"), "w").write("new\n")
        cf = asyncio.run(dev_agent.changed_files(wt))
        check("changed_files yangi faylni ko'rdi", "b.txt" in cf, f"{cf}")
        asyncio.run(dev_agent.cleanup_worktree(wt))
        check("worktree tozalandi", not os.path.isdir(wt))
    finally:
        dev_agent.ROOT = orig_root
        subprocess.run(["rm", "-rf", repo])
        subprocess.run(["rm", "-rf", dev_agent._worktree_path(branch)])


def main():
    print("=" * 56)
    print("DEV_AGENT (Phase 4 scaffolding) CHECKS")
    print("=" * 56)
    test_protected()
    test_gate_runner()
    test_push_pr_commands()
    test_real_worktree_smoke()          # real git BEFORE _prep_scenarios monkeypatches helpers
    asyncio.run(_prep_scenarios())
    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
