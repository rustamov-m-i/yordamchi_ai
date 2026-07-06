"""Local web-dev server for the Mini App.

Runs ONLY the aiohttp web layer (no Telegram bot / no polling), so it never
conflicts with the production bot on the VM. Uses the local .env and the local
data/yordamchi.db — never the production server.

Usage (from the project root, macOS/Linux):

    WEBAPP_OPEN_ACCESS=1 venv/bin/python dev_web.py

Then open http://localhost:8080 in a browser. WEBAPP_OPEN_ACCESS=1 removes the
login barrier for local browser access — safe here because localhost is not
public. Press Ctrl+C to stop. Override the port with WEB_DEV_PORT (default 8080).
"""
import os

import asyncio

from aiohttp import web

import database
import webapp

if __name__ == "__main__":
    port = int(os.getenv("WEB_DEV_PORT", "8080"))
    # Prod'da bot.py init qiladi; dev'da migratsiya + seed ishlashi uchun shu yerda.
    asyncio.run(database.init())
    print(f"→ Mini App (dev): http://localhost:{port}   (Ctrl+C to stop)")
    web.run_app(webapp.create_app(), host="127.0.0.1", port=port)
