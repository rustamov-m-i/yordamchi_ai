"""Marketing Hub — QO'LDA ishga tushiriladigan DB migratsiyalari.

MUHIM: bu migratsiya `database.init()` ichida AVTOMATIK ishlamaydi (bekor qilingan
Marketing Hub versiyasining eng katta xatosi shu edi — har startup'da jonli bazani
o'zgartirardi). Buni deploy paytida qo'lда, backup bilan ishga tushiring:

    python migrations.py --dry-run     # nima bo'lishini ko'rsatadi, hech narsa yozmaydi
    python migrations.py               # qo'llaydi (avval avtomatik backup oladi)
    python migrations.py --no-backup   # backup'siz qo'llaydi (tavsiya etilmaydi)

Qayta-ishga-tushiriladigan va idempotent: har qatorda id bo'yicha tekshiriladi —
allaqachon ko'chirilgan post'lar o'tkazib yuboriladi, faqat yangilari ko'chiriladi.
content_posts O'ZGARMAYDI (faqat o'qiladi).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time

import config
import config_marketing

# content_posts'ning SMM-ga xos ustunlari — project_items.fields JSON blobiga yig'iladi.
_POST_FIELDS = ("format", "platform", "hashtags", "published_url", "published_at", "reject_reason")
MIGRATION_NAME = "content_posts_to_project_items"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_schema_version(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")


def backup_db(path: str) -> str:
    """DB faylining vaqt-tamg'ali nusxasini yaratadi va yo'lini qaytaradi."""
    dst = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def migrate_content_to_items(db_path: str | None = None, *, dry_run: bool = False,
                             backup: bool = True) -> dict:
    """content_posts → project_items(type='post'). Ma'lumot ko'chiriladi, content_posts
    O'ZGARMAYDI (faqat o'qiladi). Qator bo'yicha idempotent (mavjud id o'tkaziladi),
    bitta tranzaksiyada — qisman muvaffaqiyatsizlik hech narsa yozmaydi."""
    db_path = db_path or config.DATABASE_PATH
    if not os.path.exists(db_path):
        return {"error": f"DB topilmadi: {db_path}"}

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema_version(con)
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='project_items'").fetchone():
            return {"error": "project_items jadvali yo'q — avval database.init() (Bosqich 1) ishga tushsin."}
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='content_posts'").fetchone():
            return {"status": "no_content_posts", "migrated": 0, "total": 0}

        posts = con.execute("SELECT * FROM content_posts").fetchall()
        existing = {r[0] for r in con.execute("SELECT id FROM project_items")}
        to_migrate = [p for p in posts if p["id"] not in existing]
        summary = {"total": len(posts), "to_migrate": len(to_migrate),
                   "already_present": len(posts) - len(to_migrate)}

        if dry_run:
            return {"status": "dry_run", **summary}
        if not to_migrate:
            return {"status": "up_to_date", "migrated": 0, **summary}

        backup_path = backup_db(db_path) if backup else None
        con.execute("BEGIN")
        for p in to_migrate:
            cols = p.keys()
            fields = {k: p[k] for k in _POST_FIELDS if k in cols and p[k] not in (None, "")}
            con.execute(
                """INSERT INTO project_items
                     (id, project_id, type, title, description, status, assignee, category,
                      primary_date, order_index, fields, created_at, updated_at)
                   VALUES (?, ?, 'post', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (p["id"], p["project_id"], (p["topic"] or "(post)"), p["message"],
                 config_marketing.map_legacy_post_status(p["status"]), p["assignee"],
                 p["category"], p["date"],
                 json.dumps(fields, ensure_ascii=False) if fields else None,
                 p["created_at"], p["updated_at"]))
        con.execute("INSERT OR REPLACE INTO schema_version (name, applied_at) VALUES (?, ?)",
                    (MIGRATION_NAME, _now()))
        con.commit()
        return {"status": "applied", "backup": backup_path, "migrated": len(to_migrate), **summary}
    finally:
        con.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    do_backup = "--no-backup" not in sys.argv
    result = migrate_content_to_items(dry_run=dry, backup=do_backup)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result.get("error") else 0)
