"""Rasmiy bayonnoma hujjat generatori — Agrobank korporativ shabloni.

Word (.docx) va PDF chiqishini bitta strukturalangan `fields` dict'idan chizadi.
Lotin/Kiril — `script` parametri orqali (translit.py).

Dizayn (foydalanuvchi shabloni "Bayonnoma shabloni.docx"):
  - Korporativ ko'k #1F3864 (sarlavhalar, jadval boshi, № ustun, imzo yorliqlari)
  - Och-ko'k zebra #F2F5FB (topshiriqlar jadvali navbatma-navbat qatorlari)
  - Kulrang #595959 / #808080 (boshqarma subtitle, izoh, "imzo")
  - Arial, US Letter (chap 2.54 / o'ng 1.3 sm)
  - Tuzilma: shapka → joy/sana → Mavzu → Ishtirokchilar → Kun tartibi →
    topshiriqlar jadvali → imzolar → "Bayonnomani tuzdi"

handlers.py minimal o'zgartirilsin (race-ni kamaytirish) — bu modul mustaqil:
faqat `translit`ga bog'liq; topshiriqlar ro'yxati tashqarida ajratiladi va
`tasks` orqali uzatiladi (har biri {assignee, title, deadline}).
"""

from __future__ import annotations

import io

import translit

# ── Palette ──
NAVY = "1F3864"
ZEBRA = "F2F5FB"
GRAY = "595959"
GRAY2 = "808080"
WHITE = "FFFFFF"

_UZ_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun",
              "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]

# Sozlamalar (database.get_settings()) bilan almashtiriladigan standart qiymatlar.
DEFAULTS = {
    "org_name": '"AGROBANK" ATB',
    "org_dept": "Marketing boshqarmasi",
    "org_place": "Toshkent shahri",
    "protocol_author": "Maqsud Rustamov",
    "protocol_subtitle": "(ish uchrashuvi)",
}


def _fmt_deadline(iso) -> str:
    """Jadval 'Muddat' ustuni: 'DD-oy' yoki '—' (noma'lum)."""
    if not iso:
        return "—"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(iso))
        return f"{dt.day}-{_UZ_MONTHS[dt.month - 1]}"
    except (ValueError, TypeError, IndexError):
        return str(iso)


def build_fields(meeting: dict | None, protocol_text: str, tasks, settings: dict | None = None) -> dict:
    """Meeting + settings + (oldindan ajratilgan) tasks → strukturalangan maydonlar.

    `tasks` — har biri {assignee, title, deadline} bo'lgan ro'yxat (handlers tomonidan
    `_proto_tasks_from_actions` / `_proto_tasks_from_text` orqali ajratiladi)."""
    from datetime import datetime
    s = settings or {}
    m = meeting or {}

    def cfg(key: str) -> str:
        v = s.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        return DEFAULTS.get(key, "")

    date_str = ""
    ds = m.get("datetime_start")
    if ds:
        try:
            dt = datetime.fromisoformat(str(ds))
            date_str = f"{dt.year}-yil {dt.day}-{_UZ_MONTHS[dt.month - 1]}"
        except (ValueError, TypeError, IndexError):
            date_str = ""

    participants = [str(p).strip() for p in (m.get("participants") or []) if str(p).strip()]

    norm_tasks = []
    for t in (tasks or []):
        norm_tasks.append({
            "assignee": (t.get("assignee") or "").strip() or "—",
            "title": (t.get("title") or "").strip() or "—",
            "deadline": t.get("deadline"),
        })

    return {
        "org_name": cfg("org_name"),
        "org_dept": cfg("org_dept"),
        "subtitle": cfg("protocol_subtitle"),
        "place": cfg("org_place"),
        "date_str": date_str,
        "mavzu": (m.get("title") or "").strip(),
        "participants": participants,
        "agenda": (m.get("agenda") or "").strip(),
        "tasks": norm_tasks,
        "author": cfg("protocol_author"),
    }


_HEADERS = ["№", "Topshiriq mazmuni", "Mas'ul shaxs(lar)", "Muddat"]
_IZOH = ('Izoh: "—" bilan belgilangan kataklardagi mas\'ul shaxslar va muddatlar '
         "uchrashuv yakunida aniqlashtirilib, to'ldiriladi.")


