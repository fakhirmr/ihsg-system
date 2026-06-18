"""
IHSG Trading System — Data Fetcher
Wraps yfinance to provide clean StockData objects.
"""
from __future__ import annotations

import logging
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
            
            if title:
                results.append({
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "summary": summary
                })

        return results
    except Exception as exc:
        logger.debug(f"[fetch_news] {ticker}: {exc}")
        return []


def fetch_market_news(max_items: int = 8) -> list[dict[str, str]]:
    """
    Ambil berita market-wide IHSG dari Yahoo Finance (^JKSE + IDR=X).
    Digunakan untuk mendeteksi event besar: MSCI, BI Rate, Fed, dll.
    """
    # ^GSPC + ^TNX untuk berita global (Fed, inflasi AS, dll)
    # ^JKSE + IDR=X + EIDO untuk berita domestik IHSG
    sources = ["^GSPC", "^TNX", "^JKSE", "IDR=X", "EIDO"]
    seen_titles: set[str] = set()
    combined = []
    for source_ticker in sources:
        news_list = fetch_news(source_ticker, max_items=max_items)
        for item in news_list:
            title = item.get("title", "").strip()
            if title and title not in seen_titles:
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
