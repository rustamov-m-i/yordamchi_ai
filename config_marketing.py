"""Marketing Hub — loyiha turlari, workflow'lar, item turlari, maydonlar va shablonlar.

Bu modul — Marketing Hub konfiguratsiyasining YAGONA HAQIQAT MANBAI. Frontend uni
`/api/marketing-config` orqali yuklab oladi — JS'da nusxa SAQLANMAYDI. (Bekor qilingan
Marketing Hub versiyasida config Python va JS'da qo'lda takrorlanib "lockstep" tutilardi;
bu drift manbai edi — endi bitta manba, dublikatsiz.)

Muhim: SMM workflow'ining 6 legacy status kaliti (reja|jarayonda|tekshiruvda|
joylandi|rad_etildi|bekor) RENAME QILINMAYDI — migratsiyadan o'tgan mavjud
project_items qatorlari va migrations.py status-mapping shularga bog'langan.
"""

from __future__ import annotations

# Rang palitrasi — frontend STCOL/CATCOL bilan bir xil HEX qiymatlar.
_C = {
    "grey": "#8a8a9e", "amber": "#E8A317", "amber2": "#B45309", "violet": "#7C3AED",
    "blue": "#2f7ae5", "navy": "#0C4A6E", "green": "#16A34A", "red": "#d64545",
    "pink": "#E8557F", "teal": "#0EA5A5",
}


# ─────────────────────────── WORKFLOWS (status ustunlari) ───────────────────────────
# Har workflow — tartiblangan status ro'yxati. Kanban ustunlari va Calendar rang
# shaffofligi shundan olinadi. Kalit (key) DB'da `project_items.status` sifatida yoziladi.
WORKFLOWS: dict[str, list[dict]] = {
    # smm — LEGACY 6 kalit, identity map. O'ZGARTIRILMAYDI.
    "smm": [
        {"key": "reja", "label": "Reja", "color": _C["amber2"]},
        {"key": "jarayonda", "label": "Jarayonda", "color": _C["blue"]},
        {"key": "tekshiruvda", "label": "Tekshiruvda", "color": _C["violet"]},
        {"key": "joylandi", "label": "Joylandi", "color": _C["green"]},
        {"key": "rad_etildi", "label": "Rad etildi", "color": _C["red"]},
        {"key": "bekor", "label": "Bekor", "color": _C["grey"]},
    ],
    "campaign": [
        {"key": "brif", "label": "Brif", "color": _C["grey"]},
        {"key": "rejalashtirish", "label": "Rejalashtirish", "color": _C["amber"]},
        {"key": "ishlab_chiqarish", "label": "Ishlab chiqarish", "color": _C["violet"]},
        {"key": "tasdiqlash", "label": "Tasdiqlash", "color": _C["blue"]},
        {"key": "ishga_tushirish", "label": "Ishga tushirish", "color": _C["teal"]},
        {"key": "monitoring", "label": "Monitoring", "color": _C["navy"]},
        {"key": "hisobot", "label": "Hisobot", "color": _C["green"]},
    ],
    "pr": [
        {"key": "qoralama", "label": "Qoralama", "color": _C["grey"]},
        {"key": "korib_chiqish", "label": "Ko'rib chiqish", "color": _C["amber"]},
        {"key": "tarqatish", "label": "Tarqatish", "color": _C["blue"]},
        {"key": "chop_etildi", "label": "Chop etildi", "color": _C["green"]},
        {"key": "qamrov", "label": "Qamrov tahlili", "color": _C["teal"]},
    ],
    "branding": [
        {"key": "brif", "label": "Brif", "color": _C["grey"]},
        {"key": "dizayn", "label": "Dizayn", "color": _C["violet"]},
        {"key": "tasdiqlash", "label": "Tasdiqlash", "color": _C["blue"]},
        {"key": "ishlab_chiqarish", "label": "Ishlab chiqarish", "color": _C["amber"]},
        {"key": "ornatish", "label": "O'rnatish", "color": _C["teal"]},
        {"key": "foto_hisobot", "label": "Foto-hisobot", "color": _C["green"]},
    ],
    "roadmap": [
        {"key": "tayyorlov", "label": "Tayyorlov", "color": _C["grey"]},
        {"key": "ishlab_chiqish", "label": "Ishlab chiqish", "color": _C["violet"]},
        {"key": "tasdiqlash", "label": "Tasdiqlash", "color": _C["blue"]},
        {"key": "ishga_tushirish", "label": "Ishga tushirish", "color": _C["teal"]},
        {"key": "monitoring", "label": "Monitoring", "color": _C["navy"]},
        {"key": "hisobot", "label": "Hisobot", "color": _C["green"]},
    ],
    # simple — event / custom / internal uchun umumiy uch-status oqim.
    "simple": [
        {"key": "todo", "label": "Bajarilishi kerak", "color": _C["grey"]},
        {"key": "in_progress", "label": "Jarayonda", "color": _C["blue"]},
        {"key": "done", "label": "Bajarildi", "color": _C["green"]},
    ],
    # video — video-kontent ishlab chiqarish quvuri.
    "video": [
        {"key": "ssenariy", "label": "Ssenariy", "color": _C["grey"]},
        {"key": "suratga_olish", "label": "Suratga olish", "color": _C["amber"]},
        {"key": "montaj", "label": "Montaj", "color": _C["violet"]},
        {"key": "tasdiqlash", "label": "Tasdiqlash", "color": _C["blue"]},
        {"key": "chop_etildi", "label": "Chop etildi", "color": _C["green"]},
    ],
    # tender — pudratchi tanlash / tender jarayoni.
    "tender": [
        {"key": "talab", "label": "Talabnoma", "color": _C["grey"]},
        {"key": "elon", "label": "E'lon", "color": _C["amber"]},
        {"key": "takliflar", "label": "Takliflar", "color": _C["violet"]},
        {"key": "tanlov", "label": "Tanlov", "color": _C["blue"]},
        {"key": "shartnoma", "label": "Shartnoma", "color": _C["teal"]},
        {"key": "ijro", "label": "Ijro", "color": _C["green"]},
    ],
}


