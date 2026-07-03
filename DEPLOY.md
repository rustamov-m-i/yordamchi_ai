# Yordamchi — Google Compute Engine Deployment

Bu qo'llanma `e2-micro` VM (Free Tier'ga sig'adi) ga bot'ni deploy qiladi. Taxminiy vaqt: **30 daqiqa**.

---

## 0. GCP loyihasi tayyorlash

```bash
# gcloud CLI o'rnatish (agar yo'q bo'lsa)
brew install --cask google-cloud-sdk

# Auth
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Compute Engine API'ni yoqish
gcloud services enable compute.googleapis.com
```

---

## 1. VM yaratish (Free Tier)

```bash
# us-central1, us-west1, us-east1 — Free Tier hududlari
gcloud compute instances create yordamchi-bot \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=yordamchi-bot
```

**Narx:** `e2-micro` + 30GB pd-standard `us-central1` da = **$0/oy** (Free Tier limitlari ichida).

---

## 2. SSH orqali kirish

```bash
gcloud compute ssh yordamchi-bot --zone=us-central1-a
```

---

## 3. VM ichida — system o'rnatish

```bash
# Update + python + git + tools
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12 python3.12-venv python3-pip git tmux curl

# Bot user yaratish (xavfsizlik uchun root emas)
sudo useradd -m -s /bin/bash yordamchi
sudo su - yordamchi
```

---

## 4. Repodan klonlash

```bash
# GitHub'dan klonlash (private repo uchun SSH key yoki PAT kerak)
cd ~
git clone https://github.com/YOUR_USERNAME/yordamchi.git
cd yordamchi

# Virtual env + dependencies
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. `.env` faylini ko'chirish

Lokal machine'dan VM'ga `.env` ni xavfsiz ko'chirish:

```bash
# Lokal terminal'dan:
gcloud compute scp .env yordamchi-bot:/home/yordamchi/yordamchi/.env \
  --zone=us-central1-a

# VM ichida sozlash
cd /home/yordamchi/yordamchi
chmod 600 .env
ls -l .env  # -rw------- bo'lishi kerak
```

---

## 6. Birinchi test ishga tushirish

```bash
# VM ichida
cd ~/yordamchi
source venv/bin/activate
mkdir -p data
venv/bin/python -c "import asyncio, database; asyncio.run(database.init())"

# Smoke test
venv/bin/python tests/tasks_section_smoke.py

# Bot test ishga tushirish (Ctrl+C bilan to'xtatish)
venv/bin/python bot.py
```

Telegramda `/cockpit` yozib tekshiring. Ishlasa — to'xtating va systemd'ga o'tkazamiz.

---

## 7. Systemd service (auto-restart, boot'da ishga tushish)

```bash
# Root sifatida service fayli yaratish
sudo tee /etc/systemd/system/yordamchi.service > /dev/null <<'EOF'
[Unit]
Description=Yordamchi Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=yordamchi
Group=yordamchi
WorkingDirectory=/home/yordamchi/yordamchi
Environment="PATH=/home/yordamchi/yordamchi/venv/bin"
ExecStart=/home/yordamchi/yordamchi/venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=append:/home/yordamchi/yordamchi/bot.log
StandardError=append:/home/yordamchi/yordamchi/bot.err.log

# Resource limits
MemoryMax=512M
CPUQuota=80%

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=/home/yordamchi/yordamchi
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Yoqish + ishga tushirish
sudo systemctl daemon-reload
sudo systemctl enable yordamchi
sudo systemctl start yordamchi

# Holatni tekshirish
sudo systemctl status yordamchi
sudo journalctl -u yordamchi -f  # real-time log
```

### ⚠️ Log rotatsiyasi (MAJBURIY — aks holda disk to'ladi)

`bot.log`/`bot.err.log` `append:` bilan CHEKSIZ o'sadi. Rotatsiyasiz ular
30GB diskni to'ldirib, SQLite'да **"disk I/O error"** keltirib chiqaradi.

```bash
sudo tee /etc/logrotate.d/yordamchi > /dev/null <<'EOF'
/home/yordamchi/yordamchi/bot.log /home/yordamchi/yordamchi/bot.err.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

### Diskни kuzatish / "disk I/O error" tez tuzatish

