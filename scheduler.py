"""Background scheduler: morning/evening briefings, task & meeting reminders, follow-up intelligence."""

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

import calendar_service
import claude_service
import config
import database

logger = logging.getLogger(__name__)

# Singleton instance — used by handlers to register one-shot reminders when meetings are scheduled.
_instance: "YordamchiScheduler | None" = None


def get_scheduler() -> "YordamchiScheduler | None":
    return _instance


class YordamchiScheduler:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
        self.meeting_reminder_min = 15
        self.task_reminder_hours = 2
        global _instance
        _instance = self

    @staticmethod
    def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
        """Parse an 'HH:MM' string. Returns default on invalid input."""
        try:
            h, m = (value or "").split(":", 1)
            h, m = int(h), int(m)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
        except (ValueError, AttributeError):
            pass
        return default

    def start(self) -> None:
        # Briefings are registered with defaults; an async _apply_settings_briefings()
        # call right after start() reschedules them with the user's settings.
        self.scheduler.add_job(
            self._morning_briefing,
            CronTrigger(hour=8, minute=0, timezone=config.TIMEZONE),
            id="morning_briefing",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._evening_summary,
            CronTrigger(hour=18, minute=0, timezone=config.TIMEZONE),
            id="evening_summary",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._followup_check,
            IntervalTrigger(hours=6),
            id="followup_check",
            replace_existing=True,
            next_run_time=datetime.now(database.TZ) + timedelta(minutes=10),
        )
        # Rehydrate one-shot reminders for meetings that already exist in the DB
        # (covers restarts). Sweeps every 5 minutes as a safety net.
        self.scheduler.add_job(
            self._reschedule_pending_meeting_reminders,
            IntervalTrigger(minutes=5),
            id="meeting_reminder_rehydrate",
            replace_existing=True,
            next_run_time=datetime.now(database.TZ) + timedelta(seconds=10),
        )
        self.scheduler.add_job(
            self._task_reminder_sweep,
            IntervalTrigger(minutes=5),
            id="task_reminder",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._reminder_sweep,
            IntervalTrigger(minutes=1),
            id="personal_reminder",
            replace_existing=True,
            next_run_time=datetime.now(database.TZ) + timedelta(seconds=20),
        )
        self.scheduler.add_job(
            self._meeting_prep_sweep,
            IntervalTrigger(minutes=5),
            id="meeting_prep",
            replace_existing=True,
            next_run_time=datetime.now(database.TZ) + timedelta(seconds=30),
        )
        self.scheduler.add_job(
            self._post_meeting_followup_sweep,
            IntervalTrigger(minutes=15),
            id="post_meeting_followup",
            replace_existing=True,
            next_run_time=datetime.now(database.TZ) + timedelta(minutes=5),
        )
        # Proactive insights — every 4 hours during work hours, only push when there's something
        self.scheduler.add_job(
            self._proactive_insight_sweep,
            IntervalTrigger(hours=4),
            id="proactive_insights",
            replace_existing=True,
            next_run_time=datetime.now(database.TZ) + timedelta(hours=1),
        )

        if config.ICLOUD_ENABLED:
            self.scheduler.add_job(
                self._icloud_sync,
                IntervalTrigger(minutes=config.ICLOUD_SYNC_INTERVAL_MIN),
                id="icloud_sync",
                replace_existing=True,
                next_run_time=datetime.now(database.TZ) + timedelta(seconds=20),
            )
            self.scheduler.add_job(
                self._icloud_retry_sweep,
                IntervalTrigger(minutes=2),
                id="icloud_retry",
                replace_existing=True,
                next_run_time=datetime.now(database.TZ) + timedelta(minutes=1),
            )
            logger.info("iCloud sync enabled (every %d min) + retry queue (every 2 min)",
                        config.ICLOUD_SYNC_INTERVAL_MIN)

        self.scheduler.start()
        logger.info("Scheduler started")

    def remove_meeting_reminder(self, meeting_id: str) -> None:
        """Cancel a scheduled reminder for a meeting (idempotent)."""
        job_id = f"meeting_reminder:{meeting_id}"
        try:
            self.scheduler.remove_job(job_id)
            logger.info("Meeting reminder cancelled for %s", meeting_id)
        except Exception:
            # Job may not exist (e.g. reminder already fired or never scheduled)
            pass

    def schedule_meeting_reminder(self, meeting_id: str, meeting_start_iso: str) -> None:
        """Register a one-shot reminder for a single meeting.

        Called by handlers right after a meeting is created. Safe to call repeatedly
        for the same meeting_id — replace_existing=True ensures only one job exists.
        """
        try:
            start = datetime.fromisoformat(meeting_start_iso)
        except (ValueError, TypeError):
            logger.warning("Cannot schedule reminder: invalid datetime %r", meeting_start_iso)
            return
        if start.tzinfo is None:
            start = database.TZ.localize(start)
        lead_minutes = max(1, int(getattr(self, "meeting_reminder_min", 15) or 15))
        fire_at = start - timedelta(minutes=lead_minutes)
        if fire_at <= datetime.now(database.TZ):
            logger.info("Skipping past-due meeting reminder for %s (start=%s)", meeting_id, start)
            return
        self.scheduler.add_job(
            self._fire_meeting_reminder,
            DateTrigger(run_date=fire_at, timezone=config.TIMEZONE),
            args=[meeting_id, lead_minutes],
            id=f"meeting_reminder:{meeting_id}",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Meeting reminder scheduled for %s at %s", meeting_id, fire_at.isoformat())

    async def apply_briefing_settings(self) -> None:
        """Read current morning/evening times from DB settings and reschedule jobs.

        Called once right after start() (post-DB-init) and again every time the user
        changes a briefing time from the /settings UI. Idempotent.
        """
        settings = await database.get_settings()
        morning_h, morning_m = self._parse_hhmm(
            settings.get("morning_briefing_time", "08:00"), default=(8, 0)
        )
        evening_h, evening_m = self._parse_hhmm(
            settings.get("evening_summary_time", "18:00"), default=(18, 0)
        )
        self.scheduler.reschedule_job(
            "morning_briefing",
            trigger=CronTrigger(hour=morning_h, minute=morning_m, timezone=config.TIMEZONE),
        )
        self.scheduler.reschedule_job(
            "evening_summary",
            trigger=CronTrigger(hour=evening_h, minute=evening_m, timezone=config.TIMEZONE),
        )
        logger.info("Briefings rescheduled from settings — morning %02d:%02d, evening %02d:%02d",
                    morning_h, morning_m, evening_h, evening_m)

    async def apply_reminder_settings(self) -> None:
        """Read reminder lead times from DB and re-register pending meeting reminders."""
        settings = await database.get_settings()
        try:
            self.meeting_reminder_min = max(1, int(settings.get("meeting_reminder_min", 15)))
        except (TypeError, ValueError):
            self.meeting_reminder_min = 15
        try:
            self.task_reminder_hours = max(1, int(settings.get("task_reminder_hours", 2)))
        except (TypeError, ValueError):
            self.task_reminder_hours = 2
        await self._reschedule_pending_meeting_reminders()
        logger.info(
            "Reminder settings applied — meetings %d min, tasks %d h",
            self.meeting_reminder_min,
            self.task_reminder_hours,
        )

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _send(self, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            settings = await database.get_settings()
            if not settings.get("notifications_enabled", True):
                logger.info("Scheduled message suppressed because notifications are disabled")
                return
        except Exception:
            logger.exception("Failed to read notification settings; sending message anyway")
        try:
            await self.bot.send_message(
                config.PRINCIPAL_USER_ID, text, parse_mode="Markdown", reply_markup=reply_markup
            )
        except TelegramBadRequest as e:
            if "parse" in str(e).lower():
                try:
                    await self.bot.send_message(config.PRINCIPAL_USER_ID, text, reply_markup=reply_markup)
                except Exception:
                    logger.exception("Failed to send scheduled message (plain fallback)")
            else:
                logger.exception("TelegramBadRequest sending scheduled message")
        except Exception:
            logger.exception("Failed to send scheduled message")

    async def _morning_briefing(self) -> None:
        logger.info("Generating morning briefing")
        from handlers import _build_briefing_text
        text = await _build_briefing_text()
        if text:
            await self._send(text)

    async def _evening_summary(self) -> None:
        logger.info("Generating evening summary")
        response = await claude_service.process_message(
            "", internal_directive="[INTERNAL] generate_evening_summary"
        )
        text = response.get("user_message", "")
        if text:
            await self._send(text)

    async def _followup_check(self) -> None:
        logger.info("Running follow-up check")
        response = await claude_service.process_message(
            "",
            internal_directive=(
                "[INTERNAL] check_followups — review current state for: "
                "(a) in_progress tasks not updated in 48h, "
                "(b) overdue tasks, "
                "(c) meetings ended without follow-up actions. "
                "If nothing actionable, respond with user_message='' and actions=[]."
            ),
        )
        text = response.get("user_message", "").strip()
        if text:
            await self._send(text)

    async def _fire_meeting_reminder(self, meeting_id: str, lead_minutes: int | None = None) -> None:
        """Single-meeting reminder. Scheduled at create-time."""
        meeting = await database.get_meeting(meeting_id)
        if not meeting:
            logger.info("Meeting %s no longer exists, skipping reminder", meeting_id)
            return
        if meeting.get("reminded_at"):
            return
        try:
            dt = datetime.fromisoformat(meeting["datetime_start"]).astimezone(database.TZ)
            time_str = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = meeting["datetime_start"]
        participants = ", ".join(meeting.get("participants", [])) or "—"
        location = meeting.get("location_or_link") or "—"
        prep = meeting.get("prep_notes") or ""
        text_lines = [
            f"📞 **15 daqiqa qoldi: {meeting['title']}**",
            f"• Vaqt: {time_str}",
            f"• Ishtirokchilar: {participants}",
            f"• Joy/havola: {location}",
        ]
        if prep:
            text_lines.append(f"• Tayyorgarlik: {prep}")
        lead_label = lead_minutes or self.meeting_reminder_min
        text_lines[0] = f"📞 **{lead_label} daqiqa qoldi: {meeting['title']}**"
        await self._send("\n".join(text_lines))
        await database.mark_meeting_reminded(meeting_id)

    async def _meeting_prep_sweep(self) -> None:
        """Send a prep brief once for meetings starting in the next hour."""
        meetings = await database.list_meetings_needing_prep(window_minutes=60)
        if not meetings:
            return
        try:
            from handlers import _generate_meeting_prep
        except Exception:
            logger.exception("Cannot import meeting prep generator")
            return
        for meeting in meetings[:3]:
            try:
                text = await _generate_meeting_prep(meeting)
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📝 Keyin action items", callback_data=f"meeting_followup:{meeting['id']}")
                ]])
                await self._send(text, reply_markup=kb)
                await database.mark_meeting_prep_sent(meeting["id"])
            except Exception:
                logger.exception("Meeting prep sweep failed for %s", meeting.get("id"))

    async def _post_meeting_followup_sweep(self) -> None:
        """Ask for notes after a meeting so action items don't evaporate."""
        meetings = await database.list_meetings_needing_followup(min_age_minutes=30, max_age_hours=24)
        for meeting in meetings[:3]:
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📝 Action items chiqarish", callback_data=f"meeting_followup:{meeting['id']}")
                ]])
                await self._send(
                    f"📝 **Uchrashuv follow-up:** {meeting['title']}\n\n"
                    "Uchrashuvdan keyingi qisqa yozuv yoki ovoz yuborsangiz, action itemlarni tasklarga ajrataman.",
                    reply_markup=kb,
                )
                await database.mark_meeting_followup_sent(meeting["id"])
            except Exception:
                logger.exception("Post-meeting follow-up sweep failed for %s", meeting.get("id"))

    async def _icloud_prime_cache(self) -> None:
        """Eagerly warm the CalDAV client cache so the first user push is fast."""
        try:
            import asyncio
            cal = await asyncio.to_thread(calendar_service._get_calendar_cached)
            if cal:
                logger.info("iCloud cache primed (calendar: %s)", getattr(cal, "name", "?"))
        except Exception:
            logger.exception("iCloud cache prime failed (non-fatal)")

    async def _proactive_insight_sweep(self) -> None:
        """Check for noteworthy patterns and push an insight if found.

        Only fires push during 09:00–20:00 local time. Pushes at most 2 insights per sweep.
        """
        now = datetime.now(database.TZ)
        if not (9 <= now.hour <= 20):
            return
        try:
            from handlers import _generate_proactive_insights
            insights = await _generate_proactive_insights(limit=2)
            if not insights:
                return

            text = "💡 **Yangi tavsiyalar**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for i, ins in enumerate(insights, 1):
                text += f"**{i}. {ins['title']}**\n{ins['body']}\n\n"
                await database.log_insight(ins["type"], ins["payload"])
            text += "_Toʻliq roʻyxat: /insights_"
            await self._send(text)
        except Exception:
            logger.exception("Proactive insight sweep failed")

    async def _icloud_sync(self) -> None:
        """Pull next-30-days events from iCloud into local DB."""
        try:
            imported = await calendar_service.sync_events_to_db()
            if imported:
                logger.info("iCloud sync: %d new meetings imported", imported)
        except Exception:
            logger.exception("iCloud sync failed (non-fatal)")

    async def _icloud_retry_sweep(self) -> None:
        """Retry queued iCloud operations whose backoff has elapsed."""
        import asyncio
        from datetime import datetime as dt_

        due = await database.list_due_icloud_retries(limit=10)
        if not due:
            return

        processed = succeeded = failed = 0
        for item in due:
            processed += 1
            try:
                op = item["operation"]
                payload = item["payload"]
                if op == "push":
                    uid = await asyncio.to_thread(
                        calendar_service.push_meeting,
                        item["meeting_id"],
                        payload.get("title", "Uchrashuv"),
                        dt_.fromisoformat(payload["dt_start"]),
                        dt_.fromisoformat(payload["dt_end"]),
                        payload.get("participants"),
                        payload.get("location"),
                        payload.get("description"),
                    )
                    if uid:
                        # Persist UID on the meeting and clear the retry row
                        import aiosqlite
                        async with aiosqlite.connect(config.DATABASE_PATH) as db:
                            await db.execute(
                                "UPDATE meetings SET icloud_uid = ? WHERE id = ?",
                                (uid, item["meeting_id"]),
                            )
                            await db.commit()
                        await database.mark_icloud_retry_success(item["id"])
                        succeeded += 1
                    else:
                        await database.mark_icloud_retry_failure(item["id"], "push returned None")
                        failed += 1
                elif op == "delete":
                    ok = await asyncio.to_thread(calendar_service.delete_meeting, item["meeting_id"])
                    if ok:
                        await database.mark_icloud_retry_success(item["id"])
                        succeeded += 1
                    else:
                        await database.mark_icloud_retry_failure(item["id"], "delete returned False")
                        failed += 1
                else:
                    logger.warning("Unknown retry operation: %s", op)
                    await database.mark_icloud_retry_success(item["id"])  # drop unknown ops
                    succeeded += 1
            except Exception as e:
                logger.exception("iCloud retry sweep item failed")
                await database.mark_icloud_retry_failure(item["id"], f"{type(e).__name__}: {e}")
                failed += 1

        # Single summary line per sweep — concise but informative for ops monitoring.
        still_pending = max(0, processed - succeeded - failed)
        logger.info(
            "iCloud retry sweep: %d processed, %d succeeded, %d failed, %d still pending",
            processed, succeeded, failed, still_pending,
        )

    async def _reschedule_pending_meeting_reminders(self) -> None:
        """Safety net: re-register one-shot reminders for any future meeting without a job.

        Handles two scenarios: (a) bot restart between meeting creation and reminder time,
        (b) meeting created via a path that didn't call schedule_meeting_reminder.
        """
        upcoming = await database.list_unreminded_future_meetings()
        for meeting in upcoming:
            self.schedule_meeting_reminder(meeting["id"], meeting["datetime_start"])

    async def _task_reminder_sweep(self) -> None:
        now = datetime.now(database.TZ)
        settings = await database.get_settings()
        try:
            hours = max(1, int(settings.get("task_reminder_hours", self.task_reminder_hours)))
        except (TypeError, ValueError):
            hours = self.task_reminder_hours
        self.task_reminder_hours = hours
        window_start = (now + timedelta(hours=hours, minutes=-5)).isoformat()
        window_end = (now + timedelta(hours=hours, minutes=5)).isoformat()
        due_soon = await database.list_due_in_window(window_start, window_end)
        # Raw P0/P1 kodlari foydalanuvchiga ko'rinmasin — Uzbek labellarda chiqaramiz.
        priority_uz = {"P0": "Shoshilinch", "P1": "Muhim", "P2": "Rejadagi", "P3": "Oddiy"}
        for task in due_soon:
            deadline = task.get("deadline", "")
            try:
                dt = datetime.fromisoformat(deadline)
                deadline_str = dt.strftime("%d-%m %H:%M")
            except (ValueError, TypeError):
                deadline_str = deadline
            pri_label = priority_uz.get(task.get("priority"), task.get("priority") or "—")
            await self._send(
                f"⏰ **{hours} soat qoldi:** {task['title']}\n"
                f"• Muddat: {deadline_str}\n"
                f"• Ustuvorlik: {pri_label}"
            )
            await database.mark_task_reminded(task["id"])

    async def _reminder_sweep(self) -> None:
        due = await database.list_due_reminders(limit=20)
        if not due:
            return
        for reminder in due:
            try:
                remind_at = reminder.get("remind_at", "")
                try:
                    dt = datetime.fromisoformat(remind_at).astimezone(database.TZ)
                    time_label = dt.strftime("%d-%m %H:%M")
                except (ValueError, TypeError):
                    time_label = remind_at or "—"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"remdone:{reminder['id']}"),
                        InlineKeyboardButton(text="⏰ 15 daq", callback_data=f"remsnooze:{reminder['id']}:15m"),
                    ],
                    [
                        InlineKeyboardButton(text="🕐 1 soat", callback_data=f"remsnooze:{reminder['id']}:1h"),
                        InlineKeyboardButton(text="📅 Ertaga", callback_data=f"remsnooze:{reminder['id']}:1d"),
                    ],
                ])
                lines = [
                    f"⏰ **Eslatma:** {reminder['title']}",
                    f"• Vaqt: {time_label}",
                ]
                if reminder.get("note"):
                    lines.append(f"• Izoh: {reminder['note']}")
                if reminder.get("recurrence_rule"):
                    labels = {
                        "daily": "har kuni",
                        "weekly": "har hafta",
                        "monthly": "har oy",
                        "quarterly": "har chorak",
                        "yearly": "har yil",
                    }
                    lines.append(f"• Takror: {labels.get(reminder['recurrence_rule'], reminder['recurrence_rule'])}")
                await self._send("\n".join(lines), reply_markup=kb)
                await database.mark_reminder_sent(reminder["id"])
            except Exception:
                logger.exception("Reminder sweep failed for %s", reminder.get("id"))
