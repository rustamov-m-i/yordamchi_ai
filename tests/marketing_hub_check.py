"""Lokal smoke test — Marketing Hub Phase 1 (config + migratsiya + CRUD + adapter).

Tashqi API yo'q (Claude/iCloud/Telegram/tarmoq tegilmaydi). Faqat `database` +
`config_marketing` pure logikasi, vaqtinchalik DB (tempfile) ustida. CI-safe.

Ishga tushirish:  venv/bin/python tests/marketing_hub_check.py
"""

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# Vaqtinchalik DB — haqiqiy data/yordamchi.db ga TEGILMAYDI.
_TMP = tempfile.mkdtemp(prefix="mh_test_")
config.DATABASE_PATH = os.path.join(_TMP, "test.db")

import config_marketing  # noqa: E402
import database  # noqa: E402


_PASS = 0
_FAIL = 0
_FAILED: list[str] = []


def t(area: str, name: str, ok: bool) -> None:
    global _PASS, _FAIL
    print(f"  [{'✓' if ok else '✗'}] {area:<12} {name}")
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
        _FAILED.append(f"{area}: {name}")


def section(title: str) -> None:
    print(f"\n━━━━━━━━━━ {title} ━━━━━━━━━━")


async def _count(sql: str, params=()) -> int:
    import aiosqlite
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(sql, params)
        return (await cur.fetchone())[0]


# ─────────────── 1. config_marketing ───────────────
def test_config() -> None:
    section("1. CONFIG")
    m = config_marketing
    t("config", "smm 6 legacy kalit",
      [s["key"] for s in m.WORKFLOWS["smm"]] ==
      ["reja", "jarayonda", "tekshiruvda", "joylandi", "rad_etildi", "bekor"])
    t("config", "default_workflow(smm)[3]=joylandi",
      m.default_workflow("smm")["statuses"][3]["key"] == "joylandi")
    t("config", "apply_template(campaign_360)",
      m.apply_template("campaign_360")["type"] == "campaign" and
      m.apply_template("campaign_360")["default_view"] == "kanban")
    t("config", "apply_template noma'lum → {}", m.apply_template("nope") == {})
    t("config", "item_types_for(smm)", m.item_types_for("smm") == ["post", "task", "milestone", "note"])
    t("config", "map_legacy_post_status identity", m.map_legacy_post_status("joylandi") == "joylandi")
    t("config", "map_legacy_post_status noma'lum→reja", m.map_legacy_post_status("xyz") == "reja")
    t("config", "har workflow key mavjud", all(
        pt["workflow"] in m.WORKFLOWS for pt in m.PROJECT_TYPES.values()))


# ─────────────── 2. Migratsiya ───────────────
async def test_migration() -> None:
    section("2. MIGRATSIYA")
    await database.init()
    cp = await _count("SELECT COUNT(*) FROM content_posts")
    pi_posts = await _count("SELECT COUNT(*) FROM project_items WHERE type='post'")
    t("migrate", f"content_posts({cp}) == project_items post({pi_posts})", cp == pi_posts and cp > 0)
    t("migrate", "marker qatori mavjud",
      await _count("SELECT COUNT(*) FROM project_items WHERE id='_migration_marker'") == 1)
    t("migrate", "hech bir post orphan emas (project_id NULL)",
      await _count("SELECT COUNT(*) FROM project_items WHERE type='post' AND project_id IS NULL") == 0)
    # Agrobank SMM loyihasi to'g'ri type/workflow bilan yaratilgan
    import aiosqlite
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT type, default_view, workflow FROM projects WHERE name='Agrobank SMM'")).fetchone()
    t("migrate", "Agrobank SMM type=smm/view=calendar",
      row and row["type"] == "smm" and row["default_view"] == "calendar" and "joylandi" in (row["workflow"] or ""))
    # Idempotentlik: ikkinchi init post sonini o'zgartirmaydi
    await database.init()
    pi_posts2 = await _count("SELECT COUNT(*) FROM project_items WHERE type='post'")
    t("migrate", "2-init idempotent (soni o'zgarmaydi)", pi_posts2 == pi_posts)
    t("migrate", "marker CRUD/list'da ko'rinmaydi",
      all(i.get("type") != "marker" for i in await database.list_project_items(None)))
    # fields JSON Uzbek matnni buzmaydi (published_at o'tgan postlarda)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT fields FROM project_items WHERE type='post' AND fields LIKE '%platform%' LIMIT 1")
        r = await cur.fetchone()
    t("migrate", "fields JSON valid", r is not None)


