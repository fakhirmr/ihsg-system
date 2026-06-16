"""
IHSG Trading System — Technical Calculator
Computes all technical indicators from raw OHLCV data using only pandas/numpy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TechnicalData:
    """Holds all computed technical indicator values for a ticker."""
    ticker: str

    # Current price
    current_price: float = 0.0

    # Moving Averages
    ema_20: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0

    # Momentum
    rsi_14: float = 50.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0

    # Bollinger Bands
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0

    # VWAP
    vwap: float = 0.0

    # Support / Resistance
    support_1: float = 0.0
    support_2: float = 0.0
    resistance_1: float = 0.0
    resistance_2: float = 0.0

    # Trend
    trend: str = "NEUTRAL"          # UPTREND | DOWNTREND | NEUTRAL

    # Patterns
    is_breakout: bool = False
    is_breakdown: bool = False
    is_consolidation_breakout: bool = False  # breakout setelah ATR rendah (konsolidasi)
    higher_high: bool = False
    lower_low: bool = False

    # Computed from raw data
    atr_14: float = 0.0             # Average True Range (volatility)
    price_vs_ema20: float = 0.0     # % distance of price from EMA20
    is_above_ema20: bool = False
    is_above_ema50: bool = False
    is_above_ema200: bool = False


# ─── Indicator Functions ──────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def _macd(closes: pd.Series) -> tuple[float, float, float]:
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    line = ema12 - ema26
    signal = _ema(line, 9)
    hist = line - signal
    return (
        float(line.iloc[-1]) if pd.notna(line.iloc[-1]) else 0.0,
        float(signal.iloc[-1]) if pd.notna(signal.iloc[-1]) else 0.0,
        float(hist.iloc[-1]) if pd.notna(hist.iloc[-1]) else 0.0,
    )


def _bollinger(closes: pd.Series, period: int = 20, std: float = 2.0) -> tuple[float, float, float]:
    sma = closes.rolling(period).mean()
    stdev = closes.rolling(period).std()
    upper = sma + stdev * std
    lower = sma - stdev * std
    return (
        float(upper.iloc[-1]) if pd.notna(upper.iloc[-1]) else 0.0,
        float(sma.iloc[-1]) if pd.notna(sma.iloc[-1]) else 0.0,
        float(lower.iloc[-1]) if pd.notna(lower.iloc[-1]) else 0.0,
    )


def _vwap(hist: pd.DataFrame) -> float:
    tp = (hist["High"] + hist["Low"] + hist["Close"]) / 3
    vwap = (tp * hist["Volume"]).cumsum() / hist["Volume"].cumsum()
    val = vwap.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _atr(hist: pd.DataFrame, period: int = 14) -> float:
    high = hist["High"]
    low = hist["Low"]
    prev_close = hist["Close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(period).mean()
    val = atr.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _support_resistance(closes: pd.Series, lookback: int = 30) -> tuple[float, float, float, float]:
    recent = closes.tail(lookback)
    r1 = float(recent.max())
    s1 = float(recent.min())
    mid = (r1 + s1) / 2
    r2 = r1 + abs(r1 - mid) * 0.618
    s2 = s1 - abs(s1 - mid) * 0.618
    return s1, s2, r1, r2


def _detect_trend(closes: pd.Series, short: int = 10, long: int = 20) -> str:
    if len(closes) < long:
        return "NEUTRAL"
    short_avg = float(closes.tail(short).mean())
    long_avg = float(closes.tail(long).mean())
    current = float(closes.iloc[-1])
    ema20 = float(_ema(closes, 20).iloc[-1])
    ema50 = float(_ema(closes, min(50, len(closes))).iloc[-1])

    if current > ema20 > ema50 and short_avg > long_avg:
        return "UPTREND"
    if current < ema20 < ema50 and short_avg < long_avg:
        return "DOWNTREND"
    return "NEUTRAL"


def _detect_breakout(closes: pd.Series, lookback: int = 20) -> tuple[bool, bool]:
    if len(closes) < lookback + 2:
        return False, False
    prior = closes.iloc[-(lookback + 1):-1]
    r = float(prior.max())
    s = float(prior.min())
    current = float(closes.iloc[-1])
    is_breakout = current > r * 1.01
    is_breakdown = current < s * 0.99
    return is_breakout, is_breakdown


# ─── Main Calculator ──────────────────────────────────────────────────────────

def calculate_technical_data(ticker: str, hist: pd.DataFrame) -> TechnicalData:
    """
    Compute all technical indicators from OHLCV history.

    Args:
        ticker: Ticker symbol string.
        hist:   OHLCV DataFrame from yfinance (auto-adjusted).

    Returns:
        TechnicalData with all indicators populated.
    """
    td = TechnicalData(ticker=ticker)

    if hist.empty or len(hist) < 20:
        logger.warning(f"[{ticker}] Insufficient history ({len(hist)} bars). Skipping indicators.")
        return td

    closes = hist["Close"].dropna()

    if len(closes) < 20:
        return td

    td.current_price = float(closes.iloc[-1])

    # Moving averages
    td.ema_20 = float(_ema(closes, 20).iloc[-1])
    td.ema_50 = float(_ema(closes, min(50, len(closes))).iloc[-1])
    td.ema_200 = float(_ema(closes, min(200, len(closes))).iloc[-1])

    # Flags
    td.is_above_ema20 = td.current_price > td.ema_20
    td.is_above_ema50 = td.current_price > td.ema_50
    td.is_above_ema200 = td.current_price > td.ema_200
    td.price_vs_ema20 = (
        (td.current_price - td.ema_20) / td.ema_20 * 100 if td.ema_20 > 0 else 0.0
    )

    # Momentum
    td.rsi_14 = _rsi(closes)
    td.macd_line, td.macd_signal, td.macd_histogram = _macd(closes)

    # Bollinger Bands
    td.bb_upper, td.bb_middle, td.bb_lower = _bollinger(closes)

    # VWAP
    td.vwap = _vwap(hist)

    # ATR
    td.atr_14 = _atr(hist)

    # Support / Resistance
    td.support_1, td.support_2, td.resistance_1, td.resistance_2 = _support_resistance(closes)

    # Trend
    td.trend = _detect_trend(closes)

    # Breakout / Breakdown
    td.is_breakout, td.is_breakdown = _detect_breakout(closes)

    # Consolidation Breakout: breakout + ATR sebelumnya rendah (harga diam lalu meledak)
    if td.is_breakout and len(hist) >= 30:
        _h  = hist["High"]
        _lo = hist["Low"]
        _pc = hist["Close"].shift(1)
        _tr = pd.concat(
            [_h - _lo, (_h - _pc).abs(), (_lo - _pc).abs()], axis=1
        ).max(axis=1)
        _atr5      = _tr.rolling(5).mean()
        _atr5_mean = _atr5.rolling(20).mean()
        if pd.notna(_atr5.iloc[-2]) and pd.notna(_atr5_mean.iloc[-2]) and float(_atr5_mean.iloc[-2]) > 0:
            td.is_consolidation_breakout = float(_atr5.iloc[-2]) < float(_atr5_mean.iloc[-2]) * 0.7

    # Higher High / Lower Low (last 3 bars)
    if len(closes) >= 3:
        c = closes.iloc
        td.higher_high = float(c[-1]) > float(c[-2]) > float(c[-3])
        td.lower_low = float(c[-1]) < float(c[-2]) < float(c[-3])

    logger.debug(
        f"[{ticker}] TechData — RSI:{td.rsi_14:.1f} | MACD:{td.macd_histogram:+.2f} "
        f"| Trend:{td.trend} | Breakout:{td.is_breakout} | ConsolBrk:{td.is_consolidation_breakout}"
    )

    return td
