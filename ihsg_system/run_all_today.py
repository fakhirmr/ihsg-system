"""
Laporan Harian Manual — Efisien
================================
1. Macro Agent (Gemini langsung, skip Groq)
2. Technical Fast Screener (no LLM, semua 58 saham)
3. Sentiment untuk top 10 saham aktif
4. Supervisor untuk top 10 BUY candidates saja
"""
import sys, io, logging
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING)

from datetime import datetime
from config import DEFAULT_TICKERS, MIN_CONFIDENCE_ALERT
from utils.data_fetcher import fetch_stock_data
from utils.technical_calculator import calculate_technical_data
from utils.telegram_sender import send_alert_chunked, send_message

ts = datetime.now().strftime("%d %b %Y %H:%M WIB")
NL = "\n"

send_message(f"<b>Laporan Hari Ini — {ts}</b>\nMemulai... (4 tahap)")

# ─── 1. MACRO ─────────────────────────────────────────────────────────────────
print("[1/4] Macro Agent...")
try:
    from agents.macro_agent import MacroAgent
    macro_result = MacroAgent().analyze(context=ts)
    cond    = macro_result.get("market_condition", "N/A")
    bias    = macro_result.get("ihsg_bias", "N/A")
    pos     = ", ".join(macro_result.get("positive_sectors", [])[:4])
    neg     = ", ".join(macro_result.get("negative_sectors", [])[:3])
    summary = macro_result.get("summary", "")
    bias_emoji = {"Bullish": "📈", "Bearish": "📉"}.get(bias, "➡️")

    send_alert_chunked(
        f"<b>🌐 Macro — {ts}</b>\n\n"
        f"<b>Kondisi:</b> {cond}\n"
        f"<b>IHSG Bias:</b> {bias_emoji} {bias}\n\n"
        f"<b>Positif:</b> {pos or '-'}\n"
        f"<b>Negatif:</b> {neg or '-'}\n\n"
        f"<i>{summary}</i>"
    )
    print(f"  Macro: {cond} | {bias}")
except Exception as e:
    print(f"  Macro error: {e}")
    macro_result = {}

# ─── 2. TECHNICAL FAST SCREENER (no LLM) ─────────────────────────────────────
print("[2/4] Technical Fast Screener (58 saham)...")
send_message("🔍 <b>Technical Screener</b>\nScanning 58 saham...")

scored = []
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
        if td.is_above_ema200:          score += 7
        else:                           score -= 5
        if td.macd_histogram > 0:       score += 8
        else:                           score -= 5
        if td.macd_line > td.macd_signal: score += 5
        if td.is_breakout:              score += 15
        if td.is_breakdown:             score -= 15
        if td.higher_high:              score += 8
        if td.lower_low:               score -= 8
        if 40 <= td.rsi_14 <= 65:      score += 5
        elif td.rsi_14 > 70:           score -= 10
        elif td.rsi_14 < 35:           score += 5  # oversold bounce potential
        if sd.relative_volume >= 2.0:  score += 10
        elif sd.relative_volume >= 1.3: score += 5

        # Hitung level teknikal
        entry = p
        tp1   = round(td.resistance_1 if td.resistance_1 > entry else entry * 1.04, 0)
        tp2   = round(td.resistance_2 if td.resistance_2 > tp1   else entry * 1.08, 0)
        sl    = round(max(td.support_1, entry * 0.95) if td.support_1 > 0 and td.support_1 < entry else entry * 0.95, 0)

        scored.append({
            "ticker": ticker, "company": sd.company_name,
            "price": p, "change": sd.day_change_pct,
            "score": score, "rsi": td.rsi_14,
            "vol": sd.relative_volume, "trend": td.trend,
            "breakout": td.is_breakout,
            "entry": entry, "tp1": tp1, "tp2": tp2, "sl": sl,
            "td": td, "sd": sd,
        })
        print(f"  {ticker}: {score:+d}")
    except Exception as e:
        print(f"  {ticker}: skip ({e})")

scored.sort(key=lambda x: x["score"], reverse=True)
buy_list  = [r for r in scored if r["score"] >= 30]
watch_list = [r for r in scored if 10 <= r["score"] < 30]
weak_list  = [r for r in scored if r["score"] <= -35]

buy_lines = []
for r in buy_list[:10]:
    tag = " 🔥BREAKOUT" if r["breakout"] else ""
    tp1_pct = (r["tp1"] - r["entry"]) / r["entry"] * 100 if r["entry"] > 0 else 0
    sl_pct  = (r["sl"]  - r["entry"]) / r["entry"] * 100 if r["entry"] > 0 else 0
    buy_lines.append(
        f"  <b>{r['ticker']}</b> {r['price']:,.0f} ({r['change']:+.1f}%) Skor:{r['score']:+d}{tag}\n"
        f"  Entry:{r['entry']:,.0f} TP1:{r['tp1']:,.0f}({tp1_pct:+.1f}%) SL:{r['sl']:,.0f}({sl_pct:+.1f}%) RSI:{r['rsi']:.0f}"
    )

watch_lines = [f"  <b>{r['ticker']}</b> {r['price']:,.0f} ({r['change']:+.1f}%) Skor:{r['score']:+d}" for r in watch_list[:6]]
weak_lines  = [f"  <b>{r['ticker']}</b> {r['price']:,.0f} ({r['change']:+.1f}%) Skor:{r['score']:+d}" for r in weak_list[:5]]

