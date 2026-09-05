"""
IHSG Trading System — Watchlist yang Tumbuh dari Jurnal Mentor
===============================================================
Daftar 58 saham di sistem ini sejak awal berasal dari jurnal mentor.
Modul ini membuat kaitan itu hidup: setiap jurnal baru dimasukkan, emiten
yang belum ada di watchlist diuji dan ditambahkan sendiri.

Sumber kebenarannya `data/watchlist.json` — BUKAN daftar di config.py.
Berkas itu sengaja TIDAK di-gitignore: GitHub Actions perlu membacanya,
jadi ia harus ikut ter-commit. `config.py` membacanya dan jatuh ke daftar
bawaan bila berkasnya belum ada (clone baru, sebelum sinkron pertama).

Yang TIDAK dilakukan: menghapus otomatis. Mentor tidak menyebut sebuah
emiten hari ini bukan berarti emiten itu dibuang — bisa saja memang tidak
ada yang perlu dikatakan. Yang dilakukan hanya mencatat kapan terakhir
disebut, lalu melaporkannya supaya keputusan buang tetap di tangan user.

Isi berkasnya hanya KODE saham. Level, target, dan stop dari mentor tetap
di data/journal/ yang dirahasiakan dan tidak pernah ikut ke repo.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = BASE_DIR / "data" / "watchlist.json"

# Ambang kelayakan sebuah kode boleh masuk watchlist
MIN_BARS = 40          # minimal hari bursa dengan harga dalam 3 bulan terakhir
MIN_AVG_VOLUME = 50_000   # rata-rata volume 20 hari; menyaring saham tidur


def _today() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d")


# ── Baca & tulis ──────────────────────────────────────────────────────────────

def load_raw() -> dict[str, Any]:
    if not WATCHLIST_PATH.exists():
        return {}
    try:
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[watchlist] gagal membaca {WATCHLIST_PATH}: {exc}")
        return {}


def load(fallback: Optional[list[str]] = None) -> list[str]:
    """Daftar ticker siap pakai ('PGAS.JK'). Jatuh ke `fallback` bila kosong."""
    data = load_raw()
    rows = data.get("tickers") or []
    codes = [r["code"] for r in rows if isinstance(r, dict) and r.get("code")]
    if not codes:
        return list(fallback or [])
    return [f"{c}.JK" for c in codes]


def save(rows: list[dict[str, Any]]) -> None:
    rows = sorted(rows, key=lambda r: r["code"])
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(
        json.dumps(
            {"updated": datetime.now(WIB).isoformat(), "tickers": rows},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )


def seed(codes: list[str]) -> list[dict[str, Any]]:
    """Bangun berkas pertama kali dari daftar bawaan config.py."""
    today = _today()
    rows = [
        {"code": c.replace(".JK", ""), "added": today,
         "source": "awal", "last_journal": None}
        for c in codes
    ]
    save(rows)
    logger.info(f"[watchlist] dibuat dengan {len(rows)} emiten")
    return rows


# ── Kelayakan ─────────────────────────────────────────────────────────────────

def is_tradeable(code: str) -> tuple[bool, str]:
    """
    Apakah kode ini benar-benar saham IDX yang bisa ditransaksikan?

    Jurnal ditulis manusia: bisa ada salah ketik, kode lama, atau singkatan
    yang kebetulan empat huruf. Satu-satunya uji yang jujur adalah mencoba
    mengambil harganya.
    """
    try:
        import yfinance as yf
        h = yf.Ticker(f"{code}.JK").history(period="3mo", auto_adjust=True)
        h = h[h["Close"].notna()]
    except Exception as exc:
        return False, f"gagal ambil data ({type(exc).__name__})"

    if len(h) < MIN_BARS:
        return False, f"riwayat cuma {len(h)} hari bursa"

    vol = float(h["Volume"].tail(20).mean())
    if vol < MIN_AVG_VOLUME:
        return False, f"volume rata-rata {vol:,.0f} — terlalu sepi"

    return True, f"{len(h)} bar, volume {vol:,.0f}"


# ── Sinkronisasi dari jurnal ──────────────────────────────────────────────────

def sync_from_journal(
    journal: dict[str, Any], auto_add: bool = True
) -> dict[str, Any]:
    """
    Cocokkan watchlist dengan emiten yang dibahas jurnal.

    Mengembalikan laporan: apa yang ditambahkan, apa yang ditolak beserta
    alasannya, dan emiten watchlist yang sudah lama tidak disebut.
    """
    from config import DEFAULT_TICKERS_BUILTIN

    rows = load_raw().get("tickers") or []
    if not rows:
        rows = seed(DEFAULT_TICKERS_BUILTIN)

    by_code = {r["code"]: r for r in rows}
    jdate = journal.get("date") or _today()
    jcodes = sorted(journal.get("tickers") or {})

    added, rejected = [], []
    for code in jcodes:
        if code in by_code:
            by_code[code]["last_journal"] = jdate
            continue
        if not auto_add:
            rejected.append((code, "penambahan otomatis dimatikan"))
            continue

        ok, why = is_tradeable(code)
        if ok:
            by_code[code] = {
                "code": code, "added": _today(),
                "source": f"jurnal {jdate}", "last_journal": jdate,
            }
            added.append((code, why))
            logger.info(f"[watchlist] + {code} ({why})")
        else:
            rejected.append((code, why))
            logger.info(f"[watchlist] tolak {code}: {why}")

    # Emiten watchlist yang tidak disebut jurnal ini — dicatat, tidak dibuang
    stale = []
    for r in by_code.values():
        if r["code"] in journal.get("tickers", {}):
            continue
        last = r.get("last_journal")
        if last:
            days = (datetime.strptime(jdate, "%Y-%m-%d")
                    - datetime.strptime(last, "%Y-%m-%d")).days
            if days >= 14:
                stale.append((r["code"], days))

    if added:
        save(list(by_code.values()))

    return {
        "added": added,
        "rejected": rejected,
        "stale": sorted(stale, key=lambda x: -x[1]),
        "total": len(by_code),
    }
