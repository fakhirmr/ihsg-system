"""
Backtest: Buy on Weakness / Mean Reversion
Cari kondisi saat saham sedang lemah/oversold tapi berpotensi bounce.
Win = harga tutup hari ke-N >= entry + 2%
"""
from __future__ import annotations
import sys, os, warnings
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict
from datetime import datetime, timedelta
from config import DEFAULT_TICKERS

HOLD_DAYS   = [3, 5, 10]
WIN_PCT     = 0.02
MIN_SIGNALS = 8
LOOKBACK    = 400

end   = datetime.today()
start = end - timedelta(days=LOOKBACK + 30)

print("=== Backtest: Buy on Weakness / Mean Reversion ===")
print(f"Tickers : {len(DEFAULT_TICKERS)}")
print(f"Period  : {start.strftime('%Y-%m-%d')} s/d {end.strftime('%Y-%m-%d')}")
print(f"Win     : close hari-N >= entry + {WIN_PCT*100:.0f}%\n")
print("Downloading...", flush=True)

all_data: dict[str, pd.DataFrame] = {}
for ticker in DEFAULT_TICKERS:
    try:
        df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        if len(df) >= 60:
            all_data[ticker] = df
    except Exception:
        pass

print(f"Berhasil: {len(all_data)} tickers\n")


