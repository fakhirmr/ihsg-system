"""
IHSG Trading System -- Master Scheduler v2
==========================================
Jadwal otomatis setiap agent:

  Technical + Volume : setiap 15 menit (jam market 09:00-16:00 WIB)
  News Sentiment     : setiap 1 jam (jam market)
  Macro              : 1x sehari (08:00 WIB)
  Fundamental        : 1x seminggu (Senin 07:30 WIB)
                       + dipicu otomatis jika Sentiment mendeteksi
                         berita signifikan terhadap saham tertentu
  Supervisor         : saat closing market (15:50 WIB)

Hubungan Sentiment <-> Fundamental:
  - Setiap hasil Sentiment dicek apakah trigger_fundamental_review = True
  - Jika ya, Fundamental Agent dijalankan untuk saham tsb + affected_tickers
  - Fundamental juga menyertakan konteks sentimen terkini saat analisis mingguan

Usage:
    python scheduler.py              # Jalankan scheduler penuh
    python scheduler.py --send-schedule  # Kirim kartu jadwal ke Telegram
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("Scheduler")

from config import DEFAULT_TICKERS, MIN_CONFIDENCE_ALERT
from utils.telegram_sender import send_alert_chunked, send_message
from utils.agent_cache import get as cache_get, set as cache_set

# ── Konstanta Jadwal ───────────────────────────────────────────────────────────
MARKET_OPEN  = (9, 0)    # 09:00 WIB
MARKET_CLOSE = (16, 0)   # 16:00 WIB
TECHNICAL_INTERVAL_MIN  = 15   # menit
SENTIMENT_INTERVAL_MIN  = 60   # menit
MACRO_TIME              = "08:00"
FUNDAMENTAL_WEEKDAY     = 0    # Senin (0=Mon ... 6=Sun)
FUNDAMENTAL_TIME        = "07:30"
SUPERVISOR_TIME         = "15:50"

# TTL cache fundamental per ticker (7 hari)
TTL_FUNDAMENTAL_WEEKLY = 7 * 24 * 3600

# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now()

def _is_weekday() -> bool:
    return _now().weekday() < 5

def _is_market_hours() -> bool:
    n = _now()
    oh, om = MARKET_OPEN
    ch, cm = MARKET_CLOSE
    market_open  = n.replace(hour=oh, minute=om, second=0, microsecond=0)
    market_close = n.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return market_open <= n <= market_close

def _notify(emoji: str, title: str, body: str = "") -> None:
    msg = f"{emoji} <b>{title}</b>"
    if body:
        msg += f"\n{body}"
    send_message(msg)

def _run_thread(fn, name: str = "") -> None:
    t = threading.Thread(target=fn, name=name or fn.__name__, daemon=True)
    t.start()

def _hhmm_to_today(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return _now().replace(hour=h, minute=m, second=0, microsecond=0)


# ── 1. TECHNICAL + VOLUME — setiap 15 menit ───────────────────────────────────

def run_technical_volume() -> None:
    """Fast technical screener (tanpa LLM). Alert jika ada sinyal kuat."""
    if not _is_market_hours():
        return

    from utils.data_fetcher import fetch_stock_data
    from utils.technical_calculator import calculate_technical_data

    ts = _now().strftime("%H:%M WIB")
    logger.info(f"[Technical+Volume] Scan dimulai — {ts}")

    buy_alerts, sell_alerts = [], []

    for ticker in DEFAULT_TICKERS:
        try:
            sd = fetch_stock_data(ticker)
            if not sd.is_valid:
                continue
            td = calculate_technical_data(ticker, sd.price_history)
            p  = sd.current_price

            score = 0
            if td.trend == "UPTREND":       score += 20
            elif td.trend == "DOWNTREND":   score -= 20
            if td.is_above_ema20:           score += 10
            else:                           score -= 10
            if td.is_above_ema50:           score += 8
            else:                           score -= 8
            if td.macd_histogram > 0:       score += 8
            else:                           score -= 5
            if td.is_breakout:              score += 15
            if td.is_breakdown:             score -= 15
            if td.higher_high:              score += 8
            if td.lower_low:               score -= 8
            if 40 <= td.rsi_14 <= 65:      score += 5
            elif td.rsi_14 > 70:           score -= 10
            if sd.relative_volume >= 2.0:  score += 10

            entry = p
            tp1   = round(td.resistance_1 if td.resistance_1 > entry else entry * 1.04, 0)
            tp2   = round(td.resistance_2 if td.resistance_2 > tp1   else entry * 1.08, 0)
            sl    = round(max(td.support_1, entry * 0.95) if td.support_1 > 0 else entry * 0.95, 0)

            if score >= 35:
                buy_alerts.append({
                    "ticker": ticker, "score": score, "price": p,
                    "change": sd.day_change_pct, "rsi": td.rsi_14,
                    "entry": entry, "tp1": tp1, "tp2": tp2, "sl": sl,
                    "breakout": td.is_breakout, "vol": sd.relative_volume,
                })
            elif score <= -35:
                sell_alerts.append({
                    "ticker": ticker, "score": score, "price": p,
                    "change": sd.day_change_pct, "rsi": td.rsi_14,
                })
        except Exception as e:
            logger.debug(f"[Tech] {ticker}: {e}")

    logger.info(f"[Technical+Volume] {ts} -> {len(buy_alerts)} BUY | {len(sell_alerts)} SELL")

    if not buy_alerts and not sell_alerts:
        return  # Tidak ada sinyal kuat, tidak kirim notifikasi

    NL = "\n"
    msg = f"<b>Technical Scan — {ts}</b>\n\n"

    if buy_alerts:
        buy_alerts.sort(key=lambda x: x["score"], reverse=True)
        lines = []
        for r in buy_alerts[:8]:
            tag = " BREAKOUT" if r["breakout"] else ""
            vol_tag = f" Vol:{r['vol']:.1f}x" if r["vol"] >= 1.5 else ""
            lines.append(
                f"  <b>{r['ticker']}</b> {r['price']:,.0f} ({r['change']:+.1f}%){tag}{vol_tag}\n"
                f"  Entry:{r['entry']:,.0f} TP1:{r['tp1']:,.0f} SL:{r['sl']:,.0f}"
            )
        msg += f"<b>BUY Signal ({len(buy_alerts)}):</b>\n" + NL.join(lines) + "\n\n"

    if sell_alerts:
        sell_alerts.sort(key=lambda x: x["score"])
        lines = [
            f"  <b>{r['ticker']}</b> {r['price']:,.0f} ({r['change']:+.1f}%) RSI:{r['rsi']:.0f}"
            for r in sell_alerts[:5]
        ]
        msg += f"<b>SELL/Hindari ({len(sell_alerts)}):</b>\n" + NL.join(lines)

    send_alert_chunked(msg)


# ── 2. NEWS SENTIMENT — setiap 1 jam ──────────────────────────────────────────

def run_sentiment_scan(trigger_fundamental_for: list[str] | None = None) -> None:
    """
    Scan sentimen untuk semua ticker.
    Jika trigger_fundamental_review=True pada hasil, jalankan Fundamental Agent
    untuk ticker tersebut secara otomatis.
    """
    if not _is_market_hours() and trigger_fundamental_for is None:
        return

    from agents.news_sentiment_agent import NewsSentimentAgent
    from utils.data_fetcher import fetch_stock_data
    from utils.agent_cache import set as cache_set, get as cache_get

    ts = _now().strftime("%H:%M WIB")
    logger.info(f"[Sentiment] Scan dimulai — {ts}")

    agent = NewsSentimentAgent()
    tickers_to_scan = trigger_fundamental_for or DEFAULT_TICKERS
    fund_triggers: list[str] = []  # Ticker yang butuh fundamental review

    bearish_alerts, bullish_alerts = [], []

    for ticker in tickers_to_scan:
        try:
            sd = fetch_stock_data(ticker)
            if not sd.is_valid:
                continue

            result = agent.analyze(
                ticker=ticker,
                company_name=sd.company_name,
                sector=sd.sector,
                industry=sd.industry,
                current_price=sd.current_price,
                day_change_pct=sd.day_change_pct,
                watchlist=DEFAULT_TICKERS,
            )

            # Cache hasil sentimen
            cache_set(f"sentiment:{ticker}", result)

            # Cek apakah perlu trigger fundamental
            if result.get("trigger_fundamental_review"):
                fund_triggers.append(ticker)
                logger.info(f"[Sentiment] {ticker} -> fundamental review triggered!")

            # Cek affected_tickers dari berita
            for affected in result.get("affected_tickers", []):
                t_full = affected + ".JK" if not affected.endswith(".JK") else affected
                if t_full in DEFAULT_TICKERS and t_full not in fund_triggers:
                    fund_triggers.append(t_full)
                    logger.info(f"[Sentiment] {t_full} terdampak berita {ticker}")

            sent = result.get("sentiment", "Neutral")
            conf = result.get("confidence", 0)
            fund_impact = result.get("fundamental_impact", "Unknown")

            if sent == "Bearish" and conf >= 60:
                bearish_alerts.append({
                    "ticker": ticker, "conf": conf,
                    "summary": result.get("summary", "")[:80],
                    "fund_impact": fund_impact,
                    "fund_reason": result.get("fundamental_reason", "")[:60],
                })
            elif sent == "Bullish" and conf >= 60:
                bullish_alerts.append({
                    "ticker": ticker, "conf": conf,
                    "summary": result.get("summary", "")[:80],
                    "fund_impact": fund_impact,
                    "fund_reason": result.get("fundamental_reason", "")[:60],
                })

        except Exception as e:
            logger.debug(f"[Sentiment] {ticker}: {e}")

    logger.info(
        f"[Sentiment] Selesai — {len(bullish_alerts)} Bullish | "
        f"{len(bearish_alerts)} Bearish | {len(fund_triggers)} Fund Triggers"
    )

    # Kirim alert sentimen jika ada
    if bullish_alerts or bearish_alerts:
        NL = "\n"
        msg = f"<b>Sentiment Scan — {ts}</b>\n\n"

        if bullish_alerts:
            lines = [
                f"  <b>{a['ticker']}</b> ({a['conf']}%) — {a['summary']}\n"
                f"  Dampak Fundamental: {a['fund_impact']} | {a['fund_reason']}"
                for a in bullish_alerts[:5]
            ]
            msg += f"<b>Bullish ({len(bullish_alerts)}):</b>\n" + NL.join(lines) + "\n\n"

        if bearish_alerts:
            lines = [
                f"  <b>{a['ticker']}</b> ({a['conf']}%) — {a['summary']}\n"
                f"  Dampak Fundamental: {a['fund_impact']} | {a['fund_reason']}"
                for a in bearish_alerts[:5]
            ]
            msg += f"<b>Bearish ({len(bearish_alerts)}):</b>\n" + NL.join(lines)

        send_alert_chunked(msg)

    # Auto-trigger Fundamental untuk saham yang perlu review
    if fund_triggers:
        unique = list(dict.fromkeys(fund_triggers))  # deduplicate
        logger.info(f"[Sentiment] Auto-trigger Fundamental: {unique}")
        _notify(
            "🔍", "Fundamental Review Dipicu",
            f"Sentimen mendeteksi berita signifikan.\n"
            f"Memulai analisis fundamental: {', '.join(t.replace('.JK','') for t in unique[:5])}"
        )
        _run_thread(lambda: run_fundamental_targeted(unique), "fund-triggered")


# ── 3. FUNDAMENTAL — 1x seminggu (+ dipicu otomatis oleh Sentiment) ───────────

def run_fundamental_weekly() -> None:
    """Analisis fundamental semua 58 saham — dijadwalkan setiap Senin pagi."""
    logger.info("[Fundamental] Weekly scan dimulai...")
    _notify("📊", "Fundamental Weekly Scan", f"Menganalisis {len(DEFAULT_TICKERS)} saham...")

    from agents.fundamental_agent import FundamentalAgent
    from utils.data_fetcher import fetch_stock_data
    from utils.agent_cache import get as cache_get, set as cache_set

    agent = FundamentalAgent()
    results = {"strong_bullish": [], "bullish": [], "bearish": [], "weak": []}
    NL = "\n"

    for ticker in DEFAULT_TICKERS:
        try:
            # Ambil sentimen terkini dari cache untuk konteks
            sent_cache = cache_get(f"sentiment:{ticker}", ttl=3600 * 24)

            sd = fetch_stock_data(ticker)
            if not sd.is_valid:
                continue

            result = agent.analyze(sd)
            cache_set(f"fundamental:{ticker}", result)  # Cache 7 hari

            status = result.get("status", "Neutral")
            score  = result.get("score", 50)
            sent_context = ""
            if sent_cache:
                sent_context = f" | Sentimen: {sent_cache.get('sentiment','?')}"

            if status in ("Strong Bullish",):
                results["strong_bullish"].append((ticker, score, sent_context))
            elif status == "Bullish":
                results["bullish"].append((ticker, score, sent_context))
            elif status in ("Bearish", "Weak"):
                results["bearish"].append((ticker, score, sent_context))

            logger.info(f"[Fundamental] {ticker} -> {status} ({score}){sent_context}")
        except Exception as e:
            logger.error(f"[Fundamental] {ticker}: {e}")

    # Format & kirim ringkasan
    ts = _now().strftime("%d %b %Y")
    msg = f"<b>Fundamental Weekly Report — {ts}</b>\n\n"

    if results["strong_bullish"]:
        lines = [f"  <b>{t}</b> Skor:{s}{ctx}" for t, s, ctx in results["strong_bullish"][:5]]
        msg += f"<b>Strong Bullish:</b>\n" + NL.join(lines) + "\n\n"
    if results["bullish"]:
        lines = [f"  <b>{t}</b> Skor:{s}{ctx}" for t, s, ctx in results["bullish"][:8]]
        msg += f"<b>Bullish:</b>\n" + NL.join(lines) + "\n\n"
    if results["bearish"]:
        lines = [f"  <b>{t}</b> Skor:{s}{ctx}" for t, s, ctx in results["bearish"][:5]]
        msg += f"<b>Bearish/Weak:</b>\n" + NL.join(lines)

    send_alert_chunked(msg)
    logger.info("[Fundamental] Weekly scan selesai.")


def run_fundamental_targeted(tickers: list[str]) -> None:
    """
    Analisis fundamental untuk saham tertentu saja.
    Dipanggil otomatis oleh Sentiment Agent ketika ada berita signifikan.
    """
    from agents.fundamental_agent import FundamentalAgent
    from utils.data_fetcher import fetch_stock_data
    from utils.agent_cache import get as cache_get, set as cache_set

    agent = FundamentalAgent()
    NL = "\n"
    results_lines = []
    ts = _now().strftime("%H:%M WIB")

    for ticker in tickers:
        try:
            # Ambil sentimen terkini dari cache
            sent_cache = cache_get(f"sentiment:{ticker}", ttl=3600 * 4) or {}

            sd = fetch_stock_data(ticker)
            if not sd.is_valid:
                continue

            result = agent.analyze(sd)
            cache_set(f"fundamental:{ticker}", result)

            status = result.get("status", "Neutral")
            score  = result.get("score", 50)
            per    = result.get("per_assessment", "?")
            summary = result.get("summary", "")[:100]
            fund_impact = sent_cache.get("fundamental_impact", "")
            fund_reason = sent_cache.get("fundamental_reason", "")

            results_lines.append(
                f"<b>{ticker}</b> — {status} (Skor:{score} | PER:{per})\n"
                f"  {summary}\n"
                + (f"  Sentimen: {sent_cache.get('sentiment','?')} | Dampak: {fund_impact}\n"
                   f"  {fund_reason}" if fund_impact else "")
            )
            logger.info(f"[Fundamental-Targeted] {ticker} -> {status} | Sentiment: {sent_cache.get('sentiment','?')}")
        except Exception as e:
            logger.error(f"[Fundamental-Targeted] {ticker}: {e}")

    if results_lines:
        msg = (
            f"<b>Fundamental Review (Dipicu Sentimen) — {ts}</b>\n"
            f"<i>Saham: {', '.join(t.replace('.JK','') for t in tickers)}</i>\n\n"
            + NL.join(results_lines)
        )
        send_alert_chunked(msg)


# ── 4. MACRO — 1x sehari ──────────────────────────────────────────────────────

def run_macro() -> None:
    from agents.macro_agent import MacroAgent
    logger.info("[Macro] Analisis dimulai...")
    _notify("🌐", "Macro Agent Aktif", "Menganalisis kondisi makro...")
    try:
        agent = MacroAgent()
        context = _now().strftime("%Y-%m-%d %H:%M WIB")
        result = agent.analyze(context=context)
        cache_set("macro:daily", result)

        cond = result.get("market_condition", "N/A")
        bias = result.get("ihsg_bias", "N/A")
        pos  = ", ".join(result.get("positive_sectors", [])[:4])
        neg  = ", ".join(result.get("negative_sectors", [])[:3])
        summary = result.get("summary", "")
        bias_emoji = {"Bullish": "📈", "Bearish": "📉"}.get(bias, "➡️")

        msg = (
            f"<b>🌐 Macro Update — {_now().strftime('%d %b %Y')}</b>\n\n"
            f"<b>Kondisi:</b> {cond}\n"
            f"<b>IHSG Bias:</b> {bias_emoji} {bias}\n\n"
            f"<b>Positif:</b> {pos or '-'}\n"
            f"<b>Negatif:</b> {neg or '-'}\n\n"
            f"<i>{summary}</i>"
        )
        send_alert_chunked(msg)
        logger.info(f"[Macro] Selesai: {cond} | {bias}")
    except Exception as e:
        logger.error(f"[Macro] Error: {e}")
        _notify("❌", "Macro Error", str(e)[:150])


# ── 5. SUPERVISOR — closing market 15:50 ──────────────────────────────────────

def run_supervisor_closing() -> None:
    from agents.supervisor import SupervisorAI
    from utils.report_generator import format_aftermarket_report, save_report

    logger.info("[Supervisor] Closing scan dimulai...")
    _notify("🔔", "Supervisor Closing Scan", f"Menganalisis {len(DEFAULT_TICKERS)} saham...")

    try:
        supervisor = SupervisorAI()
        results = supervisor.screen(DEFAULT_TICKERS, send_alerts=True, min_confidence=0)

        buy  = [r for r in results if r.get("final_signal") == "BUY"]
        sell = [r for r in results if r.get("final_signal") == "SELL"]
        neut = [r for r in results if r.get("final_signal") == "NEUTRAL"]

        # Learning agent evaluation
        learning = supervisor.learning_agent.analyze()

        NL = "\n"
        ts = _now().strftime("%d %b %Y %H:%M WIB")
        buy_lines = [
            f"  <b>{r['ticker']}</b> {r['current_price']:,.0f} ({r['day_change_pct']:+.1f}%) — {r['confidence']}%"
            for r in sorted(buy, key=lambda x: x["confidence"], reverse=True)[:8]
        ]
        sell_lines = [
            f"  <b>{r['ticker']}</b> {r['current_price']:,.0f} ({r['day_change_pct']:+.1f}%) — {r['confidence']}%"
            for r in sorted(sell, key=lambda x: x["confidence"], reverse=True)[:5]
        ]

        msg = (
            f"<b>Supervisor Closing Report — {ts}</b>\n"
            f"<b>{len(buy)} BUY | {len(sell)} SELL | {len(neut)} NEUTRAL</b>\n\n"
        )
        if buy_lines:
            msg += f"<b>Top BUY:</b>\n" + NL.join(buy_lines) + "\n\n"
        if sell_lines:
            msg += f"<b>Top SELL:</b>\n" + NL.join(sell_lines) + "\n\n"
        msg += f"<b>Evaluasi Sinyal:</b>\n{learning.get('summary','N/A')}"

        send_alert_chunked(msg)
        logger.info(f"[Supervisor] Selesai: {len(buy)} BUY | {len(sell)} SELL")
    except Exception as e:
        logger.error(f"[Supervisor] Error: {e}")
        _notify("❌", "Supervisor Error", str(e)[:150])


# ── Kartu Jadwal ───────────────────────────────────────────────────────────────

def send_schedule_card() -> None:
    ts = _now().strftime("%A, %d %B %Y %H:%M WIB")
    msg = (
        f"<b>IHSG System — Jadwal Agent</b>\n"
        f"<i>{ts}</i>\n\n"
        f"<b>Technical + Volume</b>\n"
        f"  Setiap 15 menit | 09:00-16:00 WIB\n"
        f"  Alert jika ada sinyal kuat (skor >=35)\n\n"
        f"<b>News Sentiment</b>\n"
        f"  Setiap 1 jam | 09:00-16:00 WIB\n"
        f"  Auto-trigger Fundamental jika ada berita signifikan\n\n"
        f"<b>Macro</b>\n"
        f"  Setiap hari | 08:00 WIB\n\n"
        f"<b>Fundamental</b>\n"
        f"  Setiap Senin | 07:30 WIB (weekly)\n"
        f"  + Dipicu otomatis oleh Sentiment saat ada\n"
        f"    corporate action / berita fundamental\n\n"
        f"<b>Supervisor</b>\n"
        f"  Setiap hari kerja | 15:50 WIB (closing)\n\n"
        f"<b>Watchlist:</b> {len(DEFAULT_TICKERS)} saham\n"
        f"<i>Sistem berjalan di GitHub Actions — 24/7</i>"
    )
    ok = send_alert_chunked(msg)
    print("Kartu jadwal terkirim!" if ok else "Gagal kirim.")
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))


# ── Main Scheduler Loop ────────────────────────────────────────────────────────

def run_scheduler() -> None:
    logger.info("=" * 50)
    logger.info("  IHSG Scheduler v2 aktif")
    logger.info(f"  Watchlist: {len(DEFAULT_TICKERS)} saham")
    logger.info("=" * 50)

    send_schedule_card()

    # State tracker
    last_technical  = _now() - timedelta(minutes=TECHNICAL_INTERVAL_MIN)
    last_sentiment  = _now() - timedelta(minutes=SENTIMENT_INTERVAL_MIN)
    last_macro_date = None
    last_fund_week  = None

    # Jadwal harian (reset tiap hari)
    supervisor_fired_today = False
    today_date = _now().date()

    while True:
        now = _now()

        # Reset harian
        if now.date() != today_date:
            today_date = now.date()
            supervisor_fired_today = False
            logger.info(f"[Scheduler] Hari baru: {today_date}")

        if _is_weekday():
            # ── Technical + Volume (15 menit, jam market) ────────────────────
            if (
                _is_market_hours()
                and (now - last_technical).total_seconds() >= TECHNICAL_INTERVAL_MIN * 60
            ):
                last_technical = now
                _run_thread(run_technical_volume, "tech-vol")

            # ── Sentiment (1 jam, jam market) ────────────────────────────────
            if (
                _is_market_hours()
                and (now - last_sentiment).total_seconds() >= SENTIMENT_INTERVAL_MIN * 60
            ):
                last_sentiment = now
                _run_thread(run_sentiment_scan, "sentiment")

            # ── Macro (1x/hari jam 08:00) ─────────────────────────────────────
            if (
                last_macro_date != now.date()
                and now.hour == 8 and now.minute < 5
            ):
                last_macro_date = now.date()
                _run_thread(run_macro, "macro")

            # ── Fundamental (1x/minggu Senin 07:30) ──────────────────────────
            if (
                now.weekday() == FUNDAMENTAL_WEEKDAY
                and now.hour == 7 and 30 <= now.minute < 35
                and last_fund_week != now.date()
            ):
                last_fund_week = now.date()
                _run_thread(run_fundamental_weekly, "fund-weekly")

            # ── Supervisor (closing 15:50) ────────────────────────────────────
            if (
                not supervisor_fired_today
                and now.hour == 15 and 50 <= now.minute < 55
            ):
                supervisor_fired_today = True
                _run_thread(run_supervisor_closing, "supervisor")

        time.sleep(30)  # Cek setiap 30 detik


def main() -> None:
    parser = argparse.ArgumentParser(description="IHSG Scheduler v2")
    parser.add_argument("--send-schedule", action="store_true",
                        help="Kirim kartu jadwal ke Telegram lalu keluar")
    args = parser.parse_args()

    if args.send_schedule:
        send_schedule_card()
    else:
        run_scheduler()


if __name__ == "__main__":
    main()
