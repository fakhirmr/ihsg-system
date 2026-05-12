"""
IHSG Trading System -- Automated Scheduler
==========================================
Menjalankan setiap agent secara otomatis sesuai jadwal WIB
dan mengirim laporan ke Telegram.

Jadwal:
  08:00  -- Macro Agent       : Analisis kondisi makro pagi
  08:30  -- Pre-Market Report : Briefing + watchlist
  09:15  -- Morning Scan      : Scan semua ticker (semua agent)
  12:00  -- Midday Scan       : Technical + Volume (cepat)
  14:30  -- Afternoon Scan    : Technical + Volume
  15:50  -- Closing Scan      : Scan akhir sebelum close
  16:30  -- After-Market      : Laporan penutupan + evaluasi
  (Setiap 4 jam)  -- Macro refresh

Usage:
    python scheduler.py              # Jalankan scheduler (mode daemon)
    python scheduler.py --send-schedule  # Hanya kirim kartu jadwal ke Telegram
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import time
import threading
from datetime import datetime, timedelta

# Force UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from config import DEFAULT_TICKERS, MIN_CONFIDENCE_ALERT
from utils.logger import log
from utils.telegram_sender import send_alert_chunked, send_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("Scheduler")

# ── Jadwal definisi ────────────────────────────────────────────────────────────

SCHEDULE: list[dict] = [
    {
        "time": "08:00",
        "name": "Macro Morning",
        "agent": "MacroAgent",
        "emoji": "🌐",
        "desc": "Analisis kondisi makro & ekonomi global",
        "func": "run_macro",
    },
    {
        "time": "08:30",
        "name": "Pre-Market Report",
        "agent": "MacroAgent + Supervisor",
        "emoji": "🌅",
        "desc": "Briefing pre-market + watchlist rekomendasi",
        "func": "run_premarket",
    },
    {
        "time": "09:15",
        "name": "Morning Scan",
        "agent": "All Agents (5 agent)",
        "emoji": "📊",
        "desc": "Scan lengkap semua ticker (Technical, Fundamental, Volume, Macro, Sentiment)",
        "func": "run_full_scan",
    },
    {
        "time": "12:00",
        "name": "Midday Scan",
        "agent": "Technical + Volume",
        "emoji": "🔍",
        "desc": "Scan cepat intraday — cari breakout & volume spike",
        "func": "run_quick_scan",
    },
    {
        "time": "14:30",
        "name": "Afternoon Scan",
        "agent": "Technical + Volume",
        "emoji": "📈",
        "desc": "Scan sore — konfirmasi tren & peluang entry",
        "func": "run_quick_scan",
    },
    {
        "time": "15:50",
        "name": "Closing Scan",
        "agent": "All Agents (5 agent)",
        "emoji": "🔔",
        "desc": "Scan akhir sebelum penutupan — sinyal untuk besok",
        "func": "run_full_scan",
    },
    {
        "time": "16:30",
        "name": "After-Market Report",
        "agent": "Supervisor + Learning Agent",
        "emoji": "🌆",
        "desc": "Rekap penutupan, top gainer/loser, evaluasi sinyal & outlook besok",
        "func": "run_aftermarket",
    },
]

# Macro refresh setiap 4 jam (di luar jadwal di atas)
MACRO_REFRESH_HOURS = 4


# ── Format kartu jadwal ────────────────────────────────────────────────────────

def _format_schedule_card() -> str:
    now = datetime.now()
    date_str = now.strftime("%A, %d %B %Y")
    wib_now = now.strftime("%H:%M")

    lines = [
        "<b>IHSG Trading System</b>",
        "<b>Jadwal Notifikasi Otomatis</b>",
        f"<i>{date_str} | WIB</i>",
        "",
        "=" * 35,
    ]

    for job in SCHEDULE:
        job_time = job["time"]
        # Tandai job yang sudah lewat hari ini
        try:
            job_dt = datetime.strptime(job_time, "%H:%M").replace(
                year=now.year, month=now.month, day=now.day
            )
            status = "✅" if job_dt < now else "⏰"
        except Exception:
            status = "⏰"

        lines += [
            f"{status} <b>{job['emoji']} {job_time} — {job['name']}</b>",
            f"   Agent   : {job['agent']}",
            f"   Laporan : {job['desc']}",
            "",
        ]

    lines += [
        "=" * 35,
        "",
        f"<b>Macro Refresh</b> setiap {MACRO_REFRESH_HOURS} jam sekali",
        "<b>Alert otomatis</b> jika confidence >= 65%",
        f"<b>Watchlist</b> : {len(DEFAULT_TICKERS)} saham",
        "",
        f"<i>Scheduler aktif sejak {wib_now} WIB</i>",
    ]

    return "\n".join(lines)


def send_schedule_card() -> None:
    """Kirim kartu jadwal ke Telegram."""
    msg = _format_schedule_card()
    ok = send_alert_chunked(msg)
    if ok:
        logger.info("Kartu jadwal berhasil dikirim ke Telegram.")
        print("\nKartu jadwal berhasil dikirim ke Telegram!")
    else:
        logger.error("Gagal mengirim kartu jadwal ke Telegram.")
        print("\nGagal mengirim kartu jadwal ke Telegram.")
    print(_format_schedule_card().replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", ""))


# ── Runner functions ───────────────────────────────────────────────────────────

def _notify(emoji: str, title: str, body: str = "") -> None:
    """Kirim notifikasi singkat ke Telegram."""
    msg = f"{emoji} <b>{title}</b>"
    if body:
        msg += f"\n{body}"
    send_message(msg)


def run_macro() -> None:
    logger.info("[Scheduler] Menjalankan Macro Agent...")
    _notify("🌐", "Macro Agent Aktif", "Menganalisis kondisi makro ekonomi global & domestik...")
    try:
        from agents.macro_agent import MacroAgent
        agent = MacroAgent()
        context = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        result = agent.analyze(context=context)

        cond = result.get("market_condition", "N/A")
        bias = result.get("ihsg_bias", "N/A")
        pos  = ", ".join(result.get("positive_sectors", [])[:3])
        neg  = ", ".join(result.get("negative_sectors", [])[:3])
        summary = result.get("summary", "")

        bias_emoji = {"Bullish": "📈", "Bearish": "📉"}.get(bias, "➡️")

        msg = (
            f"<b>🌐 Macro Update</b> — <i>{context}</i>\n\n"
            f"<b>Kondisi:</b> {cond}\n"
            f"<b>IHSG Bias:</b> {bias_emoji} {bias}\n\n"
            f"<b>Sektor Positif:</b> {pos or '-'}\n"
            f"<b>Sektor Negatif:</b> {neg or '-'}\n\n"
            f"<i>{summary}</i>"
        )
        send_alert_chunked(msg)
        logger.info(f"[Scheduler] Macro done: {cond} | Bias: {bias}")
    except Exception as e:
        logger.error(f"[Scheduler] Macro error: {e}")
        _notify("❌", "Macro Agent Error", str(e)[:200])


def run_premarket() -> None:
    logger.info("[Scheduler] Menjalankan Pre-Market Report...")
    _notify("🌅", "Pre-Market Dimulai", "Menyiapkan briefing pre-market...")
    try:
        from agents.supervisor import SupervisorAI
        from agents.macro_agent import MacroAgent
        from utils.report_generator import format_premarket_report, save_report

        macro_agent = MacroAgent()
        context = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        macro = macro_agent.analyze(context=f"pre-market {context}")

        supervisor = SupervisorAI()
        quick_results = supervisor.screen(DEFAULT_TICKERS[:5], send_alerts=False)
        watchlist = [
            {
                "ticker": r.get("ticker"),
                "signal": r.get("final_signal"),
                "confidence": r.get("confidence"),
                "note": r.get("agent_results", {}).get("technical", {}).get("summary", "")[:60],
            }
            for r in quick_results
        ]

        report = format_premarket_report(
            global_macro=macro.get("summary", "N/A"),
            ihsg_outlook=(
                f"Kondisi: {macro.get('market_condition')} | "
                f"Bias: {macro.get('ihsg_bias')} | "
                f"Positif: {', '.join(macro.get('positive_sectors', [])[:3])}"
            ),
            watchlist=watchlist,
            top_news=macro.get("key_drivers", ["Tidak ada data"]),
        )
        save_report(report, "premarket")
        send_alert_chunked(report)
        logger.info("[Scheduler] Pre-market report sent.")
    except Exception as e:
        logger.error(f"[Scheduler] Pre-market error: {e}")
        _notify("❌", "Pre-Market Report Error", str(e)[:200])


def run_full_scan() -> None:
    logger.info("[Scheduler] Menjalankan Full Scan semua ticker...")
    _notify(
        "📊", "Full Scan Dimulai",
        f"Menganalisis {len(DEFAULT_TICKERS)} saham dengan 5 agent...\n"
        f"Estimasi waktu: {len(DEFAULT_TICKERS) * 1} menit"
    )
    try:
        from agents.supervisor import SupervisorAI
        supervisor = SupervisorAI()
        results = supervisor.screen(DEFAULT_TICKERS, send_alerts=True, min_confidence=0)

        buy   = [r for r in results if r.get("final_signal") == "BUY"]
        sell  = [r for r in results if r.get("final_signal") == "SELL"]
        neut  = [r for r in results if r.get("final_signal") == "NEUTRAL"]

        # Ringkasan scan
        buy_list  = "\n".join(f"  🟢 {r['ticker']} — {r['confidence']}%" for r in buy[:5])
        sell_list = "\n".join(f"  🔴 {r['ticker']} — {r['confidence']}%" for r in sell[:5])

        ts = datetime.now().strftime("%H:%M WIB")
        msg = (
            f"<b>📊 Scan Selesai — {ts}</b>\n\n"
            f"<b>Hasil:</b> {len(buy)} BUY | {len(sell)} SELL | {len(neut)} NEUTRAL\n\n"
        )
        if buy_list:
            msg += f"<b>Top BUY:</b>\n{buy_list}\n\n"
        if sell_list:
            msg += f"<b>Top SELL:</b>\n{sell_list}\n"

        send_alert_chunked(msg)
        logger.info(f"[Scheduler] Full scan done: {len(buy)} BUY, {len(sell)} SELL")
    except Exception as e:
        logger.error(f"[Scheduler] Full scan error: {e}")
        _notify("❌", "Full Scan Error", str(e)[:200])


def run_quick_scan() -> None:
    """Scan cepat — Technical + Volume only (tanpa LLM call berat)."""
    logger.info("[Scheduler] Menjalankan Quick Scan (Technical + Volume)...")
    _notify("🔍", "Quick Scan Dimulai", f"Scan {len(DEFAULT_TICKERS)} saham (Technical + Volume)...")
    try:
        from agents.supervisor import SupervisorAI
        # Gunakan hanya ticker prioritas (top 5) untuk scan cepat
        quick_tickers = DEFAULT_TICKERS[:6]
        supervisor = SupervisorAI()
        results = supervisor.screen(quick_tickers, send_alerts=True, min_confidence=65)

        buy  = [r for r in results if r.get("final_signal") == "BUY"]
        sell = [r for r in results if r.get("final_signal") == "SELL"]

        ts = datetime.now().strftime("%H:%M WIB")
        if buy or sell:
            buy_list  = "\n".join(f"  🟢 {r['ticker']} — {r['confidence']}%" for r in buy)
            sell_list = "\n".join(f"  🔴 {r['ticker']} — {r['confidence']}%" for r in sell)
            msg = (
                f"<b>🔍 Quick Scan — {ts}</b>\n\n"
                f"<b>Sinyal Kuat:</b>\n"
                f"{buy_list}\n{sell_list}"
            )
            send_alert_chunked(msg)
        else:
            _notify("🔍", f"Quick Scan {ts}", "Tidak ada sinyal kuat saat ini. Market sideways.")

        logger.info(f"[Scheduler] Quick scan done: {len(buy)} BUY, {len(sell)} SELL")
    except Exception as e:
        logger.error(f"[Scheduler] Quick scan error: {e}")
        _notify("❌", "Quick Scan Error", str(e)[:200])


def run_aftermarket() -> None:
    logger.info("[Scheduler] Menjalankan After-Market Report...")
    _notify("🌆", "After-Market Dimulai", "Menyiapkan laporan penutupan pasar...")
    try:
        from agents.supervisor import SupervisorAI
        from agents.macro_agent import MacroAgent
        from utils.report_generator import format_aftermarket_report, save_report

        supervisor = SupervisorAI()
        macro_agent = MacroAgent()
        context = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        macro = macro_agent.analyze(context=f"after-market {context}")

        results = supervisor.screen(DEFAULT_TICKERS, send_alerts=False)
        gainers = sorted(results, key=lambda r: r.get("day_change_pct", 0), reverse=True)[:5]
        losers  = sorted(results, key=lambda r: r.get("day_change_pct", 0))[:5]
        learning = supervisor.learning_agent.analyze()

        report = format_aftermarket_report(
            ihsg_summary=macro.get("summary", "N/A"),
            top_gainers=[{"ticker": r["ticker"], "change_pct": r["day_change_pct"]} for r in gainers],
            top_losers=[{"ticker": r["ticker"], "change_pct": r["day_change_pct"]} for r in losers],
            foreign_flow=macro.get("ihsg_bias", "Neutral"),
            sector_best=", ".join(macro.get("positive_sectors", ["N/A"])[:3]),
            sector_worst=", ".join(macro.get("negative_sectors", ["N/A"])[:3]),
            signal_eval=learning.get("summary", "Belum ada evaluasi."),
            tomorrow_outlook=macro.get("summary", "N/A"),
        )
        save_report(report, "aftermarket")
        send_alert_chunked(report)
        logger.info("[Scheduler] After-market report sent.")
    except Exception as e:
        logger.error(f"[Scheduler] After-market error: {e}")
        _notify("❌", "After-Market Error", str(e)[:200])


# ── Core scheduler loop ────────────────────────────────────────────────────────

_FUNC_MAP = {
    "run_macro":      run_macro,
    "run_premarket":  run_premarket,
    "run_full_scan":  run_full_scan,
    "run_quick_scan": run_quick_scan,
    "run_aftermarket": run_aftermarket,
}


def _time_to_today(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    now = datetime.now()
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def _run_in_thread(func_name: str) -> None:
    fn = _FUNC_MAP.get(func_name)
    if fn:
        t = threading.Thread(target=fn, name=func_name, daemon=True)
        t.start()


def _is_weekday() -> bool:
    return datetime.now().weekday() < 5  # Mon-Fri


def run_scheduler() -> None:
    logger.info("=" * 50)
    logger.info("  IHSG Scheduler aktif")
    logger.info("=" * 50)

    # Kirim kartu jadwal ke Telegram saat startup
    send_schedule_card()

    # Track last macro refresh
    last_macro_refresh = datetime.now() - timedelta(hours=MACRO_REFRESH_HOURS)

    # Build today's job queue
    def _build_queue() -> list[dict]:
        queue = []
        for job in SCHEDULE:
            dt = _time_to_today(job["time"])
            if dt > datetime.now():
                queue.append({"dt": dt, "func": job["func"], "name": job["name"]})
        queue.sort(key=lambda x: x["dt"])
        return queue

    job_queue = _build_queue()
    logger.info(f"Jobs hari ini: {[j['name'] for j in job_queue]}")

    while True:
        now = datetime.now()

        # ── Jalankan scheduled jobs ──────────────────────────────────────────
        for job in list(job_queue):
            if now >= job["dt"]:
                if _is_weekday():
                    logger.info(f"[Scheduler] Menjalankan: {job['name']}")
                    _run_in_thread(job["func"])
                else:
                    logger.info(f"[Scheduler] Weekend — skip {job['name']}")
                job_queue.remove(job)

        # ── Macro refresh setiap N jam ───────────────────────────────────────
        if (now - last_macro_refresh).total_seconds() >= MACRO_REFRESH_HOURS * 3600:
            if _is_weekday() and 8 <= now.hour < 17:
                logger.info("[Scheduler] Macro refresh...")
                _run_in_thread("run_macro")
            last_macro_refresh = now

        # ── Reset queue untuk hari berikutnya ────────────────────────────────
        if not job_queue and now.hour >= 17:
            next_day_check = now.replace(hour=7, minute=55, second=0)
            if now > next_day_check:
                tomorrow = now + timedelta(days=1)
                logger.info(f"[Scheduler] Queue habis. Reset besok: {tomorrow.strftime('%Y-%m-%d')}")
                time.sleep(60)
                job_queue = _build_queue()

        time.sleep(30)  # Cek setiap 30 detik


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="IHSG Scheduler")
    parser.add_argument(
        "--send-schedule", action="store_true",
        help="Hanya kirim kartu jadwal ke Telegram, lalu keluar"
    )
    args = parser.parse_args()

    if args.send_schedule:
        send_schedule_card()
    else:
        run_scheduler()


if __name__ == "__main__":
    main()
