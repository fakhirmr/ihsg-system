"""Test MacroAgent dan kirim laporan ke Telegram."""
import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from agents.macro_agent import MacroAgent
from utils.telegram_sender import send_alert_chunked
from datetime import datetime

print("Memanggil MacroAgent...")
agent = MacroAgent()
context = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
result = agent.analyze(context=context)

# ── Print ke console ──────────────────────────────────────────────────────────
print()
print("=" * 55)
print("  LAPORAN KONDISI MAKRO IHSG")
print("=" * 55)
print(f"  Kondisi Pasar : {result.get('market_condition')}")
print(f"  Sentimen      : {result.get('sentiment')}")
print(f"  IHSG Bias     : {result.get('ihsg_bias')}")
print()
print("  Sektor Positif:")
for s in result.get("positive_sectors", []):
    print(f"    + {s}")
print("  Sektor Negatif:")
for s in result.get("negative_sectors", []):
    print(f"    - {s}")
print("  Sektor Netral:")
for s in result.get("neutral_sectors", []):
    print(f"    ~ {s}")
print()
print("  Key Drivers:")
for d in result.get("key_drivers", []):
    print(f"    * {d}")
print()
print("  Risiko:")
for r in result.get("risks", []):
    print(f"    ! {r}")
print()
print("  Summary:")
print(f"  {result.get('summary')}")
print("=" * 55)

# ── Format pesan Telegram ─────────────────────────────────────────────────────
NL = "\n"
pos  = NL.join("  + " + s for s in result.get("positive_sectors", []))
neg  = NL.join("  - " + s for s in result.get("negative_sectors", []))
drv  = NL.join("  * " + d for d in result.get("key_drivers", []))
risk = NL.join("  ! " + r for r in result.get("risks", []))

bias_emoji = {"Bullish": "📈", "Bearish": "📉", "Neutral": "➡️"}.get(
    result.get("ihsg_bias", "Neutral"), "➡️"
)
cond_emoji = {
    "Risk-On Bullish": "🟢", "Mild Bullish": "🟢",
    "Neutral": "🟡",
    "Mild Bearish": "🔴", "Risk-Off Bearish": "🔴",
}.get(result.get("market_condition", "Neutral"), "🟡")

msg = (
    f"<b>{cond_emoji} IHSG Macro Report</b>\n"
    f"<i>{context}</i>\n\n"
    f"<b>Kondisi Pasar:</b> {result.get('market_condition')}\n"
    f"<b>IHSG Bias:</b> {bias_emoji} {result.get('ihsg_bias')}\n\n"
    f"<b>Sektor Positif:</b>\n{pos if pos else '  (tidak ada)'}\n\n"
    f"<b>Sektor Negatif:</b>\n{neg if neg else '  (tidak ada)'}\n\n"
    f"<b>Key Drivers:</b>\n{drv}\n\n"
    f"<b>Risiko:</b>\n{risk}\n\n"
    f"<b>Summary:</b>\n{result.get('summary')}"
)

print()
print("Mengirim laporan ke Telegram...")
ok = send_alert_chunked(msg)
if ok:
    print("Laporan makro berhasil dikirim ke Telegram!")
else:
    print("Gagal kirim ke Telegram.")
