"""Hitung entry/TP/SL langsung dari indikator teknikal (tanpa LLM parse issue)."""
import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from datetime import datetime
from utils.data_fetcher import fetch_stock_data
from utils.technical_calculator import calculate_technical_data
from utils.telegram_sender import send_alert_chunked

BUY_TICKERS = ["ADRO.JK", "ISAT.JK"]
ts = datetime.now().strftime("%d %b %Y %H:%M WIB")
NL = "\n"

msg_parts = [
    "<b>IHSG BUY Signal — Entry / TP / Cut Loss</b>",
    f"<i>{ts}</i>",
    "",
]

for ticker in BUY_TICKERS:
    sd = fetch_stock_data(ticker)
    td = calculate_technical_data(ticker, sd.price_history)
    p  = sd.current_price

    # ── Hitung level dari indikator ──────────────────────────────────────────
    # Entry: harga sekarang (atau EMA20 jika lebih rendah → entry lebih baik)
    entry = round(min(p, td.ema_20) if td.ema_20 > 0 else p, 0)

    # TP1: Resistance 1 (target konservatif)
    # TP2: Resistance 2 (target optimis)
    tp1 = round(td.resistance_1 if td.resistance_1 > entry else entry * 1.04, 0)
    tp2 = round(td.resistance_2 if td.resistance_2 > tp1   else entry * 1.08, 0)

    # Cut Loss: Support 1, tidak lebih dari -5% dari entry
    cl_from_support = td.support_1 if td.support_1 > 0 and td.support_1 < entry else 0
    cl_max          = entry * 0.95   # maksimal -5%
    cl = round(max(cl_from_support, cl_max), 0) if cl_from_support > 0 else round(cl_max, 0)

    # Potensi %
    tp1_pct = (tp1 - entry) / entry * 100
    tp2_pct = (tp2 - entry) / entry * 100
    cl_pct  = (cl  - entry) / entry * 100
    rr1     = tp1_pct / abs(cl_pct) if cl_pct != 0 else 0

    print(f"\n{ticker} — {sd.company_name}")
    print(f"  Harga kini : {p:,.0f}")
    print(f"  Entry      : {entry:,.0f}")
    print(f"  Sell TP1   : {tp1:,.0f}  ({tp1_pct:+.1f}%)")
    print(f"  Sell TP2   : {tp2:,.0f}  ({tp2_pct:+.1f}%)")
    print(f"  Cut Loss   : {cl:,.0f}  ({cl_pct:+.1f}%)")
    print(f"  Risk:Reward: 1:{rr1:.2f}")
    print(f"  RSI:{td.rsi_14:.0f} | Trend:{td.trend} | EMA20:{td.ema_20:,.0f} | EMA50:{td.ema_50:,.0f}")

    # ── Konteks teknikal ─────────────────────────────────────────────────────
    notes = []
    if td.trend == "UPTREND":         notes.append("Tren Naik (Uptrend)")
    if td.is_above_ema20:             notes.append("Harga di atas EMA20")
    if td.is_above_ema50:             notes.append("Harga di atas EMA50")
    if td.is_breakout:                notes.append("Breakout resistance!")
    if td.macd_histogram > 0:         notes.append("MACD momentum positif")
    if td.higher_high:                notes.append("Pola Higher High")
    if 40 < td.rsi_14 < 70:          notes.append(f"RSI sehat ({td.rsi_14:.0f})")
    if sd.relative_volume >= 1.5:    notes.append(f"Volume naik {sd.relative_volume:.1f}x")

    warnings = []
    if td.rsi_14 > 68:               warnings.append(f"RSI mendekati overbought ({td.rsi_14:.0f})")
    if sd.relative_volume < 0.5:     warnings.append("Volume tipis — hati-hati")
    if not td.is_above_ema50:        warnings.append("Belum di atas EMA50")

    notes_txt    = NL.join(f"  + {x}" for x in notes[:4])
    warnings_txt = NL.join(f"  ! {x}" for x in warnings[:2])

    conf_label = "KUAT" if td.trend == "UPTREND" and td.macd_histogram > 0 else "MODERAT"
    rr_label   = "Bagus" if rr1 >= 1.5 else "Cukup" if rr1 >= 1.0 else "Kecil — Pastikan SL ketat"

    block = [
        "=" * 38,
        f"<b>{ticker}  |  {sd.company_name}</b>",
        f"Harga Kini : <b>{p:,.0f}</b> ({sd.day_change_pct:+.1f}%)",
        f"Timeframe  : Swing  |  Signal: {conf_label}",
        "",
        f"<b>ENTRY    :  {entry:,.0f}</b>",
        f"<b>SELL TP1 :  {tp1:,.0f}</b>  ({tp1_pct:+.1f}%)",
        f"<b>SELL TP2 :  {tp2:,.0f}</b>  ({tp2_pct:+.1f}%)",
        f"<b>CUT LOSS :  {cl:,.0f}</b>   ({cl_pct:+.1f}%)",
        f"Risk:Reward : 1:{rr1:.2f}  [{rr_label}]",
        "",
        f"<b>Sinyal Teknikal:</b>",
        notes_txt if notes_txt else "  (tidak ada sinyal tambahan)",
    ]
    if warnings_txt:
        block += ["", f"<b>Perhatian:</b>", warnings_txt]

    block += [
        "",
        f"<b>Support  :</b> {td.support_1:,.0f}",
        f"<b>Resistance:</b> {td.resistance_1:,.0f}",
        f"RSI: {td.rsi_14:.0f} | MACD: {'Positif' if td.macd_histogram > 0 else 'Negatif'} | Vol: {sd.relative_volume:.1f}x",
    ]
    msg_parts.extend(block)
    msg_parts.append("")

msg_parts += [
    "=" * 38,
    "<i>Bukan rekomendasi investasi.</i>",
    "<i>Selalu gunakan manajemen risiko yang tepat.</i>",
]

full_msg = NL.join(msg_parts)
print("\nMengirim ke Telegram...")
ok = send_alert_chunked(full_msg)
print("Terkirim!" if ok else "Gagal.")
