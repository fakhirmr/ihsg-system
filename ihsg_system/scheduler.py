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


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("Scheduler")

from config import DEFAULT_TICKERS
from utils.telegram_sender import send_alert_chunked, send_message
from utils.agent_cache import (
    get as cache_get,
    set as cache_set,
    exists as cache_exists,
    mark as cache_mark,
    hash_news_titles,
    TTL_ARTICLE_DEDUP,
)

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

# ── Anti-Spam: Jam Notifikasi Aktif ───────────────────────────────────────────
NOTIF_HOUR_START = 7    # 07:00 WIB — mulai kirim notifikasi
NOTIF_HOUR_END   = 22   # 22:00 WIB — berhenti kirim notifikasi

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

def _is_notif_hours() -> bool:
    """Cek apakah saat ini dalam rentang jam notifikasi aktif (07:00–22:00 WIB)."""
    h = _now().hour
    return NOTIF_HOUR_START <= h < NOTIF_HOUR_END


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

    if not buy_alerts:
        return  # Tidak ada sinyal kuat, tidak kirim notifikasi

    buy_alerts.sort(key=lambda x: x["score"], reverse=True)

    # ── Anti-Spam: Digest Mode ────────────────────────────────────────────────
    # Pisahkan BREAKOUT kuat (skor ≥ 60) dari sinyal biasa
    strong_breakouts = [r for r in buy_alerts if r["breakout"] and r["score"] >= 60]
    regular_signals  = [r for r in buy_alerts if not (r["breakout"] and r["score"] >= 60)]

    # Kirim BREAKOUT kuat sebagai pesan individual (prioritas tinggi)
    for r in strong_breakouts:
        vol_tag = f" 🌊 Vol:{r['vol']:.1f}x" if r["vol"] >= 1.5 else ""
        msg = (
            f"🔥 <b>BREAKOUT ALERT</b>\n"
            f"<i>{ts}</i>\n\n"
            f"🎯 <b>{r['ticker']}</b> | Harga: <b>{r['price']:,.0f}</b> ({r['change']:+.1f}%)\n"
            f"    Skor: {r['score']:+d} | RSI: {r['rsi']:.0f}{vol_tag}\n\n"
            f"    Entry: {r['entry']:,.0f}\n"
            f"    TP1 : {r['tp1']:,.0f}\n"
            f"    SL  : {r['sl']:,.0f}\n\n"
            f"<i>*Sinyal Breakout kuat — auto-generated</i>"
        )
        send_alert_chunked(msg)

    # Kirim sinyal biasa sebagai 1 pesan digest ringkas
    if regular_signals:
        NL = "\n"
        lines = []
        for r in regular_signals[:8]:  # Maks 8 sinyal dalam 1 pesan
            tag = " 🔥" if r["breakout"] else ""
            vol_tag = f" Vol:{r['vol']:.1f}x" if r["vol"] >= 1.5 else ""
            lines.append(
                f"  <b>{r['ticker']}</b>{tag} — {r['price']:,.0f} ({r['change']:+.1f}%) "
                f"Skor:{r['score']:+d} RSI:{r['rsi']:.0f}{vol_tag}\n"
                f"    Entry:{r['entry']:,.0f} | TP1:{r['tp1']:,.0f} | SL:{r['sl']:,.0f}"
            )
        digest_msg = (
            f"🚨 <b>Technical & Volume Digest</b> — {ts}\n"
            f"<i>{len(regular_signals)} sinyal BUY terdeteksi</i>\n\n"
            + NL.join(lines)
            + f"\n\n<i>*Auto-generated dari Technical/Volume scan</i>"
        )
        send_alert_chunked(digest_msg)


# ── 2. NEWS SENTIMENT — setiap 1 jam ──────────────────────────────────────────

