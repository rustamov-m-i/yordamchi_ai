"""Gibrid LLM (DeepSeek matn / Claude vision) marshrutlash regression testi.

openai klientini MOCK qiladi — haqiqiy DeepSeek kaliti/tarmoq kerak emas. Tekshiradi:
  - LLM_TEXT_PROVIDER=deepseek → process_message/stream/text-document DeepSeek'ga boradi
  - _blocks_need_vision: rasm/PDF → Claude (True), matn → DeepSeek (False)
  - _oai_messages: Anthropic content-bloklar → OpenAI system+matn xabarlari
  - JSON envelope parse + xato → _fallback
Ishga tushirish:  PYTHONPATH=. venv/bin/python3 tests/deepseek_hybrid_check.py
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import types

import config

_SP = "/private/tmp/claude-501/-Users-maqsudrustamov-Documents-Yordamchi-oxirgi/75cf7db8-bb71-4bce-83d1-e0d24afca88b/scratchpad"
config.DATABASE_PATH = tempfile.mktemp(suffix=".db", dir=_SP if os.path.isdir(_SP) else None)
config.LLM_TEXT_PROVIDER = "deepseek"
config.DEEPSEEK_API_KEY = "test-key-not-real"

import database        # noqa: E402
import claude_service  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    _pass, _fail = (_pass + 1, _fail) if cond else (_pass, _fail + 1)
    print(("  ✅ " if cond else "  ❌ ") + name)


_ENV = ('{"intent":"create_task",'
        '"actions":[{"type":"create_task","data":{"title":"Bannerlar tayyorlash"}}],'
        '"user_message":"Bir vazifa qo\'shildi"}')


class _Usage:
    prompt_tokens = 42
    completion_tokens = 88


class _FakeDeepSeek:
    """_deepseek() o'rniga qo'yiladi. .with_options() → self; .chat.completions.create()
    non-stream'da javob obyektini, stream'da async-generatorni qaytaradi."""
    def __init__(self, content, chunks=None, raise_exc=None):
        self._content = content
        self._chunks = chunks
        self._raise = raise_exc
        self.chat = types.SimpleNamespace(completions=self)
        self.captured = None

    def with_options(self, **kw):
        return self

    async def create(self, **kw):
        self.captured = kw
        if self._raise:
            raise self._raise
        if kw.get("stream"):
            return self._agen()
        msg = types.SimpleNamespace(content=self._content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=_Usage())

    async def _agen(self):
        for ch in (self._chunks or [self._content]):
            yield types.SimpleNamespace(
                choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=ch))],
                usage=None)
        # OpenAI stream_options=include_usage: oxirgi chunk usage'li, choices bo'sh
        yield types.SimpleNamespace(choices=[], usage=_Usage())


async def main():
    await database.init()

    # ── marshrutlash yoqilgan ──
    check("_use_deepseek_text() = True (provider=deepseek + kalit)", claude_service._use_deepseek_text() is True)

    # ── _blocks_need_vision ──
    check("_blocks_need_vision([text]) = False", claude_service._blocks_need_vision([{"type": "text", "text": "x"}]) is False)
    check("_blocks_need_vision([image]) = True → Claude", claude_service._blocks_need_vision([{"type": "image"}]) is True)
    check("_blocks_need_vision([document/PDF]) = True → Claude", claude_service._blocks_need_vision([{"type": "document"}]) is True)

    # ── _oai_messages: Anthropic → OpenAI ──
    oai = claude_service._oai_messages("HOLAT-BLOKI", [{"role": "user", "content": [{"type": "text", "text": "salom"}]}])
    check("_oai_messages[0] = system (SYSTEM_PROMPT + holat)", oai[0]["role"] == "system" and "HOLAT-BLOKI" in oai[0]["content"])
    check("_oai_messages: content-bloklar matnga tekislandi", oai[1] == {"role": "user", "content": "salom"})

    # ── process_message → DeepSeek (mock) ──
    fake = _FakeDeepSeek(_ENV)
    claude_service._ds_client = fake
    r = await claude_service.process_message("Bannerlarni tayyorla")
    check("process_message: DeepSeek javobi parse qilindi (create_task)",
          r.get("actions") and r["actions"][0]["data"]["title"] == "Bannerlar tayyorlash")
    check("process_message: user_message keldi", r.get("user_message") == "Bir vazifa qo'shildi")
    check("DeepSeek create() chaqirildi (stream'siz)", fake.captured is not None and not fake.captured.get("stream"))
    check("system xabari yuborildi", fake.captured["messages"][0]["role"] == "system")

    # ── LLM audit log: provider='deepseek' ──
    import sqlite3
    c = sqlite3.connect(config.DATABASE_PATH)
    rows = c.execute("SELECT provider, model FROM llm_audit_log ORDER BY rowid DESC LIMIT 1").fetchall()
    c.close()
    check("audit log: provider='deepseek'", rows and rows[0][0] == "deepseek" and rows[0][1] == "deepseek-chat")

    # ── process_document (matnli) → DeepSeek ──
    claude_service._ds_client = _FakeDeepSeek(_ENV)
    rd = await claude_service.process_document("Vazifalarni ajrat", [{"type": "text", "text": "1. Banner\n2. Post"}], file_label="reja.xlsx")
    check("process_document (matn): DeepSeek'ga bordi va parse qilindi",
          rd.get("actions") and rd["actions"][0]["data"]["title"] == "Bannerlar tayyorlash")

    # ── streaming → partial + complete ──
    part1 = _ENV[: len(_ENV) // 2]
    part2 = _ENV[len(_ENV) // 2:]
    claude_service._ds_client = _FakeDeepSeek(_ENV, chunks=[part1, part2])
    events = [ev async for ev in claude_service.process_message_stream("Bannerlar")]
    kinds = [e[0] for e in events]
    complete = [e[1] for e in events if e[0] == "complete"]
    check("stream: oxirida ('complete', envelope)", kinds and kinds[-1] == "complete")
    check("stream: complete envelope create_task bilan",
          complete and complete[0].get("actions") and complete[0]["actions"][0]["data"]["title"] == "Bannerlar tayyorlash")

    # ── FIX #1: prose/fence bilan o'ralgan JSON parse qilinadi (DeepSeek odati) ──
    fenced = "Mana natija:\n```json\n" + _ENV + "\n```\nRahmat!"
    pj = claude_service._extract_json(fenced)
    check("prose+fence bilan o'ralgan JSON parse qilindi", pj and pj.get("intent") == "create_task")
    trailing = _ENV + "  — vazifa tayyor."
    tj = claude_service._extract_json(trailing)
    check("JSON + keyingi matn (trailing prose) parse qilindi", tj and tj.get("intent") == "create_task")
    leading = "Xo'p. " + _ENV
    check("oldida matn bo'lgan JSON ham parse qilindi",
          (claude_service._extract_json(leading) or {}).get("intent") == "create_task")
    # process_message: prose-o'ralgan javob ham ishlaydi (endi "Texnik xato" emas)
    claude_service._ds_client = _FakeDeepSeek(fenced)
    rp = await claude_service.process_message("Bannerlar")
    check("process_message: prose-o'ralgan javobdan action ajratildi",
          rp.get("actions") and rp["actions"][0]["data"]["title"] == "Bannerlar tayyorlash")

    # ── FIX #1b: braceli suhbat javobi prose sifatida ko'rinadi (fallback emas) ──
    env = claude_service._envelope_from_raw("Salom! :-} Sizga yordam beraman.")
    check("braceli suhbat javobi prose sifatida (Texnik xato emas)",
          env and "Salom" in (env.get("user_message") or ""))

    # ── FIX #2: _deepseek_model — aniq complexity g'olib (/plan arzon chat'da) ──
    check("_deepseek_model('default', executive_plan) = chat (reasoner EMAS)",
          claude_service._deepseek_model("default", "[INTERNAL] executive_plan") == config.DEEPSEEK_MODEL)
    check("_deepseek_model('fast', ...) = chat",
          claude_service._deepseek_model("fast", None) == config.DEEPSEEK_MODEL)
    check("_deepseek_model('complex', None) = reasoner",
          claude_service._deepseek_model("complex", None) == config.DEEPSEEK_MODEL_COMPLEX)
    check("_deepseek_model(None, executive_plan) = reasoner (complexity berilmaganda kalit-so'z)",
          claude_service._deepseek_model(None, "[INTERNAL] executive_plan") == config.DEEPSEEK_MODEL_COMPLEX)

    # ── xato → _fallback (crash emas) ──
    import openai
    claude_service._ds_client = _FakeDeepSeek(_ENV, raise_exc=openai.APITimeoutError(request=None))
    rf = await claude_service.process_message("test")
    check("DeepSeek xatosi → _fallback (crash yo'q, user_message bor)", isinstance(rf, dict) and "user_message" in rf)

    # ── ovoz transkript-tuzatish ham DeepSeek'ga (gibrid) ──
    import voice_service
    claude_service._ds_client = _FakeDeepSeek("soat 11:30 uchrashuv")
    vc = await voice_service._correct_transcript("soat o'n bir yarim uchrashuv")
    check("_correct_transcript: DeepSeek orqali tuzatildi", vc == "soat 11:30 uchrashuv")
    c2 = sqlite3.connect(config.DATABASE_PATH)
    vrow = c2.execute("SELECT provider FROM llm_audit_log WHERE purpose='voice_transcript_correction' ORDER BY rowid DESC LIMIT 1").fetchone()
    c2.close()
    check("ovoz-tuzatish audit log: provider='deepseek'", vrow and vrow[0] == "deepseek")
    # xato bo'lsa asl matn qaytadi (ovoz yo'qolmaydi)
    claude_service._ds_client = _FakeDeepSeek("x", raise_exc=openai.APITimeoutError(request=None))
    vc2 = await voice_service._correct_transcript("asl transkript matni")
    check("_correct_transcript: xatoda asl matn qaytadi (fail-safe)", vc2 == "asl transkript matni")


try:
    asyncio.run(main())
finally:
    for f in __import__("glob").glob(config.DATABASE_PATH + "*"):
        try:
            os.remove(f)
        except OSError:
            pass

print("\n" + "=" * 48)
print(f"NATIJA:  ✅ {_pass} o'tdi   ❌ {_fail} yiqildi")
print("=" * 48)
import sys  # noqa: E402
sys.exit(1 if _fail else 0)
