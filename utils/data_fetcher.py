"""
IHSG Trading System — Data Fetcher
Wraps yfinance to provide clean StockData objects.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


WIB = ZoneInfo("Asia/Jakarta")

# Jam perdagangan pasar reguler IDX (WIB). Sesi 2 hari Jumat mulai lebih siang.
_IDX_SESSIONS: dict[bool, list[tuple[tuple[int, int], tuple[int, int]]]] = {
    False: [((9, 0), (12, 0)), ((13, 30), (15, 50))],   # Senin-Kamis = 320 menit
    True:  [((9, 0), (11, 30)), ((14, 0), (15, 50))],   # Jumat        = 260 menit
}


def session_elapsed_fraction(now: Optional[datetime] = None) -> float:
    """
    Porsi sesi perdagangan hari ini yang sudah berlalu (0.0-1.0).

    Dipakai untuk menormalkan volume: bar harian yfinance saat pasar masih
    buka berisi volume SEBAGIAN hari, sementara rata-rata 20 hari berisi
    volume hari PENUH. Tanpa koreksi, relative_volume selalu terlalu kecil
    di pagi hari dan syarat '>= 1.5x' praktis mustahil terpenuhi sebelum
    siang — padahal backtest-nya memakai volume hari penuh.
    """
    now = now or datetime.now(WIB)
    if now.tzinfo is None:
        now = now.replace(tzinfo=WIB)
    else:
        now = now.astimezone(WIB)

    sessions = _IDX_SESSIONS[now.weekday() == 4]
    total = elapsed = 0.0
    for (sh, sm), (eh, em) in sessions:
        start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        span  = (end - start).total_seconds()
        total += span
        if now >= end:
            elapsed += span
        elif now > start:
            elapsed += (now - start).total_seconds()

    if total <= 0:
        return 1.0
    # Lantai 0.15: sebelum pasar buka (dan beberapa menit pertama) pembagi
    # yang mendekati nol akan meledakkan relative_volume.
    return min(1.0, max(0.15, elapsed / total))


@dataclass
class StockData:
    """Holds all raw market data for a single ticker."""
    ticker: str

    # Price
    current_price: float = 0.0
    prev_close: float = 0.0
    day_open: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    day_change_pct: float = 0.0

    # Volume
    current_volume: float = 0.0
    volume_avg_20: float = 0.0
    relative_volume: float = 1.0          # sudah dinormalkan ke setara hari penuh
    relative_volume_raw: float = 1.0      # apa adanya, sebelum normalisasi
    session_fraction: float = 1.0         # porsi sesi yang sudah berlalu
    is_partial_bar: bool = False          # True = bar terakhir hari berjalan

    # History (OHLCV DataFrame)
    price_history: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Company meta
    info: dict = field(default_factory=dict)
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    market_cap: float = 0.0

    # Financials
    financials: pd.DataFrame = field(default_factory=pd.DataFrame)
    balance_sheet: pd.DataFrame = field(default_factory=pd.DataFrame)
    cashflow: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Status
    error: Optional[str] = None
    is_valid: bool = False


def fetch_news_yahoo(ticker: str, max_items: int = 5) -> list[dict[str, str]]:
    """
    Ambil berita dari Yahoo Finance untuk satu ticker.
    Sumber CADANGAN saja — untuk ticker .JK dan ^JKSE feed Yahoo sudah
    lama tidak ter-update. Sumber utama ada di utils/news_feed.py.

    Setiap artikel di-parse dalam try/except sendiri: sebelumnya satu
    artikel cacat membuat SELURUH daftar dibuang dan fungsi ini
    mengembalikan [], sehingga pilar sentimen mati tanpa jejak di log.
    """
    try:
        stock = yf.Ticker(ticker)
        news_items = stock.news or []
    except Exception as exc:
        logger.warning(f"[fetch_news] {ticker} gagal: {type(exc).__name__}: {exc}")
        return []

    results = []
    for item in news_items[:max_items]:
        try:
            content = item.get("content", item) or {}

            title = (content.get("title") or item.get("title") or "").strip()
            if not title:
                continue

            # `or {}` — bukan .get(key, {}) — karena key-nya ADA tapi bernilai
            # None pada sebagian artikel, sehingga default tidak pernah dipakai
            # dan .get() dipanggil di atas None -> AttributeError.
            publisher = (
                (content.get("provider") or {}).get("displayName")
                or item.get("publisher")
                or ""
            )
            link = (
                item.get("link")
                or (content.get("clickThroughUrl") or {}).get("url")
                or (content.get("canonicalUrl") or {}).get("url")
                or content.get("url")
                or ""
            )
            summary = (content.get("summary") or item.get("summary") or "").strip()

            # Extract publish timestamp (Unix int or ISO-8601 string)
            pub_ts = None
            raw_ts = (
                content.get("pubDate")
                or item.get("providerPublishTime")
                or content.get("displayTime")
            )
            if isinstance(raw_ts, (int, float)):
                pub_ts = int(raw_ts)
            elif isinstance(raw_ts, str):
                try:
                    dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    pub_ts = int(dt.timestamp())
                except Exception:
                    pass

            results.append({
                "title": title,
                "publisher": publisher,
                "link": link,
                "summary": summary,
                "pub_ts": pub_ts,
            })
        except Exception as exc:
            logger.warning(
                f"[fetch_news] {ticker} lewati 1 artikel: {type(exc).__name__}: {exc}"
            )
            continue

    return results


def fetch_news(
    ticker: str,
    max_items: int = 5,
    company_name: str = "",
    pool: list[dict] | None = None,
) -> list[dict[str, str]]:
    """
    Berita untuk satu emiten. RSS Indonesia + Google News sebagai sumber
    utama, Yahoo Finance sebagai cadangan kalau keduanya kosong.
    """
    from utils.news_feed import fetch_stock_news

    items = fetch_stock_news(
        ticker, company_name=company_name, pool=pool, max_items=max_items
    )
    if items:
        return items
    return fetch_news_yahoo(ticker, max_items=max_items)


_IHSG_KEYWORDS = {
    # Indonesia & IHSG
    "indonesia", "ihsg", "rupiah", "idr", "bank indonesia", "bi rate",
    "suku bunga", "idx", "jakarta", "jkse", "eido",
    # Global macro yang relevan untuk IHSG
    "federal reserve", "fed rate", "fomc", "rate hike", "rate cut",
    "interest rate", "us rate", "treasury yield", "10-year yield",
    "inflation", "cpi data", "recession", "emerging market", "em fund",
    "china economy", "commodity", "crude oil", "coal price", "nickel",
    "palm oil", "cpo", "gold price", "dollar index",
    # Capital flow & indeks
    "msci", "ftse", "capital outflow", "capital inflow", "foreign fund",
}


def _is_ihsg_relevant(title: str, summary: str = "") -> bool:
    """Cek apakah artikel relevan untuk IHSG/investor Indonesia."""
    text = (title + " " + summary).lower()
    return any(kw in text for kw in _IHSG_KEYWORDS)


def fetch_market_news(max_items: int = 6) -> list[dict[str, str]]:
    """
    Berita market-wide yang relevan untuk IHSG (BI Rate, Fed, MSCI, dll).

    Utama : RSS Kontan / CNBC Indonesia / Antara — berbahasa Indonesia
            dan ter-update harian.
    Cadangan: Yahoo Finance lewat ^JKSE dan proksi global. Feed Yahoo untuk
            IHSG sudah basi berbulan-bulan, jadi ini benar-benar jaring
            pengaman terakhir saja.
    """
    from utils.news_feed import fetch_market_rss

    items = fetch_market_rss(max_items=max_items, max_age_hours=24)
    if items:
        return items

    logger.warning("[fetch_market_news] RSS kosong — fallback ke Yahoo Finance")
    sources = ["^JKSE", "IDR=X", "EIDO", "^TNX", "^GSPC"]
    cutoff = time.time() - 48 * 3600

    seen_titles: set[str] = set()
    combined = []
    for source_ticker in sources:
        news_list = fetch_news_yahoo(source_ticker, max_items=max_items)
        for item in news_list:
            title   = item.get("title", "").strip()
            summary = item.get("summary", "")
            pub_ts  = item.get("pub_ts")
            if not title or title in seen_titles:
                continue
            if pub_ts is not None and pub_ts < cutoff:
                logger.debug(f"[fetch_market_news] Skip lama: {title[:60]}")
                continue
            if not _is_ihsg_relevant(title, summary):
                logger.debug(f"[fetch_market_news] Skip tidak relevan: {title[:60]}")
                continue
            seen_titles.add(title)
            combined.append(item)
        if len(combined) >= max_items:
            break
    return combined[:max_items]


def fetch_stock_data(ticker: str, period: str = "3mo") -> StockData:
    """
    Fetch comprehensive stock data from Yahoo Finance.

    Args:
        ticker: Yahoo Finance ticker symbol (e.g., 'BBRI.JK')
        period: History period string (e.g., '1mo', '3mo', '6mo', '1y')

    Returns:
        Populated StockData object. Check .error and .is_valid.
    """
    data = StockData(ticker=ticker)

    try:
        stock = yf.Ticker(ticker)

        # ── Price History ────────────────────────────────────
        hist = stock.history(period=period, auto_adjust=True)
        if hist.empty:
            data.error = f"No price history returned for {ticker}"
            logger.warning(data.error)
            return data

        # Yahoo sudah menyiapkan baris untuk hari yang belum dibuka, dengan
        # OHLC NaN. Kalau dibiarkan, harga terakhir menjadi NaN dan SEMUA
        # perbandingan di aturan sinyal jadi False tanpa pesan error — papan
        # web sempat tampil 0 sinyal karena ini. Buang baris tanpa Close.
        before = len(hist)
        hist = hist[hist["Close"].notna()]
        if len(hist) < before:
            logger.debug(f"[{ticker}] {before - len(hist)} bar tanpa Close dibuang")
        if hist.empty:
            data.error = f"All bars have NaN Close for {ticker}"
            logger.warning(data.error)
            return data

        data.price_history = hist
        data.current_price = float(hist["Close"].iloc[-1])
        data.prev_close = (
            float(hist["Close"].iloc[-2]) if len(hist) > 1 else data.current_price
        )
        data.day_open = float(hist["Open"].iloc[-1])
        data.day_high = float(hist["High"].iloc[-1])
        data.day_low = float(hist["Low"].iloc[-1])
        data.day_change_pct = (
            (data.current_price - data.prev_close) / data.prev_close * 100
            if data.prev_close > 0
            else 0.0
        )

        # ── Volume ───────────────────────────────────────────
        data.current_volume = float(hist["Volume"].iloc[-1])
        # Rata-rata dihitung dari bar SEBELUM bar terakhir, supaya hari
        # berjalan yang belum tutup tidak ikut menurunkan pembandingnya.
        vol_series = hist["Volume"].shift(1).rolling(20).mean()
        data.volume_avg_20 = float(vol_series.iloc[-1]) if not vol_series.empty else 0.0
        data.relative_volume_raw = (
            data.current_volume / data.volume_avg_20
            if data.volume_avg_20 > 0
            else 1.0
        )

        # Bar terakhir = hari ini & pasar belum tutup -> volume masih separuh
        now_wib = datetime.now(WIB)
        last_bar_date = hist.index[-1].date()
        data.is_partial_bar = (
            last_bar_date == now_wib.date() and now_wib.hour < 16
        )
        data.session_fraction = (
            session_elapsed_fraction(now_wib) if data.is_partial_bar else 1.0
        )
        data.relative_volume = data.relative_volume_raw / data.session_fraction

        # ── Company Info ─────────────────────────────────────
        try:
            info = stock.info or {}
        except Exception:
            info = {}
        data.info = info
        data.company_name = info.get("longName", ticker)
        data.sector = info.get("sector", "Unknown")
        data.industry = info.get("industry", "Unknown")
        data.market_cap = float(info.get("marketCap", 0) or 0)

        # ── Financial Statements ─────────────────────────────
        for attr, fetcher in [
            ("financials", lambda: stock.financials),
            ("balance_sheet", lambda: stock.balance_sheet),
            ("cashflow", lambda: stock.cashflow),
        ]:
            try:
                result = fetcher()
                setattr(data, attr, result if result is not None else pd.DataFrame())
            except Exception as exc:
                logger.debug(f"[{ticker}] Could not fetch {attr}: {exc}")
                setattr(data, attr, pd.DataFrame())

        data.is_valid = True
        partial_note = (
            f" | bar berjalan {data.session_fraction*100:.0f}% sesi "
            f"(mentah {data.relative_volume_raw:.2f}x)"
            if data.is_partial_bar else ""
        )
        logger.info(
            f"[{ticker}] Fetched OK — Price: {data.current_price:,.0f} "
            f"| Change: {data.day_change_pct:+.2f}% "
            f"| RelVol: {data.relative_volume:.2f}x{partial_note}"
        )

    except Exception as exc:
        data.error = str(exc)
        logger.error(f"[{ticker}] fetch_stock_data failed: {exc}")

    return data
