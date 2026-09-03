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
from typing import Any
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

    td = calculate_technical_data("^JKSE", df)
    closes = df["Close"].dropna()
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
    }


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
            status = ("critical" if disabled
                      else "good" if concl == "success"
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

def build() -> dict[str, Any]:
    now = _now()
    logger.info("[snapshot] merakit data papan...")

    index = build_index()
    tape  = build_tape()
    signals, counts = build_signals()
    macro = build_macro()
    news  = build_news()
    runs  = build_runs()

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
    }


def write(path: Path | None = None) -> Path:
    target = path or OUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    logger.info(
        f"[snapshot] ditulis ke {target} — {data['counts']['total']} sinyal, "
        f"{len(data['news'])} berita, {len(data['tape'])} tape"
    )
    return target
