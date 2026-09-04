"""
IHSG Trading System — Snapshot untuk Papan Web
===============================================
Merakit SATU berkas JSON yang menjadi seluruh isi halaman web, lalu
menuliskannya ke web/data/dashboard.json. Halaman web murni statis dan
hanya membaca berkas ini — tidak ada server aplikasi yang perlu dijaga.

Dijalankan lewat `python run_job.py --job dashboard`, dan di GitHub Actions
hasilnya langsung diterbitkan ke GitHub Pages tanpa commit apa pun.

Status workflow diambil dari GitHub REST API supaya halaman menampilkan
kapan tiap agent BENAR-BENAR terakhir jalan. Itu penting: sistem ini pernah
mati 17 hari tanpa ada yang tahu, karena setiap run tetap berstatus sukses.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config import BASE_DIR, DEFAULT_TICKERS

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")
OUT_PATH = BASE_DIR / "web" / "data" / "dashboard.json"

# Saham yang tampil di tape berjalan
TAPE_TICKERS = [
    "PTBA.JK", "ANTM.JK", "BBCA.JK", "ADRO.JK", "MEDC.JK", "INCO.JK",
    "ASII.JK", "TLKM.JK", "UNVR.JK", "ELSA.JK", "LSIP.JK", "AADI.JK",
]

# Hasil backtest_honest.py — out-of-sample, sudah dipotong biaya transaksi.
# Perbarui angkanya bila backtest dijalankan ulang.
BACKTEST = [
    {"name": "Consol Breakout", "n": 80,   "wr": 46, "exp": -0.60, "pf": 0.74, "base": False},
    {"name": "Buy on Weakness", "n": 74,   "wr": 27, "exp":  1.65, "pf": 1.85, "base": False},
    {"name": "Entry acak",      "n": 6508, "wr": 41, "exp": -0.28, "pf": 0.88, "base": True},
]
BACKTEST_PERIOD = "Apr 2024 – Sep 2026 · 58 saham"

# Ulasan naratif BARU yang boleh ditulis dalam satu run.
# Lihat catatan di build_fundamental_scan.
MAX_NEW_REVIEWS = 30

# Jadwal cron tiap workflow, untuk menghitung "berikutnya"
SCHEDULE = {
    "technical.yml":   {"label": "Technical + Volume", "hint": "tiap 30 mnt · 09:00–15:30"},
    "sentiment.yml":   {"label": "News Sentiment",     "hint": "tiap jam · 09:00–16:00"},
    "macro.yml":       {"label": "Macro",              "hint": "harian · 08:00"},
    "fundamental.yml": {"label": "Fundamental",        "hint": "Senin · 07:30"},
    "supervisor.yml":  {"label": "Supervisor",         "hint": "harian · 15:50"},
    "dashboard.yml":   {"label": "Papan web",          "hint": "tiap 30 mnt"},
    "keepalive.yml":   {"label": "Keepalive",          "hint": "bulanan"},
}


def _now() -> datetime:
    return datetime.now(WIB)


def _is_market_open(now: datetime | None = None) -> bool:
    n = now or _now()
    if n.weekday() >= 5:
        return False
    return n.replace(hour=9, minute=0) <= n <= n.replace(hour=16, minute=0)


def _rel_time(ts: float | None) -> str:
    """Unix timestamp -> '11 mnt' / '3 jam' / '2 hari'."""
    if not ts:
        return ""
    mins = int((time.time() - ts) // 60)
    if mins < 1:
        return "baru saja"
    if mins < 60:
        return f"{mins} mnt"
    if mins < 1440:
        return f"{mins // 60} jam"
    return f"{mins // 1440} hari"


# ── Bagian-bagian snapshot ────────────────────────────────────────────────────

def build_index() -> dict[str, Any]:
    """Level IHSG + 45 bar terakhir untuk sparkline + trend/RSI."""
    import yfinance as yf
    import pandas as pd
    from utils.technical_calculator import calculate_technical_data

    df = yf.Ticker("^JKSE").history(period="4mo", auto_adjust=True)
    if df.empty:
        return {}
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    # Buang baris hari yang belum dibuka (OHLC NaN) — lihat catatan yang
    # sama di utils/data_fetcher.fetch_stock_data.
    df = df[df["Close"].notna()]
    if len(df) < 2:
        return {}

    closes = df["Close"].dropna()
    as_of = closes.index[-1]
    source = "Yahoo"

    # Feed ^JKSE Yahoo kerap tertinggal satu sesi sementara saham
    # penyusunnya sudah ter-update, sehingga papan menampilkan indeks
    # kemarin di sebelah harga hari ini. TradingView dipakai sebagai
    # sumber level terkini; kalau angkanya memang lebih baru, ia
    # disambungkan ke ujung deret SEBELUM indikator dihitung.
    from utils.tradingview_ta import get_index_quote
    live = get_index_quote()
    if live and abs(live["close"] - float(closes.iloc[-1])) > 0.01:
        nxt = as_of + pd.Timedelta(days=1)
        row = df.iloc[[-1]].copy()
        row.index = [nxt]
        for col in ("Open", "High", "Low", "Close"):
            if col in row.columns:
                row.loc[nxt, col] = live["close"]
        df = pd.concat([df, row])
        closes = df["Close"].dropna()
        as_of, source = nxt, "TradingView"
        logger.info(
            f"[snapshot] ^JKSE Yahoo tertinggal — memakai TradingView "
            f"{live['close']:,.2f} ({live['change_pct']:+.2f}%)"
        )

    td = calculate_technical_data("^JKSE", df)
    last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])

    return {
        "last": round(last, 2),
        "prev": round(prev, 2),
        "change": round(last - prev, 2),
        "change_pct": round((last - prev) / prev * 100, 2),
        "closes": [round(float(x), 2) for x in closes.tail(45)],
        "dates": [d.strftime("%d %b") for d in closes.index[-45:]],
        "trend": td.trend,
        "rsi": round(td.rsi_14, 1),
        "as_of": as_of.strftime("%d %b"),
        "source": source,
    }


# ── Pasar global ──────────────────────────────────────────────────────────────
# Dipilih mengikuti apa yang benar-benar menggerakkan IHSG dan yang dibahas
# jurnal mentor: kurs, dolar, minyak, emas, plus tiga komoditas ekspor utama.
# Yang punya riwayat di Yahoo dapat sparkline; batu bara, CPO, dan nikel
# tidak ada di Yahoo sehingga diambil dari TradingView — hanya angka.
MARKETS_YF = [
    {"name": "USD/IDR",  "sym": "IDR=X",      "dec": 0, "invert": True},
    {"name": "DXY",      "sym": "DX-Y.NYB",   "dec": 2, "invert": True},
    {"name": "Brent",    "sym": "BZ=F",       "dec": 2, "invert": False},
    {"name": "Emas",     "sym": "GC=F",       "dec": 0, "invert": False},
]
MARKETS_TV = [
    {"name": "Batu bara", "sym": "ICEEUR:ATW1!", "dec": 2},
    {"name": "CPO",       "sym": "MYX:FCPO1!",   "dec": 0},
    {"name": "Nikel",     "sym": "LME:NI1!",     "dec": 0},
]


def build_markets() -> list[dict[str, Any]]:
    """
    Pasar global yang memengaruhi IHSG.

    `invert` menandai instrumen yang kenaikannya justru menekan IHSG
    (rupiah melemah, dolar menguat) supaya warnanya tidak menyesatkan:
    di papan ini hijau selalu berarti "bagus untuk IHSG", bukan "naik".
    """
    import yfinance as yf
    from utils.tradingview_ta import get_quotes

    out: list[dict[str, Any]] = []

    for m in MARKETS_YF:
        try:
            h = yf.Ticker(m["sym"]).history(period="3mo", auto_adjust=True)
            h = h[h["Close"].notna()]
            if len(h) < 2:
                continue
            c = h["Close"]
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            out.append({
                "name": m["name"],
                "last": round(last, m["dec"]),
                "change_pct": round((last - prev) / prev * 100, 2),
                "dec": m["dec"],
                "invert": m["invert"],
                "spark": [round(float(x), 4) for x in c.tail(30)],
                "as_of": h.index[-1].strftime("%d %b"),
            })
        except Exception as exc:
            logger.debug(f"[snapshot] market {m['sym']}: {exc}")

    quotes = get_quotes([m["sym"] for m in MARKETS_TV])
    for m in MARKETS_TV:
        q = quotes.get(m["sym"])
        if not q:
            continue
        out.append({
            "name": m["name"],
            "last": round(q["close"], m["dec"]),
            "change_pct": round(q["change_pct"], 2),
            "dec": m["dec"],
            "invert": False,
            "spark": [],          # TradingView scanner tidak memberi riwayat
            "as_of": "",
        })

    logger.info(f"[snapshot] pasar global: {len(out)} instrumen")
    return out


# ── Fundamental: rasio, skor, dan ulasan naratif ──────────────────────────────

def _sane(value: Any, lo: float, hi: float) -> Optional[float]:
    """
    Loloskan angka hanya bila masuk akal.

    yfinance memulangkan PBV di atas 5.000 untuk sebagian emiten .JK —
    nilai bukunya dilaporkan dalam satuan berbeda dari harganya. Tanpa
    penjaga ini papan akan menampilkan "PBV 6.685x" seolah itu fakta.
    """
    if not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):    # NaN / inf
        return None
    return v if lo <= v <= hi else None


# Skor fundamental — HEURISTIK PAPAN INI, bukan ukuran baku industri.
# Tiap komponen dipetakan ke 0-1 memakai ambang absolut (bukan peringkat
# relatif) supaya angkanya stabil dari hari ke hari dan bisa ditelusuri.
# Format: (kunci, bobot, nilai_terburuk, nilai_terbaik, label)
# Untuk komponen "makin kecil makin baik", terburuk > terbaik — rumus
# normalisasinya sama persis.
SCORE_SPEC = [
    ("roe",  25,   0.0,  25.0, "ROE"),
    ("per",  20,  30.0,   5.0, "PER"),
    ("pbv",  15,   3.0,   0.5, "PBV"),
    ("npm",  10,   0.0,  30.0, "Marjin"),
    ("rev",  10, -20.0,  30.0, "Growth"),
    ("divy", 10,   0.0,   8.0, "Dividen"),
    ("der",  10,   2.0,   0.0, "Utang"),
]


def score_fundamental(row: dict[str, Any]) -> dict[str, Any]:
    """
    Skor 0-100 dari komponen yang tersedia.

    Komponen yang datanya kosong TIDAK dihitung sebagai nol — bobotnya
    dikeluarkan dari penyebut. Bank yang tidak melaporkan DER karena itu
    tidak dihukum; kalau tidak, seluruh sektor keuangan akan tenggelam
    hanya karena satu kolom kosong.
    """
    total_w = 0.0
    got = 0.0
    parts: list[dict[str, Any]] = []

    for key, weight, worst, best in [(k, w, lo, hi) for k, w, lo, hi, _ in SCORE_SPEC]:
        v = row.get(key)
        if not isinstance(v, (int, float)):
            continue
        t = (float(v) - worst) / (best - worst)
        t = max(0.0, min(1.0, t))
        total_w += weight
        got += t * weight
        parts.append({"k": key, "t": round(t, 3)})

    if total_w == 0:
        return {"score": None, "parts": [], "coverage": 0}

    return {
        "score": round(got / total_w * 100),
        "parts": parts,
        "coverage": round(total_w / sum(w for _, w, _, _, _ in SCORE_SPEC) * 100),
    }


_EARNINGS_SYSTEM = """\
Kamu analis fundamental saham Indonesia. Kamu menerima angka laporan
keuangan triwulanan satu emiten yang SUDAH dihitung. Tugasmu menuliskan
ulasannya, bukan menghitung ulang.

