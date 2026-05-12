"""
IHSG — Fast Technical Screener (tanpa LLM)
==========================================
Scan 58 saham menggunakan indikator teknikal murni (no API call):
EMA, RSI, MACD, Bollinger Bands, Support/Resistance, Volume.

Menghasilkan skor BUY per saham dan ranking siapa yang paling layak di-call.
"""
from __future__ import annotations

import io
import sys
import logging
from datetime import datetime
from typing import Any

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING)  # Suppress noise

from config import DEFAULT_TICKERS
from utils.data_fetcher import fetch_stock_data
from utils.technical_calculator import calculate_technical_data
from utils.telegram_sender import send_alert_chunked


def score_ticker(ticker: str) -> dict[str, Any] | None:
    """Hitung skor teknikal murni untuk 1 ticker. Return None jika data tidak valid."""
    try:
        sd = fetch_stock_data(ticker)
        if not sd.is_valid or sd.current_price <= 0:
            return None

        td = calculate_technical_data(ticker, sd.price_history)
        score = 0
        signals: list[str] = []
        warnings: list[str] = []

        # ── Trend (bobot tinggi) ──────────────────────────────────────────────
        if td.trend == "UPTREND":
            score += 20
            signals.append("Uptrend")
        elif td.trend == "DOWNTREND":
            score -= 20
            warnings.append("Downtrend")

        # ── EMA Alignment ────────────────────────────────────────────────────
        if td.is_above_ema20:
            score += 10
            signals.append("Di atas EMA20")
        else:
            score -= 10
            warnings.append("Di bawah EMA20")

        if td.is_above_ema50:
            score += 8
            signals.append("Di atas EMA50")
        else:
            score -= 8

        if td.is_above_ema200:
            score += 7
            signals.append("Di atas EMA200")
        else:
            score -= 5
            warnings.append("Di bawah EMA200")

        # ── RSI ──────────────────────────────────────────────────────────────
        if 40 <= td.rsi_14 <= 60:
            score += 5
            signals.append(f"RSI normal ({td.rsi_14:.0f})")
        elif td.rsi_14 < 35:
            score += 8   # Oversold = potensi rebound
            signals.append(f"RSI oversold ({td.rsi_14:.0f}) - potensi rebound")
        elif td.rsi_14 > 70:
            score -= 10
            warnings.append(f"RSI overbought ({td.rsi_14:.0f})")
        elif 60 < td.rsi_14 <= 70:
            score += 3
            signals.append(f"RSI bullish ({td.rsi_14:.0f})")

        # ── MACD ─────────────────────────────────────────────────────────────
        if td.macd_histogram > 0:
            score += 8
            signals.append("MACD histogram positif")
        else:
            score -= 5
            warnings.append("MACD histogram negatif")

        if td.macd_line > td.macd_signal:
            score += 5
            signals.append("MACD bullish crossover")
        else:
            score -= 3

        # ── Breakout ─────────────────────────────────────────────────────────
        if td.is_breakout:
            score += 15
            signals.append("BREAKOUT resistance!")
        if td.is_breakdown:
            score -= 15
            warnings.append("BREAKDOWN support!")

        # ── Higher High (momentum naik) ───────────────────────────────────────
        if td.higher_high:
            score += 8
            signals.append("Higher High terbentuk")
        if td.lower_low:
            score -= 8
            warnings.append("Lower Low terbentuk")

        # ── Volume ───────────────────────────────────────────────────────────
        if sd.relative_volume >= 2.0:
            score += 10
            signals.append(f"Volume spike {sd.relative_volume:.1f}x")
        elif sd.relative_volume >= 1.3:
            score += 5
            signals.append(f"Volume naik {sd.relative_volume:.1f}x")
        elif sd.relative_volume < 0.5:
            score -= 5
            warnings.append("Volume rendah")

        # ── Bollinger Band position ───────────────────────────────────────────
        if td.bb_lower > 0 and sd.current_price <= td.bb_lower * 1.02:
            score += 8
            signals.append("Dekat BB Lower (potential bounce)")
        elif td.bb_upper > 0 and sd.current_price >= td.bb_upper * 0.98:
            score -= 5
            warnings.append("Dekat BB Upper (potential reversal)")

        # ── Perubahan harian ─────────────────────────────────────────────────
        if sd.day_change_pct > 2:
            score += 3
        elif sd.day_change_pct < -3:
            score -= 5
            warnings.append(f"Turun tajam {sd.day_change_pct:.1f}%")

        return {
            "ticker": ticker,
            "company": sd.company_name,
            "price": sd.current_price,
            "change_pct": sd.day_change_pct,
            "score": score,
            "rsi": td.rsi_14,
            "rel_vol": sd.relative_volume,
            "trend": td.trend,
            "breakout": td.is_breakout,
            "macd_bull": td.macd_histogram > 0,
            "signals": signals[:4],   # top 4 reasons
            "warnings": warnings[:2],
            "r1": td.resistance_1,
            "s1": td.support_1,
            "atr": td.atr_14,
        }
    except Exception as e:
        print(f"  [SKIP] {ticker}: {e}")
        return None


