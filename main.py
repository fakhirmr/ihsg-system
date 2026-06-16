"""
IHSG Multi-Agent Trading & Investment Intelligence System
=========================================================
Entry point — CLI interface.

Usage examples:
  python main.py --ticker BBRI.JK
  python main.py --tickers BBRI.JK BBCA.JK BMRI.JK
  python main.py --screen
  python main.py --screen --min-confidence 70
  python main.py --ticker BBRI.JK --no-alert
  python main.py --evaluate
  python main.py --pre-market
  python main.py --after-market
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from typing import Any

# Force UTF-8 output on Windows (avoids UnicodeEncodeError with emoji)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from config import DEFAULT_TICKERS, MIN_CONFIDENCE_ALERT
from utils.logger import log
from utils.report_generator import (
    format_premarket_report,
    format_aftermarket_report,
    save_report,
)
from utils.telegram_sender import send_alert_chunked


# ── Pretty Print ──────────────────────────────────────────────────────────────

def _print_result(result: dict[str, Any]) -> None:
    """Print a supervisor result to console in a readable format."""
    if result.get("error"):
        print(f"\n❌ ERROR [{result['ticker']}]: {result['error']}\n")
        return

    signal = result.get("final_signal", "N/A")
    emoji = {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(signal, "⚪")
    conf = result.get("confidence", 0)
    conflicts = result.get("conflicts", [])

    print("\n" + "=" * 60)
    print(f"{emoji}  SIGNAL: {signal}  |  Confidence: {conf}%")
    print("=" * 60)
    print(f"  Ticker  : {result.get('ticker')}")
    print(f"  Company : {result.get('company_name')}")
    print(f"  Sector  : {result.get('sector')}")
    print(f"  Price   : {result.get('current_price', 0):,.0f} IDR  ({result.get('day_change_pct', 0):+.2f}%)")
    print(f"  Entry   : {result.get('entry', 0):,.0f}")
    print(f"  TP1     : {result.get('tp1', 0):,.0f}")
    print(f"  TP2     : {result.get('tp2', 0):,.0f}")
    print(f"  SL      : {result.get('sl', 0):,.0f}")
    print(f"  TF      : {result.get('timeframe', 'N/A')}")

    if conflicts:
        print("\n  ⚠️  Conflicts Detected:")
        for c in conflicts:
            print(f"     • {c}")

    agents = result.get("agent_results", {})

    # Technical
    tech = agents.get("technical", {})
    if tech:
        print(f"\n  📈 Technical: {tech.get('signal')} {tech.get('confidence')}%")
        for r in tech.get("reasons", []):
            print(f"     • {r}")

    # Fundamental
    fund = agents.get("fundamental", {})
    if fund:
        print(f"\n  📊 Fundamental: {fund.get('status')} (Score: {fund.get('score')})")
        for s in fund.get("strengths", []):
            print(f"     ✅ {s}")
        for w in fund.get("weaknesses", []):
            print(f"     ⚠️  {w}")

    # Volume
    vol = agents.get("volume", {})
    if vol:
        print(f"\n  📦 Volume: {vol.get('status')} (Unusual: {vol.get('is_unusual')})")
        for r in vol.get("reasons", []):
            print(f"     • {r}")

    # Macro
    macro = agents.get("macro", {})
    if macro:
        print(f"\n  🌐 Macro: {macro.get('market_condition')}")
        pos = ", ".join(macro.get("positive_sectors", []))
        neg = ", ".join(macro.get("negative_sectors", []))
        if pos:
            print(f"     ✅ Positive: {pos}")
        if neg:
            print(f"     ❌ Negative: {neg}")

    # Sentiment
    sent = agents.get("sentiment", {})
    if sent:
        print(f"\n  📰 Sentiment: {sent.get('sentiment')} {sent.get('confidence')}%")
        for r in sent.get("reasons", []):
            print(f"     • {r}")

    print("\n" + "=" * 60)


def _print_screen_summary(results: list[dict[str, Any]]) -> None:
    """Print a concise summary table for screening results."""
    print("\n" + "=" * 70)
    print("  SCREENING RESULTS")
    print("=" * 70)
    print(f"  {'Ticker':<12} {'Signal':<8} {'Conf':>5} {'Price':>10} {'Change':>8}")
    print("-" * 70)
    for r in results:
        if r.get("error"):
            print(f"  {r.get('ticker',''):<12} {'ERROR':<8}")
            continue
        sig = r.get("final_signal", "N/A")
        emoji = {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(sig, "  ")
        print(
            f"  {emoji} {r.get('ticker',''):<10} "
            f"{sig:<8} "
            f"{r.get('confidence',0):>4}% "
            f"{r.get('current_price',0):>10,.0f} "
            f"{r.get('day_change_pct',0):>+7.2f}%"
        )
    print("=" * 70)


# ── Pre / After Market Helpers ────────────────────────────────────────────────

def _run_premarket(supervisor: Any) -> None:
    """Generate and send a pre-market briefing."""
    from agents.macro_agent import MacroAgent
    macro_agent = MacroAgent()
    macro = macro_agent.analyze(context="pre-market hari ini")

    print("\n🌅  Generating pre-market report...")

    condition = macro.get("market_condition", "Neutral")
    summary = macro.get("summary", "Kondisi makro sedang dianalisis.")
    pos_sectors = ", ".join(macro.get("positive_sectors", ["N/A"]))
    neg_sectors = ", ".join(macro.get("negative_sectors", ["N/A"]))

    ihsg_outlook = f"Market Condition: {condition}\nPositive Sectors: {pos_sectors}\nNegative Sectors: {neg_sectors}"

    # Quick screen top tickers
    quick_results = supervisor.screen(DEFAULT_TICKERS[:5], send_alerts=False, min_confidence=0)
    watchlist = [
        {
            "ticker": r.get("ticker"),
            "signal": r.get("final_signal"),
            "confidence": r.get("confidence"),
            "note": r.get("agent_results", {}).get("technical", {}).get("summary", ""),
        }
        for r in quick_results
    ]

    report = format_premarket_report(
        global_macro=summary,
        ihsg_outlook=ihsg_outlook,
        watchlist=watchlist,
        top_news=macro.get("key_drivers", ["Tidak ada berita utama tersedia."]),
    )
    print(report)
    save_report(report, "premarket")
    send_alert_chunked(report)


def _run_aftermarket(supervisor: Any) -> None:
    """Generate and send an after-market summary."""
    print("\n🌆  Generating after-market report...")

    from agents.macro_agent import MacroAgent
    macro_agent = MacroAgent()
    macro = macro_agent.analyze(context="after-market hari ini")

    quick_results = supervisor.screen(DEFAULT_TICKERS, send_alerts=False, min_confidence=0)

    buy_results = [r for r in quick_results if r.get("final_signal") == "BUY"]
    sell_results = [r for r in quick_results if r.get("final_signal") == "SELL"]

    gainers = sorted(
        quick_results, key=lambda r: r.get("day_change_pct", 0), reverse=True
    )[:5]
    losers = sorted(quick_results, key=lambda r: r.get("day_change_pct", 0))[:5]

    learning = supervisor.learning_agent.analyze()

    report = format_aftermarket_report(
        ihsg_summary=macro.get("summary", "N/A"),
        top_gainers=[{"ticker": r["ticker"], "change_pct": r["day_change_pct"]} for r in gainers],
        top_losers=[{"ticker": r["ticker"], "change_pct": r["day_change_pct"]} for r in losers],
        foreign_flow=macro.get("ihsg_bias", "Neutral"),
        sector_best=", ".join(macro.get("positive_sectors", ["N/A"])),
        sector_worst=", ".join(macro.get("negative_sectors", ["N/A"])),
        signal_eval=learning.get("summary", "Belum ada evaluasi."),
        tomorrow_outlook=macro.get("summary", "N/A"),
    )
    print(report)
    save_report(report, "aftermarket")
    send_alert_chunked(report)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="IHSG Multi-Agent Trading Intelligence System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mutually exclusive main modes
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--ticker", metavar="TICKER",
        help="Analyze a single ticker, e.g. BBRI.JK",
    )
    mode_group.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="Analyze multiple tickers, e.g. BBRI.JK BBCA.JK",
    )
    mode_group.add_argument(
        "--screen", action="store_true",
        help=f"Screen all {len(DEFAULT_TICKERS)} default tickers",
    )
    mode_group.add_argument(
        "--pre-market", action="store_true",
        help="Generate and send pre-market briefing report",
    )
    mode_group.add_argument(
        "--after-market", action="store_true",
        help="Generate and send after-market summary report",
    )
    mode_group.add_argument(
        "--evaluate", action="store_true",
        help="Run Learning Agent evaluation on signal history",
    )

    # Options
    parser.add_argument(
        "--no-alert", action="store_true",
        help="Suppress Telegram alerts",
    )
    parser.add_argument(
        "--min-confidence", type=int, default=0, metavar="PCT",
        help="Minimum confidence %% to include in output (default: 0)",
    )
    parser.add_argument(
        "--news", type=str, metavar="TEXT",
        help="Optional news text to pass to sentiment agent (single ticker mode)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of formatted report",
    )

    args = parser.parse_args()
    send_alert = not args.no_alert

    # Import supervisor lazily to keep startup fast
    from agents.supervisor import SupervisorAI
    supervisor = SupervisorAI()

    # ── Modes ──────────────────────────────────────────────────────────────────

    if args.pre_market:
        _run_premarket(supervisor)

    elif args.after_market:
        _run_aftermarket(supervisor)

    elif args.evaluate:
        from agents.learning_agent import LearningAgent
        agent = LearningAgent()
        result = agent.analyze(min_records=1)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("\n📊  LEARNING & EVALUATION REPORT")
            print("=" * 50)
            print(f"  Total Signals : {result.get('total_signals', 0)}")
            print(f"  Win Rate      : {result.get('winrate_pct', 0):.1f}%")
            print(f"  Avg Return    : {result.get('avg_return_pct', 0):+.2f}%")
            print(f"  Max Drawdown  : {result.get('max_drawdown_pct', 0):.2f}%")
            print(f"\n  Best Setup    : {result.get('best_setup')}")
            print(f"  Worst Setup   : {result.get('worst_setup')}")
            print(f"  Best Sector   : {result.get('highest_winrate_sector')}")
            print("\n  Recommendations:")
            for rec in result.get("recommendations", []):
                print(f"    • {rec}")
            print("\n  Summary:")
            print(f"  {result.get('summary')}")
            print("=" * 50)

    elif args.ticker:
        result = supervisor.analyze(
            args.ticker,
            news_text=args.news,
            send_alert=send_alert,
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            _print_result(result)

    elif args.tickers:
        results = []
        for t in args.tickers:
            r = supervisor.analyze(t, send_alert=send_alert)
            results.append(r)
            if not args.json:
                _print_result(r)

        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        else:
            _print_screen_summary(results)

    elif args.screen:
        log.info(f"Screening {len(DEFAULT_TICKERS)} tickers...")
        results = supervisor.screen(
            DEFAULT_TICKERS,
            send_alerts=send_alert,
            min_confidence=args.min_confidence,
        )
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        else:
            _print_screen_summary(results)
            buy_signals = [r for r in results if r.get("final_signal") == "BUY"]
            if buy_signals:
                print(f"\n  📋 Top BUY Signal Detail:")
                _print_result(buy_signals[0])


if __name__ == "__main__":
    main()