Kembalikan HANYA JSON valid, tanpa markdown:
{
  "verdict": "<Kuat|Sehat|Campuran|Lemah>",
  "narrative": "<2-3 kalimat bahasa Indonesia>",
  "highlights": ["<poin singkat 1>", "<poin singkat 2>"]
}

Aturan:
- SELURUH jawaban berbahasa Indonesia, termasuk penulisan angkanya:
  desimal memakai KOMA dan ribuan memakai TITIK (29,6% bukan 29.6%;
  1.250 bukan 1,250). Seluruh papan memakai format ini.
- Pakai HANYA angka yang diberikan. Jangan mengarang angka, tanggal,
  aksi korporasi, atau nama produk yang tidak tercantum.
- Sebut arah yang penting: pendapatan, laba bersih, marjin, dan posisi
  utang/kas. Jelaskan artinya, bukan sekadar mengulang angkanya.
- "verdict": Kuat = tumbuh dan marjin membaik; Sehat = stabil, neraca
  aman; Campuran = ada yang membaik ada yang memburuk; Lemah = laba
  turun tajam, rugi, atau utang menekan.
- Kalau angkanya sedikit atau banyak yang kosong, katakan terus terang
  di narasi bahwa datanya terbatas. Jangan menutupinya.
- Jangan memberi ajakan beli atau jual.
"""


def _fmt_t(v: Any) -> str:
    """Rupiah triliun, atau '-' kalau tidak ada."""
    if not isinstance(v, (int, float)) or v != v:
        return "-"
    return f"{v / 1e12:,.2f} T"


def _pct_change(now: Any, before: Any) -> Optional[float]:
    if not all(isinstance(x, (int, float)) and x == x for x in (now, before)):
        return None
    if before == 0:
        return None
    return (now - before) / abs(before) * 100


def _parse_json(raw: str) -> dict[str, Any]:
    """Ambil objek JSON dari balasan LLM; {} kalau gagal."""
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.splitlines()
        clean = "\n".join(parts[1:-1]).strip() if len(parts) > 2 else clean
    a, b = clean.find("{"), clean.rfind("}")
    if a == -1 or b <= a:
        return {}
    try:
        val = json.loads(clean[a : b + 1])
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_fundamental_scan(
    tickers: list[str], max_new_reviews: int = MAX_NEW_REVIEWS
) -> list[dict[str, Any]]:
    """
    Scan fundamental SELURUH watchlist: rasio + skor + ulasan naratif,
    diurutkan dari skor tertinggi.

    Satu objek Ticker dipakai ulang untuk info dan laporan triwulanan —
    dua panggilan terpisah akan menggandakan waktu scan tanpa guna.

    Dua lapis cache dengan umur berbeda karena datanya berubah pada
    ritme berbeda:
      - rasio valuasi ikut harga  -> 24 jam
      - ulasan naratif ikut LAPORAN -> kunci berisi tanggal laporan,
        jadi hanya ditulis ulang saat emiten benar-benar merilis yang baru

    Ulasan ditulis SEBANYAK MUNGKIN sampai kuota LLM habis, lalu berhenti
    di situ; sisanya dikerjakan run-run berikutnya. Menulis 58 sekaligus
    menembus kuota Groq, dan yang mahal bukan penolakannya melainkan
    retry bawaan yang menunggu 3x30 detik lalu jatuh ke Gemini. Karena
    itu retry dimatikan (_retries=1) dan begitu satu panggilan kena
    limit, seluruh penulisan ulasan dihentikan untuk run ini.

    Papan dibangun tiap 30 menit, jadi seluruh watchlist tercakup dalam
    beberapa jam tanpa satu run pun membengkak. `max_new_reviews` hanya
    pagar waktu tambahan, bukan penghenti utama.

    Emiten yang ulasannya belum ada TETAP muncul lengkap dengan rasio dan
    skornya — hanya narasinya yang menyusul.
    """
    budget = max_new_reviews
    rate_limited = False
    import yfinance as yf
    from utils.agent_cache import get as cache_get, set as cache_set
    from agents.base_agent import BaseAgent

    class _Reader(BaseAgent):
        def analyze(self, *a: Any, **k: Any) -> dict[str, Any]:
            return {}

    reader = _Reader()
    reader.max_tokens = 1024

    def pick(frame: Any, names: list[str], col: Any) -> Any:
        if frame is None or frame.empty:
            return None
        for n in names:
            if n in frame.index:
                v = frame.loc[n, col]
                if isinstance(v, (int, float)) and v == v:
                    return float(v)
        return None

    out: list[dict[str, Any]] = []
    for code in tickers:
        try:
            tk = yf.Ticker(f"{code}.JK")

            # ── Rasio valuasi ────────────────────────────────────────
            rkey = f"ratio:{code}"
            row = cache_get(rkey, ttl=24 * 3600)
            if not row:
                info = tk.info or {}
                der  = _sane(info.get("debtToEquity"), 0, 1000)
                roe  = _sane(info.get("returnOnEquity"), -5, 5)
                npm  = _sane(info.get("profitMargins"), -5, 5)
                rev  = _sane(info.get("revenueGrowth"), -5, 20)
                mcap = _sane(info.get("marketCap"), 1e9, 1e16)
                per  = _sane(info.get("trailingPE"), 0, 500)
                pbv  = _sane(info.get("priceToBook"), 0, 100)
                divy = _sane(info.get("dividendYield"), 0, 100)
                row = {
                    "ticker": code,
                    "sector": info.get("sector") or "",
                    "name":   info.get("longName") or code,
                    "per":  round(per, 1) if per is not None else None,
                    "pbv":  round(pbv, 2) if pbv is not None else None,
                    "roe":  round(roe * 100, 1) if roe is not None else None,
                    "der":  round(der / 100, 2) if der is not None else None,   # % -> x
                    "divy": round(divy, 1) if divy is not None else None,
                    "npm":  round(npm * 100, 1) if npm is not None else None,
                    "rev":  round(rev * 100, 1) if rev is not None else None,
                    "mcap": round(mcap / 1e12, 1) if mcap is not None else None,
                }
                cache_set(rkey, row)
            row = dict(row)

            # ── Ulasan laporan triwulanan ────────────────────────────
            qf = tk.quarterly_financials
            if qf is not None and not qf.empty:
                cols = list(qf.columns)
                last = cols[0]
                prev = cols[1] if len(cols) > 1 else None
                yoy = next((c for c in cols
                            if c.month == last.month and c.year == last.year - 1), None)
                period = f"Q{(last.month - 1) // 3 + 1} {last.year}"
                ekey = f"earnings2:{code}:{last.strftime('%Y%m%d')}"

                cached = cache_get(ekey, ttl=120 * 24 * 3600)
                if cached and (cached.get("revenue") or cached.get("net_income")):
                    row.update(cached)
                elif not cached and budget > 0 and not rate_limited:
                    qb = tk.quarterly_balance_sheet
                    rev_f = lambda c: pick(qf, ["Total Revenue", "Operating Revenue"], c)
                    net_f = lambda c: pick(qf, ["Net Income", "Net Income Common Stockholders"], c)
                    rev_now, net_now = rev_f(last), net_f(last)

                    # Emiten yang baru IPO belum punya angka apa pun.
                    if rev_now or net_now:
                        opi_now = pick(qf, ["Operating Income"], last)
                        npm_q = (net_now / rev_now * 100) if rev_now and net_now else None

                        facts = [
                            f"Emiten        : {code}",
                            f"Periode       : {period} (per {last.strftime('%d %b %Y')})",
                            f"Pendapatan    : {_fmt_t(rev_now)}",
                            f"Laba operasi  : {_fmt_t(opi_now)}",
                            f"Laba bersih   : {_fmt_t(net_now)}",
                            (f"Marjin bersih : {npm_q:.1f}%" if npm_q is not None
                             else "Marjin bersih : -"),
                        ]
                        if prev is not None:
                            d = _pct_change(rev_now, rev_f(prev))
                            if d is not None:
                                facts.append(f"vs triwulan sebelumnya: pendapatan {d:+.1f}%")
                            d = _pct_change(net_now, net_f(prev))
                            if d is not None:
                                facts.append(f"  laba bersih {d:+.1f}%")
                        if yoy is not None:
                            d = _pct_change(rev_now, rev_f(yoy))
                            if d is not None:
                                facts.append(f"vs triwulan sama tahun lalu: pendapatan {d:+.1f}%")
                            d = _pct_change(net_now, net_f(yoy))
                            if d is not None:
                                facts.append(f"  laba bersih {d:+.1f}%")
                        if qb is not None and not qb.empty and last in qb.columns:
                            facts += [
                                f"Total aset    : {_fmt_t(pick(qb, ['Total Assets'], last))}",
                                f"Total utang   : {_fmt_t(pick(qb, ['Total Debt'], last))}",
                                f"Ekuitas       : {_fmt_t(pick(qb, ['Stockholders Equity'], last))}",
                                f"Kas           : {_fmt_t(pick(qb, ['Cash And Cash Equivalents'], last))}",
                            ]

                        # Retry dimatikan: yang mahal bukan penolakannya,
                        # melainkan 3x30 detik menunggu lalu jatuh ke
                        # Gemini yang kuncinya mati.
                        raw_text = reader.call_claude(
                            _EARNINGS_SYSTEM, "\n".join(facts), _retries=1
                        )
                        low = raw_text.lower()
                        if '"error"' in low and (
                            "rate limit" in low or "rate_limit" in low or "429" in low
                        ):
                            rate_limited = True
                            logger.info(
                                f"[snapshot] kuota LLM habis di {code} — "
                                "sisa ulasan menyusul pada run berikutnya"
                            )
                            raw = {}
                        else:
                            raw = _parse_json(raw_text)
                        er = {
                            "period": period,
                            "as_of": last.strftime("%d %b %Y"),
                            "stale_days": (datetime.now()
                                           - last.to_pydatetime().replace(tzinfo=None)).days,
                            "verdict": str(raw.get("verdict", "")).strip(),
                            "narrative": str(raw.get("narrative", "")).strip(),
                            "highlights": [str(h) for h in (raw.get("highlights") or [])][:3],
                            "revenue": round(rev_now / 1e12, 2) if rev_now else None,
                            "net_income": round(net_now / 1e12, 2) if net_now else None,
                        }
                        if er["narrative"]:
                            cache_set(ekey, er)
                            budget -= 1
                        row.update(er)

            row.update(score_fundamental(row))
            if row.get("score") is not None:
                out.append(row)

        except Exception as exc:
            logger.debug(f"[snapshot] fundamental {code}: {exc}")

    out.sort(key=lambda r: (-(r.get("score") or 0), r["ticker"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i

    with_review = sum(1 for r in out if r.get("narrative"))
    logger.info(
        f"[snapshot] scan fundamental: {len(out)}/{len(tickers)} emiten, "
        f"{with_review} punya ulasan, {len(out) - with_review} menyusul "
        f"({max_new_reviews - budget} ditulis run ini"
        + (", berhenti karena kuota LLM)" if rate_limited else ")")
    )
    return out


def build_tape() -> list[dict[str, Any]]:
    from utils.data_fetcher import fetch_stock_data
    out = []
    for t in TAPE_TICKERS:
        try:
            sd = fetch_stock_data(t)
            if sd.is_valid:
                out.append({
                    "t": t.replace(".JK", ""),
                    "p": round(sd.current_price),
                    "c": round(sd.day_change_pct, 1),
                })
        except Exception as exc:
            logger.debug(f"[snapshot] tape {t}: {exc}")
    return out


def build_signals() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Scan seluruh watchlist memakai aturan yang sama persis dengan alert
    Telegram (utils/signal_rules), tanpa dedup dan tanpa mengirim apa pun.
    """
    from utils.data_fetcher import fetch_stock_data
    from utils.technical_calculator import calculate_technical_data
    from utils.tradingview_ta import get_tv_ta_batch, tv_label
    from utils.signal_rules import classify, BREAKOUT, WEAKNESS, RADAR

    tv_map = get_tv_ta_batch(list(DEFAULT_TICKERS))
    rows: list[dict[str, Any]] = []

    for ticker in DEFAULT_TICKERS:
        try:
            sd = fetch_stock_data(ticker)
            if not sd.is_valid:
                continue
            td = calculate_technical_data(ticker, sd.price_history)
            v = classify(sd, td)
            if not v:
                continue

            ta = tv_map.get(ticker) or {}
            rows.append({
                "kind": v["kind"],
                "ticker": ticker.replace(".JK", ""),
                "price": round(v["price"]),
                "change": round(v["change"], 1),
                "rsi": round(v["rsi"]),
                "vol": round(v["vol"], 1),
                "tv": tv_label(ta.get("1D", {}).get("All")),
                "reasons": [r[0].upper() + r[1:] for r in v["reasons"]],
                **v["levels"].as_dict(),
            })
        except Exception as exc:
            logger.debug(f"[snapshot] signal {ticker}: {exc}")

    order = {BREAKOUT: 0, WEAKNESS: 1, RADAR: 2}
    rows.sort(key=lambda r: (order.get(r["kind"], 9), -r["vol"]))

    counts = {
        "breakout": sum(1 for r in rows if r["kind"] == BREAKOUT),
        "weakness": sum(1 for r in rows if r["kind"] == WEAKNESS),
        "radar":    sum(1 for r in rows if r["kind"] == RADAR),
        "total":    len(rows),
    }
    return rows, counts


