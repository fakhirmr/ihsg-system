"""
IHSG Trading System — Aturan Sinyal (satu sumber kebenaran)
============================================================
Kondisi entry dan rumus level dipakai di TIGA tempat: alert Telegram
(scheduler.run_technical_volume), snapshot web (utils/snapshot.py), dan
uji balik (backtest_honest.py). Kalau ketiganya menyalin logika sendiri-
sendiri, cepat atau lambat angkanya berbeda dan tidak ada yang sadar —
persis cara klaim winrate lama bisa bertahan begitu lama.

Modul ini tidak memanggil jaringan, tidak menulis apa pun, dan tidak tahu
soal Telegram. Masukannya StockData + TechnicalData, keluarannya keputusan.

Angka acuan out-of-sample (Apr 2024 - Sep 2026, sudah dipotong biaya):
  CONSOL BREAKOUT  n=80  profit 46%  ekspektasi -0,60%/trade  PF 0,74
  BUY ON WEAKNESS  n=74  profit 27%  ekspektasi +1,65%/trade  PF 1,85
  Entry acak       n=6508 profit 41% ekspektasi -0,28%/trade  PF 0,88
Jalankan `python backtest_honest.py` untuk memperbaruinya.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

BREAKOUT = "BREAKOUT"
WEAKNESS = "WEAKNESS"
RADAR    = "RADAR"


@dataclass
class Levels:
    """Level yang dipublikasikan ke pengguna."""
    entry: float
    entry2: Optional[float]
    tp1: float
    tp2: float
    sl: float

    @property
    def risk_pct(self) -> float:
        return (self.entry - self.sl) / self.entry * 100 if self.entry else 0.0

    @property
    def reward_pct(self) -> float:
        return (self.tp1 - self.entry) / self.entry * 100 if self.entry else 0.0

    @property
    def rr(self) -> float:
        r = self.risk_pct
        return self.reward_pct / r if r > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry, "entry2": self.entry2,
            "tp1": self.tp1, "tp2": self.tp2, "sl": self.sl,
            "risk_pct": round(self.risk_pct, 2),
            "reward_pct": round(self.reward_pct, 2),
            "rr": round(self.rr, 2),
        }


def compute_levels(entry: float, td: Any) -> Levels:
    """
    Entry / TP / SL dari data teknikal.

    Catatan stop loss: `support_1` adalah minimum 30 close TERMASUK bar ini.
    Saat sinyal muncul di titik terendah 30 hari — persis situasi BUY ON
    WEAKNESS — support_1 sama dengan entry, dan rumus lama
    `max(support_1, entry*0.95)` memasang stop di harga beli. Itu terjadi
    pada 38% sinyal weakness dan menekan winrate-nya ke 9,5%. Karena itu
    di sini diambil yang LEBIH RENDAH dari support, minimal 2% di bawah
    entry, dengan risiko maksimum tetap 5%.
    """
    tp1 = round(td.resistance_1 if td.resistance_1 > entry else entry * 1.04, 0)
    tp2 = round(td.resistance_2 if td.resistance_2 > tp1 else entry * 1.08, 0)

    if td.support_1 > 0:
        sl_raw = min(td.support_1, entry * 0.98)
    else:
        sl_raw = entry * 0.95
    sl = round(max(sl_raw, entry * 0.95), 0)

    entry2 = (
        round(td.support_1, 0)
        if td.support_1 > 0 and td.support_1 < entry * 0.97
        else None
    )
    return Levels(entry=entry, entry2=entry2, tp1=tp1, tp2=tp2, sl=sl)


def is_breakout(sd: Any, td: Any) -> bool:
    """CONSOL BREAKOUT — breakout dari konsolidasi + momentum + volume."""
    return bool(
        td.is_consolidation_breakout
        and td.macd_line > 0
        and td.macd_histogram > 0
        and sd.relative_volume >= 1.5
    )


def is_weakness(sd: Any, td: Any) -> bool:
    """BUY ON WEAKNESS — jatuh beruntun, oversold, MACD mulai berbalik."""
    return bool(
        td.down_days >= 3
        and td.macd_hist_rising
        and td.macd_histogram < 0
        and td.bb_pct < 0.20
        and td.rsi_14 < 40
    )


def radar_reasons(sd: Any, td: Any) -> list[str]:
    """Alasan sebuah saham layak dipantau meski belum memenuhi kondisi BUY."""
    out: list[str] = []

    # Near-breakout: breakout biasa (bukan konsolidasi) + MACD positif
    if td.is_breakout and td.macd_line > 0 and td.macd_histogram > 0 and sd.relative_volume >= 1.2:
        out.append(
            f"breakout resistance + MACD positif, volume {sd.relative_volume:.1f}x (belum kuat)"
        )

    # Consol breakout tapi volume belum konfirmasi
    if td.is_consolidation_breakout and td.macd_line > 0 and 1.1 <= sd.relative_volume < 1.5:
        out.append(
            f"consolidation breakout, tunggu volume konfirmasi ({sd.relative_volume:.1f}x)"
        )

    # Hampir weakness: turun 2 hari + MACD berbalik + RSI rendah
    if td.down_days == 2 and td.macd_hist_rising and td.macd_histogram < 0 and td.rsi_14 < 45:
        out.append(
            f"turun 2 hari, MACD histogram mulai berbalik, RSI {td.rsi_14:.0f}"
        )

    # Weakness hampir masuk: RSI 40-50 atau BB% 20-35%
    if (
        td.down_days >= 3 and td.macd_hist_rising and td.macd_histogram < 0
        and (40 <= td.rsi_14 < 50 or 0.20 <= td.bb_pct < 0.35)
    ):
        out.append(
            f"turun {td.down_days} hari, RSI {td.rsi_14:.0f}, "
            f"BB {td.bb_pct*100:.0f}% — hampir oversold"
        )

    # Momentum membangun di atas EMA20/50
    if (
        td.is_above_ema20 and td.is_above_ema50
        and td.macd_line > 0 and td.macd_hist_rising
        and 45 <= td.rsi_14 <= 62
        and sd.relative_volume >= 1.2
    ):
        out.append(
            f"momentum membangun di atas EMA20/50, RSI {td.rsi_14:.0f}, "
            f"volume {sd.relative_volume:.1f}x"
        )

    return out


def classify(sd: Any, td: Any) -> Optional[dict[str, Any]]:
    """
    Keputusan lengkap untuk satu saham, atau None kalau tidak ada apa-apa.

    Mengembalikan: kind, levels (Levels), reasons, dan metrik pendukung
    yang dipakai baik oleh pesan Telegram maupun kartu di web.
    """
    levels = compute_levels(sd.current_price, td)
    common = {
        "ticker": sd.ticker,
        "price": sd.current_price,
        "change": sd.day_change_pct,
        "rsi": td.rsi_14,
        "vol": sd.relative_volume,
        "levels": levels,
    }

    if is_breakout(sd, td):
        return {**common, "kind": BREAKOUT, "reasons": [
            f"Breakout dari konsolidasi, volume {sd.relative_volume:.1f}x rata-rata",
        ]}

    if is_weakness(sd, td):
        return {**common, "kind": WEAKNESS, "reasons": [
            f"Turun {td.down_days} hari, RSI {td.rsi_14:.0f}, "
            f"BB {td.bb_pct*100:.0f}%, MACD histogram membalik",
        ], "down_days": td.down_days, "bb_pct": td.bb_pct,
            "macd_h": td.macd_histogram}

    reasons = radar_reasons(sd, td)
    if reasons:
        return {**common, "kind": RADAR, "reasons": reasons}

    return None