# "Yopiq" (yakunlangan/bekor qilingan) statuslar — overdue/progress hisobida "tugagan"
# deb sanaladi. Har workflow'ning yakuniy holatlaridan yig'ilgan (SMM: joylandi tugatadi,
# rad_etildi/bekor yopadi). Markazlashtirilgan — database.py'da literal tarqatilmaydi.
TERMINAL_STATUSES: frozenset[str] = frozenset({
    "joylandi", "rad_etildi", "bekor",   # smm
    "hisobot",                           # campaign, roadmap (yakuniy)
    "chop_etildi", "qamrov",             # pr, video (chop_etildi yakuniy)
    "foto_hisobot",                      # branding
    "done",                              # simple
    "ijro",                              # tender (yakuniy bosqich)
})


# ─────────────────────────── PROJECT_TYPES ───────────────────────────
# Har loyiha turi — icon, dastlabki view, va qaysi workflow'ni ishlatishi.
PROJECT_TYPES: dict[str, dict] = {
    "smm":       {"label": "SMM kontent", "icon": "brand-instagram", "default_view": "calendar", "workflow": "smm"},
    "campaign":  {"label": "Marketing kampaniya", "icon": "speakerphone", "default_view": "kanban", "workflow": "campaign"},
    "pr":        {"label": "PR loyiha", "icon": "news", "default_view": "kanban", "workflow": "pr"},
    "branding":  {"label": "Brending", "icon": "palette", "default_view": "kanban", "workflow": "branding"},
    "event":     {"label": "Tadbir (Event)", "icon": "calendar-event", "default_view": "table", "workflow": "simple"},
    "roadmap":   {"label": "Roadmap", "icon": "route", "default_view": "table", "workflow": "roadmap"},
    "media_plan": {"label": "Media plan", "icon": "device-tv", "default_view": "table", "workflow": "campaign"},
    "video":     {"label": "Video-kontent", "icon": "video", "default_view": "kanban", "workflow": "video"},
    "tender":    {"label": "Tender / pudratchi", "icon": "briefcase", "default_view": "table", "workflow": "tender"},
    "custom":    {"label": "Universal", "icon": "folder", "default_view": "table", "workflow": "simple"},
}


