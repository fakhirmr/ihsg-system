"""
TradingView Scanner — Technical Analysis (no auth, no Node.js required).
Replicates getTA() from @mathieuc/tradingview directly in Python via HTTP.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TV_SCAN_URL = "https://scanner.tradingview.com/global/scan"
_INDICATORS  = ["Recommend.Other", "Recommend.All", "Recommend.MA"]
_TIMEFRAMES  = ["1", "5", "15", "60", "240", "1D", "1W", "1M"]

_COLUMNS: list[str] = [
    ind if tf == "1D" else f"{ind}|{tf}"
    for tf in _TIMEFRAMES
    for ind in _INDICATORS
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
}


def _jk_to_idx(ticker_jk: str) -> str:
    return "IDX:" + ticker_jk.upper().replace(".JK", "")


def _parse_row(values: list) -> dict[str, dict[str, Optional[float]]]:
    advice: dict[str, dict[str, Optional[float]]] = {}
    for i, val in enumerate(values):
        col = _COLUMNS[i]
        parts = col.split("|")
        period = parts[1] if len(parts) > 1 else "1D"
        name   = parts[0].split(".")[-1]
        advice.setdefault(period, {})[name] = (
            round(val * 1000) / 500 if val is not None else None
        )
    return advice


def get_tv_ta_batch(tickers_jk: list[str]) -> dict[str, Optional[dict]]:
    """
    Fetch TradingView TA for multiple .JK tickers in a single HTTP call.
    Returns {ticker_jk: ta_dict | None}

    Each ta_dict keyed by timeframe ('1D', '1W', etc.):
      {'All': float, 'MA': float, 'Other': float}
    Values in range [-2, 2]:
      2.0 = Strong Buy  1.0 = Buy  0.0 = Neutral  -1.0 = Sell  -2.0 = Strong Sell
    """
    if not tickers_jk:
        return {}

    tv_tickers = [_jk_to_idx(t) for t in tickers_jk]
    result = {t: None for t in tickers_jk}

    try:
        resp = requests.post(
            _TV_SCAN_URL,
            json={"symbols": {"tickers": tv_tickers}, "columns": _COLUMNS},
            headers=_HEADERS,
            timeout=15,
        )
        data = resp.json().get("data", [])
        for i, row in enumerate(data):
            if i >= len(tickers_jk):
                break
            values = row.get("d", []) if row else []
            if values:
                result[tickers_jk[i]] = _parse_row(values)

    except Exception as exc:
        logger.warning(f"[TV-TA] Batch fetch failed: {exc}")

    return result


def get_tv_ta(ticker_jk: str) -> Optional[dict]:
    """Fetch TradingView TA for a single .JK ticker."""
    res = get_tv_ta_batch([ticker_jk])
    return res.get(ticker_jk)


def tv_label(value: Optional[float]) -> str:
    if value is None:  return "N/A"
    if value >= 1.5:   return "Strong Buy"
    if value >= 0.5:   return "Buy"
    if value >= -0.5:  return "Neutral"
    if value >= -1.5:  return "Sell"
    return                    "Strong Sell"


def tv_emoji(value: Optional[float]) -> str:
    if value is None:  return "⬜"
    if value >= 1.5:   return "🟢🟢"
    if value >= 0.5:   return "🟢"
    if value >= -0.5:  return "🟡"
    if value >= -1.5:  return "🔴"
    return                    "🔴🔴"


def tv_signal_line(ta: Optional[dict]) -> str:
    """One-line Telegram string: TV Signal: 🟢 Buy (1D)  |  🟡 Neutral (1W)"""
    if not ta:
        return ""
    d_val = ta.get("1D", {}).get("All")
    w_val = ta.get("1W", {}).get("All")
    return (
        f"TV Signal: {tv_emoji(d_val)} {tv_label(d_val)} (1D)"
        f"  |  {tv_emoji(w_val)} {tv_label(w_val)} (1W)"
    )
