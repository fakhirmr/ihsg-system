"""
IHSG Trading System — Supervisor AI
Orchestrates all agents, resolves conflicts, and produces the final signal.

Agent execution strategy:
  Real-time (every scan) : TechnicalAgent, VolumeAgent
  Cached per ticker 24h  : FundamentalAgent
  Cached per ticker 4h   : NewsSentimentAgent
  Cached once per session: MacroAgent (shared across all tickers)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from agents.base_agent import BaseAgent
from agents.fundamental_agent import FundamentalAgent
from agents.technical_agent import TechnicalAgent
from agents.volume_agent import VolumeAgent
from agents.macro_agent import MacroAgent
from agents.news_sentiment_agent import NewsSentimentAgent
from agents.alert_engine import AlertEngine
from agents.learning_agent import LearningAgent
from config import MIN_CONFIDENCE_ALERT
from utils.data_fetcher import StockData, fetch_stock_data
from utils.technical_calculator import TechnicalData, calculate_technical_data
from utils.agent_cache import get as cache_get, set as cache_set
from utils.agent_cache import TTL_FUNDAMENTAL, TTL_SENTIMENT, TTL_MACRO

logger = logging.getLogger(__name__)

# Signal weights per agent (must sum to 1.0)
_AGENT_WEIGHTS: dict[str, float] = {
    "technical": 0.35,
    "fundamental": 0.20,
    "volume": 0.20,
    "sentiment": 0.15,
    "macro": 0.10,
}

_SENTIMENT_TO_SIGNAL: dict[str, str] = {
    "Bullish": "BUY",
    "Neutral": "NEUTRAL",
    "Bearish": "SELL",
    "Strong Bullish": "BUY",
    "Weak": "SELL",
    "Weak Bearish": "SELL",
}


class SupervisorAI(BaseAgent):
    """
    Master orchestrator.
    Runs all agents independently, aggregates results, detects conflicts,
    and determines the final trading signal.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fundamental_agent = FundamentalAgent()
        self.technical_agent = TechnicalAgent()
        self.volume_agent = VolumeAgent()
        self.macro_agent = MacroAgent()
        self.sentiment_agent = NewsSentimentAgent()
        self.alert_engine = AlertEngine()
        self.learning_agent = LearningAgent()
        # Session-level macro cache: one call shared across all tickers this run
        self._session_macro: Optional[dict[str, Any]] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(  # type: ignore[override]
        self,
        ticker: str,
        news_text: Optional[str] = None,
        send_alert: bool = True,
        record: bool = True,
    ) -> dict[str, Any]:
        """
        Full multi-agent analysis for a single ticker.

        Args:
            ticker:     Yahoo Finance ticker (e.g., 'BBRI.JK').
            news_text:  Optional news text to inject into sentiment agent.
            send_alert: Whether to dispatch a Telegram alert.
            record:     Whether to save the signal to history.

        Returns:
            Complete supervisor result dict.
        """
        logger.info(f"[Supervisor] Starting analysis for {ticker}")
        context = datetime.now().strftime("%Y-%m-%d %H:%M WIB")

        # ── 1. Fetch data ─────────────────────────────────────────────────────
        stock_data: StockData = fetch_stock_data(ticker)
        if not stock_data.is_valid:
            return self._error_result(ticker, stock_data.error or "Data fetch failed")

        tech_data: TechnicalData = calculate_technical_data(
            ticker, stock_data.price_history
        )

        # ── 2. Run agents (real-time vs cached) ──────────────────────────────
        logger.info(f"[Supervisor] Running agents for {ticker}...")

        # ── REAL-TIME: always fresh ───────────────────────────────────────────
        technical_result = self.technical_agent.analyze(stock_data, tech_data)
        volume_result    = self.volume_agent.analyze(stock_data)

        # ── CACHED: Fundamental (24h per ticker) ──────────────────────────────
        fund_key = f"fundamental:{ticker}"
        fundamental_result = cache_get(fund_key, TTL_FUNDAMENTAL)
        if fundamental_result is None:
            logger.info(f"[Supervisor] Fundamental CACHE MISS for {ticker}, calling API...")
            fundamental_result = self.fundamental_agent.analyze(stock_data)
            cache_set(fund_key, fundamental_result)
        else:
            logger.info(f"[Supervisor] Fundamental CACHE HIT for {ticker}")

        # ── CACHED: Macro (2h, shared across all tickers this session) ────────
        if self._session_macro is None:
            macro_cached = cache_get("macro:session", TTL_MACRO)
            if macro_cached is None:
                logger.info(f"[Supervisor] Macro CACHE MISS, calling API...")
                self._session_macro = self.macro_agent.analyze(
                    ticker=ticker, sector=stock_data.sector, context=context
                )
                cache_set("macro:session", self._session_macro)
            else:
                logger.info(f"[Supervisor] Macro CACHE HIT (session)")
                self._session_macro = macro_cached
        macro_result = self._session_macro

        # ── CACHED: Sentiment (4h per ticker) ─────────────────────────────────
        sent_key = f"sentiment:{ticker}"
        sentiment_result = cache_get(sent_key, TTL_SENTIMENT)
        if sentiment_result is None:
            logger.info(f"[Supervisor] Sentiment CACHE MISS for {ticker}, calling API...")
            sentiment_result = self.sentiment_agent.analyze(
                ticker=ticker,
                company_name=stock_data.company_name,
                sector=stock_data.sector,
                industry=stock_data.industry,
                current_price=stock_data.current_price,
                day_change_pct=stock_data.day_change_pct,
                news_text=news_text,
            )
            cache_set(sent_key, sentiment_result)
        else:
            logger.info(f"[Supervisor] Sentiment CACHE HIT for {ticker}")

        agent_results = {
            "fundamental": fundamental_result,
            "technical": technical_result,
            "volume": volume_result,
            "macro": macro_result,
            "sentiment": sentiment_result,
        }

        # ── 3. Aggregate and resolve ───────────────────────────────────────────
        final_signal, confidence, conflicts = self._aggregate(
            technical_result, fundamental_result,
            volume_result, macro_result, sentiment_result,
        )

        # ── 4. Determine levels ───────────────────────────────────────────────
        entry = float(technical_result.get("entry", stock_data.current_price))
        tp1 = float(technical_result.get("tp1", stock_data.current_price))
        tp2 = float(technical_result.get("tp2", stock_data.current_price))
        sl = float(technical_result.get("sl", stock_data.current_price))
        timeframe = technical_result.get("timeframe", "Swing")

        # Fallback: derive levels from tech data if not available
        if tp1 <= entry:
            tp1 = tech_data.resistance_1
        if tp2 <= tp1:
            tp2 = tech_data.resistance_2
        if sl >= entry and final_signal == "BUY":
            sl = tech_data.support_1

        result: dict[str, Any] = {
            "ticker": ticker,
            "company_name": stock_data.company_name,
            "sector": stock_data.sector,
            "current_price": stock_data.current_price,
            "day_change_pct": stock_data.day_change_pct,
            "final_signal": final_signal,
            "confidence": confidence,
            "entry": round(entry, 0),
            "tp1": round(tp1, 0),
            "tp2": round(tp2, 0),
            "sl": round(sl, 0),
            "timeframe": timeframe,
            "conflicts": conflicts,
            "agent_results": agent_results,
            "timestamp": context,
            "error": None,
        }

        # ── 5. Alert ──────────────────────────────────────────────────────────
        if send_alert and confidence >= MIN_CONFIDENCE_ALERT:
            alert_meta = self.alert_engine.analyze(result)
            result["alert_meta"] = alert_meta
        else:
            result["alert_meta"] = None

        # ── 6. Record ─────────────────────────────────────────────────────────
        if record:
            self.learning_agent.record_signal(result)

        logger.info(
            f"[Supervisor] {ticker} DONE → "
            f"Signal:{final_signal} | Conf:{confidence}% | "
            f"Conflicts:{len(conflicts)}"
        )
        return result

    def screen(
        self,
        tickers: list[str],
        send_alerts: bool = True,
        min_confidence: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Screen multiple tickers and return a sorted watchlist.

        Args:
            tickers:       List of Yahoo Finance tickers.
            send_alerts:   Whether to dispatch alerts for qualifying signals.
            min_confidence: Only return results above this confidence level.

        Returns:
            List of result dicts sorted by confidence descending.
        """
        results: list[dict[str, Any]] = []
        for ticker in tickers:
            try:
                result = self.analyze(
                    ticker, send_alert=send_alerts, record=True
                )
                if result.get("confidence", 0) >= min_confidence:
                    results.append(result)
            except Exception as exc:
                logger.error(f"[Supervisor] Error screening {ticker}: {exc}")

        results.sort(key=lambda r: r.get("confidence", 0), reverse=True)
        return results

    # ── Aggregation Logic ─────────────────────────────────────────────────────

    def _aggregate(
        self,
        tech: dict, fund: dict, vol: dict, macro: dict, sent: dict
    ) -> tuple[str, int, list[str]]:
        """
        Combine agent outputs into a final signal + confidence.

        Returns:
            (final_signal, confidence_pct, conflicts_list)
        """
        conflicts: list[str] = []

        # Map each agent to a numeric vote: +1=BUY, -1=SELL, 0=NEUTRAL
        def _score(signal_str: str) -> int:
            s = str(signal_str).upper()
            if s in ("BUY", "BULLISH", "STRONG BULLISH"):
                return 1
            if s in ("SELL", "BEARISH", "WEAK", "WEAK BEARISH"):
                return -1
            return 0

        tech_vote = _score(tech.get("signal", "NEUTRAL"))
        fund_vote = _score(fund.get("status", "Neutral"))
        vol_vote = _score(vol.get("status", "Normal"))
        macro_vote = _score(macro.get("ihsg_bias", "Neutral"))
        sent_vote = _score(sent.get("sentiment", "Neutral"))

        # Weighted vote
        weighted = (
            tech_vote * _AGENT_WEIGHTS["technical"]
            + fund_vote * _AGENT_WEIGHTS["fundamental"]
            + vol_vote * _AGENT_WEIGHTS["volume"]
            + macro_vote * _AGENT_WEIGHTS["macro"]
            + sent_vote * _AGENT_WEIGHTS["sentiment"]
        )

        # Determine final signal
        if weighted >= 0.15:
            final_signal = "BUY"
        elif weighted <= -0.15:
            final_signal = "SELL"
        else:
            final_signal = "NEUTRAL"

        # Detect conflicts
        if tech_vote == 1 and fund_vote == -1:
            conflicts.append("Technical BULLISH tetapi Fundamental BEARISH/WEAK")
        if tech_vote == -1 and fund_vote == 1:
            conflicts.append("Technical BEARISH tetapi Fundamental BULLISH")
        if vol_vote == -1 and tech_vote == 1:
            conflicts.append("Volume tidak mendukung sinyal BULLISH teknikal")
        if macro_vote == -1 and tech_vote == 1:
            conflicts.append("Kondisi makro BEARISH, teknikal BULLISH — Hati-hati")
        if sent_vote == -1 and final_signal == "BUY":
            conflicts.append("Sentimen berita negatif pada saat sinyal BUY")

        # Base confidence from technical agent
        base_conf = int(tech.get("confidence", 50))

        # Boost: multiple agents agree
        bullish_count = sum(1 for v in [tech_vote, fund_vote, vol_vote, macro_vote, sent_vote] if v == 1)
        bearish_count = sum(1 for v in [tech_vote, fund_vote, vol_vote, macro_vote, sent_vote] if v == -1)
        agreement_boost = max(bullish_count, bearish_count) * 3

        # Penalty: conflicts reduce confidence
        conflict_penalty = len(conflicts) * 8

        # Volume spike boost
        vol_boost = 5 if vol.get("is_unusual") and vol_vote >= 0 else 0

        confidence = max(0, min(100, base_conf + agreement_boost + vol_boost - conflict_penalty))

        return final_signal, confidence, conflicts

    # ── Error Result ──────────────────────────────────────────────────────────

    @staticmethod
    def _error_result(ticker: str, error: str) -> dict[str, Any]:
        logger.error(f"[Supervisor] Cannot analyze {ticker}: {error}")
        return {
            "ticker": ticker,
            "company_name": ticker,
            "sector": "Unknown",
            "current_price": 0.0,
            "day_change_pct": 0.0,
            "final_signal": "NEUTRAL",
            "confidence": 0,
            "entry": 0.0,
            "tp1": 0.0,
            "tp2": 0.0,
            "sl": 0.0,
            "timeframe": "N/A",
            "conflicts": [],
            "agent_results": {},
            "alert_meta": None,
            "timestamp": datetime.now().isoformat(),
            "error": error,
        }
