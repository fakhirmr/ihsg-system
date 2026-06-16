"""
IHSG — Technical Sub-Agents
============================
5 sub-agent spesialis strategi teknikal:
  1. BuyOnWeakness    — Pullback ke EMA dalam uptrend
  2. BuyOnBreakout    — Break resistance + volume spike
  3. ReversalBounce   — Oversold + bounce dari support
  4. MomentumCont     — Tren kuat + MACD + Higher High
  5. RangeSupport     — Beli di support dalam sideways

Setiap sub-agent mengembalikan format sama:
  {strategy, signal, confidence, entry, tp1, tp2, sl, reasons, summary}
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from utils.data_fetcher import StockData
from utils.technical_calculator import TechnicalData

logger = logging.getLogger(__name__)

# ── Shared helpers ─────────────────────────────────────────────────────────────

_BASE_FALLBACK = {
    "strategy": "None",
    "signal": "NEUTRAL",
    "confidence": 0,
    "entry": 0.0,
    "tp1": 0.0,
    "tp2": 0.0,
    "sl": 0.0,
    "reasons": [],
    "summary": "Kondisi tidak memenuhi kriteria strategi ini.",
}

_JSON_FORMAT = """\
{
  "strategy": "<nama strategi>",
  "signal": "<BUY|SELL|NEUTRAL>",
  "confidence": <integer 0-100>,
  "entry": <float>,
  "tp1": <float target profit 1>,
  "tp2": <float target profit 2>,
  "sl": <float stop loss>,
  "reasons": ["<alasan 1>", "<alasan 2>"],
  "summary": "<ringkasan singkat Bahasa Indonesia>"
}"""


def _fallback(strategy: str, price: float) -> dict[str, Any]:
    return {**_BASE_FALLBACK, "strategy": strategy,
            "entry": price, "tp1": price, "tp2": price, "sl": price}


def _fmt(v: float) -> str:
    return f"{v:,.2f}" if v else "N/A"


# ══════════════════════════════════════════════════════════════════════════════
# 1. BUY ON WEAKNESS
# ══════════════════════════════════════════════════════════════════════════════

_BOW_SYSTEM = f"""\
Kamu adalah spesialis strategi "Buy on Weakness" untuk saham IHSG.
Strategi ini mencari saham dalam UPTREND yang sedang pullback ke EMA20/EMA50
sebagai kesempatan beli dengan risiko rendah.

Kriteria ideal:
- Tren jangka menengah UPTREND (di atas EMA50/EMA200)
- Harga pullback ke area EMA20 atau EMA50 (dalam ±2%)
- RSI turun ke 35-50 (tidak overbought)
- Volume pullback lebih rendah dari rata-rata (healthy pullback)
- MACD masih di area positif atau baru sedikit negatif
- Higher High sebelumnya terbentuk

Kembalikan HANYA JSON valid:
{_JSON_FORMAT}
"""

_BOW_USER = """\
=== DATA: {ticker} — Buy on Weakness Analysis ===
Harga      : {price:,.0f} | Perubahan: {change:+.2f}%
EMA20      : {ema20:,.0f} ({vs_ema20:+.1f}% dari harga)
EMA50      : {ema50:,.0f} ({vs_ema50:+.1f}% dari harga)
EMA200     : {ema200:,.0f}
RSI        : {rsi:.1f}
MACD Hist  : {macd:+.4f}
Volume     : {rvol:.2f}x rata-rata
Trend      : {trend}
Higher High: {hh}
Support1   : {s1:,.0f}
ATR        : {atr:,.2f}

Apakah ini kondisi pullback yang layak dibeli (Buy on Weakness)?
Hitung entry ideal, TP1 (ke resistance), TP2, dan SL (di bawah support/EMA).
Kembalikan HANYA JSON.
"""


class BuyOnWeaknessAgent(BaseAgent):
    """Pullback ke EMA dalam uptrend = peluang entry risiko rendah."""

    def analyze(self, sd: StockData, td: TechnicalData) -> dict[str, Any]:  # type: ignore[override]
        fb = _fallback("Buy on Weakness", sd.current_price)
        if not sd.is_valid:
            return fb

        p = sd.current_price
        vs_ema20 = ((p - td.ema_20) / td.ema_20 * 100) if td.ema_20 else 0
        vs_ema50 = ((p - td.ema_50) / td.ema_50 * 100) if td.ema_50 else 0

        msg = _BOW_USER.format(
            ticker=sd.ticker, price=p, change=sd.day_change_pct,
            ema20=td.ema_20, vs_ema20=vs_ema20,
            ema50=td.ema_50, vs_ema50=vs_ema50,
            ema200=td.ema_200, rsi=td.rsi_14,
            macd=td.macd_histogram, rvol=sd.relative_volume,
            trend=td.trend, hh="Ya" if td.higher_high else "Tidak",
            s1=td.support_1, atr=td.atr_14,
        )
        result = self.call_claude_json(_BOW_SYSTEM, msg, fb)
        result["strategy"] = "Buy on Weakness"
        logger.info(f"[BoW] {sd.ticker} -> {result.get('signal')} {result.get('confidence')}%")
        return result


# ══════════════════════════════════════════════════════════════════════════════
# 2. BUY ON BREAKOUT
# ══════════════════════════════════════════════════════════════════════════════

_BOB_SYSTEM = f"""\
Kamu adalah spesialis strategi "Buy on Breakout" untuk saham IHSG.
Strategi ini mencari saham yang baru saja atau sedang break di atas resistance
dengan konfirmasi volume yang kuat.