def build_macro() -> dict[str, Any]:
    """Kondisi makro; hasilnya di-cache 12 jam agar hemat panggilan LLM."""
    from utils.agent_cache import get as cache_get, set as cache_set

    cached = cache_get("macro:dashboard", ttl=12 * 3600)
    if cached:
        return cached

    try:
        from agents.macro_agent import MacroAgent
        result = MacroAgent().analyze(context=_now().strftime("%Y-%m-%d %H:%M WIB"))
        out = {
            "condition": result.get("market_condition", "N/A"),
            "bias": result.get("ihsg_bias", "N/A"),
            "positive_sectors": result.get("positive_sectors", [])[:6],
            "negative_sectors": result.get("negative_sectors", [])[:4],
            "drivers": result.get("key_drivers", [])[:3],
            "summary": result.get("summary", ""),
            "llm_ok": True,
        }
        cache_set("macro:dashboard", out)
        return out
    except Exception as exc:
        logger.warning(f"[snapshot] macro gagal: {exc}")
        return {"condition": "N/A", "bias": "N/A", "positive_sectors": [],
                "negative_sectors": [], "drivers": [], "summary": "", "llm_ok": False}


def build_news(max_items: int = 6) -> list[dict[str, Any]]:
    from utils.news_feed import fetch_market_rss
    return [
        {"title": x["title"], "publisher": x["publisher"],
         "link": x["link"], "ago": _rel_time(x.get("pub_ts"))}
        for x in fetch_market_rss(max_items=max_items, max_age_hours=48)
    ]


