# Yordamchi — Executive AI Assistant (Telegram Bot)

Shaxsiy executive AI yordamchi. Faqat bir foydalanuvchi uchun. O'zbek tilida ovoz va matn orqali ishlaydi. Topshiriqlarni saqlaydi, uchrashuvlarni rejalashtiradi, kunlik briefinglar yuboradi va matnlarni rasmiy ko'rinishga keltiradi.

## 📋 CHANGELOG — 2026-05-28

**📥 Qaydlar (Inbox)** — GTD uslubidagi quick-capture qo'shildi:
- `/notes` — qayta ishlanmagan qaydlar inbox'i (3 holat: Inbox / Ishlangan / Arxiv)
- `/qayd <matn>` — tezkor qayd qo'shish
- Boshqa chatdan xabarni forward qiling → avtomatik qayd (chat + author kontekst saqlanadi)
- Voice: _"qayd qil: ..."_ — ovozdan ham mumkin
- Har qayd uchun 5 ta amal: 🤖 Tahlil · 📝 Vazifaga · ⏰ Eslatmaga · 📦 Arxiv · 🗑 O'chir
- Bugungi brifingda inbox count ko'rinadi
- GlobalSearchFSM bilan birga ishlaydi

## Asosiy imkoniyatlar

- **Voice & text input** — O'zbek tilida ovoz orqali (Whisper) yoki matn orqali topshiriq yuborish
- **Intent detection** — bot avtomatik aniqlaydi: vazifa qo'yishmi, matnni tahrirlashmi, yoki ikkalasi
- **Professional polishing** — kundalik dictation'lardan rasmiy, executive-grade matn
- **Task management** — P0/P1/P2/P3 prioritetlar, deadline'lar, statuslar
- **Recurring tasks** — har kuni/hafta/oy/chorak/yil takrorlanadigan vazifalar
- **Meeting intelligence** — agenda, prep brief, post-meeting action item extraction
- **Executive cockpit** — bitta oynada top-3 vazifa, delegatsiya, risklar va tavsiyalar
- **Professional statistics** — KPI, deadline pressure, delegation, meeting productivity, LLM audit
- **Executive reports** — weekly/monthly rahbarona hisobot
- **Daily briefings** — 08:00 morning, 18:00 evening
- **Strict single-user access** — boshqa foydalanuvchilar avtomatik rad etiladi
- **Persistent memory** — kontaktlar, loyihalar, ish patternlari saqlanadi

## Texnik stack

- **Bot framework:** aiogram 3.x (async Telegram bot)
- **LLM:** Anthropic Claude Sonnet 4.6 (default) / Opus 4.7 (optional)
- **Voice → text:** OpenAI Whisper API
- **Database:** SQLite (aiosqlite, async)
- **Scheduler:** APScheduler (cron-style + interval triggers)
- **Hosting:** har qanday VPS (Linux + Python 3.11+)

## O'rnatish

### 1. Loyihani klonlash

```bash
git clone <repo-url> yordamchi
cd yordamchi
```

### 2. Python virtual environment

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Sozlamalar

```bash
cp .env.example .env
nano .env
```

`.env` faylida quyidagilarni to'ldiring:

| O'zgaruvchi | Qayerdan olish | Majburiy |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` | ✓ |
| `PRINCIPAL_USER_ID` | [@userinfobot](https://t.me/userinfobot) → bosing | ✓ |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com | ✓ |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys | ✓ (voice uchun) |
| `CLAUDE_MODEL` | default: `claude-sonnet-4-6` | optional |

### 4. Ishga tushirish

```bash
python bot.py
```

Logga "Bot started" yozuvi chiqsa — tayyor. Telegramda botingizga `/start` yuboring.

## Komandalar

- `/start` — botni boshlash
- `/cockpit` — boshqaruv paneli
- `/tasks` — vazifalar boshqaruvi
- `/meetings` — uchrashuvlar boshqaruvi
- `/stats today|week|month` — natijalar statistikasi
- `/plan` — ish rejasini tuzish
- `/settings` — sozlamalar
- `/help` — qo'llanma

Qo'shimcha funksiyalar ichki tugmalar orqali ochiladi: bugungi brifing, proactive tavsiyalar,
takroriy vazifalar, weekly/monthly report va kalendar holati.

## Production deployment

VPS'da uzluksiz ishlash uchun `systemd` service:

```ini
# /etc/systemd/system/yordamchi.service
[Unit]
Description=Yordamchi Telegram Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/yordamchi
ExecStart=/home/youruser/yordamchi/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable yordamchi
sudo systemctl start yordamchi
sudo systemctl status yordamchi
```

## Xavfsizlik

- `.env` faylni HECH QACHON git'ga commit qilmang (`.gitignore`'da bor)
- VPS'da DB faylga faqat sizning user'ingiz uchun ruxsat (`chmod 600 data/yordamchi.db`)
- Telegram bot tokenni umumiy resurslarda baham ko'rmang
- Bot faqat sizning `user_id`'ingiz uchun javob beradi — boshqalar avtomatik rad etiladi

## Tuzilma

```
yordamchi/
├── bot.py                 # Asosiy entry point
├── config.py              # .env loader + validatsiya
├── database.py            # SQLite ops (async)
├── claude_service.py      # Anthropic Claude API wrapper
├── voice_service.py       # Muxlisa STT (primary) + Whisper (fallback)
├── calendar_service.py    # Apple iCloud CalDAV two-way sync
├── redaction.py           # PII filter + cost estimator
├── handlers.py            # Telegram message/callback handlerlari
├── scheduler.py           # APScheduler (briefings, reminders, iCloud sync)
├── system_prompts/        # Modular Claude system prompt
├── requirements.txt
├── .env.example
└── data/                  # SQLite DB (runtime)
    └── yordamchi.db
```

## Litsenziya

Shaxsiy foydalanish uchun. Qayta tarqatish ta'qiqlangan.