def run_screen():
    print("=" * 60)
    print("  IHSG Fast Technical Screener")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M WIB')}")
    print(f"  Scanning {len(DEFAULT_TICKERS)} saham...")
    print("=" * 60)

    results: list[dict] = []

    for i, ticker in enumerate(DEFAULT_TICKERS, 1):
        print(f"  [{i:02d}/{len(DEFAULT_TICKERS)}] {ticker}...", end=" ", flush=True)
        r = score_ticker(ticker)
        if r:
            results.append(r)
            signal = "BUY " if r["score"] >= 30 else ("SELL" if r["score"] <= -20 else "HOLD")
            print(f"Score:{r['score']:+d} | RSI:{r['rsi']:.0f} | {signal}")
        else:
            print("SKIP (data tidak valid)")

    # ── Ranking ──────────────────────────────────────────────────────────────
    results.sort(key=lambda x: x["score"], reverse=True)

    buy_signals  = [r for r in results if r["score"] >= 30]
    hold_signals = [r for r in results if 10 <= r["score"] < 30]
    sell_signals = [r for r in results if r["score"] <= -20]

    # ── Print ke konsol ───────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  HASIL: {len(buy_signals)} BUY | {len(hold_signals)} WATCH | {len(sell_signals)} AVOID")
    print("=" * 60)

    if buy_signals:
        print("\n  LAYAK DI-CALL (BUY Signal):")
        print(f"  {'#':<3} {'Ticker':<10} {'Score':>6} {'Harga':>10} {'Chg%':>7} {'RSI':>5} {'Vol':>5}  Alasan")
        print("  " + "-" * 70)
        for i, r in enumerate(buy_signals, 1):
            reasons = " | ".join(r["signals"][:2])
            print(
                f"  {i:<3} {r['ticker']:<10} {r['score']:>+6} "
                f"{r['price']:>10,.0f} {r['change_pct']:>+6.1f}% "
                f"{r['rsi']:>5.0f} {r['rel_vol']:>4.1f}x  {reasons}"
            )

    # ── Format Telegram ───────────────────────────────────────────────────────
    ts = datetime.now().strftime("%d %b %Y %H:%M WIB")
    NL = "\n"

    # Top BUY
    buy_lines = []
    for i, r in enumerate(buy_signals[:15], 1):
        tag = " BREAKOUT!" if r["breakout"] else ""
        reasons = ", ".join(r["signals"][:2])
        buy_lines.append(
            f"  {i}. <b>{r['ticker']}</b> [{r['price']:,.0f}] "
            f"{r['change_pct']:+.1f}% | Skor:{r['score']:+d}"
            f"{tag}\n"
            f"     {reasons}"
        )

    # Watch list
    watch_lines = [
        f"  {r['ticker']} [{r['price']:,.0f}] Skor:{r['score']:+d}"
        for r in hold_signals[:8]
    ]

    # Avoid list
    avoid_lines = [
        f"  {r['ticker']} [{r['price']:,.0f}] Skor:{r['score']:+d}"
        for r in sell_signals[:5]
    ]

    msg = (
        f"<b>IHSG Technical Screen</b>\n"
        f"<i>{ts} | {len(DEFAULT_TICKERS)} saham dianalisis</i>\n\n"
        f"<b>Hasil: {len(buy_signals)} BUY | {len(hold_signals)} WATCH | {len(sell_signals)} AVOID</b>\n"
        f"{'=' * 30}\n\n"
    )

    if buy_signals:
        msg += f"<b>LAYAK DI-CALL (BUY Signal):</b>\n"
        msg += NL.join(buy_lines)
        msg += "\n\n"

    if hold_signals:
        msg += f"<b>WATCH LIST:</b>\n"
        msg += NL.join(watch_lines)
        msg += "\n\n"

    if sell_signals:
        msg += f"<b>HINDARI (Teknikal Lemah):</b>\n"
        msg += NL.join(avoid_lines)
        msg += "\n"

    msg += f"\n<i>Skor teknikal murni — tanpa LLM</i>"

    print()
    print("Mengirim laporan ke Telegram...")
    ok = send_alert_chunked(msg)
    print("Terkirim!" if ok else "Gagal kirim ke Telegram.")
    return results


if __name__ == "__main__":
    run_screen()
