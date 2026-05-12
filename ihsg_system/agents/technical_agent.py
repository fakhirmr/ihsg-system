"""
IHSG Trading System — Technical Analysis Agent
Generates entry/TP/SL signals from computed technical indicators.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from utils.data_fetcher import StockData
from utils.technical_calculator import TechnicalData

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Technical Analysis Agent untuk saham IHSG Indonesia yang sangat berpengalaman.
Kamu menganalisis indikator teknikal dan menentukan sinyal trading dengan entry, TP, dan SL.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "signal": "<BUY|SELL|NEUTRAL>",
  "confidence": <integer 0-100>,
  "entry": <float harga entry>,
  "tp1": <float target profit 1>,
  "tp2": <float target profit 2>,
  "sl": <float stop loss>,
  "timeframe": "<Scalping|Intraday|Swing|Position>",
  "reasons": ["<alasan teknikal 1>", "<alasan teknikal 2>"],
  "invalidation": "<kondisi yang membatalkan sinyal>",
  "summary": "<ringkasan singkat dalam Bahasa Indonesia>"
}

Aturan wajib:
- Entry harus dekat harga saat ini (±2%)
- TP1 dan TP2 harus realistis berdasarkan ATR dan resistance
- SL harus di bawah support untuk BUY, di atas resistance untuk SELL
- Risk:Reward minimal 1:1.5
- Jika sinyal NEUTRAL, tetap isi entry=TP1=TP2=SL=harga saat ini
"""


class TechnicalAgent(BaseAgent):
    """Generates BUY/SELL signals with precise entry, TP, and SL levels."""

    def analyze(  # type: ignore[override]
        self, stock_data: StockData, tech_data: TechnicalData
    ) -> dict[str, Any]:
        """
        Run technical analysis.

        Args:
            stock_data: Raw stock data (price, volume).
            tech_data:  Computed indicators from technical_calculator.

        Returns:
            Dict with signal, confidence, entry, tp1, tp2, sl, reasons, etc.
        """
        fallback = {
            "signal": "NEUTRAL",
            "confidence": 0,
            "entry": stock_data.current_price,
            "tp1": stock_data.current_price,
            "tp2": stock_data.current_price,
            "sl": stock_data.current_price,
            "timeframe": "Swing",
            "reasons": ["Data teknikal tidak mencukupi"],
            "invalidation": "N/A",
            "summary": "Analisis teknikal tidak dapat diselesaikan.",
        }

        if not stock_data.is_valid or stock_data.current_price <= 0:
            return fallback

        user_message = self._build_prompt(stock_data, tech_data)
        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        # Sanity check: ensure numeric fields are float
        for field in ("entry", "tp1", "tp2", "sl"):
            try:
                result[field] = float(result[field])
            except (TypeError, ValueError, KeyError):
                result[field] = stock_data.current_price

        result.setdefault("confidence", 0)
        result.setdefault("signal", "NEUTRAL")

        logger.info(
            f"[TechnicalAgent] {stock_data.ticker} → "
            f"Signal:{result['signal']} Conf:{result['confidence']}% "
            f"Entry:{result['entry']:,.0f} TP1:{result['tp1']:,.0f} SL:{result['sl']:,.0f}"
        )
        return result

    def _build_prompt(self, sd: StockData, td: TechnicalData) -> str:
        def _fmt(v: float) -> str:
            return f"{v:,.2f}" if v else "N/A"

        trend_emoji = {
            "UPTREND": "📈 UPTREND",
            "DOWNTREND": "📉 DOWNTREND",
            "NEUTRAL": "➡️ NEUTRAL",
        }.get(td.trend, td.trend)

        lines = [
            f"=== TECHNICAL DATA: {sd.ticker} ({sd.company_name}) ===",
            f"Harga Saat Ini: {_fmt(sd.current_price)}",
            f"Open: {_fmt(sd.day_open)} | High: {_fmt(sd.day_high)} | Low: {_fmt(sd.day_low)}",
            f"Perubahan Harian: {sd.day_change_pct:+.2f}%",
            f"Relative Volume: {sd.relative_volume:.2f}x rata-rata 20 hari",
            "",
            "--- MOVING AVERAGES ---",
            f"EMA 20 : {_fmt(td.ema_20)} {'▲ Di Atas' if td.is_above_ema20 else '▼ Di Bawah'}",
            f"EMA 50 : {_fmt(td.ema_50)} {'▲ Di Atas' if td.is_above_ema50 else '▼ Di Bawah'}",
            f"EMA 200: {_fmt(td.ema_200)} {'▲ Di Atas' if td.is_above_ema200 else '▼ Di Bawah'}",
            f"Jarak dari EMA20: {td.price_vs_ema20:+.2f}%",
            "",
            "--- MOMENTUM ---",
            f"RSI (14): {td.rsi_14:.1f} {'(Overbought)' if td.rsi_14 > 70 else '(Oversold)' if td.rsi_14 < 30 else '(Normal)'}",
            f"MACD Line   : {td.macd_line:+.4f}",
            f"MACD Signal : {td.macd_signal:+.4f}",
            f"MACD Histogram: {td.macd_histogram:+.4f} {'(Bullish momentum)' if td.macd_histogram > 0 else '(Bearish momentum)'}",
            "",
            "--- BOLLINGER BANDS ---",
            f"BB Upper : {_fmt(td.bb_upper)}",
            f"BB Middle: {_fmt(td.bb_middle)}",
            f"BB Lower : {_fmt(td.bb_lower)}",
            "",
            "--- SUPPORT & RESISTANCE ---",
            f"Resistance 2: {_fmt(td.resistance_2)}",
            f"Resistance 1: {_fmt(td.resistance_1)}",
            f"Support 1   : {_fmt(td.support_1)}",
            f"Support 2   : {_fmt(td.support_2)}",
            f"VWAP        : {_fmt(td.vwap)}",
            "",
            "--- TREND & PATTERN ---",
            f"Tren       : {trend_emoji}",
            f"ATR (14)   : {_fmt(td.atr_14)}",
            f"Breakout   : {'✅ YA' if td.is_breakout else '❌ Tidak'}",
            f"Breakdown  : {'✅ YA' if td.is_breakdown else '❌ Tidak'}",
            f"Higher High: {'✅ YA' if td.higher_high else '❌ Tidak'}",
            f"Lower Low  : {'✅ YA' if td.lower_low else '❌ Tidak'}",
            "",
            "Berikan sinyal trading dengan entry, TP1, TP2, dan SL yang spesifik. "
            "Kembalikan HANYA JSON sesuai format.",
        ]

        return "\n".join(lines)