# ─────────────────────────── ITEM_TYPES ───────────────────────────
# Universal ProjectItem turlari — picker'da ko'rsatiladigan yorliq + icon.
ITEM_TYPES: dict[str, dict] = {
    "post":            {"label": "Post", "icon": "photo"},
    "task":            {"label": "Vazifa", "icon": "checkbox"},
    "milestone":       {"label": "Milestone", "icon": "flag"},
    "note":            {"label": "Qayd", "icon": "note"},
    "media_placement": {"label": "Media joylashtirish", "icon": "device-tv"},
    "pr_material":     {"label": "PR material", "icon": "news"},
    "design":          {"label": "Dizayn", "icon": "palette"},
    "report":          {"label": "Hisobot", "icon": "report"},
    "approval":        {"label": "Tasdiqlash", "icon": "check"},
    "event":           {"label": "Tadbir", "icon": "calendar-event"},
    "risk":            {"label": "Risk", "icon": "alert-triangle"},
}


# Har loyiha turi uchun "Item qo'shish" picker'ida ko'rinadigan item turlari.
PROJECT_ITEM_TYPES: dict[str, list[str]] = {
    "smm":       ["post", "task", "milestone", "note"],
    "campaign":  ["media_placement", "task", "report", "approval", "milestone"],
    "pr":        ["pr_material", "task", "report", "approval"],
    "branding":  ["design", "task", "approval", "report"],
    "event":     ["task", "milestone", "note"],
    "roadmap":   ["milestone", "task", "report"],
    "media_plan": ["media_placement", "task", "report"],
    "video":     ["task", "milestone", "approval", "note"],
    "tender":    ["task", "approval", "report", "milestone"],
    "custom":    ["task", "milestone", "note"],
}


# ─────────────────────────── ITEM_FIELDS (turga xos maydonlar) ───────────────────────────
# Umumiy maydonlar (title/status/assignee/primary_date/category/description) HAR item'da
# bor va bu yerda takrorlanmaydi. Bu yerda faqat turga xos — `fields` JSON blobiga
# yig'iladigan — maydonlar. kind: text|textarea|date|number|select|url.
ITEM_FIELDS: dict[str, list[dict]] = {
    "post": [
        {"key": "format", "label": "Format", "kind": "text"},
        {"key": "platform", "label": "Platforma", "kind": "text"},
        {"key": "hashtags", "label": "Hashteglar", "kind": "text"},
        {"key": "published_url", "label": "Joylangan havola", "kind": "url"},
        {"key": "reject_reason", "label": "Rad sababi", "kind": "text"},
    ],
    "media_placement": [
        {"key": "channel", "label": "Kanal", "kind": "select",
         "options": ["TV", "Radio", "Outdoor", "SMM", "Mobil ilova", "SMS", "Filial", "PR"]},
        {"key": "budget", "label": "Byudjet", "kind": "number"},
        {"key": "placement", "label": "Joylashuv", "kind": "text"},
        {"key": "vendor", "label": "Pudratchi", "kind": "text"},
        {"key": "launch_date", "label": "Boshlanish sanasi", "kind": "date"},
    ],
    "pr_material": [
        {"key": "media_name", "label": "OAV nomi", "kind": "text"},
        {"key": "material_type", "label": "Material turi", "kind": "text"},
        {"key": "speaker", "label": "Spiker", "kind": "text"},
        {"key": "journalist", "label": "Jurnalist", "kind": "text"},
        {"key": "coverage_link", "label": "Chiqish havolasi", "kind": "url"},
    ],
    "design": [
        {"key": "object_type", "label": "Obyekt turi", "kind": "text"},
        {"key": "location", "label": "Manzil", "kind": "text"},
        {"key": "size", "label": "O'lcham", "kind": "text"},
        {"key": "contractor", "label": "Pudratchi", "kind": "text"},
    ],
    "report": [
        {"key": "report_url", "label": "Hisobot havolasi", "kind": "url"},
    ],
    "task": [],
    "milestone": [],
    "note": [],
    "approval": [],
    "event": [
        {"key": "venue", "label": "Joy", "kind": "text"},
    ],
    "risk": [
        {"key": "impact", "label": "Ta'sir darajasi", "kind": "select",
         "options": ["Past", "O'rta", "Yuqori"]},
    ],
}


