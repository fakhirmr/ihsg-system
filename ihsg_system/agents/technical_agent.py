"""
IHSG Trading System — Technical Analysis Agent (Orchestrator)
=============================================================
TechnicalAgent kini menjadi orchestrator yang menjalankan 5 sub-agent
spesialis strategi, lalu memilih sinyal dengan confidence tertinggi.

Sub-agents:
  1. BuyOnWeaknessAgent     — Pullback ke EMA dalam uptrend
  2. BuyOnBreakoutAgent     — Break resistance + volume spike
  3. ReversalBounceAgent    — Oversold + bounce dari support
  4. MomentumContinuationAgent — Tren kuat + MACD + Higher High
  5. RangeSupportBuyAgent   — Beli di support dalam sideways
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from agents.technical_sub_agents import (
    BuyOnWeaknessAgent,
    BuyOnBreakoutAgent,
    ReversalBounceAgent,
    MomentumContinuationAgent,
    RangeSupportBuyAgent,
)
from utils.data_fetcher import StockData
from utils.technical_calculator import TechnicalData

logger = logging.getLogger(__name__)

# Sub-agents diinstansiasi sekali (lazy, shared)
_SUB_AGENTS: dict[str, BaseAgent] | None = None


def _get_sub_agents() -> dict[str, BaseAgent]:
    global _SUB_AGENTS
    if _SUB_AGENTS is None:
        _SUB_AGENTS = {
            "Buy on Weakness":       BuyOnWeaknessAgent(),
            "Buy on Breakout":       BuyOnBreakoutAgent(),
            "Reversal Bounce":       ReversalBounceAgent(),
            "Momentum Continuation": MomentumContinuationAgent(),
            "Range Support Buy":     RangeSupportBuyAgent(),
        }
    return _SUB_AGENTS


_SYSTEM_PROMPT = """\
Kamu adalah Technical Analysis Agent untuk saham IHSG Indonesia yang sangat berpengalaman.
Kembalikan HANYA JSON valid tanpa teks tambahan.
Format JSON wajib:
{
  "signal": "<BUY|SELL|NEUTRAL>",
  "confidence": <integer 0-100>,
  "entry": <float>,
  "tp1": <float>,
  "tp2": <float>,
  "sl": <float>,
  "timeframe": "<Scalping|Intraday|Swing|Position>",
  "reasons": ["<alasan 1>", "<alasan 2>"],
  "invalidation": "<kondisi pembatalan>",
  "summary": "<ringkasan Bahasa Indonesia>"
}
"""


class TechnicalAgent(BaseAgent):
    """
    Orchestrator teknikal: menjalankan 5 sub-agent strategi,
    memilih sinyal dengan confidence tertinggi, dan melaporkan
    strategi mana yang aktif.
    """

    def analyze(  # type: ignore[override]
        self, stock_data: StockData, tech_data: TechnicalData
    ) -> dict[str, Any]:
        p = stock_data.current_price
        fallback = {
            "signal": "NEUTRAL", "confidence": 0,
            "entry": p, "tp1": p, "tp2": p, "sl": p,
            "timeframe": "Swing", "strategy": "None",
            "reasons": ["Data tidak mencukupi"], "invalidation": "N/A",
            "summary": "Analisis teknikal tidak dapat diselesaikan.",
            "sub_signals": {},
        }

        if not stock_data.is_valid or p <= 0:
            return fallback

        sub_agents = _get_sub_agents()
        sub_results: dict[str, dict] = {}

        for name, agent in sub_agents.items():
            try:
                sub_results[name] = agent.analyze(stock_data, tech_data)
            except Exception as e:
                logger.warning(f"[TechOrchestrator] {stock_data.ticker} {name}: {e}")

        # Pilih sinyal terbaik
        best: dict[str, Any] | None = None
        for r in sub_results.values():
            sig  = r.get("signal", "NEUTRAL")
            conf = r.get("confidence", 0)
            if best is None:
                best = r
            else:
                bsig  = best.get("signal", "NEUTRAL")
                bconf = best.get("confidence", 0)
                if (bsig == "NEUTRAL" and sig != "NEUTRAL") or \
                   (sig != "NEUTRAL" and conf > bconf) or \
                   (sig == "NEUTRAL" and conf > bconf and bsig == "NEUTRAL"):
                    best = r

        if best is None:
            return fallback

        for field in ("entry", "tp1", "tp2", "sl"):
            try:
                best[field] = float(best[field])
            except (TypeError, ValueError, KeyError):
                best[field] = p

        best.setdefault("timeframe", "Swing")
        best.setdefault("invalidation", "N/A")
        best["sub_signals"] = {
            name: {"signal": r.get("signal"), "confidence": r.get("confidence")}
            for name, r in sub_results.items()
        }

        logger.info(
            f"[TechOrchestrator] {stock_data.ticker} -> "
            f"Strategy:{best.get('strategy')} | "
            f"Signal:{best.get('signal')} Conf:{best.get('confidence')}% | "
            f"Entry:{best.get('entry'):,.0f} TP1:{best.get('tp1'):,.0f}"
        )
        return best

    def get_all_strategies(
        self, stock_data: StockData, tech_data: TechnicalData
    ) -> dict[str, dict[str, Any]]:
        """Jalankan semua sub-agent, kembalikan semua hasil per strategi."""
        results = {}
        for name, agent in _get_sub_agents().items():
            try:
                results[name] = agent.analyze(stock_data, tech_data)
            except Exception as e:
                logger.warning(f"[TechOrchestrator] {stock_data.ticker} {name}: {e}")
        return results

