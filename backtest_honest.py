"""
Backtest Jujur — hanya dua aturan yang benar-benar dikirim ke Telegram
======================================================================
Dibuat untuk menggantikan angka winrate dari backtest_*.py yang lama.

Yang berbeda dari skrip backtest sebelumnya:

1. TIDAK ada grid search. Skrip lama menguji ~398 kondisi x 3 hold period
   (~1.200 kombinasi) lalu mengambil yang winrate-nya tertinggi dengan
   MIN_SIGNALS = 8. Pada n sekecil itu, winrate 75% muncul dengan mudah
   hanya karena kebetulan. Di sini hanya 2 aturan produksi yang diuji.

2. Exit-nya SAMA dengan yang dipublikasikan ke pengguna: TP1 / SL yang
   tercetak di pesan alert, disimulasikan bar per bar pakai High/Low.
   Skrip lama memakai "close hari ke-N >= entry + 2%" tanpa stop loss —
   aturan yang tidak pernah dikirim ke siapa pun.

3. Ada biaya transaksi (beli 0,15% + jual 0,25%, tarif broker ritel IDX).

4. Ada pembagian in-sample / out-of-sample. Aturan ini dipilih dari data
   sekitar 2025-2026, jadi periode itu TIDAK bisa dipakai menilainya.

5. Ada baseline: hasil membeli di SEMUA bar dengan exit yang sama persis.
   Winrate sebuah sinyal tidak berarti apa-apa tanpa tahu berapa winrate
   entry acak pada periode dan instrumen yang sama.

6. Entry di OPEN bar berikutnya, bukan close bar sinyal — sinyal baru
   diketahui setelah bar-nya terbentuk.

Catatan survivorship: universe memakai DEFAULT_TICKERS hari ini. Saham
yang delisting/suspend tidak terwakili, jadi hasil di bawah ini masih
sedikit optimistis.

Usage:
    python backtest_honest.py
    python backtest_honest.py --years 8 --hold 10
"""
from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import dataclass

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

from config import DEFAULT_TICKERS

# ── Biaya transaksi broker ritel IDX ──────────────────────────────────────────
FEE_BUY  = 0.0015   # 0,15%
FEE_SELL = 0.0025   # 0,25% (sudah termasuk PPh final 0,1%)


