"""Secret log redaction (redaction.py) — API keys / token never reach the logs,
even inside a provider-error traceback. Pure; no network."""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
config.DATABASE_PATH = "/tmp/yd_logredact_test.db"
config.ICLOUD_ENABLED = False  # tests must NEVER push to the real iCloud calendar
config.APPLE_ID = ""; config.APPLE_APP_SPECIFIC_PASSWORD = ""
import redaction  # noqa: E402

_P = _F = 0
_FAILED: list = []


def check(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print(f"  ✅ {name}")
    else:
        _F += 1
        _FAILED.append(name)
        print(f"  ❌ {name}   {detail}")


def main():
    SECRET = "sk-ant-SUPERSECRET-abcdef1234567890"
    base = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    fmt = redaction._RedactingFormatter(base, [SECRET])

    # 1) message + args
    rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "auth header=%s", (SECRET,), None)
    out = fmt.format(rec)
    check("message+args dagi sir yashiriladi",
          SECRET not in out and redaction._SECRET_MASK in out, out)

    # 2) traceback (the real leak path — provider error exc_info)
    try:
        raise RuntimeError(f"connect failed with token={SECRET}")
    except RuntimeError:
        rec2 = logging.LogRecord("t", logging.ERROR, __file__, 1, "crash", None, sys.exc_info())
    out2 = fmt.format(rec2)
    check("traceback dagi sir yashiriladi", SECRET not in out2, out2[-80:])

    # 3) non-secret text untouched
    rec3 = logging.LogRecord("t", logging.INFO, __file__, 1, "oddiy xabar", None, None)
    check("oddiy matn o'zgarmaydi", "oddiy xabar" in fmt.format(rec3))

    # 4) config secrets auto-discovered (token + API keys, by name)
    secs = redaction._collect_secrets()
    check("_collect_secrets kamida 1 sir topadi", len(secs) >= 1, str(len(secs)))

    # 5) install returns a count + idempotent (no double-wrap)
    n1 = redaction.install_secret_log_redaction()
    n2 = redaction.install_secret_log_redaction()
    check("install >0 va idempotent", n1 > 0 and n1 == n2, f"{n1}/{n2}")

    # 6) bot.py actually wires it at startup
    bsrc = (ROOT / "bot.py").read_text(encoding="utf-8")
    check("bot.py install_secret_log_redaction chaqiradi",
          "install_secret_log_redaction" in bsrc)

    print("\n" + "=" * 50)
    print(f"RESULT: {_P} passed, {_F} failed")
    if _FAILED:
        print("FAILED: " + ", ".join(_FAILED))
    print("=" * 50)
    sys.exit(1 if _F else 0)


if __name__ == "__main__":
    main()
