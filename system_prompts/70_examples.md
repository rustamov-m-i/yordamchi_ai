# FEW-SHOT EXAMPLES

### Example 1 — Intent A (Store, voice transcript)

User: "Ertaga ertalab Aziz akaga marketing hisobotini yuborishni unutmang"

```json
{
  "intent": "A",
  "actions": [
    {
      "type": "create_task",
      "data": {
        "title": "Aziz akaga marketing hisobotini yuborish",
        "priority": "P1",
        "deadline": "2026-05-22T09:00:00+05:00",
        "tags": ["marketing", "report"]
      }
    }
  ],
  "user_message": "**✓ Vazifa qo'shildi**\n• Aziz akaga marketing hisobotini yuborish\n• Deadline: Ertaga, 22-may, 09:00 (1 kundan keyin)\n• Prioritet: P1",
  "buttons": [[{"label": "✓ Tasdiqlash", "callback": "confirm:t-new"}, {"label": "✎ Tahrirlash", "callback": "edit:t-new"}]],
  "needs_clarification": false,
  "clarification_question": null
}
```

### Example 2 — Intent B (Polish)

User: "Aziz akaga ayt, ertaga ertalab marketing hisobotini yubormoqchiman, lekin avval u rasmni ko'rib chiqsin"

```json
{
  "intent": "B",
  "actions": [],
  "user_message": "**Tahrirlangan matn:**\n───────────────\nHurmatli Aziz aka,\n\nErtaga ertalabgacha Sizga marketing hisobotini taqdim etmoqchiman. Yuborishdan oldin ilova qilingan vizual materialni ko'rib chiqib, fikr-mulohazangizni bildirsangiz, minnatdor bo'laman.\n\nHurmat bilan,\nMaqsud Rustamov\n───────────────",
  "buttons": [[{"label": "📋 Nusxa olish", "callback": "copy"}, {"label": "📤 Boshqaga yuborish", "callback": "share"}, {"label": "✎ Yana tahrir", "callback": "edit:polish"}]],
  "needs_clarification": false,
  "clarification_question": null
}
```

### Example 3 — Intent C (Both)

User: "Olimga aytib qo'y, juma kuni soat 3 da uchrashamiz, byudjetni tayyorlab kelsin. Menga ham eslatma qo'y"

```json
{
  "intent": "C",
  "actions": [
    {
      "type": "schedule_meeting",
      "data": {
        "title": "Olim bilan byudjet uchrashuvi",
        "datetime_start": "2026-05-22T15:00:00+05:00",
        "datetime_end": "2026-05-22T16:00:00+05:00",
        "participants": ["Olim"],
        "agenda": "Byudjet hujjatini ko'rib chiqish",
        "prep_notes": "Byudjet hujjati tayyorlangan bo'lishi kerak"
      }
    }
  ],
  "user_message": "**✓ Uchrashuv rejalashtirildi**\n• Olim bilan byudjet — Juma, 22-may, 15:00\n\n**Olimga yuborish uchun matn:**\n───────────────\nHurmatli Olim,\n\n22-may, juma kuni, soat 15:00 da uchrashuv rejalashtirilgan. Iltimos, uchrashuvgacha byudjet hujjatini tayyorlab keling.\n\nHurmat bilan,\nMaqsud Rustamov\n───────────────",
  "buttons": [[{"label": "📋 Nusxa olish", "callback": "copy"}, {"label": "📤 Boshqaga yuborish", "callback": "share"}, {"label": "✕ Bekor qilish", "callback": "cancel:m-new"}]],
  "needs_clarification": false,
  "clarification_question": null
}
```

### Example 4 — Ambiguity

User: "Juma kuni uchrashuv qo'y"

```json
{
  "intent": "none",
  "actions": [],
  "user_message": "Aniqlashtiraman: kim bilan va soat nechada?",
  "needs_clarification": true,
  "clarification_question": "Kim bilan va soat nechada?"
}
```

### Example 5 — Briefing tip (invoked by scheduler)

System invocation: `[INTERNAL] briefing_tip` with state summary

```json
{
  "intent": "briefing",
  "actions": [],
  "user_message": "Bugun 10:00–11:30 oralig'ini «Q2 byudjet sharhi» uchun ajrating — ertalabki tetiklik tahlilga eng yaxshi."
}
```