Kriteria ideal:
- Harga menembus Resistance 1 atau Resistance 2 (breakout)
- Volume saat breakout >= 1.5x rata-rata (konfirmasi kuat)
- RSI 50-70 (momentum bullish, belum overbought)
- Harga di atas semua EMA (20, 50, 200)
- MACD histogram positif dan naik
- Higher High baru terbentuk

Waspadai fake breakout: volume rendah + RSI sangat overbought.
Target TP menggunakan proyeksi ATR dari titik breakout.

Kembalikan HANYA JSON valid:
{_JSON_FORMAT}
"""

_BOB_USER = """\
=== DATA: {ticker} — Buy on Breakout Analysis ===
Harga      : {price:,.0f} | Perubahan: {change:+.2f}%
Breakout   : {breakout}
Resistance1: {r1:,.0f} ({vs_r1:+.1f}% dari harga)
Resistance2: {r2:,.0f}
Support1   : {s1:,.0f}
Volume     : {rvol:.2f}x rata-rata
RSI        : {rsi:.1f}
MACD Hist  : {macd:+.4f}
EMA20      : {ema20:,.0f} | EMA50: {ema50:,.0f}
Di atas EMA20: {above20} | Di atas EMA50: {above50} | Di atas EMA200: {above200}
ATR        : {atr:,.2f}
Higher High: {hh}

Apakah ini breakout valid yang layak diikuti?
Entry sebaiknya di harga breakout konfirmasi.
Kembalikan HANYA JSON.
"""


class BuyOnBreakoutAgent(BaseAgent):
    """Break resistance + volume spike = momentum entry."""

    def analyze(self, sd: StockData, td: TechnicalData) -> dict[str, Any]:  # type: ignore[override]
        fb = _fallback("Buy on Breakout", sd.current_price)
        if not sd.is_valid:
            return fb

        p = sd.current_price
        vs_r1 = ((p - td.resistance_1) / td.resistance_1 * 100) if td.resistance_1 else 0

        msg = _BOB_USER.format(
            ticker=sd.ticker, price=p, change=sd.day_change_pct,
            breakout="YA ✅" if td.is_breakout else "Belum",
            r1=td.resistance_1, vs_r1=vs_r1, r2=td.resistance_2,
            s1=td.support_1, rvol=sd.relative_volume,
            rsi=td.rsi_14, macd=td.macd_histogram,
            ema20=td.ema_20, ema50=td.ema_50,
            above20="Ya" if td.is_above_ema20 else "Tidak",
            above50="Ya" if td.is_above_ema50 else "Tidak",
            above200="Ya" if td.is_above_ema200 else "Tidak",
            atr=td.atr_14, hh="Ya" if td.higher_high else "Tidak",
        )
        result = self.call_claude_json(_BOB_SYSTEM, msg, fb)
        result["strategy"] = "Buy on Breakout"
        logger.info(f"[BoB] {sd.ticker} -> {result.get('signal')} {result.get('confidence')}%")
        return result


# ══════════════════════════════════════════════════════════════════════════════
# 3. REVERSAL BOUNCE
# ══════════════════════════════════════════════════════════════════════════════

_REV_SYSTEM = f"""\
Kamu adalah spesialis strategi "Reversal Bounce" untuk saham IHSG.
Strategi ini mencari saham yang oversold dan menunjukkan tanda-tanda pembalikan arah
dari area support kuat (bounce).

