"""
IHSG Trading System — Data Fetcher
Wraps yfinance to provide clean StockData objects.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


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
    relative_volume: float = 1.0

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


def fetch_news(ticker: str, max_items: int = 5) -> list[dict[str, str]]:
    """
    Ambil berita terkini dari Yahoo Finance untuk satu ticker.
    Mengembalikan list of dictionaries berisi detail berita.
    """
    try:
        stock = yf.Ticker(ticker)
        news_items = stock.news or []
        if not news_items:
            return []

        results = []
        for item in news_items[:max_items]:
            content = item.get("content", item)
            title = (
                content.get("title")
                or item.get("title")
                or ""
            ).strip()
            publisher = (
                content.get("provider", {}).get("displayName")
                or item.get("publisher")
                or ""
            )
            link = (
                item.get("link")
                or content.get("clickThroughUrl", {}).get("url")
                or content.get("url")
                or ""
            )
            summary = (
                content.get("summary")
                or item.get("summary")
                or ""
            ).strip()

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
                import datetime
                try:
                    dt = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    pub_ts = int(dt.timestamp())
                except Exception:
                    pass

            if title:
                results.append({
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "summary": summary,
                    "pub_ts": pub_ts,
                })

        return results
    except Exception as exc:
        logger.debug(f"[fetch_news] {ticker}: {exc}")
        return []


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
    Ambil berita market-wide yang relevan untuk IHSG.
    Prioritas: sumber Indonesia dulu, lalu global macro.
    Digunakan untuk mendeteksi event besar: BI Rate, Fed, MSCI, dll.
    """
    # Sumber Indonesia dulu agar BI Rate/IHSG news tidak tergeser US news
    sources = ["^JKSE", "IDR=X", "EIDO", "^TNX", "^GSPC"]
    max_age_sec = 48 * 3600
    cutoff = time.time() - max_age_sec

    seen_titles: set[str] = set()
    combined = []
    for source_ticker in sources:
        news_list = fetch_news(source_ticker, max_items=max_items)
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
        vol_series = hist["Volume"].rolling(20).mean()
        data.volume_avg_20 = float(vol_series.iloc[-1]) if not vol_series.empty else 0.0
        data.relative_volume = (
            data.current_volume / data.volume_avg_20
            if data.volume_avg_20 > 0
            else 1.0
        )

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
        logger.info(
            f"[{ticker}] Fetched OK — Price: {data.current_price:,.0f} "
            f"| Change: {data.day_change_pct:+.2f}% "
            f"| RelVol: {data.relative_volume:.2f}x"
        )

    except Exception as exc:
        data.error = str(exc)
        logger.error(f"[{ticker}] fetch_stock_data failed: {exc}")

    return data