def build_docx(fields: dict, script: str = "latin") -> bytes:
    """Agrobank korporativ bayonnomasini Word (.docx) bayt sifatida qaytaradi."""
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    def tr(text: str) -> str:
        return translit.transliterate(text or "", script)

    def rgb(h: str) -> RGBColor:
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    c_navy, c_gray, c_gray2, c_white = rgb(NAVY), rgb(GRAY), rgb(GRAY2), rgb(WHITE)

    def shade(cell, fill: str) -> None:
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        tcPr.append(shd)

    def no_borders(tbl) -> None:
        tblPr = tbl._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{edge}")
            e.set(qn("w:val"), "none")
            e.set(qn("w:sz"), "0")
            borders.append(e)
        tblPr.append(borders)

    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.59)
    sec.page_height = Cm(27.94)  # US Letter
    sec.left_margin = Cm(2.54)
    sec.right_margin = Cm(1.3)
    sec.top_margin = Cm(2.54)
    sec.bottom_margin = Cm(2.54)
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(11)
    _rf = normal.element.get_or_add_rPr().get_or_add_rFonts()
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        _rf.set(qn(a), "Arial")

    C, J, R = WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.JUSTIFY, WD_ALIGN_PARAGRAPH.RIGHT

    def para(align=None, before=4, after=4, indent=None):
        p = doc.add_paragraph()
        if align is not None:
            p.alignment = align
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        if indent is not None:
            p.paragraph_format.left_indent = Cm(indent)
        return p

    def run(p, text, size=11, bold=False, italic=False, color=None):
        r = p.add_run(tr(text))
        r.bold = bold
        r.italic = italic
        r.font.name = "Arial"
        r.font.size = Pt(size)
        r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:cs"), "Arial")
        if color is not None:
            r.font.color.rgb = color
        return r

    # ── Shapka ──
    run(para(C, before=0, after=0), fields.get("org_name") or "", size=13, bold=True, color=c_navy)
    if fields.get("org_dept"):
        run(para(C, before=0, after=0), fields["org_dept"], size=10, color=c_gray)
    run(para(C, before=8, after=0), "BAYONNOMA № ____", size=15, bold=True)
    if fields.get("subtitle"):
        run(para(C, before=0, after=6), fields["subtitle"], size=10, italic=True, color=c_gray)

    # ── Joy / sana (chegrasiz 2 ustun) ──
    pd = doc.add_table(rows=1, cols=2)
    no_borders(pd)
    lc, rc = pd.rows[0].cells
    lc.width = Cm(8.25)
    rc.width = Cm(8.25)
    lc.paragraphs[0].text = ""
    run(lc.paragraphs[0], fields.get("place") or "")
    rp = rc.paragraphs[0]
    rp.text = ""
    rp.alignment = R
    run(rp, fields.get("date_str") or "")

    # ── Mavzu ──
    if fields.get("mavzu"):
        p = para(J, before=8)
        run(p, "Mavzu: ", bold=True)
        run(p, fields["mavzu"])

    # ── Ishtirokchilar ──
    if fields.get("participants"):
        run(para(J, before=8, after=2), "Uchrashuv ishtirokchilari:", bold=True, color=c_navy)
        for nm in fields["participants"]:
            run(para(J, before=0, after=0, indent=0.5), f"—  {nm}")

    # ── Kun tartibi ──
    if fields.get("agenda"):
        run(para(J, before=8, after=2), "Kun tartibi:", bold=True, color=c_navy)
        run(para(J), fields["agenda"])

    # ── Topshiriqlar ──
    run(para(J, before=8, after=2), "Muhokama qilindi va quyidagi topshiriqlar belgilandi:",
        bold=True, color=c_navy)
    run(para(J, before=0, after=4), _IZOH, size=9, italic=True, color=c_gray2)

    tasks = fields.get("tasks") or []
    tbl = doc.add_table(rows=1, cols=4)
    try:
        tbl.style = "Table Grid"
    except KeyError:
        pass
    widths = [Cm(1.2), Cm(9.55), Cm(3.25), Cm(3.75)]
    hdr = tbl.rows[0].cells
    for i, h in enumerate(_HEADERS):
        shade(hdr[i], NAVY)
        hp = hdr[i].paragraphs[0]
        hp.text = ""
        hp.alignment = C
        run(hp, h, size=10, bold=True, color=c_white)
    for idx, t in enumerate(tasks, 1):
        cells = tbl.add_row().cells
        if idx % 2 == 0:
            for cc in cells:
                shade(cc, ZEBRA)
        vals = [str(idx), t.get("title") or "—", t.get("assignee") or "—", _fmt_deadline(t.get("deadline"))]
        for i, v in enumerate(vals):
            cp = cells[i].paragraphs[0]
            cp.text = ""
            if i == 0:
                cp.alignment = C
                run(cp, v, size=10, bold=True, color=c_navy)
            else:
                run(cp, v, size=10)
    for row in tbl.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = w

    # ── Imzolar ──
    if fields.get("participants"):
        run(para(J, before=10, after=2), "Ishtirokchilar imzosi:", bold=True, color=c_navy)
        sg = doc.add_table(rows=0, cols=2)
        no_borders(sg)
        for nm in fields["participants"]:
            cells = sg.add_row().cells
            cells[0].width = Cm(9.88)
            cells[1].width = Cm(6.63)
            cells[0].paragraphs[0].text = ""
            run(cells[0].paragraphs[0], nm)
            sp = cells[1].paragraphs[0]
            sp.text = ""
            sp.alignment = R
            run(sp, "______________  imzo", size=9, color=c_gray2)

    # ── Bayonnomani tuzdi ──
    p = para(J, before=12)
    run(p, "Bayonnomani tuzdi: ", bold=True)
    run(p, (fields.get("author") or "") + "  _______________________")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
