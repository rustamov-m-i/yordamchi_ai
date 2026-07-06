"""Marketing Hub — Bosqich 0: config_marketing.py invariantlari va toza helperlar.

Bu test faqat `config_marketing`ni import qiladi (DB/env kerak emas) — konfiguratsiya
ichki ziddiyatsiz ekanini va helperlar to'g'ri ishlashini tekshiradi. Ishga tushirish:
    PYTHONPATH=. venv/bin/python tests/marketing_hub_config_check.py
"""
from __future__ import annotations

import sys

import config_marketing as M

_pass = 0
_fail = 0


def check(name: str, cond: bool) -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✅ {name}")
    else:
        _fail += 1
        print(f"  ❌ {name}")


VIEWS = {"calendar", "kanban", "table", "dashboard"}
FIELD_KINDS = {"text", "textarea", "date", "number", "select", "url"}
# SMM workflow'ining o'zgarmas 6 legacy kaliti — database.CONTENT_STATUSES bilan bir xil
# bo'lishi shart (bu ziddiyat DB bosqichida alohida tekshiriladi).
LEGACY_SMM = ["reja", "jarayonda", "tekshiruvda", "joylandi", "rad_etildi", "bekor"]

print("=== Marketing Hub config invariantlari ===")

# ── WORKFLOWS ──
for wf, statuses in M.WORKFLOWS.items():
    keys = [s.get("key") for s in statuses]
    check(f"WORKFLOWS[{wf}]: bo'sh emas", len(statuses) > 0)
    check(f"WORKFLOWS[{wf}]: har status key/label/color to'liq",
          all(s.get("key") and s.get("label") and s.get("color") for s in statuses))
    check(f"WORKFLOWS[{wf}]: kalitlar takrorlanmaydi", len(keys) == len(set(keys)))
    check(f"WORKFLOWS[{wf}]: ranglar HEX",
          all(str(s.get("color", "")).startswith("#") for s in statuses))

check("SMM workflow = 6 legacy status (identity, o'zgartirilmagan)",
      [s["key"] for s in M.WORKFLOWS["smm"]] == LEGACY_SMM)

# ── PROJECT_TYPES ──
for t, cfg in M.PROJECT_TYPES.items():
    check(f"PROJECT_TYPES[{t}]: workflow WORKFLOWS'da mavjud", cfg["workflow"] in M.WORKFLOWS)
    check(f"PROJECT_TYPES[{t}]: default_view yaroqli", cfg["default_view"] in VIEWS)
    check(f"PROJECT_TYPES[{t}]: label + icon bor", bool(cfg.get("label") and cfg.get("icon")))

# ── PROJECT_ITEM_TYPES ──
for t in M.PROJECT_TYPES:
    check(f"PROJECT_ITEM_TYPES[{t}]: mavjud", t in M.PROJECT_ITEM_TYPES)
for t, kinds in M.PROJECT_ITEM_TYPES.items():
    check(f"PROJECT_ITEM_TYPES[{t}]: hamma kind ITEM_TYPES'da",
          all(k in M.ITEM_TYPES for k in kinds))

# ── ITEM_FIELDS ──
for it, fields in M.ITEM_FIELDS.items():
    check(f"ITEM_FIELDS[{it}]: yaroqli item turi", it in M.ITEM_TYPES)
    for f in fields:
        check(f"ITEM_FIELDS[{it}].{f.get('key')}: kind yaroqli", f.get("kind") in FIELD_KINDS)
        if f.get("kind") == "select":
            check(f"ITEM_FIELDS[{it}].{f.get('key')}: select options bor", bool(f.get("options")))

# ── TEMPLATES ──
for tpl in M.TEMPLATES:
    check(f"TEMPLATES[{tpl['id']}]: type yaroqli", tpl["type"] in M.PROJECT_TYPES)
    check(f"TEMPLATES[{tpl['id']}]: label/icon/color/default_view bor",
          all(tpl.get(k) for k in ("label", "icon", "color", "default_view")))
check("TEMPLATES_BY_ID to'liq", len(M.TEMPLATES_BY_ID) == len(M.TEMPLATES))

# ── Helperlar ──
dw = M.default_workflow("smm")
check("default_workflow(smm) → {statuses:[...]}",
      isinstance(dw, dict) and isinstance(dw.get("statuses"), list) and dw["statuses"])
check("default_workflow(noma'lum) → custom fallback",
      M.default_workflow("yoq_bunday") == {"statuses": M.WORKFLOWS[M.PROJECT_TYPES["custom"]["workflow"]]})
check("item_types_for(smm) → post bor", "post" in M.item_types_for("smm"))
check("item_types_for(noma'lum) → custom fallback",
      M.item_types_for("yoq_bunday") == M.PROJECT_ITEM_TYPES["custom"])
check("fields_for(post) → bo'sh emas", len(M.fields_for("post")) > 0)
check("fields_for(task) → bo'sh", M.fields_for("task") == [])
check("fields_for(noma'lum) → bo'sh", M.fields_for("yoq_bunday") == [])

at = M.apply_template("smm_calendar")
check("apply_template(smm_calendar): type/icon/color/default_view/workflow to'liq",
      all(k in at for k in ("type", "icon", "color", "default_view", "workflow"))
      and at["type"] == "smm" and isinstance(at["workflow"], dict))
check("apply_template(noma'lum) → {}", M.apply_template("yoq_bunday") == {})

check("map_legacy_post_status(joylandi) → joylandi", M.map_legacy_post_status("joylandi") == "joylandi")
check("map_legacy_post_status(axlat) → reja", M.map_legacy_post_status("axlat") == "reja")
check("map_legacy_post_status(None) → reja", M.map_legacy_post_status(None) == "reja")

print("\n" + "=" * 48)
print(f"NATIJA:  ✅ {_pass} o'tdi   ❌ {_fail} yiqildi")
print("=" * 48)
sys.exit(1 if _fail else 0)
