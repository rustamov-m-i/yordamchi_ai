"""Phase 4 — Implementation engine (scaffolding) of the self-improvement subsystem.

The SAFE, deterministic machinery for turning an APPROVED proposal into a reviewed
PR: worktree isolation, protected-path enforcement, a test gate, and push / PR
helpers (Gates 3–4). The part that actually WRITES code is an injectable
`Implementer` plugin — the real Claude Agent SDK backend is wired once provisioned;
until then `StubImplementer` makes NO changes, so `prepare` never fakes success.

GUARDRAILS (banking, spec §9):
  • All work happens in a THROWAWAY git worktree (the live tree is never edited).
  • A diff touching ANY protected path is REJECTED (proposal → requires_manual).
  • Every step is audited via database.log_si_audit.
  • Nothing runs autonomously: approval is human (Phase 3) and the live push/merge
    wiring is intentionally OFF in this scaffolding.
"""

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

import database

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))

# Files/dirs the dev_agent must NEVER modify (spec §9 #1). A diff touching any of
# these is rejected and the proposal is flagged for manual implementation.
PROTECTED_PATHS = (
    ".env", ".env.example", "config.py", "redaction.py", "dev_agent.py",
    "deployer.py", "deploy/", ".github/",
)

# The repo verifies via standalone scripts (its convention; the spec says "pytest",
# this project runs tests as scripts — adapted here).
TEST_SUITES = (
    "tests/metrics_check.py", "tests/diagnosis_check.py", "tests/improvements_check.py",
    "tests/integration_check.py", "tests/document_routing_check.py",
    "tests/full_test.py", "tests/qa_regression.py", "tests/tasks_section_smoke.py",
    # The self-improvement subsystem must guard ITSELF: an autonomous change that
    # breaks the dev_agent / deployer / feedback / Gate-2-3 wiring is rejected here.
    "tests/dev_agent_check.py", "tests/deployer_check.py", "tests/feedback_check.py",
    "tests/si_wiring_check.py",
)


def is_protected(path: str) -> bool:
    """True if `path` (repo-relative) is a protected path the agent must not touch."""
    p = path.strip().removeprefix("./")   # exact prefix only — NOT lstrip (which would eat .env's dot)
    for prot in PROTECTED_PATHS:
        if prot.endswith("/"):
            if p == prot.rstrip("/") or p.startswith(prot):
                return True
        elif p == prot:
            return True
    return False


def diff_touches_protected(changed: list) -> list:
    """Subset of changed files that are protected (empty list = safe)."""
    return [f for f in changed if is_protected(f)]


# ── code-writing backend (pluggable) ──────────────────────────────────────────
@dataclass
class ImplementResult:
    ok: bool
    summary: str = ""
    changed_files: list = field(default_factory=list)


class Implementer(Protocol):
    """Pluggable code-writing backend. The real one wraps the Claude Agent SDK (not
    yet provisioned). It must edit files ONLY inside `worktree` and add/extend tests."""
    async def implement(self, proposal: dict, worktree: str) -> ImplementResult: ...


class StubImplementer:
    """Placeholder until the Claude Agent SDK is provisioned: makes NO changes and
    reports not-implemented, so prepare() never produces a phantom 'success'."""
    async def implement(self, proposal: dict, worktree: str) -> ImplementResult:
        return ImplementResult(ok=False, summary="Claude Agent SDK provision qilinmagan — kod yozilmadi.")


class ClaudeAgentImplementer:
    """Real backend — runs the Claude Agent SDK inside the worktree to implement the
    proposal. Requires `pip install claude-agent-sdk` + ANTHROPIC_API_KEY in the env.

    Safety is NOT from the SDK's permission mode (it runs `bypassPermissions` to be
    headless) but from the surrounding machinery: the agent edits ONLY inside the
    throwaway worktree, `.env` is not in that checkout (it cannot read secrets), the
    protected-path guard rejects a diff touching protected files, the test gate must
    pass, and the principal approves the diff at Gate 2 before anything is pushed."""

    async def implement(self, proposal: dict, worktree: str) -> ImplementResult:
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions
        except ImportError:
            return ImplementResult(ok=False, summary="claude-agent-sdk o'rnatilmagan.")
        try:
            import config
            model = getattr(config, "CLAUDE_MODEL_COMPLEX", None)
        except Exception:
            model = None
        task = (
            f"Quyidagi yaxshilanishni amalga oshir: {proposal.get('title', '')}\n"
            f"{proposal.get('proposed_change', '')}\n\n"
            "QAT'IY QOIDALAR: faqat SHU papka ichida tahrir qil. O'zgarishingga MOS "
            "test qo'sh yoki kengaytir. .env, config.py, redaction.py, deployer, CI "
            "fayllariga TEGMA. Kichik, aniq, reviewlanadigan o'zgarish qil."
        )
        kwargs = dict(cwd=worktree, allowed_tools=["Read", "Write", "Edit", "Bash"],
                      permission_mode="bypassPermissions",
                      system_prompt="Sen senior Python muhandisisan. Tasdiq so'ramay, "
                                    "kichik va aniq o'zgarish qil.")
        if model:
            kwargs["model"] = model
        out: list = []
        try:
            async for msg in query(prompt=task, options=ClaudeAgentOptions(**kwargs)):
                out.append(str(msg))
        except Exception as e:
            return ImplementResult(ok=False, summary=f"agent xato: {type(e).__name__}: {e}")
        return ImplementResult(ok=True, summary=("\n".join(out))[-2000:])


