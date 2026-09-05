"""
IHSG Trading System — Jurnal Mentor
====================================
Mengubah jurnal harian mentor (teks bebas, singkatan pasar Indonesia)
menjadi level per emiten yang bisa dibandingkan dengan sinyal sistem.

RAHASIA. Jurnal ini bertuliskan permintaan agar tidak disebarkan di luar
grup mentor. Karena itu:
  - data/journal/ seluruhnya masuk .gitignore
  - snapshot yang diterbitkan ke GitHub Pages TIDAK PERNAH memuatnya
    (utils/snapshot.build dipanggil dengan include_journal=False di CI)
  - hanya papan lokal dan alert Telegram pribadi yang memakainya

Pemisahan blok tidak memakai LLM: kode emiten IDX selalu tepat empat
huruf dan di jurnal ditulis sendirian di satu baris. Aturan itu memisah
"Ptba" dari judul bagian seperti "tuyul" (5) atau "Oil" (3) tanpa
menebak. LLM hanya dipakai untuk membaca isi tiap blok.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config import BASE_DIR

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")
JOURNAL_DIR = BASE_DIR / "data" / "journal"
RAW_DIR = JOURNAL_DIR / "raw"

# Kode emiten IDX = tepat 4 huruf, berdiri sendiri di satu baris.
_TICKER_LINE = re.compile(r"^\s*([A-Za-z]{4})\s*$")

# Baris pemisah bagian yang kadang terbaca sebagai isi
_DIVIDER = re.compile(r"^[\s\-—–_=.]*$")


@dataclass
class TickerNote:
    """Pandangan mentor untuk satu emiten."""
    ticker: str
    stance: str = "watch"
    entries: list[list[float]] = field(default_factory=list)   # zona [min, max]
    targets: list[float] = field(default_factory=list)
    stop: Optional[float] = None
    stop_type: str = ""                                        # "ts" | "sl"
    note: str = ""
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "stance": self.stance,
            "entries": self.entries, "targets": self.targets,
            "stop": self.stop, "stop_type": self.stop_type,
            "note": self.note, "raw": self.raw,
        }


# ── Pemisahan blok ────────────────────────────────────────────────────────────

def split_blocks(text: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Pisahkan jurnal jadi (bagian narasi awal, [(kode, isi), ...]).

    Bagian narasi = semua yang muncul sebelum kode emiten pertama; di situ
    letaknya pandangan IHSG, DXY, komoditas, dan alokasi taktis.
    """
    lines = text.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    head: list[str] = []
    current: Optional[str] = None

    for line in lines:
        m = _TICKER_LINE.match(line)
        if m:
            current = m.group(1).upper()
            blocks.append((current, []))
            continue
        if current is None:
            head.append(line)
        else:
            blocks[-1][1].append(line)

    out = []
    for code, body in blocks:
        joined = "\n".join(b for b in body if not _DIVIDER.match(b)).strip()
        if joined:
            out.append((code, joined))
    return "\n".join(head).strip(), out


# ── Pembacaan isi lewat LLM ───────────────────────────────────────────────────

_GLOSSARY = """\
Singkatan pasar Indonesia yang dipakai di jurnal ini:
  rute        = target harga berikutnya (bisa beberapa, dipisah "/")
  hit         = target itu sudah tercapai
  next        = target lanjutan setelah yang sudah hit
  bb          = buyback, beli lagi setelah sebelumnya jual
  tp          = take profit, sudah jual di target
  ts          = trailing stop (stop yang dinaikkan mengikuti harga)
  sl          = stop loss (batas rugi tetap)
  jaga        = pasang/pertahankan stop di angka itu
  entry       = harga beli awal; "entry 2" = beli tahap kedua
  nambah      = tambah posisi
  manjat      = beli mengikuti harga yang sedang naik
  avg up      = average up, menambah di harga lebih tinggi
  bo          = breakout
  ihns        = inverse head and shoulders
  cicil       = beli bertahap
  reentry     = masuk lagi setelah kena stop
  dl          = dulu;  lg = lagi;  msh = masih;  sdh = sudah;  yg = yang
  d / di bwh  = di bawah;  d atas = di atas;  upto = sampai
Angka ribuan sering ditulis singkat memakai titik desimal: pada saham
yang harganya puluhan ribu rupiah, "12.5" berarti 12.500 dan "13.2"
berarti 13.200. Kalau satu blok memakai gaya itu, kembalikan angka
penuhnya (12500, 13200), bukan 12,5.
"""

