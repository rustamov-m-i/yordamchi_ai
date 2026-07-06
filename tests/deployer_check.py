"""Phase 5 (Supervised deploy + self-heal) checks — the spec's REQUIRED rollback
self-check, fully simulated: injected command runner + fake heartbeat + no-op sleep,
so NO real git / systemctl / VM is touched. Also covers heartbeat.py and the
signal→result flow.

Run:  venv/bin/python tests/deployer_check.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "deploy"))

import config
config.DATABASE_PATH = "/tmp/yd_dep_test/yordamchi.db"
config.ICLOUD_ENABLED = False  # tests must NEVER push to the real iCloud calendar
config.APPLE_ID = ""; config.APPLE_APP_SPECIFIC_PASSWORD = ""
os.makedirs("/tmp/yd_dep_test", exist_ok=True)

import deployer     # noqa: E402  (deploy/deployer.py — standalone, stdlib only)
import heartbeat    # noqa: E402

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


class _R:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


class Fake:
    """Simulated VM. Records commands; models heartbeat liveness.
      heal_after_reset=True  → broken deploy that rollback FIXES (heartbeat goes fresh
                               only after `git reset --hard`).
      never_heal=True        → bot stays dead even after rollback (worst case).
      else                   → healthy deploy (heartbeat fresh from the start)."""
    def __init__(self, heal_after_reset=False, never_heal=False, good="GOODSHA",
                 req_changed=False):
        self.cmds = []
        self.good = good
        self.heal_after_reset = heal_after_reset
        self.never_heal = never_heal
        self.req_changed = req_changed
        self.fresh = not heal_after_reset and not never_heal

    def run(self, args):
        self.cmds.append(list(args))
        if args[:3] == ["git", "diff", "--name-only"]:
            return _R(stdout="handlers.py\nrequirements.txt\n" if self.req_changed else "handlers.py\n")
        if args[:3] == ["git", "reset", "--hard"] and self.heal_after_reset:
            self.fresh = True
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return _R(stdout=self.good)
        if args[:2] == ["systemctl", "is-active"]:
            return _R(stdout="active")
        return _R()

    def heartbeat_age(self):
        return 5.0 if self.fresh else 999.0


def _mk(fake, use_sudo=False):
    return deployer.Deployer(run=fake.run, heartbeat_age=fake.heartbeat_age,
                             sleep=lambda *_: None, health_timeout=4, poll_interval=2,
                             use_sudo=use_sudo)


def main():
    print("=" * 56)
    print("DEPLOYER (Phase 5) — supervised deploy + rollback self-check")
    print("=" * 56)

    print("\n[ A. Sog'lom deploy → 'deployed', rollback YO'Q ]")
    f = Fake()
    res = _mk(f).deploy()
    check("A: status=deployed", res["status"] == "deployed", f"{res}")
    check("A: healthy=True", res["healthy"] is True)
    check("A: git pull qilindi", ["git", "pull", "--ff-only"] in f.cmds)
    check("A: restart qilindi", ["systemctl", "restart", "yordamchi"] in f.cmds)
    check("A: ROLLBACK yo'q (reset --hard yo'q)",
          not any(c[:3] == ["git", "reset", "--hard"] for c in f.cmds))
    check("A: requirements o'zgarmadi → pip install YO'Q",
          not any(len(c) >= 3 and c[1] == "-m" and c[2] == "pip" for c in f.cmds))

    print("\n[ A2. requirements.txt o'zgardi → venv pip install ]")
    fr = Fake(req_changed=True)
    res_r = _mk(fr).deploy()
    check("A2: deploy sog'lom", res_r["status"] == "deployed")
    check("A2: 'pip install -r requirements.txt' qilindi (restartdan oldin)",
          any(c[1:3] == ["-m", "pip"] and "install" in c for c in fr.cmds), f"{fr.cmds}")
    _pip_idx = next((i for i, c in enumerate(fr.cmds) if c[1:3] == ["-m", "pip"]), -1)
    _restart_idx = next((i for i, c in enumerate(fr.cmds) if c == ["systemctl", "restart", "yordamchi"]), -1)
    check("A2: pip restartdan OLDIN", 0 <= _pip_idx < _restart_idx)

    print("\n[ B. Buzuq deploy → AUTO-ROLLBACK (spec acceptance) ]")
    f = Fake(heal_after_reset=True)
    res = _mk(f).deploy()
    check("B: status=rolled_back", res["status"] == "rolled_back", f"{res}")
    check("B: rollbackdan keyin bot tirik", res["healthy"] is True)
    check("B: reset --hard GOODSHA (aynan known-good)",
          ["git", "reset", "--hard", "GOODSHA"] in f.cmds, f"{f.cmds}")
    check("B: restart kamida 2 marta (deploy + rollback)",
          sum(1 for c in f.cmds if c == ["systemctl", "restart", "yordamchi"]) >= 2)

    print("\n[ C. Rollback faqat ORQAGA — known-good'dan boshqa commit'ga emas ]")
    resets = [c for c in f.cmds if c[:3] == ["git", "reset", "--hard"]]
    check("C: barcha reset nishoni == recorded good",
          all(c[3] == "GOODSHA" for c in resets) and len(resets) == 1, f"{resets}")

    print("\n[ D. Rollbackdan keyin ham o'lik (worst case) → halol healthy=False ]")
    f3 = Fake(never_heal=True)
    res3 = _mk(f3).deploy()
    check("D: status=rolled_back", res3["status"] == "rolled_back")
    check("D: healthy=False (halol)", res3["healthy"] is False)

    print("\n[ E. Scoped buyruqlar — faqat ruxsat etilgan git/systemctl ]")
    allowed = {("git", "rev-parse"), ("git", "pull"), ("git", "fetch"),
               ("git", "checkout"), ("git", "reset"), ("git", "diff"),
               ("systemctl", "restart"), ("systemctl", "is-active")}

    def _scoped_ok(c):
        if (c[0], c[1] if len(c) > 1 else "") in allowed:
            return True
        # venv pip install (deps) — scoped to the bot's own venv, no sudo
        return len(c) >= 3 and c[1] == "-m" and c[2] == "pip"
    allcmds = f.cmds + f3.cmds + fr.cmds
    bad = [c for c in allcmds if not _scoped_ok(c)]
    check("E: ruxsatsiz buyruq yo'q (git/systemctl/venv-pip)", not bad, f"{bad}")

    print("\n[ F. target deploy → checkout (pull emas) ]")
    ft = Fake()
    _mk(ft).deploy(target="abc123")
    check("F: git fetch + checkout abc123", ["git", "fetch", "origin"] in ft.cmds
          and ["git", "checkout", "abc123"] in ft.cmds)
    check("F: target bo'lsa 'git pull' YO'Q", ["git", "pull", "--ff-only"] not in ft.cmds)

    print("\n[ F2. use_sudo=True → restart sudo bilan (VM scoped) ]")
    fs = Fake()
    _mk(fs, use_sudo=True).deploy()
    check("F2: 'sudo systemctl restart yordamchi'",
          ["sudo", "systemctl", "restart", "yordamchi"] in fs.cmds, f"{fs.cmds}")

    print("\n[ G. heartbeat.py — yozish/yosh/tiriklik ]")
    heartbeat.write_heartbeat()
    age = heartbeat.heartbeat_age_seconds()
    check("G: heartbeat yozildi + yosh kichik", age is not None and age < 5)
    check("G: is_alive(60) True", heartbeat.is_alive(60))
    check("G: is_alive(0) False (eskirgan deb)", not heartbeat.is_alive(-1))
    check("G: deployer._read_heartbeat_age o'qiydi",
          deployer._read_heartbeat_age(heartbeat.heartbeat_path()) is not None)

    print("\n[ H. signal → result oqimi (real buyruqsiz) ]")
    deployer.SIGNAL_FILE = "/tmp/yd_dep_test/deploy_request.json"
    deployer.RESULT_FILE = "/tmp/yd_dep_test/deploy_result.json"
    json.dump({"target": None, "proposal_id": "imp-7"}, open(deployer.SIGNAL_FILE, "w"))

    class FakeDep:
        def __init__(self, *a, **k):
            pass
        def deploy(self, target=None):
            return {"status": "deployed", "good": "X", "healthy": True}
    orig = deployer.Deployer
    deployer.Deployer = FakeDep
    try:
        out = deployer.run_from_signal()
    finally:
        deployer.Deployer = orig
    check("H: result qaytdi + proposal_id", out and out["proposal_id"] == "imp-7")
    check("H: signal iste'mol qilindi (o'chdi)", not os.path.exists(deployer.SIGNAL_FILE))
    check("H: result fayl yozildi", os.path.exists(deployer.RESULT_FILE))

    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