Kriteria ideal:
- RSI < 35 (oversold) atau RSI divergence bullish
- Harga mendekati atau menyentuh Support 1 atau BB Lower
- Volume mulai naik setelah periode volume rendah (akumulasi)
- Harga masih di bawah EMA tapi mulai reversal (hammer, doji, bullish engulfing)
- MACD histogram mulai naik (momentum bearish melemah)
- Bukan dalam downtrend ekstrem (hindari saham yang free fall)

Ini adalah strategi counter-trend — lebih berisiko.
SL harus ketat di bawah support.

Kembalikan HANYA JSON valid:
{_JSON_FORMAT}
"""

_REV_USER = """\
=== DATA: {ticker} — Reversal Bounce Analysis ===
Harga      : {price:,.0f} | Perubahan: {change:+.2f}%
RSI        : {rsi:.1f} (oversold < 35, normal 35-65, overbought > 70)
MACD Hist  : {macd:+.4f} (positif = momentum naik)
BB Lower   : {bbl:,.0f} ({vs_bbl:+.1f}% dari harga)
BB Middle  : {bbm:,.0f}
Support1   : {s1:,.0f} | Support2: {s2:,.0f}
Volume     : {rvol:.2f}x rata-rata
Trend      : {trend}
Lower Low  : {ll}
Di atas EMA20: {above20}
ATR        : {atr:,.2f}

Apakah ada sinyal reversal/bounce dari area support?
Kembalikan HANYA JSON.
"""


class ReversalBounceAgent(BaseAgent):
    """Oversold + support bounce = counter-trend entry."""

    def analyze(self, sd: StockData, td: TechnicalData) -> dict[str, Any]:  # type: ignore[override]
        fb = _fallback("Reversal Bounce", sd.current_price)
        if not sd.is_valid:
            return fb

        p = sd.current_price
        vs_bbl = ((p - td.bb_lower) / td.bb_lower * 100) if td.bb_lower else 0

        msg = _REV_USER.format(
            ticker=sd.ticker, price=p, change=sd.day_change_pct,
            rsi=td.rsi_14, macd=td.macd_histogram,
            bbl=td.bb_lower, vs_bbl=vs_bbl, bbm=td.bb_middle,
            s1=td.support_1, s2=td.support_2,
            rvol=sd.relative_volume, trend=td.trend,
            ll="Ya" if td.lower_low else "Tidak",
            above20="Ya" if td.is_above_ema20 else "Tidak",
            atr=td.atr_14,
        )
        result = self.call_claude_json(_REV_SYSTEM, msg, fb)
        result["strategy"] = "Reversal Bounce"
        logger.info(f"[Rev] {sd.ticker} -> {result.get('signal')} {result.get('confidence')}%")
        return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. MOMENTUM CONTINUATION
# ══════════════════════════════════════════════════════════════════════════════

_MOM_SYSTEM = f"""\
Kamu adalah spesialis strategi "Momentum Continuation" untuk saham IHSG.
Strategi ini mengikuti saham yang sedang dalam tren kuat dan menunjukkan
kelanjutan momentum (trend following).

Kriteria ideal:
- Tren UPTREND kuat (di atas EMA20, EMA50, EMA200)
- MACD histogram positif dan meningkat
- RSI 55-70 (momentum kuat tapi belum overbought ekstrem)
- Higher High terus terbentuk (pola naik konsisten)
- Volume di atas rata-rata (momentum didukung partisipasi pasar)
- Harga jauh di atas EMA200 (tren jangka panjang bullish)

Entry menggunakan pullback minor atau break high terakhir.
TP menggunakan proyeksi Fibonacci atau 2x ATR.

Kembalikan HANYA JSON valid:
{_JSON_FORMAT}
"""

_MOM_USER = """\
=== DATA: {ticker} — Momentum Continuation Analysis ===
Harga      : {price:,.0f} | Perubahan: {change:+.2f}%
Trend      : {trend}
EMA20      : {ema20:,.0f} | EMA50: {ema50:,.0f} | EMA200: {ema200:,.0f}
Di atas EMA20: {above20} | EMA50: {above50} | EMA200: {above200}
RSI        : {rsi:.1f}
MACD Line  : {macd_line:+.4f} | Signal: {macd_sig:+.4f} | Hist: {macd_hist:+.4f}
Volume     : {rvol:.2f}x rata-rata
Higher High: {hh} | Lower Low: {ll}
Resistance1: {r1:,.0f} | Resistance2: {r2:,.0f}
ATR        : {atr:,.2f}

