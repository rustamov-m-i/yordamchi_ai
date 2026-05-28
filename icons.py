"""Canonical icon palette — single source of truth for emoji used in the bot UI.

Rationale: a bank-executive Telegram bot must feel professional and consistent.
Mixed icons for the same action (✓ vs ✅, ✕ vs ❌, 🔎 vs 🔍, 🔥 vs ⚡) create
cognitive friction. This module defines the canonical set.

Usage (in new code):
    from icons import ICONS
    text=f"{ICONS.CONFIRM} Tasdiqlash"

Existing code still inlines emoji literally — that's fine; this module's job is
to be the reference. When you add a button, pick the icon from this palette.

Design principles:
- Same semantic action → same emoji everywhere.
- User-initiated cancel (button) → ✕.   System error/failure → ❌.
- Status badges (final state) use a single canonical icon per state.
- Priority colors form a 4-step gradient: 🔴 🟠 🔵 ⚪.
- No celebratory (🎉 🌟 ✨), no childish (🤖 emoji-heavy).
"""

from typing import Final


class ICONS:
    """Canonical icon palette. Use these constants in new UI code."""

    # ── Actions (buttons, toasts) ────────────────────────────────────
    CONFIRM: Final[str] = "✅"           # "Tasdiqlash", "Saqlash", "Ha"
    CANCEL: Final[str] = "✕"             # User-initiated cancel button
    ADD: Final[str] = "➕"                # "Yangi vazifa", "+ qo'sh"
    EDIT: Final[str] = "✏️"              # "Tahrirlash"
    DELETE: Final[str] = "🗑"            # "O'chirish"
    SEARCH: Final[str] = "🔍"            # Universal search icon
    BACK: Final[str] = "⬅️"              # "Orqaga" — single canonical form
    REFRESH: Final[str] = "🔄"           # "Yangilash", "Vaqtni o'zgartirish"
    REPEAT: Final[str] = "🔁"            # Recurring task/reminder cycle
    SETTINGS: Final[str] = "⚙️"          # "Sozlamalar"
    SHARE: Final[str] = "📤"             # Export/forward externally

    # ── Status (final-state badges) ──────────────────────────────────
    SYSTEM_ERROR: Final[str] = "❌"      # Backup failed, scheduler down, etc.
    PENDING: Final[str] = "⏳"           # "todo" status — waiting to start
    IN_PROGRESS: Final[str] = "🔄"       # Active work
    BLOCKED: Final[str] = "⚠️"           # Stalled, awaiting input
    DONE: Final[str] = "✅"              # Completed
    CANCELLED: Final[str] = "❌"         # Cancelled task (final state)

    # ── Domain entities ──────────────────────────────────────────────
    TASK: Final[str] = "📌"              # Vazifa, task list
    NOTE: Final[str] = "📝"              # Yangi qayd, edit
    NOTE_LIST: Final[str] = "📋"         # Qaydlar ro'yxati, form/checklist
    NOTE_TEXT: Final[str] = "📄"         # Tavsif, static document
    MEETING: Final[str] = "🤝"           # Uchrashuv
    CALENDAR: Final[str] = "📅"          # Deadline, sana
    REMINDER: Final[str] = "⏰"          # Eslatma
    PERSON: Final[str] = "👤"            # Single user, ijrochi
    TEAM: Final[str] = "👥"              # Ko'p odam, jamoa
    INBOX: Final[str] = "📥"             # Yangi tushgan qayd
    ARCHIVE: Final[str] = "📦"           # Arxivlangan
    STATS: Final[str] = "📊"             # Statistika, analytics
    RISK: Final[str] = "⚠️"              # Risk/ogohlantirish
    LOCATION: Final[str] = "📍"          # Manzil (faqat geografik)
    URGENT: Final[str] = "⚡"            # P0 vazifa, "Eng yaqin", "Shoshilinch"
    EVENING: Final[str] = "🌙"           # Kechki yakun, sukunat soatlari
    VOICE: Final[str] = "🎙"             # Ovozli xabar, transcript

    # ── Priority badges (4-step color gradient) ─────────────────────
    P0_URGENT: Final[str] = "🔴"         # Shoshilinch
    P1_IMPORTANT: Final[str] = "🟠"      # Muhim
    P2_PLANNED: Final[str] = "🔵"        # Rejadagi
    P3_LOW: Final[str] = "⚪"            # Past ustuvorlik

    # ── Notifications / toggle states ────────────────────────────────
    NOTIF_ON: Final[str] = "🔔"          # Bildirishnomalar yoqilgan
    NOTIF_OFF: Final[str] = "🔕"         # Bildirishnomalar o'chirilgan

    # ── Secondary indicators ─────────────────────────────────────────
    STAR: Final[str] = "⭐"              # Mark important / favorite
    BULLET: Final[str] = "🔹"            # Inline list bullet (non-priority)


# Convenience dicts for status & priority lookups
STATUS_EMOJI: Final[dict[str, str]] = {
    "todo":        ICONS.PENDING,
    "in_progress": ICONS.IN_PROGRESS,
    "blocked":     ICONS.BLOCKED,
    "done":        ICONS.DONE,
    "cancelled":   ICONS.CANCELLED,
}

PRIORITY_BADGE: Final[dict[str, str]] = {
    "P0": ICONS.P0_URGENT,
    "P1": ICONS.P1_IMPORTANT,
    "P2": ICONS.P2_PLANNED,
    "P3": ICONS.P3_LOW,
}