_SYSTEM = f"""\
Kamu membaca catatan trading harian berbahasa Indonesia dan mengubahnya
menjadi data terstruktur. Kembalikan HANYA JSON array valid, tanpa
markdown, tanpa penjelasan.

{_GLOSSARY}

Untuk SETIAP blok yang diberikan, hasilkan satu objek:
{{
  "ticker": "<kode 4 huruf kapital>",
  "stance": "<buy|buyback|add|hold|watch|exit>",
  "entries": [[min, max]],
  "targets": [<angka>, ...],
  "stop": <angka atau null>,
  "stop_type": "<ts|sl|>",
  "note": "<ringkasan 1 kalimat bahasa Indonesia>"
}}

ATURAN PALING PENTING — bedakan harga BELI dari harga TARGET:

- Angka menjadi TARGET hanya kalau didahului kata: "rute", "next",
  "target", atau "siap ke". Contoh: "rute 106/112" -> targets [106, 112].
- Angka menjadi ENTRY kalau didahului kata beli: "bb", "buyback", "beli",
  "buy", "entry", "nambah", "manjat", "cicil", "avg up", "reentry",
  "makan", "area". Contoh: "Bb 1000-1050" -> entries [[1000, 1050]],
  BUKAN targets. Ini kesalahan yang paling sering terjadi — jangan ulangi.
- Satu blok bisa punya beberapa zona beli: "Bb 1000-1050 siap nambah di
  940-960" -> entries [[1000,1050],[940,960]], targets [].
- Target yang ditandai "hit" sudah tercapai; jangan dimasukkan lagi.
  "rute 480 hit next 500/520" -> targets [500, 520].

Aturan lain:
- Harga tunggal ditulis [x, x]. Rentang "1490-1520" -> [1490, 1520].
  Kalau tidak ada zona beli, kosongkan [].
- "stop" ambil dari ts/sl/jaga/penjagaan/lebarkan. Kalau tidak ada, null.
- "stance": "buy" kalau ajakan beli baru, "buyback" kalau menunggu beli
  lagi setelah tp, "add" kalau menambah posisi yang sudah ada, "hold"
  kalau hanya menjaga stop, "watch" kalau belum ada aksi.
- Semua angka berupa number, bukan string. Jangan mengarang angka yang
  tidak tertulis.

Contoh lengkap (kode dan angka di bawah ini rekaan, hanya untuk menunjukkan
bentuk keluaran — jangan dianggap data nyata):

Masukan  ### ABCD
         Bb 1000-1050 siap nambah di 940-960
Keluaran {{"ticker":"ABCD","stance":"buyback","entries":[[1000,1050],[940,960]],
          "targets":[],"stop":null,"stop_type":"","note":"Buyback 1000-1050, tambah lagi di 940-960."}}

Masukan  ### EFGH
         rute 500/520/540, yg msh punya jaga ts 480, yg sdh tp bisa bb max 470 until 450
Keluaran {{"ticker":"EFGH","stance":"hold","entries":[[450,470]],
          "targets":[500,520,540],"stop":480,"stop_type":"ts",
          "note":"Tahan dengan ts 480; yang sudah tp bisa buyback 450-470."}}
"""