# ── Indikator (vektorisasi, harus identik dengan utils/technical_calculator) ──

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c  = df["Close"].astype(float)
    h  = df["High"].astype(float)
    lo = df["Low"].astype(float)
    v  = df["Volume"].astype(float)

    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    # RSI versi SMA (bukan Wilder) — sama seperti technical_calculator._rsi
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    macd_line = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_sig  = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_sig
    macd_hist_rising = macd_hist > macd_hist.shift(1)

    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    bb_up, bb_lo = sma20 + 2 * std20, sma20 - 2 * std20
    bb_pct = (c - bb_lo) / (bb_up - bb_lo).replace(0, np.nan)

    # Support/resistance: min & max 30 close terakhir (termasuk bar ini)
    s1 = c.rolling(30).min()
    r1 = c.rolling(30).max()
    mid = (r1 + s1) / 2
    r2 = r1 + (r1 - mid).abs() * 0.618

    # Breakout: close > max 20 close SEBELUM bar ini, +1%
    prior_max = c.rolling(20).max().shift(1)
    is_breakout = c > prior_max * 1.01

    # Consolidation breakout: breakout + ATR5 bar sebelumnya < 70% rata-rata
    tr = pd.concat(
        [h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    atr5      = tr.rolling(5).mean()
    atr5_mean = atr5.rolling(20).mean()
    is_consol_breakout = is_breakout & (atr5.shift(1) < atr5_mean.shift(1) * 0.7)

    # Hari turun beruntun
    down = (c < c.shift(1)).astype(int)
    grp  = (down == 0).cumsum()
    down_days = down.groupby(grp).cumsum()

    # Relative volume — pembanding tidak menyertakan bar berjalan
    rel_vol = v / v.shift(1).rolling(20).mean()

    return pd.DataFrame({
        "open": df["Open"].astype(float), "high": h, "low": lo, "close": c,
        "ema20": ema20, "ema50": ema50, "rsi": rsi,
        "macd_line": macd_line, "macd_hist": macd_hist,
        "macd_hist_rising": macd_hist_rising,
        "bb_pct": bb_pct, "s1": s1, "r1": r1, "r2": r2,
        "is_breakout": is_breakout, "is_consol_breakout": is_consol_breakout,
        "down_days": down_days, "rel_vol": rel_vol,
    }, index=df.index)


# ── Dua aturan produksi (disalin persis dari scheduler.run_technical_volume) ──

def signal_breakout(i: pd.DataFrame) -> pd.Series:
    return (
        i.is_consol_breakout
        & (i.macd_line > 0)
        & (i.macd_hist > 0)
        & (i.rel_vol >= 1.5)
    ).fillna(False)


def signal_weakness(i: pd.DataFrame) -> pd.Series:
    return (
        (i.down_days >= 3)
        & i.macd_hist_rising
        & (i.macd_hist < 0)
        & (i.bb_pct < 0.20)
        & (i.rsi < 40)
    ).fillna(False)


# ── Level TP/SL ───────────────────────────────────────────────────────────────

def levels_current(entry: float, s1: float, r1: float, r2: float):
    """Rumus yang dipakai produksi saat ini."""
    tp1 = r1 if r1 > entry else entry * 1.04
    tp2 = r2 if r2 > tp1 else entry * 1.08
    sl  = max(s1, entry * 0.95) if s1 > 0 else entry * 0.95
    return tp1, tp2, sl


def levels_fixed(entry: float, s1: float, r1: float, r2: float):
    """
    Rumus usulan. Masalah versi lama: s1 adalah minimum 30 close TERMASUK
    bar ini, jadi saat sinyal muncul di titik terendah 30 hari — persis
    situasi BUY ON WEAKNESS — s1 == entry dan max(s1, entry*0.95) == entry,
    artinya stop loss dipasang di harga beli.
    """
    tp1 = r1 if r1 > entry else entry * 1.04
    tp2 = r2 if r2 > tp1 else entry * 1.08
    sl  = min(s1, entry * 0.98) if s1 > 0 else entry * 0.95
    sl  = max(sl, entry * 0.95)          # risiko maksimum tetap 5%
    return tp1, tp2, sl


# ── Simulasi satu posisi ──────────────────────────────────────────────────────

@dataclass
class Trade:
    ret: float          # return bersih setelah biaya
    bars: int
    exit_reason: str


def simulate(
    ind: pd.DataFrame, loc: int, hold: int, use_fixed_sl: bool
) -> Trade | None:
    """
    Entry di OPEN bar berikutnya setelah sinyal.
    Tiap bar dicek SL dulu baru TP — asumsi paling tidak menguntungkan
    ketika keduanya tersentuh di bar yang sama.
    """
    if loc + 1 >= len(ind):
        return None

    row   = ind.iloc[loc]
    entry = float(ind["open"].iloc[loc + 1])
    if not np.isfinite(entry) or entry <= 0:
        return None

    fn = levels_fixed if use_fixed_sl else levels_current
    tp1, _tp2, sl = fn(entry, float(row.s1), float(row.r1), float(row.r2))

    exit_price, exit_reason, bars = None, "timeout", 0
    for k in range(1, hold + 1):
        j = loc + k
        if j >= len(ind):
            break
        bars = k
        o, hi, lo, cl = (float(ind["open"].iloc[j]), float(ind["high"].iloc[j]),
                         float(ind["low"].iloc[j]),  float(ind["close"].iloc[j]))

        if k == 1:
            hi, lo = max(hi, entry), min(lo, entry)   # bar entry dimulai di open

        if o <= sl:                    # gap turun menembus stop
            exit_price, exit_reason = o, "SL (gap)"
            break
        if lo <= sl:
            exit_price, exit_reason = sl, "SL"
            break
        if hi >= tp1:
            exit_price, exit_reason = tp1, "TP1"
            break
        exit_price = cl

    if exit_price is None:
        return None

    gross = exit_price / entry
    net   = gross * (1 - FEE_SELL) / (1 + FEE_BUY) - 1
    return Trade(ret=net, bars=bars, exit_reason=exit_reason)


# ── Statistik ─────────────────────────────────────────────────────────────────

def summarize(trades: list[Trade], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    r = np.array([t.ret for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    gross_win  = wins.sum()
    gross_loss = -losses.sum()
    return {
        "label": label,
        "n": len(r),
        "winrate": float((r > 0).mean()),
        "avg": float(r.mean()),
        "median": float(np.median(r)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "pf": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": float(r.mean()),
        "avg_bars": float(np.mean([t.bars for t in trades])),
    }


def print_table(rows: list[dict], title: str) -> None:
    print(f"\n{title}")
    print(f"  {'STRATEGI':<26} {'N':>5} {'WINRATE':>8} {'EKSPEKTASI':>11} "
          f"{'AVG WIN':>8} {'AVG LOSS':>9} {'PF':>6} {'BAR':>5}")
    print("  " + "-" * 84)
    for s in rows:
        if not s["n"]:
            print(f"  {s['label']:<26} {'0':>5}   (tidak ada sinyal)")
            continue
        pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        print(f"  {s['label']:<26} {s['n']:>5} {s['winrate']*100:>7.1f}% "
              f"{s['expectancy']*100:>+10.2f}% {s['avg_win']*100:>+7.2f}% "
              f"{s['avg_loss']*100:>+8.2f}% {pf:>6} {s['avg_bars']:>5.1f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest jujur aturan produksi")
    ap.add_argument("--years", type=int, default=6, help="panjang riwayat (tahun)")
    ap.add_argument("--hold", type=int, default=10, help="maksimum bar ditahan")
    ap.add_argument("--split", type=float, default=0.6,
                    help="porsi awal sebagai in-sample")
    ap.add_argument("--baseline-step", type=int, default=5,
                    help="ambil 1 dari tiap N bar untuk baseline")
    args = ap.parse_args()

    print("=" * 88)
    print(" BACKTEST JUJUR — aturan CONSOL BREAKOUT & BUY ON WEAKNESS")
    print("=" * 88)
    print(f" Universe   : {len(DEFAULT_TICKERS)} saham (DEFAULT_TICKERS)")
    print(f" Riwayat    : {args.years} tahun")
    print(f" Exit       : TP1 / SL sesuai pesan alert, maks {args.hold} bar")
    print(f" Biaya      : beli {FEE_BUY*100:.2f}% + jual {FEE_SELL*100:.2f}%")
    print(f" Entry      : open bar berikutnya setelah sinyal")
    print("=" * 88)

    print("\nMengunduh data...", flush=True)
    data: dict[str, pd.DataFrame] = {}
    for t in DEFAULT_TICKERS:
        try:
            df = yf.download(t, period=f"{args.years}y", auto_adjust=True,
                             progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            if len(df) >= 200:
                data[t] = df
        except Exception:
            pass
    print(f"Berhasil: {len(data)} / {len(DEFAULT_TICKERS)} saham")

    if not data:
        print("Tidak ada data. Berhenti.")
        return

    all_dates = sorted({d for df in data.values() for d in df.index})
    split_date = all_dates[int(len(all_dates) * args.split)]
    print(f"Rentang    : {all_dates[0].date()} s/d {all_dates[-1].date()}")
    print(f"Batas split: {split_date.date()} "
          f"(sebelum = in-sample, sesudah = out-of-sample)")

    buckets: dict[tuple[str, str, bool], list[Trade]] = {}
    sl_at_entry = {"breakout": 0, "weakness": 0}
    sig_count   = {"breakout": 0, "weakness": 0}

    for ticker, df in data.items():
        ind = compute_indicators(df)
        sigs = {"breakout": signal_breakout(ind), "weakness": signal_weakness(ind)}

        for name, series in sigs.items():
            for loc in np.flatnonzero(series.to_numpy()):
                loc = int(loc)
                if loc < 60:
                    continue
                sig_count[name] += 1

                # seberapa sering SL versi lama jatuh di harga entry
                row = ind.iloc[loc]
                e_next = ind["open"].iloc[loc + 1] if loc + 1 < len(ind) else np.nan
                if np.isfinite(e_next):
                    _, _, sl_old = levels_current(
                        float(e_next), float(row.s1), float(row.r1), float(row.r2))
                    if sl_old >= float(e_next) * 0.999:
                        sl_at_entry[name] += 1

                period = "IS" if ind.index[loc] < split_date else "OOS"
                for fixed in (False, True):
                    tr = simulate(ind, loc, args.hold, fixed)
                    if tr:
                        buckets.setdefault((name, period, fixed), []).append(tr)

        # Baseline: entry di sembarang bar, exit persis sama
        for loc in range(60, len(ind) - 1, args.baseline_step):
            period = "IS" if ind.index[loc] < split_date else "OOS"
            tr = simulate(ind, loc, args.hold, True)
            if tr:
                buckets.setdefault(("baseline", period, True), []).append(tr)

    label = {"breakout": "CONSOL BREAKOUT", "weakness": "BUY ON WEAKNESS",
             "baseline": "BASELINE (entry acak)"}

    for period, judul in [("IS", "IN-SAMPLE (periode asal aturan dipilih)"),
                          ("OOS", "OUT-OF-SAMPLE (uji sebenarnya)")]:
        rows = []
        for name in ("breakout", "weakness"):
            for fixed in (False, True):
                tag = "SL diperbaiki" if fixed else "SL sekarang"
                rows.append(summarize(
                    buckets.get((name, period, fixed), []),
                    f"{label[name]} [{tag}]"))
        rows.append(summarize(buckets.get(("baseline", period, True), []),
                              label["baseline"]))
        print_table(rows, judul)

    print("\n" + "=" * 88)
    print(" DIAGNOSIS RUMUS STOP LOSS")
    print("=" * 88)
    for name in ("breakout", "weakness"):
        n, bad = sig_count[name], sl_at_entry[name]
        pct = bad / n * 100 if n else 0
        print(f"  {label[name]:<22} {bad:>4} dari {n:>4} sinyal "
              f"({pct:>5.1f}%) memasang SL di harga entry")
    print("\n  Penyebab: support_1 = minimum 30 close TERMASUK bar sinyal, dan")
    print("  max(support_1, entry*0.95) memilih support_1 saat ia >= entry*0.95.")
    print("  Perbaikan: min(support_1, entry*0.98), lalu dibatasi entry*0.95.")
    print("\n=== Selesai ===")


if __name__ == "__main__":
    main()
