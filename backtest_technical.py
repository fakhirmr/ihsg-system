"""
Backtest kondisi teknikal pada DEFAULT_TICKERS (1 tahun data).

Metode:
  - Hitung semua indikator secara vectorized per ticker
  - Test semua kombinasi kondisi BUY
  - Win = harga tutup hari ke-N >= entry * 1.02
  - Laporkan winrate + avg gain per kondisi + hold period
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict
from datetime import datetime, timedelta
from config import DEFAULT_TICKERS

# ── Config ────────────────────────────────────────────────────────────────────
HOLD_DAYS     = [3, 5, 10]
WIN_PCT       = 0.02          # 2% = win
MIN_SIGNALS   = 10            # abaikan kondisi dengan sinyal < ini
LOOKBACK_DAYS = 400           # ~1.5 tahun data
MIN_BARS      = 60            # minimum candle sebelum mulai sinyal

# ── Download ──────────────────────────────────────────────────────────────────
end   = datetime.today()
start = end - timedelta(days=LOOKBACK_DAYS + 30)

print(f"=== IHSG Technical Backtest — {datetime.today().strftime('%Y-%m-%d')} ===")
print(f"Tickers  : {len(DEFAULT_TICKERS)}")
print(f"Period   : {start.strftime('%Y-%m-%d')} s/d {end.strftime('%Y-%m-%d')}")
print(f"Hold     : {HOLD_DAYS} hari")
print(f"Win      : close hari-N >= entry + {WIN_PCT*100:.0f}%\n")
print("Downloading data...", flush=True)

all_data: dict[str, pd.DataFrame] = {}
failed = []
for ticker in DEFAULT_TICKERS:
    try:
        df = yf.download(ticker, start=start, end=end,
                         auto_adjust=True, progress=False)
        # yfinance 1.3+ returns MultiIndex columns — flatten
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        if len(df) >= MIN_BARS:
            all_data[ticker] = df
        else:
            failed.append(ticker)
    except Exception:
        failed.append(ticker)

print(f"Berhasil : {len(all_data)} tickers")
if failed:
    print(f"Gagal    : {', '.join(failed)}")
print()

# ── Vectorized indicator calculation ─────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c  = df["Close"].squeeze()
    v  = df["Volume"].squeeze()

    # EMA
    ema20  = c.ewm(span=20,  adjust=False).mean()
    ema50  = c.ewm(span=50,  adjust=False).mean()

    # MACD
    ema12  = c.ewm(span=12, adjust=False).mean()
    ema26  = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_s = macd_l.ewm(span=9, adjust=False).mean()
    macd_h = macd_l - macd_s

    # RSI 14
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))

    # Trend
    sma10  = c.rolling(10).mean()
    sma20  = c.rolling(20).mean()
    uptrend   = (c > ema20) & (ema20 > ema50) & (sma10 > sma20)
    downtrend = (c < ema20) & (ema20 < ema50) & (sma10 < sma20)

    # Breakout / Breakdown (vs prior 20-bar high/low)
    prior_high = c.shift(1).rolling(20).max()
    prior_low  = c.shift(1).rolling(20).min()
    breakout   = c > prior_high * 1.01
    breakdown  = c < prior_low  * 0.99

    # Bollinger
    sma20b = c.rolling(20).mean()
    std20  = c.rolling(20).std()
    bb_lower = sma20b - 2 * std20
    near_bb_lower = c <= bb_lower * 1.005   # harga menyentuh/dekat lower band

    # Higher High (3 candle terakhir naik berurutan)
    higher_high = (c > c.shift(1)) & (c.shift(1) > c.shift(2))

    # Relative volume
    avg_vol  = v.rolling(20).mean()
    rel_vol  = v / avg_vol.replace(0, np.nan)

    # Above flags
    above_ema20 = c > ema20
    above_ema50 = c > ema50

    ind = pd.DataFrame({
        "close":       c,
        "ema20":       ema20,
        "ema50":       ema50,
        "macd_h":      macd_h,
        "rsi":         rsi,
        "rel_vol":     rel_vol,
        "uptrend":     uptrend,
        "downtrend":   downtrend,
        "breakout":    breakout,
        "breakdown":   breakdown,
        "above_ema20": above_ema20,
        "above_ema50": above_ema50,
        "higher_high": higher_high,
        "near_bb_lower": near_bb_lower,
        "bb_lower":    bb_lower,
    }, index=df.index)

    return ind.dropna(subset=["rsi", "macd_h", "rel_vol"])


# ── Condition definitions ─────────────────────────────────────────────────────
# Each value: vectorized expression evaluated on indicator DataFrame row
# Returns boolean Series

def define_conditions(ind: pd.DataFrame) -> dict[str, pd.Series]:
    c   = ind
    return {
        # ── Single indicators ──────────────────────────────────────────────
        "UPTREND":              c.uptrend,
        "ABOVE_EMA20":          c.above_ema20,
        "ABOVE_EMA50":          c.above_ema50,
        "MACD_POS":             c.macd_h > 0,
        "RSI_35_65":            c.rsi.between(35, 65),
        "RSI_40_70":            c.rsi.between(40, 70),
        "RSI_40_60":            c.rsi.between(40, 60),
        "VOL_1.2x":             c.rel_vol >= 1.2,
        "VOL_1.5x":             c.rel_vol >= 1.5,
        "VOL_2x":               c.rel_vol >= 2.0,
        "BREAKOUT":             c.breakout,
        "HIGHER_HIGH":          c.higher_high,
        "NEAR_BB_LOWER":        c.near_bb_lower,

        # ── 2-condition ────────────────────────────────────────────────────
        "UP+MACD":              c.uptrend & (c.macd_h > 0),
        "UP+EMA20":             c.uptrend & c.above_ema20,
        "UP+RSI":               c.uptrend & c.rsi.between(35, 65),
        "UP+VOL":               c.uptrend & (c.rel_vol >= 1.2),
        "MACD+RSI":             (c.macd_h > 0) & c.rsi.between(35, 65),
        "MACD+VOL":             (c.macd_h > 0) & (c.rel_vol >= 1.2),
        "EMA20+MACD":           c.above_ema20 & (c.macd_h > 0),
        "BREAKOUT+VOL1.5":      c.breakout & (c.rel_vol >= 1.5),
        "BREAKOUT+MACD":        c.breakout & (c.macd_h > 0),
        "BREAKOUT+RSI":         c.breakout & c.rsi.between(35, 70),

        # ── 3-condition ────────────────────────────────────────────────────
        "UP+MACD+RSI":          c.uptrend & (c.macd_h > 0) & c.rsi.between(35, 65),
        "UP+MACD+VOL":          c.uptrend & (c.macd_h > 0) & (c.rel_vol >= 1.2),
        "UP+MACD+EMA20":        c.uptrend & (c.macd_h > 0) & c.above_ema20,
        "UP+EMA20+RSI":         c.uptrend & c.above_ema20 & c.rsi.between(35, 65),
        "UP+RSI+VOL":           c.uptrend & c.rsi.between(35, 65) & (c.rel_vol >= 1.2),
        "EMA20+MACD+RSI":       c.above_ema20 & (c.macd_h > 0) & c.rsi.between(35, 65),
        "EMA20+MACD+VOL":       c.above_ema20 & (c.macd_h > 0) & (c.rel_vol >= 1.2),
        "BREAKOUT+MACD+VOL":    c.breakout & (c.macd_h > 0) & (c.rel_vol >= 1.5),
        "BREAKOUT+MACD+RSI":    c.breakout & (c.macd_h > 0) & c.rsi.between(35, 70),
        "BREAKOUT+VOL+RSI":     c.breakout & (c.rel_vol >= 1.5) & c.rsi.between(35, 70),

        # ── 4-condition ────────────────────────────────────────────────────
        "UP+MACD+RSI+VOL":      c.uptrend & (c.macd_h > 0) & c.rsi.between(35, 65) & (c.rel_vol >= 1.2),
        "UP+EMA20+MACD+RSI":    c.uptrend & c.above_ema20 & (c.macd_h > 0) & c.rsi.between(35, 65),
        "UP+EMA20+MACD+VOL":    c.uptrend & c.above_ema20 & (c.macd_h > 0) & (c.rel_vol >= 1.2),
        "UP+MACD+RSI40_60+VOL": c.uptrend & (c.macd_h > 0) & c.rsi.between(40, 60) & (c.rel_vol >= 1.2),
        "BRK+MACD+VOL+RSI":     c.breakout & (c.macd_h > 0) & (c.rel_vol >= 1.5) & c.rsi.between(35, 70),
        "EMA20+EMA50+MACD+RSI": c.above_ema20 & c.above_ema50 & (c.macd_h > 0) & c.rsi.between(35, 65),

        # ── 5-condition (strict) ───────────────────────────────────────────
        "STRICT_BUY":           (
            c.uptrend & c.above_ema20 & (c.macd_h > 0)
            & c.rsi.between(35, 65) & (c.rel_vol >= 1.2)
        ),
        "STRICT_BUY_TIGHT":     (
            c.uptrend & c.above_ema20 & (c.macd_h > 0)
            & c.rsi.between(40, 60) & (c.rel_vol >= 1.5)
        ),
        "STRICT_BREAKOUT":      (
            c.breakout & c.above_ema20 & (c.macd_h > 0)
            & c.rsi.between(35, 70) & (c.rel_vol >= 1.5)
        ),
        "STRICT_BRK_TIGHT":     (
            c.breakout & c.above_ema20 & (c.macd_h > 0)
            & c.rsi.between(40, 65) & (c.rel_vol >= 2.0)
        ),
    }


# ── Run backtest ──────────────────────────────────────────────────────────────
# results[(cond_name, hold_days)] = list of pnl floats
from collections import defaultdict
results: dict[tuple, list] = defaultdict(list)

for ticker, df in all_data.items():
    try:
        ind = compute_indicators(df)
    except Exception as e:
        print(f"  Error {ticker}: {e}")
        continue

    closes = ind["close"]
    conds  = define_conditions(ind)

    # Only evaluate after MIN_BARS
    valid_idx = ind.index[MIN_BARS:]

    for date in valid_idx:
        loc = closes.index.get_loc(date)
        entry = float(closes.iloc[loc])
        if entry <= 0:
            continue

        # Future close prices
        future_closes = {}
        for h in HOLD_DAYS:
            future_loc = loc + h
            if future_loc < len(closes):
                future_closes[h] = float(closes.iloc[future_loc])
            else:
                future_closes[h] = None

        for cname, cseries in conds.items():
            try:
                signal = bool(cseries.loc[date])
            except Exception:
                continue

            if signal:
                for h in HOLD_DAYS:
                    fc = future_closes.get(h)
                    if fc is not None:
                        pnl = (fc - entry) / entry
                        results[(cname, h)].append(pnl)

# ── Report ────────────────────────────────────────────────────────────────────
print(f"\n{'KONDISI':<30} {'HOLD':>5} {'N':>6} {'WINRATE':>8} {'AVG%':>8} {'MED%':>8} {'MAX%':>8}")
print("-" * 80)

summary = []
for (cname, h), pnl_list in results.items():
    if len(pnl_list) < MIN_SIGNALS:
        continue
    arr = np.array(pnl_list)
    wins     = np.sum(arr >= WIN_PCT)
    win_rate = wins / len(arr)
    avg_g    = np.mean(arr)
    med_g    = np.median(arr)
    max_g    = np.max(arr)
    summary.append((cname, h, len(arr), win_rate, avg_g, med_g, max_g))

summary.sort(key=lambda x: (-x[3], x[1]))

for cname, h, n, wr, ag, mg, mx in summary:
    print(f"{cname:<30} {h:>4}d {n:>6} {wr*100:>7.1f}% {ag*100:>+7.1f}% {mg*100:>+7.1f}% {mx*100:>+7.1f}%")

# ── Top 10 per hold period ─────────────────────────────────────────────────────
print("\n" + "="*80)
for h in HOLD_DAYS:
    sub = [(c, n, w, a, m) for (c, hh, n, w, a, m, _) in summary if hh == h]
    sub.sort(key=lambda x: -x[2])
    print(f"\n TOP 10  |  Hold {h} hari  |  Win = close >= entry +{WIN_PCT*100:.0f}%")
    print(f"  {'KONDISI':<32} {'N':>6} {'WINRATE':>8} {'AVG GAIN':>9}")
    print("  " + "-"*60)
    for cname, n, wr, ag, _ in sub[:10]:
        bar = "#" * int(wr * 20)
        print(f"  {cname:<32} {n:>6} {wr*100:>7.1f}%  {ag*100:>+7.1f}%  {bar}")

print("\n=== Selesai ===")