def _parse_batch(blocks: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Baca satu batch blok lewat LLM."""
    from agents.base_agent import BaseAgent

    class _Reader(BaseAgent):
        def analyze(self, *a: Any, **k: Any) -> dict[str, Any]:
            return {}

    reader = _Reader()
    reader.max_tokens = 4096

    payload = "\n\n".join(f"### {code}\n{body}" for code, body in blocks)
    raw = reader.call_claude(_SYSTEM, payload)

    clean = raw.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:-1]).strip()
    start, end = clean.find("["), clean.rfind("]")
    if start == -1 or end <= start:
        logger.warning(f"[jurnal] batch tidak menghasilkan array: {raw[:160]}")
        return []
    try:
        data = json.loads(clean[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.warning(f"[jurnal] JSON batch gagal: {exc}")
        return []
    return data if isinstance(data, list) else []


def parse_tickers(
    blocks: list[tuple[str, str]], batch_size: int = 8
) -> list[TickerNote]:
    """
    Baca semua blok emiten, dibagi beberapa batch.

    Batch kecil bukan soal panjang keluaran saja: Groq juga membatasi token
    per menit, dan batch berisi 12 blok cukup untuk membuat satu batch
    gagal seluruhnya. Batch yang kosong dicoba ulang sekali dengan jeda.
    """
    import time as _time

    raw_by_code = {code: body for code, body in blocks}
    notes: list[TickerNote] = []
    seen: set[str] = set()

    for i in range(0, len(blocks), batch_size):
        batch = blocks[i : i + batch_size]
        logger.info(
            f"[jurnal] batch {i // batch_size + 1}: "
            f"{', '.join(c for c, _ in batch)}"
        )
        rows = _parse_batch(batch)
        if not rows:
            logger.warning("[jurnal] batch kosong — coba ulang setelah 20 detik")
            _time.sleep(20)
            rows = _parse_batch(batch)

        for row in rows:
            code = str(row.get("ticker", "")).upper().strip()
            if not code or code in seen:
                continue
            seen.add(code)

            entries = []
            for e in row.get("entries") or []:
                if isinstance(e, (int, float)):
                    entries.append([float(e), float(e)])
                elif isinstance(e, list) and len(e) == 2:
                    lo, hi = float(e[0]), float(e[1])
                    entries.append([min(lo, hi), max(lo, hi)])

            targets = [
                float(t) for t in (row.get("targets") or [])
                if isinstance(t, (int, float))
            ]
            stop = row.get("stop")
            notes.append(TickerNote(
                ticker=code,
                stance=str(row.get("stance", "watch")).lower(),
                entries=entries,
                targets=sorted(targets),
                stop=float(stop) if isinstance(stop, (int, float)) else None,
                stop_type=str(row.get("stop_type", "")).lower(),
                note=str(row.get("note", "")).strip(),
                raw=raw_by_code.get(code, ""),
            ))

    missing = [c for c, _ in blocks if c not in seen]
    if missing:
        logger.warning(f"[jurnal] {len(missing)} blok tidak terbaca: {missing}")
    return notes


_MACRO_SYSTEM = """\
Kamu membaca bagian pembuka catatan trading harian berbahasa Indonesia.
Kembalikan HANYA JSON valid, tanpa markdown.

SELURUH ISI JAWABAN WAJIB BERBAHASA INDONESIA. Jangan menerjemahkan ke
bahasa Inggris — sumbernya berbahasa Indonesia dan pembacanya juga.

{
  "ihsg": "<pandangan indeks dalam 1-2 kalimat>",
  "levels": {"support": [<angka>], "resistance": [<angka>]},
  "stance": ["<alokasi taktis 1>", "<alokasi taktis 2>"],
  "overweight": ["<sektor yang ditambah>"],
  "currency": "<pandangan DXY / USD-IDR dalam 1 kalimat>",
  "commodities": ["<pandangan komoditas per baris>"],
  "watch": ["<agenda/tanggal yang perlu dipantau>"]
}

Ringkas dan faktual. Jangan menambah pandangan yang tidak tertulis.
"""


def parse_macro(head: str) -> dict[str, Any]:
    from agents.base_agent import BaseAgent

    class _Reader(BaseAgent):
        def analyze(self, *a: Any, **k: Any) -> dict[str, Any]:
            return {}

    reader = _Reader()
    reader.max_tokens = 2048
    return reader.call_claude_json(_MACRO_SYSTEM, head, fallback={})


# ── Simpan & muat ─────────────────────────────────────────────────────────────

def journal_path(date_str: str) -> Path:
    return JOURNAL_DIR / f"{date_str}.json"


def ingest(text: str, date_str: Optional[str] = None) -> dict[str, Any]:
    """Teks jurnal mentah -> struktur tersimpan."""
    date_str = date_str or datetime.now(WIB).strftime("%Y-%m-%d")
    head, blocks = split_blocks(text)
    logger.info(f"[jurnal] {len(blocks)} blok emiten terdeteksi")

    notes = parse_tickers(blocks)
    macro = parse_macro(head) if head else {}

    data = {
        "date": date_str,
        "ingested_at": datetime.now(WIB).isoformat(),
        "macro": macro,
        "tickers": {n.ticker: n.as_dict() for n in notes},
    }
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    journal_path(date_str).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    logger.info(
        f"[jurnal] {date_str}: {len(notes)} emiten tersimpan ke {journal_path(date_str)}"
    )
    return data


def load_latest(max_age_days: int = 5) -> Optional[dict[str, Any]]:
    """Jurnal terbaru yang belum kedaluwarsa, atau None."""
    if not JOURNAL_DIR.exists():
        return None
    files = sorted(JOURNAL_DIR.glob("20*.json"), reverse=True)
    if not files:
        return None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[jurnal] gagal memuat {files[0]}: {exc}")
        return None

    try:
        age = (datetime.now(WIB).date()
               - datetime.strptime(data["date"], "%Y-%m-%d").date()).days
    except Exception:
        age = 0
    if age > max_age_days:
        logger.info(f"[jurnal] {data['date']} sudah {age} hari — dilewati")
        return None
    data["age_days"] = age
    return data


# ── Perbandingan dengan harga sekarang ────────────────────────────────────────

def check_triggers(
    note: dict[str, Any], price: float, prev_close: Optional[float] = None
) -> list[dict[str, Any]]:
    """
    Kejadian yang layak dikabarkan pada rencana mentor untuk satu emiten.

    Mentor memberi level; sistem yang menungguinya sepanjang jam pasar.
    Tiga hal yang dipantau:
      - harga MASUK zona beli   -> kesempatan yang disebut mentor terbuka
      - harga MENYENTUH target  -> saatnya mempertimbangkan realisasi
      - harga JEBOL stop        -> rencana mentor batal, jangan didiamkan

    `prev_close` dipakai supaya yang dilaporkan adalah PERLINTASAN, bukan
    keadaan. Tanpa itu, saham yang seharian diam di dalam zona beli akan
    memicu kabar yang sama tiap kali scan berjalan.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(price, (int, float)) or price <= 0:
        return out

    def crossed_into(lo: float, hi: float) -> bool:
        if not (lo <= price <= hi):
            return False
        if prev_close is None:
            return True
        return not (lo <= prev_close <= hi)

    for lo, hi in note.get("entries") or []:
        if crossed_into(float(lo), float(hi)):
            out.append({
                "kind": "zona_beli",
                "text": f"masuk zona beli {lo:,.0f}–{hi:,.0f}" if lo != hi
                        else f"menyentuh harga beli {lo:,.0f}",
                "level": lo,
            })
            break

    for t in sorted(note.get("targets") or []):
        t = float(t)
        if price >= t and (prev_close is None or prev_close < t):
            out.append({
                "kind": "target",
                "text": f"menyentuh target {t:,.0f}",
                "level": t,
            })
            break

    stop = note.get("stop")
    if isinstance(stop, (int, float)):
        stop = float(stop)
        if price <= stop and (prev_close is None or prev_close > stop):
            out.append({
                "kind": "stop",
                "text": f"jebol {note.get('stop_type') or 'stop'} {stop:,.0f}",
                "level": stop,
            })

    return out


def evaluate(note: dict[str, Any], price: float) -> dict[str, Any]:
    """
    Posisi harga sekarang terhadap level mentor.

    Mengembalikan zona ("di zona beli" / "di atas target" / dst), target
    terdekat di atas harga, jarak ke stop, dan rasio risk/reward versi
    mentor — supaya bisa disandingkan dengan R:R versi sistem.
    """
    entries = note.get("entries") or []
    targets = sorted(note.get("targets") or [])
    stop = note.get("stop")

    in_entry = any(lo <= price <= hi for lo, hi in entries)
    next_target = next((t for t in targets if t > price), None)
    all_hit = bool(targets) and next_target is None

    if stop is not None and price <= stop:
        zone = "di bawah stop"
    elif in_entry:
        zone = "di zona beli"
    elif all_hit:
        zone = "di atas semua target"
    elif entries and price < min(lo for lo, _ in entries):
        zone = "di bawah zona beli"
    elif entries and price > max(hi for _, hi in entries):
        zone = "di atas zona beli"
    else:
        zone = "netral"

    risk_pct = (price - stop) / price * 100 if stop and price > 0 else None
    reward_pct = (next_target - price) / price * 100 if next_target and price > 0 else None
    rr = (reward_pct / risk_pct) if (risk_pct and reward_pct and risk_pct > 0) else None

    return {
        "zone": zone,
        "in_entry": in_entry,
        "next_target": next_target,
        "stop": stop,
        "stop_type": note.get("stop_type", ""),
        "risk_pct": round(risk_pct, 2) if risk_pct is not None else None,
        "reward_pct": round(reward_pct, 2) if reward_pct is not None else None,
        "rr": round(rr, 2) if rr is not None else None,
    }
