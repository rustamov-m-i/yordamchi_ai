"""Marketing Hub — Bosqich 2: content_posts → project_items migratsiyasi.

Vaqtinchalik DB'da nazorat qilinadigan ma'lumot bilan: dry-run, haqiqiy migratsiya +
backup, maydon-fidelity (fields JSON), status-mapping, idempotentlik, content_posts
o'zgarmasligini tekshiradi. Ishga tushirish:
    PYTHONPATH=. venv/bin/python tests/marketing_hub_migration_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile

import config

_SCRATCH = "/private/tmp/claude-501/-Users-maqsudrustamov-Documents-Yordamchi-oxirgi/75cf7db8-bb71-4bce-83d1-e0d24afca88b/scratchpad"
_tmp = tempfile.mktemp(suffix=".db", dir=_SCRATCH if os.path.isdir(_SCRATCH) else None)
config.DATABASE_PATH = _tmp

import database   # noqa: E402  (config.DATABASE_PATH o'rnatilgandan keyin)
import migrations  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✅ {name}")
    else:
        _fail += 1
        print(f"  ❌ {name}")


def _con():
    c = sqlite3.connect(_tmp)
    c.row_factory = sqlite3.Row
    return c


_COLS = ("id", "date", "category", "topic", "format", "platform", "message", "hashtags",
         "project_id", "status", "assignee", "published_url", "published_at",
         "reject_reason", "created_at", "updated_at")
_ROWS = [
    ("cp-A", "2026-07-02", "bank", "«Omonat» — yangi reels", "Reels", "Instagram", "Matn A",
     "#omonat", "pr-1", "joylandi", "D. Karimova", "https://t.me/x", "2026-07-02T10:00",
     "", "2026-07-01T09:00", "2026-07-02T10:00"),
    ("cp-B", "2026-07-05", "jamoa", "Ichki e'lon", None, None, "Matn B", None, None,
     "reja", "N. Aliyeva", None, None, None, "2026-07-01T09:00", "2026-07-01T09:00"),
    ("cp-C", "2026-07-08", "bank", "Noto'g'ri status", "Post", "Telegram", "Matn C", None,
     "pr-1", "axlat", "S. Yusupov", None, None, "sabab", "2026-07-01", "2026-07-01"),
]

try:
    asyncio.run(database.init())
    c = _con()
    c.execute("DELETE FROM content_posts")
    c.execute("DELETE FROM project_items")
    c.executemany(
        f"INSERT INTO content_posts ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", _ROWS)
    c.commit()
    c.close()

    print("=== dry-run ===")
    dr = migrations.migrate_content_to_items(_tmp, dry_run=True, backup=False)
    check("dry-run: status=dry_run, to_migrate=3", dr.get("status") == "dry_run" and dr.get("to_migrate") == 3)
    c = _con()
    check("dry-run: project_items hali bo'sh (hech narsa yozilmadi)",
          c.execute("SELECT COUNT(*) FROM project_items").fetchone()[0] == 0)
    check("dry-run: schema_version yozilmadi",
          c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0)
    c.close()

    print("=== haqiqiy migratsiya (+backup) ===")
    res = migrations.migrate_content_to_items(_tmp, dry_run=False, backup=True)
    check("migratsiya: status=applied, migrated=3", res.get("status") == "applied" and res.get("migrated") == 3)
    check("migratsiya: backup fayli yaratildi", bool(res.get("backup")) and os.path.exists(res["backup"]))
    if res.get("backup") and os.path.exists(res["backup"]):
        os.remove(res["backup"])

    c = _con()
    check("content_posts O'ZGARMADI (3 qator)",
          c.execute("SELECT COUNT(*) FROM content_posts").fetchone()[0] == 3)
    items = {r["id"]: r for r in c.execute("SELECT * FROM project_items")}
    check("project_items: 3 ta type='post'",
          len(items) == 3 and all(r["type"] == "post" for r in items.values()))

    a = items.get("cp-A")
    fa = json.loads(a["fields"]) if a and a["fields"] else {}
    check("cp-A: title=topic, description=message, primary_date=date, category, assignee, status, project_id",
          a and a["title"] == "«Omonat» — yangi reels" and a["description"] == "Matn A"
          and a["primary_date"] == "2026-07-02" and a["category"] == "bank"
          and a["assignee"] == "D. Karimova" and a["status"] == "joylandi" and a["project_id"] == "pr-1")
    check("cp-A: created_at/updated_at saqlandi + order_index=0",
          a and a["created_at"] == "2026-07-01T09:00" and a["updated_at"] == "2026-07-02T10:00"
          and a["order_index"] == 0)
    check("cp-A: fields = format/platform/hashtags/published_url/published_at (reject_reason='' → yo'q)",
          fa.get("format") == "Reels" and fa.get("platform") == "Instagram"
          and fa.get("hashtags") == "#omonat" and fa.get("published_url") == "https://t.me/x"
          and fa.get("published_at") == "2026-07-02T10:00" and "reject_reason" not in fa)

    b = items.get("cp-B")
    check("cp-B: fields NULL (turga-xos maydon yo'q), project_id NULL, status reja",
          b and b["fields"] is None and b["project_id"] is None and b["status"] == "reja")

    cc = items.get("cp-C")
    fc = json.loads(cc["fields"]) if cc and cc["fields"] else {}
    check("cp-C: noto'g'ri status 'axlat' → 'reja' ga tushirildi", cc and cc["status"] == "reja")
    check("cp-C: fields = format/platform/reject_reason (published_* None → yo'q)",
          fc.get("format") == "Post" and fc.get("platform") == "Telegram"
          and fc.get("reject_reason") == "sabab" and "published_url" not in fc)

    # Round-trip: item → post dict, ma'lumot yo'qolmaganini tekshirish (cp-A)
    post_a = {"date": a["primary_date"], "category": a["category"], "topic": a["title"],
              "message": a["description"], "status": a["status"], "assignee": a["assignee"],
              "project_id": a["project_id"], **fa}
    check("cp-A round-trip: barcha asl maydonlar qayta tiklanadi",
          post_a["topic"] == "«Omonat» — yangi reels" and post_a["format"] == "Reels"
          and post_a["published_url"] == "https://t.me/x")
    c.close()

    print("=== idempotentlik ===")
    again = migrations.migrate_content_to_items(_tmp, dry_run=False, backup=False)
    check("qayta ishga tushirish: status=up_to_date, migrated=0", again.get("status") == "up_to_date")
    c = _con()
    check("qayta ishga tushirish: dublikat yo'q (hali 3 ta)",
          c.execute("SELECT COUNT(*) FROM project_items").fetchone()[0] == 3)
    c.close()

    print("=== yangi post → qator-idempotent sync ===")
    c = _con()
    c.execute(f"INSERT INTO content_posts ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
              ("cp-D", "2026-07-10", "bank", "Keyin qo'shilgan", None, None, "D", None, "pr-1",
               "jarayonda", "D.", None, None, None, "2026-07-10", "2026-07-10"))
    c.commit()
    c.close()
    sync = migrations.migrate_content_to_items(_tmp, dry_run=False, backup=False)
    check("yangi post: faqat 1 ta ko'chirildi", sync.get("migrated") == 1)
    c = _con()
    check("yangi post: project_items endi 4 ta",
          c.execute("SELECT COUNT(*) FROM project_items").fetchone()[0] == 4)
    c.close()

finally:
    for f in list(__import__("glob").glob(_tmp + "*")):
        try:
            os.remove(f)
        except OSError:
            pass

print("\n" + "=" * 48)
print(f"NATIJA:  ✅ {_pass} o'tdi   ❌ {_fail} yiqildi")
print("=" * 48)
import sys  # noqa: E402
sys.exit(1 if _fail else 0)
