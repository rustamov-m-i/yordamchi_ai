#!/usr/bin/env python3
"""Yordamchi — supervised deployer (Phase 5).  HIGHEST-STAKES code.

Runs OUTSIDE the bot process (systemd oneshot / watchdog) so it survives the bot
dying — a dead bot cannot roll itself back. It reads a deploy_request signal,
records the known-good commit, pulls + restarts, health-checks (service active AND
a FRESH bot heartbeat), and on failure AUTO-ROLLS-BACK to the known-good commit and
restarts again. Rollback only ever moves BACKWARD to the last good commit.

Standalone: stdlib only, NO bot imports (must work even if the bot's code is broken).
All external commands go through an injectable `run`, and time via injectable
`sleep`/`heartbeat_age`, so the rollback path is fully unit-tested without a real VM.

SCOPE (spec §9 #5): the only commands issued are `git rev-parse/pull/fetch/checkout/
reset/diff` , `systemctl restart/is-active <service>`, and — ONLY when requirements.txt
changed in the pull — `<venv>/bin/python -m pip install -r requirements.txt` (scoped to
the bot's own venv, no sudo). Nothing broader.
"""
import json
import os
import subprocess
import time

REPO = os.environ.get("YORDAMCHI_REPO", "/opt/yordamchi")
SERVICE = os.environ.get("YORDAMCHI_SERVICE", "yordamchi")
HEARTBEAT_FILE = os.path.join(REPO, "data", ".heartbeat")
SIGNAL_FILE = os.path.join(REPO, "data", "deploy_request.json")
RESULT_FILE = os.path.join(REPO, "data", "deploy_result.json")
# Bot's own venv interpreter + requirements — for the post-pull dependency install.
VENV_PY = os.environ.get("YORDAMCHI_VENV_PY", os.path.join(REPO, "venv", "bin", "python"))
REQUIREMENTS = os.path.join(REPO, "requirements.txt")

HEALTH_TIMEOUT = 60       # total seconds to wait for a healthy bot
POLL_INTERVAL = 2         # seconds between health polls
HEARTBEAT_MAX_AGE = 45    # the bot's heartbeat must be newer than this to count as alive
# On the VM the deployer runs as the non-root `yordamchi` user, so `systemctl
# restart` needs sudo (scoped via /etc/sudoers.d). Set YORDAMCHI_SUDO=0 only if the
# deployer itself runs as root. `is-active` needs no privilege.
USE_SUDO = os.environ.get("YORDAMCHI_SUDO", "1") != "0"


def _default_run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True)


def _read_heartbeat_age(path=HEARTBEAT_FILE):
    """Seconds since the bot last wrote its heartbeat, or None. Stdlib only."""
    try:
        with open(path) as f:
            return max(0.0, time.time() - int(f.read().strip()))
    except Exception:
        return None