def compute(df: pd.DataFrame) -> pd.DataFrame:
    c  = df["Close"].squeeze()
    v  = df["Volume"].squeeze()
    h  = df["High"].squeeze()
    lo = df["Low"].squeeze()

    # EMA
    ema20  = c.ewm(span=20,  adjust=False).mean()
    ema50  = c.ewm(span=50,  adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    # RSI 14
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    # Stochastic %K %D (14,3)
    low14  = lo.rolling(14).min()
    high14 = h.rolling(14).max()
    stoch_k = 100 * (c - low14) / (high14 - low14).replace(0, np.nan)
    stoch_d = stoch_k.rolling(3).mean()

    # MACD
    ema12  = c.ewm(span=12, adjust=False).mean()
    ema26  = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_s = macd_l.ewm(span=9, adjust=False).mean()
    macd_h = macd_l - macd_s
    macd_hist_rising = macd_h > macd_h.shift(1)  # histogram mulai naik (momentum membaik)

    # Bollinger Bands
    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    bb_lo = sma20 - 2 * std20
    bb_up = sma20 + 2 * std20
    bb_pct = (c - bb_lo) / (bb_up - bb_lo).replace(0, np.nan)  # 0=lower, 1=upper

    # Williams %R (14)
    willr = -100 * (high14 - c) / (high14 - low14).replace(0, np.nan)

    # CCI (20)
    tp       = (h + lo + c) / 3
    sma_tp   = tp.rolling(20).mean()
    mad      = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci      = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))

    # ATR & TR
    tr   = pd.concat([(h - lo), (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()

    # OBV
    price_up = c > c.shift(1)
    price_dn = c < c.shift(1)
    obv      = (v.where(price_up, 0) - v.where(price_dn, 0)).cumsum()
    obv_slope = obv - obv.shift(3)   # OBV naik 3 bar terakhir = accumulation

    # ADX & DI
    up_m  = h - h.shift()
    dn_m  = lo.shift() - lo
    pdm   = ((up_m > dn_m) & (up_m > 0)) * up_m
    ndm   = ((dn_m > up_m) & (dn_m > 0)) * dn_m
    pdi   = 100 * pdm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    ndi   = 100 * ndm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    bull_di = pdi > ndi

    # Volume
    avg_vol = v.rolling(20).mean()
    rel_vol = v / avg_vol.replace(0, np.nan)
    vol_spike = rel_vol >= 2.0   # volume spike saat turun = capitulation

    # Price vs EMA
    pct_below_ema20  = (ema20 - c) / ema20  # positif = harga di bawah EMA20
    pct_below_ema50  = (ema50 - c) / ema50
    oversold_ema20   = pct_below_ema20 > 0.03   # lebih dari 3% di bawah EMA20
    oversold_ema20_5 = pct_below_ema20 > 0.05   # lebih dari 5%
    oversold_ema50   = pct_below_ema50 > 0.05

    # Price position
    low30  = c.rolling(30).min()
    high30 = c.rolling(30).max()
    near_low30  = c < low30 * 1.03    # dalam 3% dari low 30 hari
    near_high30 = c > high30 * 0.97

    # Consecutive down days
    down1 = c < c.shift(1)
    down2 = down1 & (c.shift(1) < c.shift(2))
    down3 = down2 & (c.shift(2) < c.shift(3))
    down4 = down3 & (c.shift(3) < c.shift(4))
    down5 = down4 & (c.shift(4) < c.shift(5))

    # Reversal candle: today close > today open (green candle after down days)
    green_candle = c > df["Open"].squeeze()
    # Hammer: lower wick > 2x body, small upper wick
    body    = (c - df["Open"].squeeze()).abs()
    low_wick = df["Open"].squeeze().combine(c, min) - lo
    up_wick  = h - df["Open"].squeeze().combine(c, max)
    hammer   = (low_wick > 2 * body.replace(0, np.nan)) & (up_wick < body) & green_candle

    # Downtrend
    sma10    = c.rolling(10).mean()
    downtrend = (c < ema20) & (ema20 < ema50) & (sma10 < c.rolling(20).mean())

    # RSI turning up: RSI meningkat dari area oversold
    rsi_rising     = rsi > rsi.shift(1)
    rsi_was_os     = rsi.shift(1) < 35    # RSI kemarin oversold
    rsi_recovering = rsi_was_os & rsi_rising & (rsi < 50)  # mulai naik tapi belum overbought

    # MACD di bawah 0 tapi histogram mulai naik (divergence)
    macd_negative_rising = (macd_h < 0) & macd_hist_rising

    # Price bouncing from BB lower
    touched_bb_lo = c.shift(1) <= bb_lo.shift(1) * 1.005
    bounce_bb_lo  = touched_bb_lo & (c > c.shift(1))

    # Capitulation: big down day + volume spike, lalu reversal
    big_down      = (c - c.shift(1)) / c.shift(1) < -0.03   # turun > 3% kemarin
    capit_reversal = big_down.shift(1) & vol_spike.shift(1) & green_candle

    # Below all EMAs
    below_all_ema = (~(c > ema20)) & (~(c > ema50)) & (~(c > ema200))

    # Price % change recent (momentum negatif = weakness)
    mom3_neg = (c / c.shift(3) - 1) < -0.02    # turun > 2% dalam 3 hari
    mom5_neg = (c / c.shift(5) - 1) < -0.03    # turun > 3% dalam 5 hari

    return pd.DataFrame({
        "close":               c,
        # RSI conditions
        "rsi":                 rsi,
        "rsi_lt30":            rsi < 30,
        "rsi_lt35":            rsi < 35,
        "rsi_lt40":            rsi < 40,
        "rsi_30_45":           rsi.between(30, 45),
        "rsi_rising":          rsi_rising,
        "rsi_recovering":      rsi_recovering,
        "rsi_was_os":          rsi_was_os,
        # Stochastic
        "stoch_os":            stoch_k < 20,
        "stoch_d_os":          stoch_d < 20,
        "stoch_cross_up":      (stoch_k > stoch_d) & (stoch_k.shift(1) <= stoch_d.shift(1)),
        # MACD
        "macd_neg_rising":     macd_negative_rising,
        "macd_hist_rising":    macd_hist_rising,
        # Bollinger
        "near_bb_lo":          bb_pct < 0.15,    # di bawah 15% dari BB lower
        "touch_bb_lo":         bb_pct < 0.05,    # sangat dekat lower band
        "bounce_bb_lo":        bounce_bb_lo,
        # Williams %R
        "willr_os":            willr < -80,
        "willr_lt70":          willr < -70,
        # CCI
        "cci_os":              cci < -100,
        "cci_lt150":           cci < -150,
        # Volume
        "vol_spike":           vol_spike,
        "vol_high":            rel_vol >= 1.5,
        # EMA weakness
        "below_ema20":         c < ema20,
        "below_ema50":         c < ema50,
        "below_ema200":        c < ema200,
        "below_all_ema":       below_all_ema,
        "oversold_ema20":      oversold_ema20,
        "oversold_ema20_5pct": oversold_ema20_5,
        "oversold_ema50":      oversold_ema50,
        # Price levels
        "near_low30":          near_low30,
        "downtrend":           downtrend,
        # Consecutive down
        "down2":               down2,
        "down3":               down3,
        "down4":               down4,
        "down5":               down5,
        # Reversal
        "green_candle":        green_candle,
        "hammer":              hammer,
        "capit_reversal":      capit_reversal,
        "bull_di":             bull_di,
        "obv_rising":          obv_slope > 0,
        "mom3_neg":            mom3_neg,
        "mom5_neg":            mom5_neg,
    }, index=df.index).dropna(subset=["rsi", "stoch_os", "near_bb_lo", "cci_os"])


def conditions(i: pd.DataFrame) -> dict[str, pd.Series]:
    c = i
    return {
        # ── Single oversold ───────────────────────────────────────────────
        "RSI<30":               c.rsi_lt30,
        "RSI<35":               c.rsi_lt35,
        "RSI<40":               c.rsi_lt40,
        "STOCH_OS":             c.stoch_os,
        "WILLR_OS":             c.willr_os,
        "CCI_OS":               c.cci_os,
        "NEAR_BB_LO":           c.near_bb_lo,
        "TOUCH_BB_LO":          c.touch_bb_lo,
        "NEAR_LOW30":           c.near_low30,
        "DOWN3":                c.down3,
        "DOWN4":                c.down4,
        "DOWN5":                c.down5,

        # ── RSI oversold + recovery ───────────────────────────────────────
        "RSI<30+RISING":        c.rsi_lt30 & c.rsi_rising,
        "RSI<35+RISING":        c.rsi_lt35 & c.rsi_rising,
        "RSI_RECOVERING":       c.rsi_recovering,
        "RSI<30+GREEN":         c.rsi_lt30 & c.green_candle,
        "RSI<35+GREEN":         c.rsi_lt35 & c.green_candle,

        # ── RSI + konfirmasi lain ─────────────────────────────────────────
        "RSI<30+BB_LO":         c.rsi_lt30 & c.near_bb_lo,
        "RSI<35+BB_LO":         c.rsi_lt35 & c.near_bb_lo,
        "RSI<30+STOCH":         c.rsi_lt30 & c.stoch_os,
        "RSI<35+STOCH":         c.rsi_lt35 & c.stoch_os,
        "RSI<30+WILLR":         c.rsi_lt30 & c.willr_os,
        "RSI<35+WILLR":         c.rsi_lt35 & c.willr_os,
        "RSI<30+CCI":           c.rsi_lt30 & c.cci_os,
        "RSI<35+CCI":           c.rsi_lt35 & c.cci_os,
        "RSI<30+VOL":           c.rsi_lt30 & c.vol_spike,
        "RSI<35+VOL":           c.rsi_lt35 & c.vol_spike,

        # ── RSI + MACD membaik ────────────────────────────────────────────
        "RSI<30+MACD_TURN":     c.rsi_lt30 & c.macd_neg_rising,
        "RSI<35+MACD_TURN":     c.rsi_lt35 & c.macd_neg_rising,
        "RSI<40+MACD_TURN":     c.rsi_lt40 & c.macd_neg_rising,
        "RSI<30+MACD+GREEN":    c.rsi_lt30 & c.macd_neg_rising & c.green_candle,
        "RSI<35+MACD+GREEN":    c.rsi_lt35 & c.macd_neg_rising & c.green_candle,

        # ── Triple oversold ───────────────────────────────────────────────
        "RSI+STOCH+WILLR":      c.rsi_lt35 & c.stoch_os & c.willr_os,
        "RSI+STOCH+BB":         c.rsi_lt35 & c.stoch_os & c.near_bb_lo,
        "RSI+BB+WILLR":         c.rsi_lt35 & c.near_bb_lo & c.willr_os,
        "RSI+BB+CCI":           c.rsi_lt35 & c.near_bb_lo & c.cci_os,
        "RSI+STOCH+CCI":        c.rsi_lt35 & c.stoch_os & c.cci_os,

        # ── Bounce dari level ─────────────────────────────────────────────
        "BOUNCE_BB_LO":         c.bounce_bb_lo,
        "BOUNCE_BB+RSI":        c.bounce_bb_lo & c.rsi_lt40,
        "BOUNCE_BB+MACD":       c.bounce_bb_lo & c.macd_neg_rising,
        "BOUNCE_BB+VOL":        c.bounce_bb_lo & c.vol_spike,
        "BOUNCE_BB+RSI+MACD":   c.bounce_bb_lo & c.rsi_lt40 & c.macd_neg_rising,

        # ── Stochastic crossover dari oversold ────────────────────────────
        "STOCH_CROSS":          c.stoch_cross_up,
        "STOCH_CROSS+RSI":      c.stoch_cross_up & c.rsi_lt40,
        "STOCH_CROSS+BB":       c.stoch_cross_up & c.near_bb_lo,
        "STOCH_CROSS+MACD":     c.stoch_cross_up & c.macd_neg_rising,
        "STOCH_CROSS+RSI+BB":   c.stoch_cross_up & c.rsi_lt40 & c.near_bb_lo,
        "STOCH_CROSS+RSI+MACD": c.stoch_cross_up & c.rsi_lt40 & c.macd_neg_rising,

        # ── Capitulation reversal ─────────────────────────────────────────
        "CAPITULATION":         c.capit_reversal,
        "CAPIT+RSI":            c.capit_reversal & c.rsi_lt40,
        "CAPIT+RSI+MACD":       c.capit_reversal & c.rsi_lt40 & c.macd_neg_rising,

        # ── Down N hari + reversal ────────────────────────────────────────
        "DOWN3+GREEN":          c.down3 & c.green_candle,
        "DOWN3+RSI":            c.down3 & c.rsi_lt40,
        "DOWN3+RSI+GREEN":      c.down3 & c.rsi_lt40 & c.green_candle,
        "DOWN3+STOCH":          c.down3 & c.stoch_os,
        "DOWN3+MACD_TURN":      c.down3 & c.macd_neg_rising,
        "DOWN3+MACD+GREEN":     c.down3 & c.macd_neg_rising & c.green_candle,
        "DOWN4+GREEN":          c.down4 & c.green_candle,
        "DOWN4+RSI+GREEN":      c.down4 & c.rsi_lt40 & c.green_candle,
        "DOWN5+GREEN":          c.down5 & c.green_candle,
        "DOWN5+RSI+GREEN":      c.down5 & c.rsi_lt40 & c.green_candle,
        "DOWN5+RSI+MACD":       c.down5 & c.rsi_lt40 & c.macd_neg_rising,

        # ── Hammer pattern ────────────────────────────────────────────────
        "HAMMER":               c.hammer,
        "HAMMER+RSI":           c.hammer & c.rsi_lt40,
        "HAMMER+BB_LO":         c.hammer & c.near_bb_lo,
        "HAMMER+RSI+BB":        c.hammer & c.rsi_lt40 & c.near_bb_lo,
        "HAMMER+VOL":           c.hammer & c.vol_high,
        "HAMMER+VOL+RSI":       c.hammer & c.vol_high & c.rsi_lt40,

        # ── Oversold dari EMA (mean reversion) ───────────────────────────
        "BELOW_EMA20_3PCT":     c.oversold_ema20,
        "BELOW_EMA20_5PCT":     c.oversold_ema20_5pct,
        "BELOW_EMA50_5PCT":     c.oversold_ema50,
        "OVS_EMA+RSI":          c.oversold_ema20 & c.rsi_lt40,
        "OVS_EMA+RSI+MACD":     c.oversold_ema20 & c.rsi_lt40 & c.macd_neg_rising,
        "OVS_EMA+RSI+GREEN":    c.oversold_ema20 & c.rsi_lt40 & c.green_candle,
        "OVS_EMA+BB+RSI":       c.oversold_ema20 & c.near_bb_lo & c.rsi_lt40,

        # ── OBV accumulation saat harga lemah ────────────────────────────
        "WEAK+OBV_UP":          c.below_ema20 & c.obv_rising,
        "WEAK+OBV+RSI<40":      c.below_ema20 & c.obv_rising & c.rsi_lt40,
        "WEAK+OBV+MACD_TURN":   c.below_ema20 & c.obv_rising & c.macd_neg_rising,
        "WEAK+OBV+RSI+MACD":    c.below_ema20 & c.obv_rising & c.rsi_lt40 & c.macd_neg_rising,

        # ── Super ketat — semua konfirmasi ────────────────────────────────
        "ULTRA_WEAK_BOUNCE":    (
            c.rsi_lt35 & c.stoch_os & c.near_bb_lo
            & c.macd_neg_rising & c.green_candle
        ),
        "ULTRA_WEAK_V2":        (
            c.rsi_lt35 & c.willr_os & c.near_bb_lo
            & c.macd_neg_rising & c.obv_rising
        ),
        "ULTRA_WEAK_V3":        (
            c.rsi_lt30 & c.stoch_os & c.near_bb_lo
            & c.macd_neg_rising & c.vol_spike & c.green_candle
        ),
        "CAPIT_REVERSAL_FULL":  (
            c.capit_reversal & c.rsi_lt40
            & c.near_bb_lo & c.macd_neg_rising
        ),
        "DOWN5_FULL_CONFIRM":   (
            c.down5 & c.rsi_lt35 & c.near_bb_lo
            & c.green_candle & c.macd_neg_rising
        ),
        "RSI_TRIPLE_CONFIRM":   (
            c.rsi_lt30 & c.stoch_os & c.willr_os
            & c.cci_os & c.macd_neg_rising
        ),
        "MEAN_REVERT_FULL":     (
            c.oversold_ema20 & c.rsi_lt35
            & c.near_bb_lo & c.macd_neg_rising & c.green_candle
        ),
    }


# ── Backtest ──────────────────────────────────────────────────────────────────
MIN_BARS = 60
results: dict[tuple, list] = defaultdict(list)

print("Menghitung indikator dan backtest...", flush=True)
for ticker, df in all_data.items():
    try:
        ind = compute(df)
    except Exception:
        continue

    closes = ind["close"]
    conds  = conditions(ind)
    valid  = ind.index[MIN_BARS:]

    for date in valid:
        loc = closes.index.get_loc(date)
        entry = float(closes.iloc[loc])
        if entry <= 0:
            continue

        fc = {}
        for h in HOLD_DAYS:
            fl = loc + h
            fc[h] = float(closes.iloc[fl]) if fl < len(closes) else None

        for cname, cseries in conds.items():
            try:
                if bool(cseries.loc[date]):
                    for h in HOLD_DAYS:
                        if fc[h] is not None:
                            results[(cname, h)].append((fc[h] - entry) / entry)
            except Exception:
                continue

# ── Report ─────────────────────────────────────────────────────────────────────
summary = []
for (cname, h), pnl_list in results.items():
    if len(pnl_list) < MIN_SIGNALS:
        continue
    arr = np.array(pnl_list)
    wr  = np.sum(arr >= WIN_PCT) / len(arr)
    ag  = np.mean(arr)
    mg  = np.median(arr)
    summary.append((cname, h, len(arr), wr, ag, mg))

summary.sort(key=lambda x: (-x[3], -x[2]))

print(f"\n{'KONDISI':<35} {'HOLD':>4} {'N':>5} {'WINRATE':>8} {'AVG%':>8} {'MED%':>7}")
print("-" * 70)
for cname, h, n, wr, ag, mg in summary:
    flag = " <== 70%+" if wr >= 0.70 else (" <== 60%+" if wr >= 0.60 else "")
    print(f"{cname:<35} {h:>3}d {n:>5} {wr*100:>7.1f}%  {ag*100:>+7.1f}%  {mg*100:>+6.1f}%{flag}")

# Top 15 per hold period
print("\n" + "="*70)
for h in HOLD_DAYS:
    sub = [(c, n, w, a) for (c, hh, n, w, a, _) in summary if hh == h]
    sub.sort(key=lambda x: -x[2])
    print(f"\n TOP 15 | Hold {h} hari")
    print(f"  {'KONDISI':<35} {'N':>5} {'WINRATE':>8} {'AVG GAIN':>9}")
    print("  " + "-"*58)
    for cname, n, wr, ag in sub[:15]:
        bar  = "#" * int(wr * 25)
        flag = " ***" if wr >= 0.70 else (" **" if wr >= 0.60 else "")
        print(f"  {cname:<35} {n:>5} {wr*100:>7.1f}%   {ag*100:>+7.1f}%  {bar}{flag}")

# Kondisi >= 55%
high = [(c, h, n, w, a) for (c, h, n, w, a, _) in summary if w >= 0.55]
if high:
    print(f"\n{'='*70}")
    print(f" KONDISI WINRATE >= 55% (total {len(high)} kombinasi)")
    print(f"  {'KONDISI':<35} {'HOLD':>4} {'N':>5} {'WINRATE':>8} {'AVG':>9}")
    print("  " + "-"*58)
    for cname, h, n, wr, ag in sorted(high, key=lambda x: -x[3]):
        print(f"  {cname:<35} {h:>3}d {n:>5} {wr*100:>7.1f}%   {ag*100:>+7.1f}%")
else:
    print("\n[!] Tidak ada kondisi dengan winrate >= 55%")

print("\n=== Selesai ===")
