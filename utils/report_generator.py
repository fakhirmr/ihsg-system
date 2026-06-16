"""
IHSG Trading System — Report Generator
Formats pre-market, after-market, and individual signal reports.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from config import REPORTS_DIR


def _e(text: Any) -> str:
    """Escape HTML special characters in user/LLM-generated text."""
    return html.escape(str(text))

logger = logging.getLogger(__name__)

_NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M WIB")
_DATE = lambda: datetime.now().strftime("%Y-%m-%d")


# ─── Signal Report (Telegram format) ─────────────────────────────────────────

def format_signal_report(result: dict[str, Any]) -> str:
    """
    Format a SupervisorResult dict into a Telegram-ready signal message.
    Uses plain HTML so Telegram renders it cleanly.
    """
    ticker = result.get("ticker", "N/A")
    signal = result.get("final_signal", "NEUTRAL")
    conf = result.get("confidence", 0)
    entry = result.get("entry", 0.0)
    tp1 = result.get("tp1", 0.0)
    tp2 = result.get("tp2", 0.0)
    sl = result.get("sl", 0.0)
    timeframe = result.get("timeframe", "Swing")
    conflicts = result.get("conflicts", [])

    # Signal emoji
    emoji = {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(signal, "⚪")

    lines: list[str] = [
        "=" * 50,
        f"{emoji} <b>[{signal} SIGNAL]</b>",
        "",
        f"<b>Ticker:</b> {ticker}",
        f"<b>Timeframe:</b> {timeframe}",
        f"<b>Confidence:</b> {conf}%",
        "",
        f"<b>Entry :</b> {entry:,.0f}",
        f"<b>TP1   :</b> {tp1:,.0f}",
        f"<b>TP2   :</b> {tp2:,.0f}",
        f"<b>SL    :</b> {sl:,.0f}",
        "",
    ]

    # Agent summaries
    agents = result.get("agent_results", {})
    sections = {
        "fundamental": "📊 Fundamental",
        "technical": "📈 Technical",
        "volume": "📦 Volume",
        "macro": "🌐 Macro",
        "sentiment": "📰 Sentiment",
    }
    for key, label in sections.items():
        ag = agents.get(key, {})
        if not ag:
            continue
        lines.append(f"<b>{label}:</b>")
        for item in ag.get("reasons", ag.get("strengths", [])):
            lines.append(f"  • {_e(item)}")
        lines.append("")

    # Conflicts
    if conflicts:
        lines.append("⚠️ <b>Konflik Sinyal:</b>")
        for c in conflicts:
            lines.append(f"  • {_e(c)}")
        lines.append("")

    lines.append(f"<i>Generated: {_NOW()}</i>")
    lines.append("=" * 50)

    return "\n".join(lines)


# ─── Pre-Market Report ────────────────────────────────────────────────────────

def format_premarket_report(
    global_macro: str,
    ihsg_outlook: str,
    watchlist: list[dict[str, Any]],
    top_news: list[str],
) -> str:
    """Format a pre-market morning briefing report."""
    lines: list[str] = [
        "=" * 50,
        "🌅 <b>PRE-MARKET REPORT</b>",
        f"<i>{_NOW()}</i>",
        "=" * 50,
        "",
        "🌐 <b>Global Macro:</b>",
        global_macro,
        "",
        "🇮🇩 <b>IHSG Outlook:</b>",
        ihsg_outlook,
        "",
        "👁 <b>Watchlist Hari Ini:</b>",
    ]

    for item in watchlist:
        ticker = item.get("ticker", "")
        signal = item.get("signal", "NEUTRAL")
        conf = item.get("confidence", 0)
        note = item.get("note", "")
        emoji = {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(signal, "⚪")
        lines.append(f"  {emoji} <b>{ticker}</b> ({signal} {conf}%) — {note}")

    lines += [
        "",
        "📰 <b>Berita Penting:</b>",
    ]
    for news in top_news:
        lines.append(f"  • {news}")

    lines += ["", "=" * 50]
    return "\n".join(lines)


# ─── After-Market Report ──────────────────────────────────────────────────────

def format_aftermarket_report(
    ihsg_summary: str,
    top_gainers: list[dict],
    top_losers: list[dict],
    foreign_flow: str,
    sector_best: str,
    sector_worst: str,
    signal_eval: str,
    tomorrow_outlook: str,
) -> str:
    """Format an after-market closing report."""
    lines: list[str] = [
        "=" * 50,
        "🌆 <b>AFTER-MARKET REPORT</b>",
        f"<i>{_NOW()}</i>",
        "=" * 50,
        "",
        "📊 <b>IHSG Hari Ini:</b>",
        ihsg_summary,
        "",
        "🏆 <b>Top Gainers:</b>",
    ]
    for g in top_gainers:
        lines.append(f"  🟢 {g.get('ticker','')} +{g.get('change_pct',0):.2f}%")

    lines += ["", "📉 <b>Top Losers:</b>"]
    for lo in top_losers:
        lines.append(f"  🔴 {lo.get('ticker','')} {lo.get('change_pct',0):.2f}%")

    lines += [
        "",
        f"💸 <b>Foreign Flow:</b> {foreign_flow}",
        f"📈 <b>Sektor Terbaik:</b> {sector_best}",
        f"📉 <b>Sektor Terlemah:</b> {sector_worst}",
        "",
        "🎯 <b>Evaluasi Signal Hari Ini:</b>",
        signal_eval,
        "",
        "🔭 <b>Outlook Besok:</b>",
        tomorrow_outlook,
        "",
        "=" * 50,
    ]
    return "\n".join(lines)


# ─── Save Report to File ──────────────────────────────────────────────────────

def save_report(content: str, report_type: str = "signal") -> Path:
    """Save report text to a dated file in the reports directory."""
    filename = REPORTS_DIR / f"{report_type}_{_DATE()}.txt"
    with open(filename, "a", encoding="utf-8") as f:
        f.write(content + "\n\n")
    logger.info(f"Report saved: {filename}")
    return filename