def build_runs() -> list[dict[str, Any]]:
    """
    Status jalannya tiap workflow dari GitHub REST API.
    Tanpa token (mis. dijalankan lokal) jatuh ke jadwal statis saja.
    """
    import requests

    repo  = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("GITHUB_TOKEN", "")
    fallback = [
        {"name": v["label"], "status": "idle", "last": "", "hint": v["hint"], "state": ""}
        for v in SCHEDULE.values()
    ]
    if not repo or not token:
        return fallback

    try:
        head = {"Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json"}
        wf = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows",
            headers=head, timeout=15,
        ).json().get("workflows", [])

        out = []
        for w in wf:
            fname = w.get("path", "").rsplit("/", 1)[-1]
            meta = SCHEDULE.get(fname)
            if not meta:
                continue
            runs = requests.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/{w['id']}/runs",
                headers=head, params={"per_page": 1}, timeout=15,
            ).json().get("workflow_runs", [])

            last_ts, concl = None, ""
            if runs:
                concl = runs[0].get("conclusion") or runs[0].get("status") or ""
                try:
                    last_ts = datetime.fromisoformat(
                        runs[0]["run_started_at"].replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    pass

            disabled = w.get("state", "") != "active"
            # Run yang masih berjalan belum punya conclusion. Tanpa perlakuan
            # khusus, workflow papan web selalu melaporkan dirinya "warning",
            # karena ia memotret keadaan saat dirinya sendiri belum selesai.
            running = concl in ("in_progress", "queued", "requested", "waiting")
            status = ("critical" if disabled
                      else "good" if concl == "success" or running
                      else "warning" if concl else "idle")

            out.append({
                "name": meta["label"],
                "status": status,
                "last": _rel_time(last_ts) or "—",
                "hint": meta["hint"],
                "state": "nonaktif" if disabled else "",
            })
        return out or fallback
    except Exception as exc:
        logger.warning(f"[snapshot] status workflow gagal: {exc}")
        return fallback


# ── Perakitan & penulisan ─────────────────────────────────────────────────────

def attach_journal(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Tempelkan level mentor ke tiap sinyal + kembalikan blok makro jurnal.

    Catatan harga: level mentor ditulis dalam harga pasar apa adanya,
    sedangkan harga sistem berasal dari yfinance auto_adjust. Keduanya
    sama kecuali pada jendela semalam setelah tanggal ex-dividen, saat
    Yahoo sudah menyesuaikan bar terakhir tapi pasar belum berdagang lagi.
    """
    from utils.journal import load_latest, evaluate

    j = load_latest()
    if not j:
        return {}

    tickers = j.get("tickers", {})
    hit = 0
    for s in signals:
        note = tickers.get(s["ticker"])
        if not note:
            continue
        hit += 1
        s["journal"] = {
            "stance": note.get("stance", ""),
            "entries": note.get("entries", []),
            "targets": note.get("targets", []),
            "note": note.get("note", ""),
            "raw": note.get("raw", ""),
            **evaluate(note, s["price"]),
        }

    logger.info(
        f"[snapshot] jurnal {j.get('date')}: {hit}/{len(signals)} sinyal "
        f"punya level mentor ({len(tickers)} emiten di jurnal)"
    )
    return {
        "date": j.get("date"),
        "age_days": j.get("age_days", 0),
        "ticker_count": len(tickers),
        "matched": hit,
        **(j.get("macro") or {}),
    }


def build(include_journal: bool = False) -> dict[str, Any]:
    """
    Rakit snapshot.

    include_journal HARUS False untuk snapshot yang diterbitkan ke GitHub
    Pages: jurnal mentor diminta untuk tidak disebarkan di luar grup,
    sementara halaman Pages terbuka untuk siapa saja.
    """
    now = _now()
    logger.info("[snapshot] merakit data papan...")

    index = build_index()
    tape  = build_tape()
    signals, counts = build_signals()
    macro = build_macro()
    news  = build_news()
    runs  = build_runs()
    markets = build_markets()
    # Scan fundamental menyeluruh: SELURUH watchlist, bukan hanya yang
    # memunculkan sinyal hari ini — tab Fundamental menampilkan peringkat
    # penuh, dan slip mengambil barisnya dari daftar yang sama.
    fundamentals = build_fundamental_scan([t.replace(".JK", "") for t in DEFAULT_TICKERS])
    journal = attach_journal(signals) if include_journal else {}

    sources = [
        {"name": "Harga · yfinance",     "status": "good" if index else "critical",
         "note": "OK" if index else "gagal"},
        {"name": "TA · TradingView",     "status": "good" if signals else "warning",
         "note": "OK" if signals else "kosong"},
        {"name": "Berita · RSS + GNews", "status": "good" if news else "warning",
         "note": f"{len(news)} artikel" if news else "kosong"},
        {"name": "LLM · Groq",           "status": "good" if macro.get("llm_ok") else "critical",
         "note": "OK" if macro.get("llm_ok") else "gagal"},
    ]

    return {
        "generated_at": now.isoformat(),
        "generated_label": now.strftime("%d %b %Y · %H:%M WIB"),
        "market_open": _is_market_open(now),
        "watchlist_size": len(DEFAULT_TICKERS),
        "index": index,
        "tape": tape,
        "signals": signals,
        "counts": counts,
        "macro": macro,
        "news": news,
        "runs": runs,
        "backtest": {"period": BACKTEST_PERIOD, "rows": BACKTEST},
        "sources": sources,
        "markets": markets,
        "fundamentals": fundamentals,
        "journal": journal,
    }


def write(path: Path | None = None, include_journal: bool = False) -> Path:
    target = path or OUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    data = build(include_journal=include_journal)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    logger.info(
        f"[snapshot] ditulis ke {target} — {data['counts']['total']} sinyal, "
        f"{len(data['news'])} berita, {len(data['tape'])} tape"
        + (f", jurnal {data['journal'].get('date')}" if data.get("journal") else ", tanpa jurnal")
    )
    return target