def default_implementer():
    """The real Claude Agent SDK backend when it's installed; otherwise the safe
    Stub (which makes no changes) so prepare() never fakes success without the SDK."""
    try:
        import importlib.util
        if importlib.util.find_spec("claude_agent_sdk") is not None:
            return ClaudeAgentImplementer()
    except Exception:
        pass
    return StubImplementer()


# ── git plumbing (module-level so tests can monkeypatch) ───────────────────────
def _git(args: list, cwd: str = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _worktree_path(branch: str) -> str:
    return f"/tmp/yordamchi-wt-{branch.replace('/', '-')}"


async def prepare_worktree(branch: str, base: str = "HEAD") -> str:
    """Create a throwaway git worktree on a new branch off `base`; returns its path.
    The worktree lives under /tmp — never inside the live tree."""
    path = _worktree_path(branch)

    def _mk():
        if os.path.exists(path):
            _git(["worktree", "remove", "--force", path])
        r = _git(["worktree", "add", "-b", branch, path, base])
        if r.returncode != 0:
            raise RuntimeError(f"worktree add failed: {r.stderr.strip()}")
        return path
    return await asyncio.to_thread(_mk)


async def cleanup_worktree(path: str) -> None:
    await asyncio.to_thread(lambda: _git(["worktree", "remove", "--force", path]))


async def changed_files(worktree: str) -> list:
    def _diff():
        r = _git(["status", "--porcelain"], cwd=worktree)
        return [ln[3:].strip() for ln in r.stdout.splitlines() if ln[3:].strip()]
    return await asyncio.to_thread(_diff)


async def get_diff(worktree: str) -> str:
    def _d():
        _git(["add", "-A", "-N"], cwd=worktree)   # surface untracked in the diff
        return _git(["diff"], cwd=worktree).stdout
    return await asyncio.to_thread(_d)


async def run_test_gate(worktree: str, suites: tuple = TEST_SUITES,
                        python: Optional[str] = None) -> tuple:
    """Run the repo's verification suites against the worktree code. Uses the LIVE
    venv python (deps live there; the worktree has no venv) with cwd=worktree so the
    scripts import the worktree's modules. Returns (all_passed, summary)."""
    py = python or os.path.join(ROOT, "venv", "bin", "python")
    if not os.path.exists(py):
        py = "python3"

    def _run():
        lines, all_ok = [], True
        for suite in suites:
            if not os.path.exists(os.path.join(worktree, suite)):
                continue
            try:
                r = subprocess.run([py, suite], cwd=worktree, capture_output=True,
                                   text=True, timeout=300)
                ok = r.returncode == 0
            except Exception as e:
                ok = False
                lines.append(f"❌ {suite} ({type(e).__name__})")
                all_ok = False
                continue
            all_ok = all_ok and ok
            lines.append(f"{'✅' if ok else '❌'} {suite}")
        return all_ok, ("\n".join(lines) or "(no suites found)")
    return await asyncio.to_thread(_run)


# ── Gate-2 preparation orchestration ──────────────────────────────────────────
@dataclass
class PrepareResult:
    ok: bool
    proposal_id: str
    branch: str
    worktree: str = ""
    diff: str = ""
    tests_passed: bool = False
    test_summary: str = ""
    protected_hits: list = field(default_factory=list)
    reason: str = ""


async def prepare(proposal: dict, implementer: Optional[Implementer] = None) -> PrepareResult:
    """Gate-2 prep: worktree → implement → protected-path check → test gate. Returns
    everything the principal needs to review. Fully audited. The worktree is cleaned
    up on any failure; on success it is kept for the subsequent push gate."""
    pid = proposal.get("id", "?")
    branch = f"si/{pid}"
    impl = implementer or default_implementer()
    await database.log_si_audit("prepare_start", pid, f"branch={branch}")
    worktree = await prepare_worktree(branch)
    try:
        res = await impl.implement(proposal, worktree)
        if not res.ok:
            await cleanup_worktree(worktree)
            await database.log_si_audit("prepare_failed", pid, res.summary)
            return PrepareResult(False, pid, branch, reason=res.summary)

        # What did the implementer actually touch? Log the count + a summary tail so
        # an empty/odd run is diagnosable from the audit trail.
        changed = await changed_files(worktree)
        await database.log_si_audit("implement_done", pid,
                                    f"files={len(changed)} :: {(res.summary or '')[:400]}")
        # No file changes → do NOT advance to a phantom 'ready' state (which would
        # push an empty branch). Reject so the principal isn't shown an empty diff.
        if not changed:
            await cleanup_worktree(worktree)
            await database.log_si_audit("prepare_no_changes", pid, (res.summary or "")[:400])
            return PrepareResult(False, pid, branch,
                                 reason="implementer hech qanday fayl o'zgartirmadi (bo'sh diff)")

        hits = diff_touches_protected(changed)
        if hits:
            await cleanup_worktree(worktree)
            await database.update_proposal_status(pid, "requires_manual")
            await database.log_si_audit("prepare_rejected_protected", pid, ",".join(hits))
            return PrepareResult(False, pid, branch, protected_hits=hits,
                                 reason="himoyalangan fayl(lar) tegildi")

        passed, summary = await run_test_gate(worktree)
        diff = await get_diff(worktree)
        if not passed:
            await cleanup_worktree(worktree)
            await database.log_si_audit("prepare_tests_failed", pid, summary[:500])
            return PrepareResult(False, pid, branch, diff=diff, tests_passed=False,
                                 test_summary=summary, reason="testlar yiqildi")

        await database.update_proposal_status(pid, "in_progress")
        await database.log_si_audit("prepare_ok", pid, f"files_ok tests=green branch={branch}")
        return PrepareResult(True, pid, branch, worktree=worktree, diff=diff,
                             tests_passed=True, test_summary=summary)
    except Exception as e:
        await cleanup_worktree(worktree)
        await database.log_si_audit("prepare_error", pid, f"{type(e).__name__}: {e}")
        raise


# ── Gate 3 / 4 (push / PR) — functions exist; live wiring is OFF in scaffolding ──
async def push_branch(branch: str, worktree: str, commit_msg: str,
                      runner: Callable = subprocess.run) -> tuple:
    """Commit the worktree and push the branch to origin (Gate 3). `runner` is
    injectable so tests verify the command sequence without touching the remote."""
    def _push():
        runner(["git", "add", "-A"], cwd=worktree, capture_output=True, text=True)
        # Identity via -c so the commit does NOT depend on a global git user.* being
        # configured on the VM — without it `git commit` fails silently and the pushed
        # branch ends up identical to main (an empty PR: "nothing to compare").
        c = runner(["git", "-c", "user.email=si-bot@yordamchi.local",
                    "-c", "user.name=Yordamchi SI", "commit", "-m", commit_msg],
                   cwd=worktree, capture_output=True, text=True)
        if getattr(c, "returncode", 1) != 0:
            # Non-zero = no identity OR nothing to commit → never push an empty branch.
            return False, ("commit muvaffaqiyatsiz: "
                           + (getattr(c, "stderr", "") or getattr(c, "stdout", "") or "")[:200])
        r = runner(["git", "push", "-u", "origin", branch], cwd=worktree,
                   capture_output=True, text=True)
        ok = getattr(r, "returncode", 0) == 0
        return ok, (getattr(r, "stderr", "") or getattr(r, "stdout", "") or "").strip()
    return await asyncio.to_thread(_push)


async def open_and_merge_pr(branch: str, title: str, body: str = "",
                            runner: Callable = subprocess.run, auto_merge: bool = False) -> tuple:
    """Open a PR for `branch` (Gate 4); optionally merge. `runner` injectable for
    tests. Merge is NEVER automatic without explicit human approval (auto_merge gate)."""
    def _out(r):
        return ((getattr(r, "stdout", "") or "") + "\n" + (getattr(r, "stderr", "") or "")).strip()

    def _pr():
        c = runner([
            "gh", "pr", "create",
            "--base", "main",
            "--head", branch,
            "--title", title,
            "--body", body or title,
        ], cwd=ROOT, capture_output=True, text=True)

        if getattr(c, "returncode", 0) != 0:
            msg = _out(c)
            # If PR already exists, reuse it instead of failing.
            if "already exists" not in msg.lower():
                return False, msg
            v = runner(["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"],
                       cwd=ROOT, capture_output=True, text=True)
            if getattr(v, "returncode", 0) != 0:
                return False, _out(v)
            url = (getattr(v, "stdout", "") or "").strip()
        else:
            url = (getattr(c, "stdout", "") or "").strip()

        if auto_merge:
            # No --delete-branch: this branch is checked out in our /tmp worktree, so
            # gh's local branch delete fails ("Cannot delete branch ... checked out")
            # and wrongly reports the merge as failed. The branch lingers harmlessly.
            m = runner(["gh", "pr", "merge", branch, "--merge"],
                       cwd=ROOT, capture_output=True, text=True)
            if getattr(m, "returncode", 0) != 0:
                return False, _out(m) or url

        return True, url

    return await asyncio.to_thread(_pr)