```bash
df -h /                     # disk to'ldimi?
df -i /                     # inode tugadimi?
du -sh ~/yordamchi/bot.log ~/yordamchi/bot.err.log ~/yordamchi/data/backups
# Joy bo'shatish:
truncate -s 0 ~/yordamchi/bot.log ~/yordamchi/bot.err.log   # loglarni tozalash
ls -t ~/yordamchi/data/backups/*.db | tail -n +21 | xargs -r rm   # eski backuplar
# DB butunligi + stuck WAL:
sudo systemctl stop yordamchi
sqlite3 ~/yordamchi/data/yordamchi.db "PRAGMA integrity_check; PRAGMA wal_checkpoint(TRUNCATE);"
sudo systemctl start yordamchi     # bot startда WALни o'zi tozalaydi (init self-heal)
```

OOM belgisi (`MemoryMax=512M` oshsa systemd o'ldiradi):
`journalctl -u yordamchi | grep -i -E "oom|killed|memory"`.

---

## 7.5. Telegram Mini App (Web App) — ixtiyoriy

Bot ichida ochiluvchi web ilova (vazifa/uchrashuv/qayd/eslatma — to'liq boshqaruv).
`aiohttp` server bot bilan bir jarayonда `127.0.0.1:8081`да ishlaydi; nginx uni
HTTPS bilan tashqariga chiqaradi. Auth: Telegram `initData` (bot-token HMAC) +
faqat `PRINCIPAL_USER_ID`. **Domen + HTTPS majburiy** (Telegram https'siz ochmaydi).

**1) Env** (`.env`ga qo'shing, keyin `sudo systemctl restart yordamchi`):
```
WEBAPP_ENABLED=1
WEBAPP_URL=https://app.SIZNING-DOMEN.uz
WEBAPP_PORT=8081
```

**2) DNS**: `app.SIZNING-DOMEN.uz` A-yozuvини VM tashqi IP'siga yo'naltiring.

**3) nginx + TLS** (VMда):
```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo tee /etc/nginx/sites-available/yordamchi-app > /dev/null <<'EOF'
server {
    server_name app.SIZNING-DOMEN.uz;
    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/yordamchi-app /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d app.SIZNING-DOMEN.uz   # Let's Encrypt sertifikat (avtomatik yangilanadi)
```
GCP firewall'да 80/443 portlar ochiq bo'lsin (`gcloud compute firewall-rules ...` yoki konsol).

**4) systemd** `ReadWritePaths` allaqachon loyiha papkasini qamraydi — o'zgarish shart emas.
Botni qayta ishga tushiring; u startда menyu tugmasini "🗂 Ilova"ga o'zgartiradi.

**5) Tekshirish**: Telegramда botni oching → pastdagi menyu tugmasi (🗂 Ilova) → ilova ochiladi.
Muammo bo'lsa: `curl -H "Authorization: tma test" https://app.SIZNING-DOMEN.uz/api/health`
(`{"ok":true}` qaytishi kerak — health authsiz).

> Xavfsizlik: `WEBAPP_HOST=127.0.0.1` — server to'g'ridan-to'g'ri tashqariga
> chiqmaydi, faqat nginx orqali. Har API so'rovi `initData` imzosi bilan
> tekshiriladi; imzo faqat sizning Telegram klientingizda hosil bo'ladi.

---

## 8. iCloud va data zaxira (backup)

### Avtomatik kunlik backup → Cloud Storage

```bash
# GCS bucket yaratish (lokal terminal)
gsutil mb -l us-central1 gs://yordamchi-backups-YOUR_PROJECT/

# VM'ga GCS yozish ruxsati
gcloud iam service-accounts create yordamchi-vm
gsutil iam ch \
  serviceAccount:yordamchi-vm@YOUR_PROJECT.iam.gserviceaccount.com:objectAdmin \
  gs://yordamchi-backups-YOUR_PROJECT

# VM'ga service account biriktirish — yangi VM uchun:
# --service-account=yordamchi-vm@YOUR_PROJECT.iam.gserviceaccount.com \
# --scopes=cloud-platform

# Mavjud VM uchun (to'xtatib qo'yish kerak):
gcloud compute instances stop yordamchi-bot --zone=us-central1-a
gcloud compute instances set-service-account yordamchi-bot \
  --service-account=yordamchi-vm@YOUR_PROJECT.iam.gserviceaccount.com \
  --scopes=cloud-platform \
  --zone=us-central1-a
gcloud compute instances start yordamchi-bot --zone=us-central1-a
```

### Backup skript (VM ichida)