class Deployer:
    def __init__(self, run=None, heartbeat_age=None, sleep=None,
                 health_timeout=HEALTH_TIMEOUT, poll_interval=POLL_INTERVAL,
                 heartbeat_max_age=HEARTBEAT_MAX_AGE, service=SERVICE, use_sudo=None):
        self.run = run or _default_run
        self.heartbeat_age = heartbeat_age or _read_heartbeat_age
        self.sleep = sleep or time.sleep
        self.health_timeout = health_timeout
        self.poll_interval = poll_interval
        self.heartbeat_max_age = heartbeat_max_age
        self.service = service
        self.use_sudo = USE_SUDO if use_sudo is None else use_sudo
        self.events: list = []

    # ── primitives (scoped) ──
    def record_good(self) -> str:
        r = self.run(["git", "rev-parse", "HEAD"])
        good = (getattr(r, "stdout", "") or "").strip()
        self.events.append(("record_good", good))
        return good

    def _restart(self) -> None:
        cmd = (["sudo"] if self.use_sudo else []) + ["systemctl", "restart", self.service]
        self.run(cmd)
        self.events.append(("restart", self.service))

    def _service_active(self) -> bool:
        r = self.run(["systemctl", "is-active", self.service])
        return (getattr(r, "stdout", "") or "").strip() == "active"

    def _requirements_changed(self, good: str) -> bool:
        """True if requirements.txt differs between `good` and the new HEAD."""
        r = self.run(["git", "diff", "--name-only", good, "HEAD"])
        return "requirements.txt" in (getattr(r, "stdout", "") or "")

    def _pip_install(self) -> tuple:
        """Install deps into the bot's venv (no sudo — the venv is user-owned)."""
        return self._checked([VENV_PY, "-m", "pip", "install", "-r", REQUIREMENTS])

    def healthy(self) -> bool:
        """Service active AND a fresh heartbeat, polled up to health_timeout.
        Attempt-bounded (not wall-clock) so it is deterministic under tests."""
        attempts = max(1, int(self.health_timeout / max(1, self.poll_interval)))
        for _ in range(attempts):
            age = self.heartbeat_age()
            if self._service_active() and age is not None and age <= self.heartbeat_max_age:
                return True
            self.sleep(self.poll_interval)
        return False

    # ── orchestration ──
    def _checked(self, cmd: list) -> tuple:
        r = self.run(cmd)
        ok = getattr(r, "returncode", 0) == 0
        out = ((getattr(r, "stdout", "") or "") + "\n" + (getattr(r, "stderr", "") or "")).strip()
        self.events.append(("cmd", " ".join(cmd), ok, out[-300:]))
        return ok, out

    def deploy(self, target: "str | None" = None) -> dict:
        """Forward-deploy with auto-rollback. Returns a result dict."""
        good = self.record_good()
        if target:
            ok, out = self._checked(["git", "fetch", "origin"])
            if not ok:
                return {"status": "failed", "good": good, "healthy": False, "error": out[-500:]}
            ok, out = self._checked(["git", "checkout", target])
            if not ok:
                return {"status": "failed", "good": good, "healthy": False, "error": out[-500:]}
        else:
            ok, out = self._checked(["git", "pull", "--ff-only"])
            if not ok:
                return {"status": "failed", "good": good, "healthy": False, "error": out[-500:]}

        # Install new deps ONLY when requirements.txt changed in this pull — else a
        # new dependency (e.g. reportlab) is missing and the feature crashes at runtime.
        # On pip failure, roll the code back and restore the old deps before restart.
        deps_changed = self._requirements_changed(good)
        if deps_changed:
            ok, out = self._pip_install()
            if not ok:
                self.events.append(("rollback_start", good))
                self.run(["git", "reset", "--hard", good])
                self._pip_install()  # best-effort restore of the known-good deps
                self._restart()
                return {"status": "rolled_back", "good": good,
                        "healthy": self.healthy(), "error": ("pip install: " + out)[-500:]}

        self._restart()

        if self.healthy():
            self.events.append(("deployed", good))
            return {"status": "deployed", "good": good, "healthy": True}

        # ── ROLLBACK — only ever back to the recorded good commit ──
        self.events.append(("rollback_start", good))
        self.run(["git", "reset", "--hard", good])
        if deps_changed:
            self._pip_install()  # restore the known-good deps alongside the code
        self._restart()
        alive = self.healthy()
        self.events.append(("rolled_back", good))
        return {"status": "rolled_back", "good": good, "healthy": alive}


def run_from_signal() -> "dict | None":
    """Pick up a deploy_request signal (written by the bot on Gate-5/6 approval),
    run the supervised deploy, and write the result. The signal is consumed
    (removed) so a deploy never runs twice off the same request."""
    try:
        with open(SIGNAL_FILE) as f:
            req = json.load(f)
    except Exception:
        return None
    try:
        os.remove(SIGNAL_FILE)
    except OSError:
        pass
    res = Deployer().deploy(req.get("target"))
    res["proposal_id"] = req.get("proposal_id")
    res["finished_at"] = int(time.time())
    try:
        with open(RESULT_FILE, "w") as f:
            json.dump(res, f)
    except Exception:
        pass
    return res


if __name__ == "__main__":
    run_from_signal()