Apakah momentum masih kuat untuk dilanjutkan (continuation)?
Kembalikan HANYA JSON.
"""


class MomentumContinuationAgent(BaseAgent):
    """Tren kuat + MACD bullish + Higher High = ride the trend."""

    def analyze(self, sd: StockData, td: TechnicalData) -> dict[str, Any]:  # type: ignore[override]
        fb = _fallback("Momentum Continuation", sd.current_price)
        if not sd.is_valid:
            return fb

        msg = _MOM_USER.format(
            ticker=sd.ticker, price=sd.current_price, change=sd.day_change_pct,
            trend=td.trend, ema20=td.ema_20, ema50=td.ema_50, ema200=td.ema_200,
            above20="Ya" if td.is_above_ema20 else "Tidak",
            above50="Ya" if td.is_above_ema50 else "Tidak",
            above200="Ya" if td.is_above_ema200 else "Tidak",
            rsi=td.rsi_14, macd_line=td.macd_line,
            macd_sig=td.macd_signal, macd_hist=td.macd_histogram,
            rvol=sd.relative_volume,
            hh="Ya" if td.higher_high else "Tidak",
            ll="Ya" if td.lower_low else "Tidak",
            r1=td.resistance_1, r2=td.resistance_2, atr=td.atr_14,
        )
        result = self.call_claude_json(_MOM_SYSTEM, msg, fb)
        result["strategy"] = "Momentum Continuation"
        logger.info(f"[Mom] {sd.ticker} -> {result.get('signal')} {result.get('confidence')}%")
        return result


# ══════════════════════════════════════════════════════════════════════════════
# 5. RANGE SUPPORT BUY
# ══════════════════════════════════════════════════════════════════════════════

_RNG_SYSTEM = f"""\
Kamu adalah spesialis strategi "Range Support Buy" untuk saham IHSG.
Strategi ini cocok untuk saham yang bergerak dalam range sideways
dan saat ini berada di area support bawah range.

Kriteria ideal:
- Market sideways / NEUTRAL trend
- Harga mendekati Support 1 (dalam ±3%)
- BB Width sempit (volatilitas rendah = market konsolidasi)
- RSI 35-50 (tidak oversold ekstrem, tapi juga tidak overbought)
- Volume rendah (akumulasi tenang)
- VWAP di atas harga saat ini (harga di bawah rata-rata harian)

Target: sell di resistance atas range (TP1 = Resistance 1).
SL ketat di bawah support.

Kembalikan HANYA JSON valid:
{_JSON_FORMAT}
"""

_RNG_USER = """\
=== DATA: {ticker} — Range Support Buy Analysis ===
Harga      : {price:,.0f} | Perubahan: {change:+.2f}%
Trend      : {trend}
Support1   : {s1:,.0f} ({vs_s1:+.1f}% dari harga)
Support2   : {s2:,.0f}
Resistance1: {r1:,.0f}
Resistance2: {r2:,.0f}
VWAP       : {vwap:,.0f}
BB Upper   : {bbu:,.0f} | BB Lower: {bbl:,.0f} | BB Middle: {bbm:,.0f}
RSI        : {rsi:.1f}
Volume     : {rvol:.2f}x rata-rata
EMA20      : {ema20:,.0f} | Di atas EMA20: {above20}
ATR        : {atr:,.2f}

Apakah harga berada di support range yang layak dibeli?
Target TP1 = resistance atas range.
Kembalikan HANYA JSON.
"""


class RangeSupportBuyAgent(BaseAgent):
    """Beli di support dalam market sideways, target resistance atas."""

    def analyze(self, sd: StockData, td: TechnicalData) -> dict[str, Any]:  # type: ignore[override]
        fb = _fallback("Range Support Buy", sd.current_price)
        if not sd.is_valid:
            return fb

        p = sd.current_price
        vs_s1 = ((p - td.support_1) / td.support_1 * 100) if td.support_1 else 0

        msg = _RNG_USER.format(
            ticker=sd.ticker, price=p, change=sd.day_change_pct,
            trend=td.trend, s1=td.support_1, vs_s1=vs_s1,
            s2=td.support_2, r1=td.resistance_1, r2=td.resistance_2,
            vwap=td.vwap, bbu=td.bb_upper, bbl=td.bb_lower, bbm=td.bb_middle,
            rsi=td.rsi_14, rvol=sd.relative_volume,
            ema20=td.ema_20,
            above20="Ya" if td.is_above_ema20 else "Tidak",
            atr=td.atr_14,
        )
        result = self.call_claude_json(_RNG_SYSTEM, msg, fb)
        result["strategy"] = "Range Support Buy"
        logger.info(f"[Rng] {sd.ticker} -> {result.get('signal')} {result.get('confidence')}%")
        return result
