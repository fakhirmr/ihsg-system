"""
IHSG Trading System — Volume & Unusual Activity Agent
Detects smart-money accumulation, distribution, and abnormal volume.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from agents.base_agent import BaseAgent
from config import VOLUME_SPIKE_THRESHOLD
from utils.data_fetcher import StockData

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Volume & Unusual Activity Agent untuk saham IHSG Indonesia.
Tugasmu mendeteksi aktivitas volume abnormal, akumulasi smart money, dan distribusi.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "status": "<Unusual Bullish|Unusual Bearish|Normal Bullish|Normal Bearish|Normal>",
  "confidence": <integer 0-100>,
  "is_unusual": <true|false>,
  "activity_type": "<Accumulation|Distribution|Breakout Volume|Selling Climax|Normal>",
  "reasons": ["<alasan 1>", "<alasan 2>"],
  "summary": "<ringkasan singkat dalam Bahasa Indonesia>"
}
"""


class VolumeAgent(BaseAgent):
    """Detects unusual volume activity and classifies accumulation/distribution."""

    def analyze(self, stock_data: StockData) -> dict[str, Any]:  # type: ignore[override]
        """
        Analyze volume patterns for unusual activity.

        Args:
            stock_data: Populated StockData with volume and price history.

        Returns:
            Dict with status, confidence, is_unusual, activity_type, reasons.
        """
        fallback = {
            "status": "Normal",
            "confidence": 0,
            "is_unusual": False,
            "activity_type": "Normal",
            "reasons": ["Data volume tidak mencukupi"],
            "summary": "Analisis volume tidak dapat diselesaikan.",
        }

        if not stock_data.is_valid or stock_data.price_history.empty:
            return fallback

        user_message = self._build_prompt(stock_data)
        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        logger.info(
            f"[VolumeAgent] {stock_data.ticker} → "
            f"Status:{result.get('status')} "
            f"RelVol:{stock_data.relative_volume:.2f}x "
            f"Unusual:{result.get('is_unusual')}"
        )
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_volume_stats(self, hist: pd.DataFrame) -> dict[str, float]:
        """Compute rolling volume statistics from OHLCV DataFrame."""
        vol = hist["Volume"]
        stats: dict[str, float] = {
            "current": float(vol.iloc[-1]),
            "avg_5": float(vol.tail(5).mean()),
            "avg_10": float(vol.tail(10).mean()),
            "avg_20": float(vol.tail(20).mean()),
            "max_20": float(vol.tail(20).max()),
            "min_20": float(vol.tail(20).min()),
        }
        stats["rel_vol_5"] = stats["current"] / stats["avg_5"] if stats["avg_5"] > 0 else 1.0
        stats["rel_vol_20"] = stats["current"] / stats["avg_20"] if stats["avg_20"] > 0 else 1.0
        return stats

    def _compute_price_volume_relationship(self, hist: pd.DataFrame) -> dict[str, Any]:
        """Assess if volume follows price direction (accumulation vs distribution)."""
        if len(hist) < 5:
            return {"assessment": "Insufficient data"}

        last_5 = hist.tail(5)
        up_days = last_5[last_5["Close"] >= last_5["Open"]]
        down_days = last_5[last_5["Close"] < last_5["Open"]]

        avg_vol_up = float(up_days["Volume"].mean()) if not up_days.empty else 0.0
        avg_vol_down = float(down_days["Volume"].mean()) if not down_days.empty else 0.0

        assessment = "Neutral"
        if avg_vol_up > avg_vol_down * 1.3:
            assessment = "Accumulation (volume up-days > down-days)"
        elif avg_vol_down > avg_vol_up * 1.3:
            assessment = "Distribution (volume down-days > up-days)"

        return {
            "avg_vol_up_days": avg_vol_up,
            "avg_vol_down_days": avg_vol_down,
            "assessment": assessment,
        }

    def _build_prompt(self, sd: StockData) -> str:
        hist = sd.price_history
        vstats = self._compute_volume_stats(hist)
        pvr = self._compute_price_volume_relationship(hist)

        is_spike = sd.relative_volume >= VOLUME_SPIKE_THRESHOLD

        lines = [
            f"=== VOLUME DATA: {sd.ticker} ({sd.company_name}) ===",
            f"Harga Saat Ini: {sd.current_price:,.0f} | Perubahan: {sd.day_change_pct:+.2f}%",
            "",
            "--- VOLUME STATISTICS ---",
            f"Volume Hari Ini  : {vstats['current']:,.0f}",
            f"Avg Volume 5 Hari: {vstats['avg_5']:,.0f}",
            f"Avg Volume 20 Hari: {vstats['avg_20']:,.0f}",
            f"Volume Maks 20H  : {vstats['max_20']:,.0f}",
            f"Volume Min 20H   : {vstats['min_20']:,.0f}",
            "",
            f"Relative Volume (vs 5H ): {vstats['rel_vol_5']:.2f}x",
            f"Relative Volume (vs 20H): {vstats['rel_vol_20']:.2f}x",
            f"Volume Spike Terdeteksi : {'✅ YA (threshold ≥' + str(VOLUME_SPIKE_THRESHOLD) + 'x)' if is_spike else '❌ Tidak'}",
            "",
            "--- PRICE-VOLUME RELATIONSHIP (5 HARI TERAKHIR) ---",
            f"Avg Vol Hari Naik  : {pvr.get('avg_vol_up_days', 0):,.0f}",
            f"Avg Vol Hari Turun : {pvr.get('avg_vol_down_days', 0):,.0f}",
            f"Penilaian PV       : {pvr.get('assessment', 'N/A')}",
            "",
        ]

        # Add last 5 bars detail
        lines.append("--- 5 BAR TERAKHIR ---")
        for _, row in hist.tail(5).iterrows():
            date_str = str(row.name)[:10]
            close = float(row["Close"])
            vol = float(row["Volume"])
            direction = "▲" if float(row["Close"]) >= float(row["Open"]) else "▼"
            lines.append(
                f"{date_str}: {direction} Close={close:,.0f} | Vol={vol:,.0f}"
            )

        lines += [
            "",
            "Analisis aktivitas volume dan tentukan apakah ada akumulasi, distribusi, "
            "atau volume spike abnormal. Kembalikan HANYA JSON sesuai format.",
        ]

        return "\n".join(lines)
