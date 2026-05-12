"""
IHSG Trading System — Learning & Evaluation Agent
Evaluates historical signal performance to improve system quality.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from agents.base_agent import BaseAgent
from config import SIGNAL_HISTORY_FILE

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Learning & Evaluation Agent untuk sistem trading IHSG.
Tugasmu menganalisis performa historis sinyal dan memberikan rekomendasi perbaikan.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "total_signals": <integer>,
  "win_count": <integer>,
  "loss_count": <integer>,
  "winrate_pct": <float>,
  "best_setup": "<deskripsi setup terbaik>",
  "worst_setup": "<deskripsi setup terburuk>",
  "highest_winrate_sector": "<sektor dengan winrate tertinggi>",
  "weakest_signal_type": "<tipe sinyal paling lemah>",
  "avg_return_pct": <float>,
  "max_drawdown_pct": <float>,
  "recommendations": ["<rekomendasi 1>", "<rekomendasi 2>"],
  "summary": "<ringkasan evaluasi performa dalam Bahasa Indonesia>"
}
"""


class LearningAgent(BaseAgent):
    """Reads signal history and evaluates performance metrics."""

    def record_signal(self, supervisor_result: dict[str, Any]) -> None:
        """
        Save a new signal to the history file.

        Args:
            supervisor_result: Full result from SupervisorAI.
        """
        history = self._load_history()
        record = {
            "timestamp": datetime.now().isoformat(),
            "ticker": supervisor_result.get("ticker"),
            "signal": supervisor_result.get("final_signal"),
            "confidence": supervisor_result.get("confidence"),
            "entry": supervisor_result.get("entry"),
            "tp1": supervisor_result.get("tp1"),
            "tp2": supervisor_result.get("tp2"),
            "sl": supervisor_result.get("sl"),
            "outcome": None,         # To be filled manually or via update
            "return_pct": None,
            "sector": supervisor_result.get("agent_results", {})
                                      .get("macro", {})
                                      .get("positive_sectors", []),
        }
        history.append(record)
        self._save_history(history)
        logger.info(f"[LearningAgent] Signal recorded for {record['ticker']}")

    def update_outcome(self, ticker: str, timestamp: str, outcome: str, return_pct: float) -> bool:
        """
        Update the outcome of a previously recorded signal.

        Args:
            ticker:     Ticker symbol.
            timestamp:  ISO timestamp of the signal to update.
            outcome:    'WIN', 'LOSS', or 'BREAKEVEN'.
            return_pct: Actual return percentage.

        Returns:
            True if the record was found and updated.
        """
        history = self._load_history()
        updated = False
        for record in history:
            if record.get("ticker") == ticker and record.get("timestamp", "").startswith(timestamp[:16]):
                record["outcome"] = outcome
                record["return_pct"] = return_pct
                updated = True
                break
        if updated:
            self._save_history(history)
            logger.info(f"[LearningAgent] Outcome updated for {ticker} ({outcome} {return_pct:+.2f}%)")
        else:
            logger.warning(f"[LearningAgent] No matching record found for {ticker} @ {timestamp}")
        return updated

    def analyze(self, min_records: int = 5) -> dict[str, Any]:  # type: ignore[override]
        """
        Evaluate historical signal performance.

        Args:
            min_records: Minimum number of completed signals needed for evaluation.

        Returns:
            Dict with performance metrics and improvement recommendations.
        """
        fallback = {
            "total_signals": 0,
            "win_count": 0,
            "loss_count": 0,
            "winrate_pct": 0.0,
            "best_setup": "Belum ada data cukup",
            "worst_setup": "Belum ada data cukup",
            "highest_winrate_sector": "N/A",
            "weakest_signal_type": "N/A",
            "avg_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "recommendations": ["Kumpulkan lebih banyak data sinyal untuk evaluasi yang akurat."],
            "summary": "Data sinyal belum mencukupi untuk evaluasi performa.",
        }

        history = self._load_history()
        completed = [r for r in history if r.get("outcome") is not None]

        if len(completed) < min_records:
            fallback["total_signals"] = len(history)
            logger.info(
                f"[LearningAgent] Only {len(completed)} completed signals — "
                "not enough for full evaluation."
            )
            return fallback

        user_message = self._build_prompt(history, completed)
        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        logger.info(
            f"[LearningAgent] Evaluation complete — "
            f"Winrate:{result.get('winrate_pct', 0):.1f}% "
            f"AvgReturn:{result.get('avg_return_pct', 0):+.2f}%"
        )
        return result

    # ── History IO ────────────────────────────────────────────────────────────

    def _load_history(self) -> list[dict[str, Any]]:
        if not SIGNAL_HISTORY_FILE.exists():
            return []
        try:
            with open(SIGNAL_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"[LearningAgent] Failed to load history: {exc}")
            return []

    def _save_history(self, history: list[dict[str, Any]]) -> None:
        try:
            with open(SIGNAL_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error(f"[LearningAgent] Failed to save history: {exc}")

    def _build_prompt(
        self, all_history: list[dict], completed: list[dict]
    ) -> str:
        wins = [r for r in completed if r.get("outcome") == "WIN"]
        losses = [r for r in completed if r.get("outcome") == "LOSS"]
        returns = [r.get("return_pct", 0) for r in completed if r.get("return_pct") is not None]
        avg_return = sum(returns) / len(returns) if returns else 0.0
        drawdowns = [r for r in returns if r < 0]
        max_dd = min(drawdowns) if drawdowns else 0.0

        lines = [
            "=== SIGNAL PERFORMANCE HISTORY ===",
            f"Total Sinyal Tersimpan  : {len(all_history)}",
            f"Total Sinyal Selesai    : {len(completed)}",
            f"Wins                    : {len(wins)}",
            f"Losses                  : {len(losses)}",
            f"Winrate                 : {len(wins)/len(completed)*100:.1f}%",
            f"Average Return          : {avg_return:+.2f}%",
            f"Max Drawdown            : {max_dd:.2f}%",
            "",
            "=== SIGNAL DETAIL (20 TERBARU) ===",
        ]

        for record in completed[-20:]:
            outcome_icon = "✅" if record.get("outcome") == "WIN" else "❌"
            lines.append(
                f"{outcome_icon} {record.get('timestamp','')[:10]} | "
                f"{record.get('ticker','')} | "
                f"Signal:{record.get('signal','')} | "
                f"Conf:{record.get('confidence','')}% | "
                f"Return:{record.get('return_pct', 0):+.2f}%"
            )

        lines += [
            "",
            "Evaluasi performa sistem secara menyeluruh dan berikan rekomendasi perbaikan. "
            "Kembalikan HANYA JSON sesuai format.",
        ]
        return "\n".join(lines)