# ─────────────────────────── TEMPLATES ───────────────────────────
# Tayyor shablonlar — loyiha yaratishda type/icon/color/default_view/workflow'ni oldindan to'ldiradi.
TEMPLATES: list[dict] = [
    {"id": "smm_calendar", "label": "SMM Content Calendar", "type": "smm",
     "icon": "brand-instagram", "color": _C["green"], "default_view": "calendar"},
    {"id": "campaign_360", "label": "360 Marketing Campaign", "type": "campaign",
     "icon": "speakerphone", "color": _C["violet"], "default_view": "kanban"},
    {"id": "product_campaign", "label": "Yangi mahsulot kampaniyasi", "type": "campaign",
     "icon": "credit-card", "color": _C["violet"], "default_view": "kanban"},
    {"id": "seasonal_promo", "label": "Mavsumiy aksiya", "type": "campaign",
     "icon": "gift", "color": _C["red"], "default_view": "kanban"},
    {"id": "video_production", "label": "Video-kontent ishlab chiqarish", "type": "video",
     "icon": "video", "color": _C["blue"], "default_view": "kanban"},
    {"id": "tender_process", "label": "Tender / pudratchi jarayoni", "type": "tender",
     "icon": "briefcase", "color": _C["navy"], "default_view": "table"},
    {"id": "pr_campaign", "label": "PR Campaign", "type": "pr",
     "icon": "news", "color": _C["blue"], "default_view": "kanban"},
    {"id": "branding_project", "label": "Branding Project", "type": "branding",
     "icon": "palette", "color": _C["pink"], "default_view": "kanban"},
    {"id": "event_management", "label": "Event Management", "type": "event",
     "icon": "calendar-event", "color": _C["amber"], "default_view": "table"},
    {"id": "media_plan", "label": "Media Plan", "type": "media_plan",
     "icon": "device-tv", "color": _C["navy"], "default_view": "table"},
    {"id": "roadmap_project", "label": "Roadmap Project", "type": "roadmap",
     "icon": "route", "color": _C["teal"], "default_view": "table"},
    {"id": "influencer_campaign", "label": "Influencer Campaign", "type": "campaign",
     "icon": "user-star", "color": _C["pink"], "default_view": "kanban"},
    {"id": "product_launch", "label": "Product Launch", "type": "roadmap",
     "icon": "rocket", "color": _C["red"], "default_view": "table"},
    {"id": "internal_comm", "label": "Internal Communication Plan", "type": "custom",
     "icon": "messages", "color": _C["grey"], "default_view": "table"},
    {"id": "blank", "label": "Boshqa — o'zim sozlayman", "type": "custom",
     "icon": "adjustments", "color": "#6C5CE7", "default_view": "table"},
]
TEMPLATES_BY_ID: dict[str, dict] = {t["id"]: t for t in TEMPLATES}


# ─────────────────────────── Yordamchi funksiyalar ───────────────────────────
def default_workflow(project_type: str) -> dict:
    """Loyiha turi uchun standart workflow — {"statuses": [...]} shaklida."""
    cfg = PROJECT_TYPES.get(project_type) or PROJECT_TYPES["custom"]
    return {"statuses": WORKFLOWS[cfg["workflow"]]}


def item_types_for(project_type: str) -> list[str]:
    """Loyiha turida yaratish mumkin bo'lgan item turlari (picker uchun)."""
    return PROJECT_ITEM_TYPES.get(project_type) or PROJECT_ITEM_TYPES["custom"]


def fields_for(item_type: str) -> list[dict]:
    """Item turiga xos qo'shimcha maydonlar (fields blobiga yig'iladigan)."""
    return ITEM_FIELDS.get(item_type, [])


def apply_template(template_id: str) -> dict:
    """Shablon → loyiha maydonlari (type/icon/color/default_view/workflow)."""
    t = TEMPLATES_BY_ID.get(template_id)
    if not t:
        return {}
    return {
        "type": t["type"], "icon": t["icon"], "color": t["color"],
        "default_view": t["default_view"], "workflow": default_workflow(t["type"]),
    }


def map_legacy_post_status(status: str | None) -> str:
    """Eski SMM post statusini 6 legacy kalitdan biriga tushiradi (noma'lum → reja)."""
    valid = {s["key"] for s in WORKFLOWS["smm"]}
    return status if status in valid else "reja"