```bash
sudo tee /home/yordamchi/backup.sh > /dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
TS=$(date +%Y%m%d-%H%M%S)
DB=/home/yordamchi/yordamchi/data/yordamchi.db
TMP=/tmp/yordamchi-${TS}.db
sqlite3 "$DB" ".backup $TMP"
gsutil cp "$TMP" gs://yordamchi-backups-YOUR_PROJECT/daily/yordamchi-${TS}.db
rm "$TMP"
# 30 kundan eski backup'larni o'chirish
gsutil ls -l gs://yordamchi-backups-YOUR_PROJECT/daily/ | \
  awk '$1 > 0 && $2 < "'$(date -d '30 days ago' -Iseconds)'" {print $3}' | \
  xargs -r gsutil rm
EOF

sudo chmod +x /home/yordamchi/backup.sh
sudo chown yordamchi:yordamchi /home/yordamchi/backup.sh

# Crontab — har kuni 03:00 da backup
sudo crontab -u yordamchi -e
# Qo'shing:
# 0 3 * * * /home/yordamchi/backup.sh >> /home/yordamchi/backup.log 2>&1
```

---

## 9. Monitoring (ixtiyoriy)

### Bot ishlayotganini har 5 daqiqada tekshirish

```bash
# Cloud Monitoring uptime check — VM tashqi IP'siga emas, jarayonga
# (bot polling — webhook'siz, port ochmagan)
#
# O'rniga: systemd `Restart=always` allaqachon barcha xatolarni qayta ishga tushiradi.
# Telegram'ga error notification: ERROR_NOTIFY_USER_ID `.env`'da sozlangan.
```

### Disk to'lib qolmasligi uchun log rotation

```bash
sudo tee /etc/logrotate.d/yordamchi > /dev/null <<'EOF'
/home/yordamchi/yordamchi/bot.log
/home/yordamchi/yordamchi/bot.err.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
    su yordamchi yordamchi
}
EOF
```

---

## 10. Yangilanish (kelajakda kod o'zgartirish)

```bash
gcloud compute ssh yordamchi-bot --zone=us-central1-a
sudo su - yordamchi
cd ~/yordamchi
git pull
source venv/bin/activate
pip install -r requirements.txt  # agar yangi dependency bo'lsa
exit
sudo systemctl restart yordamchi
sudo journalctl -u yordamchi -f  # tekshirish
```

---

## Tezkor buyruqlar (cheat sheet)

```bash
# VM holati
gcloud compute instances list

# SSH
gcloud compute ssh yordamchi-bot --zone=us-central1-a

# VM ichida:
sudo systemctl status yordamchi      # holat
sudo systemctl restart yordamchi     # qayta ishga tushirish
sudo journalctl -u yordamchi -n 100  # oxirgi 100 log qatori
sudo journalctl -u yordamchi -f      # real-time
tail -f ~/yordamchi/bot.err.log      # error log
```

---

## Tahminiy oylik xarajat

| Resurs | Free Tier | Bizning ishlatish | Xarajat |
|--------|-----------|--------------------|---------|
| e2-micro VM (us-central1) | 1 ta bepul | 1 ta | $0 |
| 30GB pd-standard disk | 30GB bepul | 30GB | $0 |
| Tashqi tarmoq (egress) | 1GB/oy bepul | ~50-200MB | $0 |
| Cloud Storage (backup) | 5GB bepul | ~0.5GB | $0 |
| **Jami GCP** | | | **$0** |
| Anthropic Claude API | — | ~$25/oy | $25 |
| **Umumiy** | | | **~$25/oy** |

Free Tier hududlari: `us-central1`, `us-west1`, `us-east1` (faqat shu uchta zona ichidagi e2-micro bepul).

---

## Tekshirish ro'yxati (deploy oxirida)

- [ ] `gcloud compute instances list` → yordamchi-bot RUNNING
- [ ] `gcloud compute ssh ...` ishlaydi
- [ ] `.env` VM'da `chmod 600` bilan
- [ ] `data/yordamchi.db` mavjud (database.init() ishlagan)
- [ ] `sudo systemctl status yordamchi` → active (running)
- [ ] Telegramda `/cockpit` javob beradi
- [ ] iCloud sync ishlayapti (log'da `iCloud cache primed`)
- [ ] Backup crontab faol (`crontab -u yordamchi -l`)
- [ ] Log rotation sozlangan (`ls /etc/logrotate.d/yordamchi`)
