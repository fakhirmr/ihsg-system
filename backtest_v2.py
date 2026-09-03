"""
Backtest v2 — eksplorasi luas mencari winrate >= 80%
Tambahan indikator: ADX, OBV, MACD crossover, EMA alignment, momentum arah, dll.
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

# ── Config ────────────────────────────────────────────────────────────────────
HOLD_DAYS   = [3, 5, 10]
WIN_PCT     = 0.02        # 2% = win
MIN_SIGNALS = 5           # minimum sinyal agar kondisi dianggap valid
LOOKBACK    = 400

end   = datetime.today()
start = end - timedelta(days=LOOKBACK + 30)

print("=== IHSG Backtest v2 — Eksplorasi Winrate >= 80% ===")
print(f"Tickers : {len(DEFAULT_TICKERS)}")
print(f"Period  : {start.strftime('%Y-%m-%d')} s/d {end.strftime('%Y-%m-%d')}")
print(f"Win     : close hari-N >= entry + {WIN_PCT*100:.0f}%\n")
print("Downloading...", flush=True)

all_data: dict[str, pd.DataFrame] = {}
for ticker in DEFAULT_TICKERS:
    try:
        df = yf.download(ticker, start=start, end=end, auto_adjust=True,
                         progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        if len(df) >= 60:
            all_data[ticker] = df
    except Exception:
        pass

print(f"Berhasil: {len(all_data)} tickers\n")

# ── Vectorized indicator computation ─────────────────────────────────────────
def compute(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"].squeeze()
    v = df["Volume"].squeeze()
    h = df["High"].squeeze()
    lo = df["Low"].squeeze()

    # EMA
    ema9   = c.ewm(span=9,   adjust=False).mean()
    ema20  = c.ewm(span=20,  adjust=False).mean()
    ema50  = c.ewm(span=50,  adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    above_ema20  = c > ema20
    above_ema50  = c > ema50
    above_ema200 = c > ema200
    ema_aligned  = (ema9 > ema20) & (ema20 > ema50) & (ema50 > ema200)
    ema_3align   = (c > ema20) & (ema20 > ema50) & above_ema200

    # MACD
    ema12  = c.ewm(span=12, adjust=False).mean()
    ema26  = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_s = macd_l.ewm(span=9, adjust=False).mean()
    macd_h = macd_l - macd_s

    macd_pos      = macd_h > 0
    macd_rising   = macd_h > macd_h.shift(1)          # histogram naik
    macd_cross_up = (macd_l > macd_s) & (macd_l.shift(1) <= macd_s.shift(1))  # fresh crossover
    macd_accel    = macd_rising & (macd_h.shift(1) > macd_h.shift(2))          # 2 bar naik berturut

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    rsi_rising  = rsi > rsi.shift(1)
    rsi_was_low = rsi.shift(3).lt(40)                     # RSI < 40 tiga bar lalu
    rsi_recover = rsi_was_low & rsi.between(40, 65)       # sekarang pulih ke zona sehat

    # ADX
    tr    = pd.concat([(h - lo), (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    up_m  = h - h.shift()
    dn_m  = lo.shift() - lo
    pdm   = ((up_m > dn_m) & (up_m > 0)) * up_m
    ndm   = ((dn_m > up_m) & (dn_m > 0)) * dn_m
    atr14 = tr.ewm(span=14, adjust=False).mean()
    pdi   = 100 * pdm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    ndi   = 100 * ndm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    adx   = dx.ewm(span=14, adjust=False).mean()
    adx20 = adx > 20
    adx25 = adx > 25
    adx30 = adx > 30
    bull_di = pdi > ndi     # +DI > -DI = bullish directional

    # OBV
    price_up = c > c.shift(1)
    price_dn = c < c.shift(1)
    obv = (v.where(price_up, 0) - v.where(price_dn, 0)).cumsum()
    obv_rising  = obv > obv.rolling(5).mean()
    obv_accel   = obv > obv.shift(1)

    # Bollinger Bands
    sma20  = c.rolling(20).mean()
    std20  = c.rolling(20).std()
    bb_up  = sma20 + 2 * std20
    bb_lo  = sma20 - 2 * std20
    bb_w   = (bb_up - bb_lo) / sma20                      # bandwidth
    bb_squeeze = bb_w < bb_w.rolling(20).mean() * 0.85    # BB menyempit (low volatility)
    bb_expand  = bb_w > bb_w.shift(1)                     # BB mulai melebar
    near_bb_up = c > bb_up * 0.995                        # breakout atas BB
    near_bb_lo = c < bb_lo * 1.005                        # menyentuh lower band

    # Breakout / breakdown
    prior_hi = c.shift(1).rolling(20).max()
    prior_lo = c.shift(1).rolling(20).min()
    breakout  = c > prior_hi * 1.01
    breakdown = c < prior_lo * 0.99

    # Volume
    avg_vol  = v.rolling(20).mean()
    rel_vol  = v / avg_vol.replace(0, np.nan)
    vol12    = rel_vol >= 1.2
    vol15    = rel_vol >= 1.5
    vol20    = rel_vol >= 2.0
    vol30    = rel_vol >= 3.0

    # Trend
    sma10 = c.rolling(10).mean()
    sma20r = c.rolling(20).mean()
    uptrend   = (c > ema20) & (ema20 > ema50) & (sma10 > sma20r)
    downtrend = (c < ema20) & (ema20 < ema50) & (sma10 < sma20r)

    # Price momentum
    mom3  = c / c.shift(3) - 1
    mom5  = c / c.shift(5) - 1
    mom10 = c / c.shift(10) - 1
    mom_pos3  = mom3 > 0
    mom_pos5  = mom5 > 0
    mom_pos10 = mom10 > 0
    mom_strong = mom5 > 0.02   # naik > 2% dalam 5 hari terakhir

    # Price patterns
    higher_high = (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    hh3         = (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & (c.shift(2) > c.shift(3))
    inside_day  = (h < h.shift(1)) & (lo > lo.shift(1))
    inside_break = breakout & inside_day.shift(1)           # breakout setelah inside day

    # Consolidation: low ATR in prior 5 bars then breakout
    atr5    = tr.rolling(5).mean()
    atr_low = atr5 < atr5.rolling(20).mean() * 0.7         # volatilitas rendah
    consol_break = breakout & atr_low.shift(1)              # breakout dari konsolidasi

    # Gap up
    gap_up = c.shift(0) > h.shift(1) * 1.005

    # Near support (bouncing from support)
    support = c.shift(1).rolling(20).min()
    near_support = c < support * 1.02

    # Price % from EMA
    pct_from_ema20 = (c - ema20) / ema20
    not_extended   = pct_from_ema20 < 0.05     # harga tidak lebih dari 5% di atas EMA20
    slightly_above = pct_from_ema20.between(0, 0.03)

    return pd.DataFrame({
        "close":         c,
        "uptrend":       uptrend,
        "downtrend":     downtrend,
        "above_ema20":   above_ema20,
        "above_ema50":   above_ema50,
        "above_ema200":  above_ema200,
        "ema_aligned":   ema_aligned,
        "ema_3align":    ema_3align,
        "macd_pos":      macd_pos,
        "macd_rising":   macd_rising,
        "macd_cross_up": macd_cross_up,
        "macd_accel":    macd_accel,
        "rsi":           rsi,
        "rsi_rising":    rsi_rising,
        "rsi_recover":   rsi_recover,
        "adx20":         adx20,
        "adx25":         adx25,
        "adx30":         adx30,
        "bull_di":       bull_di,
        "obv_rising":    obv_rising,
        "obv_accel":     obv_accel,
        "bb_squeeze":    bb_squeeze,
        "bb_expand":     bb_expand,
        "near_bb_up":    near_bb_up,
        "near_bb_lo":    near_bb_lo,
        "breakout":      breakout,
        "vol12":         vol12,
        "vol15":         vol15,
        "vol20":         vol20,
        "vol30":         vol30,
        "rel_vol":       rel_vol,
        "mom_pos3":      mom_pos3,
        "mom_pos5":      mom_pos5,
        "mom_pos10":     mom_pos10,
        "mom_strong":    mom_strong,
        "higher_high":   higher_high,
        "hh3":           hh3,
        "inside_break":  inside_break,
        "consol_break":  consol_break,
        "gap_up":        gap_up,
        "not_extended":  not_extended,
        "slightly_above": slightly_above,
        "bull_di":       bull_di,
        "rsi_35_65":     rsi.between(35, 65),
        "rsi_40_65":     rsi.between(40, 65),
        "rsi_40_70":     rsi.between(40, 70),
        "rsi_45_65":     rsi.between(45, 65),
        "rsi_50_70":     rsi.between(50, 70),
    }, index=df.index).dropna(subset=["rsi", "adx20", "obv_rising"])


# ── Condition combinations to test ────────────────────────────────────────────
def define_conditions(i: pd.DataFrame) -> dict[str, pd.Series]:
    c = i
    return {
        # ── Dari v1 terbaik (baseline) ─────────────────────────────────────
        "V1_BREAKOUT+MACD+VOL":     c.breakout & c.macd_pos & c.vol15,
        "V1_STRICT_BREAKOUT":       c.breakout & c.above_ema20 & c.macd_pos & c.rsi_35_65 & c.vol15,

        # ── Breakout + ADX ─────────────────────────────────────────────────
        "BRK+ADX20":                c.breakout & c.adx20,
        "BRK+ADX25":                c.breakout & c.adx25,
        "BRK+ADX30":                c.breakout & c.adx30,
        "BRK+ADX25+VOL":            c.breakout & c.adx25 & c.vol15,
        "BRK+ADX25+MACD":           c.breakout & c.adx25 & c.macd_pos,
        "BRK+ADX25+MACD+VOL":       c.breakout & c.adx25 & c.macd_pos & c.vol15,
        "BRK+ADX30+MACD+VOL":       c.breakout & c.adx30 & c.macd_pos & c.vol15,
        "BRK+ADX25+BULL_DI":        c.breakout & c.adx25 & c.bull_di,
        "BRK+ADX25+BULL_DI+MACD":   c.breakout & c.adx25 & c.bull_di & c.macd_pos,
        "BRK+ADX25+BULL_DI+VOL":    c.breakout & c.adx25 & c.bull_di & c.vol15,
        "BRK+ADX25+BULL_DI+MACD+VOL": c.breakout & c.adx25 & c.bull_di & c.macd_pos & c.vol15,
        "BRK+ADX30+BULL_DI+MACD+VOL": c.breakout & c.adx30 & c.bull_di & c.macd_pos & c.vol15,
        "BRK+ADX25+RSI+VOL":        c.breakout & c.adx25 & c.rsi_40_65 & c.vol15,
        "BRK+ADX25+RSI+MACD+VOL":   c.breakout & c.adx25 & c.rsi_40_65 & c.macd_pos & c.vol15,

        # ── Breakout + OBV ─────────────────────────────────────────────────
        "BRK+OBV":                  c.breakout & c.obv_rising,
        "BRK+OBV+MACD":             c.breakout & c.obv_rising & c.macd_pos,
        "BRK+OBV+VOL":              c.breakout & c.obv_rising & c.vol15,
        "BRK+OBV+ADX25":            c.breakout & c.obv_rising & c.adx25,
        "BRK+OBV+ADX25+MACD":       c.breakout & c.obv_rising & c.adx25 & c.macd_pos,
        "BRK+OBV+ADX25+VOL":        c.breakout & c.obv_rising & c.adx25 & c.vol15,
        "BRK+OBV+ADX25+MACD+VOL":   c.breakout & c.obv_rising & c.adx25 & c.macd_pos & c.vol15,
        "BRK+OBV+ADX30+MACD+VOL":   c.breakout & c.obv_rising & c.adx30 & c.macd_pos & c.vol15,

        # ── MACD crossover ─────────────────────────────────────────────────
        "MACD_CROSS":               c.macd_cross_up,
        "MACD_CROSS+UP":            c.macd_cross_up & c.uptrend,
        "MACD_CROSS+EMA20":         c.macd_cross_up & c.above_ema20,
        "MACD_CROSS+VOL":           c.macd_cross_up & c.vol12,
        "MACD_CROSS+ADX20":         c.macd_cross_up & c.adx20,
        "MACD_CROSS+ADX25":         c.macd_cross_up & c.adx25,
        "MACD_CROSS+ADX25+VOL":     c.macd_cross_up & c.adx25 & c.vol12,
        "MACD_CROSS+UP+ADX25":      c.macd_cross_up & c.uptrend & c.adx25,
        "MACD_CROSS+UP+ADX25+VOL":  c.macd_cross_up & c.uptrend & c.adx25 & c.vol12,
        "MACD_CROSS+EMA+ADX+VOL":   c.macd_cross_up & c.above_ema50 & c.adx25 & c.vol12,
        "MACD_CROSS+RSI+ADX":       c.macd_cross_up & c.rsi_40_65 & c.adx25,
        "MACD_CROSS+OBV+ADX":       c.macd_cross_up & c.obv_rising & c.adx25,

        # ── MACD accelerating ─────────────────────────────────────────────
        "MACD_ACCEL":               c.macd_accel,
        "MACD_ACCEL+UP":            c.macd_accel & c.uptrend,
        "MACD_ACCEL+ADX25":         c.macd_accel & c.adx25,
        "MACD_ACCEL+OBV":           c.macd_accel & c.obv_rising,
        "MACD_ACCEL+UP+ADX25":      c.macd_accel & c.uptrend & c.adx25,
        "MACD_ACCEL+UP+OBV":        c.macd_accel & c.uptrend & c.obv_rising,
        "MACD_ACCEL+ADX25+OBV":     c.macd_accel & c.adx25 & c.obv_rising,
        "MACD_ACCEL+UP+ADX+OBV":    c.macd_accel & c.uptrend & c.adx25 & c.obv_rising,
        "MACD_ACCEL+UP+ADX+VOL":    c.macd_accel & c.uptrend & c.adx25 & c.vol12,

        # ── EMA full alignment ────────────────────────────────────────────
        "EMA_ALIGNED":              c.ema_aligned,
        "EMA_ALIGNED+MACD":         c.ema_aligned & c.macd_pos,
        "EMA_ALIGNED+ADX25":        c.ema_aligned & c.adx25,
        "EMA_ALIGNED+MACD+ADX25":   c.ema_aligned & c.macd_pos & c.adx25,
        "EMA_ALIGNED+VOL+ADX25":    c.ema_aligned & c.vol12 & c.adx25,
        "EMA_ALIGNED+MACD+VOL+ADX": c.ema_aligned & c.macd_pos & c.vol12 & c.adx25,
        "EMA_ALIGNED+OBV+ADX25":    c.ema_aligned & c.obv_rising & c.adx25,
        "EMA_ALIGNED+MACD+OBV+ADX": c.ema_aligned & c.macd_pos & c.obv_rising & c.adx25,
        "EMA3ALIGN+MACD+OBV+ADX25": c.ema_3align & c.macd_pos & c.obv_rising & c.adx25,
        "EMA3ALIGN+MACD+VOL+ADX25": c.ema_3align & c.macd_pos & c.vol12 & c.adx25,

        # ── Bollinger squeeze breakout ────────────────────────────────────
        "BB_SQUEEZE+BRK":           c.bb_squeeze & c.breakout,
        "BB_SQUEEZE+BRK+VOL":       c.bb_squeeze & c.breakout & c.vol15,
        "BB_SQUEEZE+BRK+MACD":      c.bb_squeeze & c.breakout & c.macd_pos,
        "BB_SQUEEZE+BRK+MACD+VOL":  c.bb_squeeze & c.breakout & c.macd_pos & c.vol15,
        "BB_SQUEEZE+BRK+ADX+VOL":   c.bb_squeeze & c.breakout & c.adx20 & c.vol15,
        "BB_SQUEEZE+BRK+ADX+MACD+VOL": c.bb_squeeze & c.breakout & c.adx20 & c.macd_pos & c.vol15,

        # ── Konsolidasi lalu breakout ─────────────────────────────────────
        "CONSOL_BRK":               c.consol_break,
        "CONSOL_BRK+VOL":           c.consol_break & c.vol15,
        "CONSOL_BRK+MACD":          c.consol_break & c.macd_pos,
        "CONSOL_BRK+ADX20":         c.consol_break & c.adx20,
        "CONSOL_BRK+MACD+VOL":      c.consol_break & c.macd_pos & c.vol15,
        "CONSOL_BRK+MACD+ADX+VOL":  c.consol_break & c.macd_pos & c.adx20 & c.vol15,

        # ── RSI recovery (oversold lalu pulih) ───────────────────────────
        "RSI_RECOVER":              c.rsi_recover,
        "RSI_RECOVER+MACD":         c.rsi_recover & c.macd_pos,
        "RSI_RECOVER+OBV":          c.rsi_recover & c.obv_rising,
        "RSI_RECOVER+UP":           c.rsi_recover & c.uptrend,
        "RSI_RECOVER+UP+MACD":      c.rsi_recover & c.uptrend & c.macd_pos,
        "RSI_RECOVER+UP+OBV+MACD":  c.rsi_recover & c.uptrend & c.obv_rising & c.macd_pos,

        # ── Momentum positif multi-period ─────────────────────────────────
        "MOM3+MOM5":                c.mom_pos3 & c.mom_pos5,
        "MOM3+MOM5+MOM10":          c.mom_pos3 & c.mom_pos5 & c.mom_pos10,
        "MOM3+MOM5+MACD":           c.mom_pos3 & c.mom_pos5 & c.macd_pos,
        "MOM3+MOM5+ADX25":          c.mom_pos3 & c.mom_pos5 & c.adx25,
        "MOM3+MOM5+OBV+ADX":        c.mom_pos3 & c.mom_pos5 & c.obv_rising & c.adx25,
        "MOM_STRONG+MACD+ADX":      c.mom_strong & c.macd_pos & c.adx25,
        "MOM_STRONG+OBV+ADX+MACD":  c.mom_strong & c.obv_rising & c.adx25 & c.macd_pos,

        # ── Inside day breakout ───────────────────────────────────────────
        "INSIDE_BRK":               c.inside_break,
        "INSIDE_BRK+VOL":           c.inside_break & c.vol15,
        "INSIDE_BRK+MACD":          c.inside_break & c.macd_pos,
        "INSIDE_BRK+MACD+VOL":      c.inside_break & c.macd_pos & c.vol15,

        # ── Gap up ────────────────────────────────────────────────────────
        "GAP_UP":                   c.gap_up,
        "GAP_UP+VOL":               c.gap_up & c.vol15,
        "GAP_UP+MACD":              c.gap_up & c.macd_pos,
        "GAP_UP+ADX20":             c.gap_up & c.adx20,
        "GAP_UP+MACD+VOL":          c.gap_up & c.macd_pos & c.vol15,
        "GAP_UP+UP+MACD+VOL":       c.gap_up & c.uptrend & c.macd_pos & c.vol15,

        # ── 5–7 kondisi super-ketat ───────────────────────────────────────
        "ULTRA_BREAKOUT":           (
            c.breakout & c.adx25 & c.bull_di
            & c.macd_pos & c.vol20
            & c.rsi_40_70 & c.obv_rising
        ),
        "ULTRA_BREAKOUT_TIGHT":     (
            c.breakout & c.adx30 & c.bull_di
            & c.macd_pos & c.macd_rising
            & c.vol20 & c.rsi_45_65
            & c.obv_rising
        ),
        "ULTRA_TREND":              (
            c.ema_aligned & c.uptrend
            & c.macd_pos & c.macd_rising
            & c.adx25 & c.bull_di
            & c.obv_rising & c.vol12
        ),
        "ULTRA_MOMENTUM":           (
            c.macd_cross_up & c.adx25
            & c.bull_di & c.obv_rising
            & c.rsi_40_65 & c.above_ema50
        ),
        "SQUEEZE_FIRE":             (
            c.bb_squeeze & c.breakout
            & c.adx20 & c.macd_pos
            & c.obv_rising & c.vol20
        ),
        "PERFECT_SETUP":            (
            c.uptrend & c.ema_aligned
            & c.breakout & c.adx25
            & c.bull_di & c.macd_pos
            & c.obv_rising & c.vol15
        ),
        "STRONG_MOM_BRK":           (
            c.breakout & c.mom_strong
            & c.adx25 & c.macd_pos
            & c.obv_rising & c.vol15
        ),
        "BRK_CONFIRM_ALL":          (
            c.breakout & c.adx25
            & c.bull_di & c.macd_pos
            & c.macd_rising & c.obv_rising
            & c.vol15 & c.rsi_40_70
        ),
        "TREND_CONFIRM_ALL":        (
            c.uptrend & c.ema_3align
            & c.adx25 & c.bull_di
            & c.macd_pos & c.macd_rising
            & c.obv_rising & c.rsi_40_65
        ),
        "MACD_CROSS_CONFIRM":       (
            c.macd_cross_up & c.uptrend
            & c.adx25 & c.bull_di
            & c.obv_rising & c.vol12
            & c.rsi_35_65
        ),
        "BB_CONSOL_FIRE":           (
            c.bb_squeeze & c.breakout
            & c.adx25 & c.bull_di
            & c.macd_pos & c.vol20
            & c.obv_rising
        ),
    }


# ── Backtest loop ─────────────────────────────────────────────────────────────
MIN_BARS = 60
results: dict[tuple, list] = defaultdict(list)

print("Menghitung indikator dan backtest...", flush=True)
for ticker, df in all_data.items():
    try:
        ind = compute(df)
    except Exception as e:
        continue

    closes = ind["close"]
    conds  = define_conditions(ind)
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

# Semua hasil
print(f"\n{'KONDISI':<38} {'HOLD':>4} {'N':>5} {'WINRATE':>8} {'AVG%':>8} {'MED%':>7}")
print("-" * 75)
for cname, h, n, wr, ag, mg in summary:
    flag = " <== 80%+" if wr >= 0.80 else (" <== 70%+" if wr >= 0.70 else "")
    print(f"{cname:<38} {h:>3}d {n:>5} {wr*100:>7.1f}% {ag*100:>+7.1f}% {mg*100:>+6.1f}%{flag}")

# Top 15 per hold
print("\n" + "="*75)
for h in HOLD_DAYS:
    sub = [(c, n, w, a, m) for (c, hh, n, w, a, m) in summary if hh == h]
    sub.sort(key=lambda x: -x[2])
    print(f"\n TOP 15 | Hold {h} hari")
    print(f"  {'KONDISI':<38} {'N':>5} {'WINRATE':>8} {'AVG':>8}")
    print("  " + "-"*60)
    for cname, n, wr, ag, _ in sub[:15]:
        bar  = "#" * int(wr * 25)
        flag = " ***" if wr >= 0.80 else (" **" if wr >= 0.70 else "")
        print(f"  {cname:<38} {n:>5} {wr*100:>7.1f}%  {ag*100:>+6.1f}%  {bar}{flag}")

# Kondisi dengan winrate >= 60%
high_wr = [(c, h, n, w, a) for (c, h, n, w, a, _) in summary if w >= 0.60]
if high_wr:
    print(f"\n{'='*75}")
    print(f" KONDISI WINRATE >= 60% (total {len(high_wr)} kombinasi)")
    print(f"  {'KONDISI':<38} {'HOLD':>4} {'N':>5} {'WINRATE':>8} {'AVG':>8}")
    print("  " + "-"*60)
    for cname, h, n, wr, ag in sorted(high_wr, key=lambda x: -x[3]):
        print(f"  {cname:<38} {h:>3}d {n:>5} {wr*100:>7.1f}%  {ag*100:>+6.1f}%")
else:
    print("\n[!] Tidak ada kondisi dengan winrate >= 60%")

print("\n=== Selesai ===")