tech_msg = (
    f"<b>Technical Screen — {ts}</b>\n"
    f"<b>{len(buy_list)} BUY | {len(watch_list)} WATCH | {len(weak_list)} HINDARI</b>\n\n"
)
if buy_lines:
    tech_msg += f"<b>BUY Signals:</b>\n" + NL.join(buy_lines) + "\n\n"
if watch_lines:
    tech_msg += f"<b>Watch List:</b>\n" + NL.join(watch_lines) + "\n\n"
if weak_lines:
    tech_msg += f"<b>Hindari:</b>\n" + NL.join(weak_lines)

send_alert_chunked(tech_msg)
print(f"  Technical done: {len(buy_list)} BUY | {len(watch_list)} WATCH")

# ─── 3. SENTIMENT — top 10 saham aktif ──────────────────────────────────────
print("[3/4] Sentiment Scan (top 10)...")
send_message("📰 <b>Sentiment Scan</b>\nMenganalisis berita top 10 saham...")

top10 = [r["ticker"] for r in scored[:10]]
from agents.news_sentiment_agent import NewsSentimentAgent
sent_agent = NewsSentimentAgent()
fund_triggers = []
sentiment_lines = []

for ticker in top10:
    try:
        r = next((x for x in scored if x["ticker"] == ticker), None)
        if not r:
            continue
        sd = r["sd"]
        result = sent_agent.analyze(
            ticker=ticker, company_name=sd.company_name,
            sector=sd.sector, industry=sd.industry,
            current_price=sd.current_price, day_change_pct=sd.day_change_pct,
            watchlist=DEFAULT_TICKERS,
        )
        sent     = result.get("sentiment", "Neutral")
        conf     = result.get("confidence", 0)
        fi       = result.get("fundamental_impact", "Unknown")
        trigger  = result.get("trigger_fundamental_review", False)
        summary  = result.get("summary", "")[:80]

        emoji = {"Bullish": "🟢", "Bearish": "🔴"}.get(sent, "🟡")
        sentiment_lines.append(f"  {emoji} <b>{ticker}</b> ({conf}%) | Fund:{fi}\n  {summary}")

        if trigger:
            fund_triggers.append(ticker)
        print(f"  {ticker}: {sent} ({conf}%) | Fund:{fi}")
    except Exception as e:
        print(f"  {ticker}: sentiment error {e}")

if sentiment_lines:
    send_alert_chunked(
        f"<b>Sentiment Report — {ts}</b>\n\n"
        + NL.join(sentiment_lines)
        + (f"\n\n<b>Fundamental Review Dipicu:</b> {', '.join(t.replace('.JK','') for t in fund_triggers)}" if fund_triggers else "")
    )

# ─── 4. SUPERVISOR — hanya top 5 BUY candidates (hemat LLM) ─────────────────
print("[4/4] Supervisor Closing (top 5 BUY)...")
top5_tickers = [r["ticker"] for r in buy_list[:5]] if buy_list else []

if not top5_tickers:
    # Jika tidak ada BUY, ambil top 5 berdasarkan skor
    top5_tickers = [r["ticker"] for r in scored[:5]]

send_message(
    f"<b>Supervisor Closing Scan</b>\n"
    f"Menganalisis top candidates:\n"
    + NL.join(f"  • {t}" for t in top5_tickers)
)

closing_lines = []
from agents.supervisor import SupervisorAI
supervisor = SupervisorAI()

try:
    results = supervisor.screen(top5_tickers, send_alerts=False, min_confidence=0)
    for r in results:
        sig  = r.get("final_signal", "NEUTRAL")
        conf = r.get("confidence", 0)
        t    = r.get("ticker", "")
        entry_p = r.get("entry", 0)
        tp1_p   = r.get("tp1", 0)
        sl_p    = r.get("sl", 0)
        strategy = r.get("agent_results", {}).get("technical", {}).get("strategy", "")
        summary  = r.get("agent_results", {}).get("technical", {}).get("summary", "")[:60]

        sig_emoji = {"BUY": "🟢", "SELL": "🔴"}.get(sig, "🟡")
        tp1_pct = (tp1_p - entry_p) / entry_p * 100 if entry_p > 0 else 0
        sl_pct  = (sl_p  - entry_p) / entry_p * 100 if entry_p > 0 else 0

        closing_lines.append(
            f"{sig_emoji} <b>{t}</b> — {sig} ({conf}%)\n"
            f"  Strategi: {strategy}\n"
            f"  Entry:{entry_p:,.0f} TP1:{tp1_p:,.0f}({tp1_pct:+.1f}%) SL:{sl_p:,.0f}({sl_pct:+.1f}%)\n"
            f"  {summary}"
        )
        print(f"  {t}: {sig} ({conf}%) | {strategy}")
except Exception as e:
    print(f"  Supervisor error: {e}")
    closing_lines.append(f"Error: {str(e)[:100]}")

# Macro bias untuk closing summary
macro_bias    = macro_result.get("ihsg_bias", "Neutral")
macro_cond    = macro_result.get("market_condition", "N/A")
macro_summary = macro_result.get("summary", "")[:100]

closing_msg = (
    f"<b>Supervisor Closing Report — {ts}</b>\n\n"
    f"<b>Macro:</b> {macro_cond} | Bias: {macro_bias}\n"
    f"<i>{macro_summary}</i>\n\n"
    f"<b>Top Candidates:</b>\n\n"
    + NL.join(closing_lines)
    + f"\n\n<i>Analisis berbasis {len(top5_tickers)} saham teratas dari 58 watchlist</i>"
)

send_alert_chunked(closing_msg)
print("\nSemua laporan selesai! Cek Telegram kamu.")
