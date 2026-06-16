"""
IHSG Trading System — Realtime Alert Engine
Formats and dispatches trading alerts to Telegram.
"""
from __future__ import annotations

import html
import logging
from typing import Any

from agents.base_agent import BaseAgent
from utils.report_generator import format_signal_report, save_report
from utils.telegram_sender import send_alert_chunked

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Realtime Alert Engine untuk sistem trading IHSG.
Tugasmu membuat pesan alert yang ringkas, jelas, dan actionable untuk trader.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "priority": "<HIGH|MEDIUM|LOW>",
  "alert_type": "<BREAKOUT|VOLUME SPIKE|REVERSAL|FOREIGN INFLOW|ACCUMULATION|SELL SIGNAL|WATCH>",
  "headline": "<judul singkat alert maksimal 10 kata>",
  "action_points": ["<aksi 1>", "<aksi 2>"],
  "warning": "<peringatan risiko jika ada, atau null>",
  "should_send": <true|false>
}

should_send = true jika confidence >= 60 dan signal bukan NEUTRAL.
"""


class AlertEngine(BaseAgent):
    """Generates and sends prioritized trading alerts."""

    def analyze(self, supervisor_result: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        """
        Process a supervisor result and dispatch a Telegram alert if warranted.

        Args:
            supervisor_result: Output dict from SupervisorAI.

        Returns:
            Dict with priority, alert_type, headline, action_points, sent status.
        """
        fallback = {
            "priority": "LOW",
            "alert_type": "WATCH",
            "headline": "Monitoring signal",
            "action_points": ["Pantau pergerakan harga"],
            "warning": None,
            "should_send": False,
        }

        ticker = supervisor_result.get("ticker", "N/A")
        signal = supervisor_result.get("final_signal", "NEUTRAL")
        confidence = supervisor_result.get("confidence", 0)

        user_message = self._build_prompt(supervisor_result)
        alert_meta = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        # Decide whether to dispatch
        should_send = bool(alert_meta.get("should_send", False))
        if should_send and signal != "NEUTRAL" and confidence >= 60:
            self._dispatch(supervisor_result, alert_meta)
            alert_meta["dispatched"] = True
        else:
            alert_meta["dispatched"] = False
            logger.info(
                f"[AlertEngine] {ticker} → Alert suppressed "
                f"(signal={signal}, confidence={confidence}%)"
            )

        return alert_meta

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_prompt(self, sr: dict[str, Any]) -> str:
        ticker = sr.get("ticker", "N/A")
        signal = sr.get("final_signal", "NEUTRAL")
        conf = sr.get("confidence", 0)
        entry = sr.get("entry", 0.0)
        tp1 = sr.get("tp1", 0.0)
        tp2 = sr.get("tp2", 0.0)
        sl = sr.get("sl", 0.0)
        conflicts = sr.get("conflicts", [])
        agents = sr.get("agent_results", {})

        tech = agents.get("technical", {})
        vol = agents.get("volume", {})

        lines = [
            f"=== SUPERVISOR RESULT: {ticker} ===",
            f"Final Signal : {signal}",
            f"Confidence   : {conf}%",
            f"Entry: {entry:,.0f} | TP1: {tp1:,.0f} | TP2: {tp2:,.0f} | SL: {sl:,.0f}",
            "",
            f"Technical Reasons: {tech.get('reasons', [])}",
            f"Volume Status: {vol.get('status', 'N/A')} (Unusual: {vol.get('is_unusual', False)})",
            f"Conflicts: {conflicts if conflicts else 'None'}",
            "",
            "Evaluasi apakah alert ini layak dikirim dan tentukan prioritas serta tipe alert. "
            "Kembalikan HANYA JSON sesuai format.",
        ]
        return "\n".join(lines)

    def _dispatch(self, supervisor_result: dict[str, Any], alert_meta: dict[str, Any]) -> None:
        """Format and send the alert via Telegram, then save to file."""
        ticker = supervisor_result.get("ticker", "N/A")
        priority = alert_meta.get("priority", "MEDIUM")
        headline = alert_meta.get("headline", "Trading Alert")

        # Build Telegram message
        formatted = format_signal_report(supervisor_result)
        header = (
            f"🚨 <b>IHSG ALERT — {html.escape(priority)}</b>\n"
            f"<b>{html.escape(headline)}</b>\n\n"
        )
        full_message = header + formatted

        sent = send_alert_chunked(full_message)
        if sent:
            logger.info(f"[AlertEngine] Alert sent for {ticker} ({priority})")
        else:
            logger.warning(f"[AlertEngine] Failed to send alert for {ticker}")

        # Save report locally regardless of Telegram status
        save_report(formatted, report_type=f"signal_{ticker.replace('.', '_')}")