def run_sentiment_scan(trigger_fundamental_for: list[str] | None = None) -> None:
    """
    Scan sentimen untuk semua ticker.
    Jika trigger_fundamental_review=True pada hasil, jalankan Fundamental Agent
    untuk ticker tersebut secara otomatis.
    """
    from agents.news_sentiment_agent import NewsSentimentAgent
    from utils.data_fetcher import fetch_stock_data, fetch_news, fetch_market_news

    ts = _now().strftime("%H:%M WIB")
    today_str = _now().strftime("%Y-%m-%d")
    notif_ok = _is_notif_hours()  # Hanya kirim notifikasi dalam jam 07:00–22:00 WIB

    logger.info(f"[Sentiment] Scan dimulai — {ts} | Notifikasi: {'aktif' if notif_ok else 'dimatikan (luar jam)'}")

    agent = NewsSentimentAgent()
    tickers_to_scan = trigger_fundamental_for or DEFAULT_TICKERS
    fund_triggers: list[str] = []  # Ticker yang butuh fundamental review

    bearish_alerts, bullish_alerts = [], []

    # ── Market & Macro News — hanya weekday, per artikel, dedup per artikel ──
    macro_sent_today = cache_exists("macro:daily", ttl=24 * 3600)
    market_news      = fetch_market_news(max_items=8)

    if not notif_ok:
        logger.info("[Sentiment] Luar jam notifikasi — market news dilewati")
    elif macro_sent_today:
        logger.info("[Sentiment] Macro sudah dikirim hari ini — market news dilewati")
    elif not market_news:
        logger.info("[Sentiment] Tidak ada market news dari yfinance")
    else:
        # Pre-check: ada artikel baru yang belum dikirim?
        new_raw = [
            a for a in market_news
            if not cache_exists(f"article_sent:{hash_news_titles([a])}", ttl=TTL_ARTICLE_DEDUP)
        ]
        if not new_raw:
            logger.info("[Sentiment] Semua market articles sudah pernah dikirim — skip LLM")
        else:
            try:
                market_result = agent.analyze(
                    ticker="Makro & IHSG",
                    company_name="Indeks Harga Saham Gabungan",
                    sector="Market",
                    industry="Macro Economy",
                    news_text=market_news,
                    market_news_text="",
                )

                sent_m     = market_result.get("sentiment", "Neutral")
                conf_m     = market_result.get("confidence", 0)
                cond_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(sent_m, "🟡")
                conclusion = market_result.get("summary", "")

                # Buat lookup analyzed_news berdasarkan judul
                analyzed_map = {
                    n.get("title", "").strip(): n
                    for n in market_result.get("analyzed_news", [])
                }

                # ── Kirim SATU pesan PER ARTIKEL baru ────────────────────────
                for raw_article in new_raw:
                    title = raw_article.get("title", "").strip()
                    if not title:
                        continue

                    article_fp  = hash_news_titles([raw_article])
                    article_key = f"article_sent:{article_fp}"

                    analyzed     = analyzed_map.get(title, {})
                    publisher    = raw_article.get("publisher", analyzed.get("publisher", ""))
                    link         = raw_article.get("link", analyzed.get("link", ""))
                    link_str     = f'\n<a href="{link}">Baca Selengkapnya</a>' if link else ""
                    summary_txt  = analyzed.get("news_summary", raw_article.get("summary", ""))
                    analysis_txt = analyzed.get("analysis", "")

                    article_msg = (
                        f"🌐 <b>Market & Macro News</b>\n"
                        f"<i>{ts}</i>\n\n"
                        f"📰 <b>{title}</b>\n"
                        f"<i>{publisher}</i>{link_str}\n\n"
                        + (f"<i>{summary_txt}</i>\n\n" if summary_txt else "")
                        + (f"💬 {analysis_txt}\n\n" if analysis_txt else "")
                        + f"<b>Sentimen Pasar:</b> {cond_emoji} {sent_m} ({conf_m}%)\n"
                        + (f"<i>{conclusion}</i>" if conclusion else "")
                    )
                    ids = send_alert_chunked(article_msg)
                    if ids:
                        cache_mark(article_key)
                        logger.info(f"[Sentiment] Market artikel '{title[:50]}' terkirim")

            except Exception as e:
                logger.error(f"[Sentiment] Error analyzing market news: {e}")

    for ticker in tickers_to_scan:
        try:
            sd = fetch_stock_data(ticker)
            if not sd.is_valid:
                continue

            # Fetch berita spesifik per saham
            stock_news = fetch_news(ticker, max_items=5)
            
            # Jika tidak ada berita, lewati pemanggilan LLM untuk hemat kuota API
            if not stock_news:
                result = {
                    "sentiment": "Neutral",
                    "confidence": 0,
                    "summary": "Tidak ada berita spesifik.",
                    "fundamental_impact": "Unknown",
                    "trigger_fundamental_review": False,
                    "affected_tickers": []
                }
            else:
                result = agent.analyze(
                    ticker=ticker,
                    company_name=sd.company_name,
                    sector=sd.sector,
                    industry=sd.industry,
                    current_price=sd.current_price,
                    day_change_pct=sd.day_change_pct,
                    news_text=stock_news,
                    market_news_text=market_news,
                    watchlist=DEFAULT_TICKERS,
                )

            # Cache hasil sentimen
            cache_set(f"sentiment:{ticker}", result)

            # ── Anti-Spam: Cek apakah perlu trigger fundamental ──────────────
            # Hanya trigger + mark jika dalam jam notifikasi aktif (07-22 WIB).
            # Jika luar jam, jangan mark → pagi hari akan ter-detect & di-trigger.
            if result.get("trigger_fundamental_review") and notif_ok:
                fund_key = f"fund_triggered:{ticker}:{today_str}"
                if cache_exists(fund_key, ttl=24 * 3600):
                    logger.info(f"[Sentiment] {ticker} sudah di-trigger fundamental hari ini — skip")
                else:
                    fund_triggers.append(ticker)
                    cache_mark(fund_key)
                    logger.info(f"[Sentiment] {ticker} -> fundamental review triggered!")

            # Cek affected_tickers dari berita
            for affected in result.get("affected_tickers", []):
                t_full = affected + ".JK" if not affected.endswith(".JK") else affected
                if t_full in DEFAULT_TICKERS and t_full not in fund_triggers and notif_ok:
                    fund_key = f"fund_triggered:{t_full}:{today_str}"
                    if not cache_exists(fund_key, ttl=24 * 3600):
                        fund_triggers.append(t_full)
                        cache_mark(fund_key)
                        logger.info(f"[Sentiment] {t_full} terdampak berita {ticker}")

            sent = result.get("sentiment", "Neutral")
            conf = result.get("confidence", 0)
            fund_impact = result.get("fundamental_impact", "Unknown")
            fund_reason = result.get("fundamental_reason", "")[:120]
            analyzed_news = result.get("analyzed_news", [])

            # Hanya proses Bullish/Bearish dengan confidence ≥ 60%
            if sent not in ("Bullish", "Bearish") or conf < 60:
                continue
            if not notif_ok:
                logger.info(f"[Sentiment] {ticker} {sent} {conf}% — tidak dikirim (luar jam notifikasi)")
                continue

            sent_emoji   = "🟢" if sent == "Bullish" else "🔴"
            ticker_short = ticker.replace(".JK", "")
            conclusion   = result.get("summary", "")

            # ── Kirim SATU pesan PER ARTIKEL ─────────────────────────────────
            # Gunakan analyzed_news (punya field analysis per artikel) jika ada,
            # fallback ke stock_news mentah.
            articles = analyzed_news if analyzed_news else stock_news

            any_sent = False
            for article in articles:
                title = article.get("title", "").strip()
                if not title:
                    continue

                # Dedup per artikel — key berdasarkan hash judul (7 hari)
                article_fp  = hash_news_titles([article])
                article_key = f"article_sent:{article_fp}"
                if cache_exists(article_key, ttl=TTL_ARTICLE_DEDUP):
                    logger.info(f"[Sentiment] {ticker_short} — '{title[:50]}' sudah dikirim, skip")
                    continue

                publisher   = article.get("publisher", "")
                link        = article.get("link", "")
                link_str    = f'\n<a href="{link}">Baca Selengkapnya</a>' if link else ""
                summary_txt = article.get("news_summary", article.get("summary", ""))
                analysis_txt = article.get("analysis", "")

                fund_line = ""
                if fund_impact not in ("Unknown", ""):
                    fund_emoji = {"Positive": "⬆️", "Negative": "⬇️", "Neutral": "➡️"}.get(fund_impact, "➡️")
                    fund_line  = f"<b>Dampak Fundamental:</b> {fund_emoji} {fund_impact}\n"
                    if fund_reason:
                        fund_line += f"<i>{fund_reason}</i>\n\n"
                    else:
                        fund_line += "\n"

                article_msg = (
                    f"{sent_emoji} <b>Sentiment Alert — {ticker_short}</b>\n"
                    f"<i>{ts}</i>\n\n"
                    f"<b>Saham:</b> {ticker_short} | {sd.current_price:,.0f} IDR ({sd.day_change_pct:+.1f}%)\n"
                    f"<b>Sentimen:</b> {sent} ({conf}%)\n\n"
                    f"📰 <b>{title}</b>\n"
                    f"<i>{publisher}</i>{link_str}\n\n"
                    + (f"<i>{summary_txt}</i>\n\n" if summary_txt else "")
                    + (f"💬 {analysis_txt}\n\n" if analysis_txt else "")
                    + fund_line
                    + f"<b>Kesimpulan:</b>\n<i>{conclusion}</i>"
                )
                ids = send_alert_chunked(article_msg)
                if ids:
                    cache_mark(article_key)
                    any_sent = True
                    logger.info(f"[Sentiment] {ticker_short} — '{title[:50]}' terkirim")

            if any_sent:
                if sent == "Bearish":
                    bearish_alerts.append(ticker)
                else:
                    bullish_alerts.append(ticker)

        except Exception as e:
            logger.debug(f"[Sentiment] {ticker}: {e}")

    logger.info(
        f"[Sentiment] Selesai — {len(bullish_alerts)} Bullish | "
        f"{len(bearish_alerts)} Bearish | {len(fund_triggers)} Fund Triggers"
    )

    # Auto-trigger Fundamental untuk saham yang perlu review (hanya dalam jam notifikasi)
    if fund_triggers and notif_ok:
        unique = list(dict.fromkeys(fund_triggers))  # deduplicate
        logger.info(f"[Sentiment] Auto-trigger Fundamental (background): {unique}")
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
    if not _is_notif_hours():
        logger.info("[Fundamental-Targeted] Luar jam notifikasi (07-22 WIB) — dibatalkan")
        return

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
        # Tandai agar sentiment scan tidak kirim ulang market news hari ini
        cache_mark(f"market_news_sent_daily:{_now().strftime('%Y-%m-%d')}")
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
        f"  Setiap 1 jam | 24/7 (Setiap Hari)\n"
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

        # ── Macro (1x/hari jam 08:00, termasuk weekend) ──────────────────────
        if (
            last_macro_date != now.date()
            and now.hour == 8 and now.minute < 5
        ):
            last_macro_date = now.date()
            _run_thread(run_macro, "macro")

        if _is_weekday():
            # ── Technical + Volume (15 menit, jam market) ────────────────────
            if (
                _is_market_hours()
                and (now - last_technical).total_seconds() >= TECHNICAL_INTERVAL_MIN * 60
            ):
                last_technical = now
                _run_thread(run_technical_volume, "tech-vol")

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

# ── Sentiment (1 jam, 24/7 setiap hari) ─────────────────────────────
        if (now - last_sentiment).total_seconds() >= SENTIMENT_INTERVAL_MIN * 60:
            last_sentiment = now
            _run_thread(run_sentiment_scan, "sentiment")

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