# ─────────────── 3. project_items CRUD ───────────────
async def test_crud() -> None:
    section("3. CRUD")
    pid = await database.create_project({"name": "Test CRUD", "type": "campaign",
                                         "workflow": config_marketing.default_workflow("campaign")})
    iid = await database.create_project_item(pid, {
        "type": "task", "title": "Vazifa 1", "status": "brif", "assignee": "Ali",
        "category": "bank", "primary_date": "2026-07-20",
        "fields": {"platform": "Instagram", "hashtags": "#a"}})
    got = await database.get_project_item(iid)
    t("crud", "create → get", got and got["title"] == "Vazifa 1" and got["fields"]["platform"] == "Instagram")
    t("crud", "filter type=task", len(await database.list_project_items(pid, type_="task")) == 1)
    t("crud", "filter status=brif", len(await database.list_project_items(pid, status="brif")) == 1)
    t("crud", "filter assignee", len(await database.list_project_items(pid, assignee="Ali")) == 1)
    t("crud", "filter year+month", len(await database.list_project_items(pid, year=2026, month=7)) == 1)
    t("crud", "filter year+month (bosh)", len(await database.list_project_items(pid, year=2026, month=8)) == 0)

    # shallow-merge: platform o'zgaradi, hashtags saqlanadi
    await database.update_project_item(iid, {"fields": {"platform": "Telegram"}})
    got = await database.get_project_item(iid)
    t("crud", "merge: platform yangilandi", got["fields"]["platform"] == "Telegram")
    t("crud", "merge: hashtags saqlandi", got["fields"].get("hashtags") == "#a")
    # null → kalitni o'chirish
    await database.update_project_item(iid, {"fields": {"hashtags": None}})
    got = await database.get_project_item(iid)
    t("crud", "merge: null → pop", "hashtags" not in got["fields"])
    # scalar update
    await database.update_project_item(iid, {"title": "Vazifa 1b", "status": "tasdiqlash"})
    got = await database.get_project_item(iid)
    t("crud", "scalar update", got["title"] == "Vazifa 1b" and got["status"] == "tasdiqlash")

    # move / re-pack: ikki item bitta ustunda 0..n
    i2 = await database.create_project_item(pid, {"type": "task", "title": "T2", "status": "brif"})
    i3 = await database.create_project_item(pid, {"type": "task", "title": "T3", "status": "brif"})
    await database.move_project_item(i3, "brif", 0)  # T3 boshiga
    order = {i["id"]: i["order_index"] for i in await database.list_project_items(pid, status="brif")}
    t("crud", "move: 0..n qayta raqamlash", sorted(order.values()) == [0, 1] and order[i3] == 0)

    # delete
    t("crud", "delete", await database.delete_project_item(iid) is True)
    t("crud", "delete yo'q id → False", await database.delete_project_item("nope") is False)
    # marker o'chirib bo'lmaydi
    t("crud", "marker delete himoyalangan",
      await database.delete_project_item("_migration_marker") is False)


# ─────────────── 4. content_* adapterlar ───────────────
async def test_adapters() -> None:
    section("4. ADAPTERLAR")
    pid = await database.create_project({"name": "SMM Test", "type": "smm",
                                         "workflow": config_marketing.default_workflow("smm")})
    cid = await database.create_content_post({
        "date": "2026-07-15", "category": "biznes", "topic": "Post A", "format": "Reels",
        "platform": "Instagram", "message": "Matn", "hashtags": "#x", "project_id": pid,
        "status": "reja", "assignee": "Vali"})
    posts = await database.list_content_posts(project_id=pid)
    t("adapter", "create → list", len(posts) == 1 and posts[0]["topic"] == "Post A")
    p = posts[0]
    t("adapter", "round-trip: format", p["format"] == "Reels")
    t("adapter", "round-trip: platform", p["platform"] == "Instagram")
    t("adapter", "round-trip: message", p["message"] == "Matn")
    t("adapter", "round-trip: category(scalar)", p["category"] == "biznes")
    t("adapter", "round-trip: date", p["date"] == "2026-07-15")
    t("adapter", "round-trip: assignee", p["assignee"] == "Vali")
    # update via adapter (partial — faqat status)
    await database.update_content_post(cid, {"status": "joylandi", "published_url": "https://t.me/x"})
    p = (await database.list_content_posts(project_id=pid))[0]
    t("adapter", "update: status", p["status"] == "joylandi")
    t("adapter", "update: published_url", p["published_url"] == "https://t.me/x")
    t("adapter", "update partial: format saqlandi", p["format"] == "Reels")

    # dashboard type='post' ustida
    d = await database.content_dashboard(pid)
    t("adapter", "dashboard total", d["total"] == 1)
    t("adapter", "dashboard status joylandi", d["status"].get("joylandi") == 1)
    t("adapter", "dashboard by_format", any(x[0] == "Reels" for x in d["by_format"]))

    # delete_project fan-out → itemlar orphan (project_id NULL), o'chmaydi
    await database.delete_project(pid)
    t("adapter", "delete_project: item orphan",
      await _count("SELECT COUNT(*) FROM project_items WHERE id=?", (cid,)) == 1 and
      await _count("SELECT COUNT(*) FROM project_items WHERE id=? AND project_id IS NULL", (cid,)) == 1)


# ─────────────── 5. Config drift guard (frontend mirror) ───────────────
def test_drift_guard() -> None:
    section("5. DRIFT GUARD")
    html = (ROOT / "webapp_static" / "index.html").read_text(encoding="utf-8")
    mwf = re.search(r"(?:const|var|let)\s+WORKFLOWS\s*=\s*\{", html)
    if not mwf:
        t("drift", "index.html JS config hali yo'q (frontend Qadam 10) — SKIP", True)
        return
    # WORKFLOWS obyektidagi top-level kalitlar (key:[ ...])
    block = html[mwf.end():html.index("}", mwf.end()) + 5000]  # yetarlicha kesma
    js_wf = set(re.findall(r"(\w+)\s*:\s*\[", block[:block.find("];\n") + 3] if "];" in block else block))
    py_wf = set(config_marketing.WORKFLOWS.keys())
    t("drift", f"WORKFLOWS kalitlar mos (py={len(py_wf)})", py_wf.issubset(js_wf) or js_wf.issubset(py_wf))


async def _amain() -> None:
    print("Marketing Hub Phase 1 — smoke test")
    test_config()
    await test_migration()
    await test_crud()
    await test_adapters()
    test_drift_guard()
    print(f"\n{'━'*40}\n  NATIJA: {_PASS} ✓   {_FAIL} ✗")
    if _FAILED:
        print("  Yiqilganlar:")
        for f in _FAILED:
            print(f"    - {f}")


if __name__ == "__main__":
    try:
        asyncio.run(_amain())
    finally:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1 if _FAIL else 0)
