"""
Backtest: Cari balance antara winrate, avg gain, dan frekuensi sinyal untuk kondisi BREAKOUT.
Composite score = winrate * avg_gain * log(N) / log(100)
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

HOLD_DAYS   = [5, 10, 15]
WIN_PCT     = 0.02      # win = naik >= 2% dari entry
MIN_SIGNALS = 20
LOOKBACK    = 500       # lebih panjang agar sinyal breakout cukup

end   = datetime.today()
start = end - timedelta(days=LOOKBACK + 30)

print("=== Backtest Breakout Balance: Winrate x Gain x Frekuensi ===")
print(f"Tickers : {len(DEFAULT_TICKERS)}")
print(f"Period  : {start.strftime('%Y-%m-%d')} s/d {end.strftime('%Y-%m-%d')}")
print(f"Win     : close hari-N >= entry + {WIN_PCT*100:.0f}%")
print(f"Min sig : {MIN_SIGNALS}\n")
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

    # ── Price Breakout ───────────────────────────────────────────────────────
    # Breakout dari berbagai lookback period
    high10 = c.rolling(10).max().shift(1)
    high20 = c.rolling(20).max().shift(1)
    high30 = c.rolling(30).max().shift(1)
    high50 = c.rolling(50).max().shift(1)

    brk10 = c > high10 * 1.005   # minimal 0.5% di atas range (menghindari false break)
    brk20 = c > high20 * 1.005
    brk30 = c > high30 * 1.005
    brk50 = c > high50 * 1.005

    # ── ATR dan Konsolidasi ──────────────────────────────────────────────────
    prev_c = c.shift(1)
    tr = pd.concat([(h - lo), (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr5  = tr.rolling(5).mean()
    atr14 = tr.rolling(14).mean()
    atr25 = tr.rolling(25).mean()

    # Konsolidasi = ATR5 sebelum breakout rendah dibanding rata-ratanya
    atr5_avg20 = atr5.rolling(20).mean()
    consol_tight = atr5.shift(1) < atr5_avg20.shift(1) * 0.70   # ketat
    consol_mild  = atr5.shift(1) < atr5_avg20.shift(1) * 0.85   # lebih longgar

    # Consolidation breakout dari berbagai range
    consol_brk20 = brk20 & consol_tight
    consol_brk20_mild = brk20 & consol_mild
    consol_brk30 = brk30 & consol_tight
    consol_brk10 = brk10 & consol_tight

    # ── Volume ───────────────────────────────────────────────────────────────
    avg_vol = v.rolling(20).mean()
    rel_vol = v / avg_vol.replace(0, np.nan)
    vol12   = rel_vol >= 1.2
    vol15   = rel_vol >= 1.5
    vol20   = rel_vol >= 2.0
    vol25   = rel_vol >= 2.5
    vol_spike = rel_vol >= 3.0

    # ── MACD ─────────────────────────────────────────────────────────────────
    ema12   = c.ewm(span=12, adjust=False).mean()
    ema26   = c.ewm(span=26, adjust=False).mean()
    macd_l  = ema12 - ema26
    macd_s  = macd_l.ewm(span=9, adjust=False).mean()
    macd_h  = macd_l - macd_s

    macd_pos        = macd_h > 0
    macd_h_rising   = macd_h > macd_h.shift(1)
    macd_cross_up   = (macd_l > macd_s) & (macd_l.shift(1) <= macd_s.shift(1))
    macd_line_pos   = macd_l > 0              # MACD line di atas zero
    macd_all_pos    = macd_pos & macd_line_pos # kedua line positif

    # ── EMA dan Trend ────────────────────────────────────────────────────────
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    above_ema20   = c > ema20
    above_ema50   = c > ema50
    above_ema200  = c > ema200
    ema20_x_ema50 = (ema20 > ema50) & (ema20.shift(1) <= ema50.shift(1))  # golden cross
    ema_align_up  = above_ema20 & above_ema50   # harga di atas kedua EMA

    # ── RSI ──────────────────────────────────────────────────────────────────
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    rsi_gt40 = rsi > 40   # tidak terlalu oversold
    rsi_gt45 = rsi > 45
    rsi_gt50 = rsi > 50   # momentum positif
    rsi_gt55 = rsi > 55   # momentum kuat
    rsi_lt70 = rsi < 70   # belum overbought
    rsi_lt65 = rsi < 65
    rsi_rising = rsi > rsi.shift(1)

    # ── ADX ──────────────────────────────────────────────────────────────────
    up_m = h - h.shift()
    dn_m = lo.shift() - lo
    pdm  = ((up_m > dn_m) & (up_m > 0)) * up_m
    ndm  = ((dn_m > up_m) & (dn_m > 0)) * dn_m
    pdi  = 100 * pdm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    ndi  = 100 * ndm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    adx  = dx.ewm(span=14, adjust=False).mean()

    adx_gt20 = adx > 20   # ada tren
    adx_gt25 = adx > 25   # tren cukup kuat
    adx_gt30 = adx > 30   # tren kuat
    adx_rising = adx > adx.shift(1)

    # ── OBV ──────────────────────────────────────────────────────────────────
    obv     = (v.where(c > c.shift(1), 0) - v.where(c < c.shift(1), 0)).cumsum()
    obv_up3 = (obv > obv.shift(1)) & (obv.shift(1) > obv.shift(2)) & (obv.shift(2) > obv.shift(3))
    obv_ema = obv.ewm(span=20, adjust=False).mean()
    obv_above_ema = obv > obv_ema   # OBV di atas moving avg-nya

    # ── Bollinger Band ───────────────────────────────────────────────────────
    sma20  = c.rolling(20).mean()
    std20  = c.rolling(20).std()
    bb_up  = sma20 + 2 * std20
    bb_lo  = sma20 - 2 * std20
    bb_pct = (c - bb_lo) / (bb_up - bb_lo).replace(0, np.nan)

    bb_squeeze   = std20 < std20.rolling(20).mean() * 0.7   # BB menyempit (konsolidasi)
    bb_expand    = std20 > std20.shift(1)                    # BB mulai melebar
    bb_high      = bb_pct > 0.80                             # harga di atas 80% BB range
    bb_brk_upper = c > bb_up                                 # breakout dari BB atas
    bb_squeeze_brk = bb_squeeze.shift(1) & bb_expand         # squeeze kemudian expand

    # ── Candle Patterns ──────────────────────────────────────────────────────
    body_pct   = (c - df["Open"].squeeze()).abs() / df["Open"].squeeze()
    big_candle = body_pct > 0.02   # body candle > 2%
    green      = c > df["Open"].squeeze()
    big_green  = green & big_candle

    # Candle prev hijau (konfirmasi momentum)
    prev_green   = green.shift(1)
    prev_big_green = big_green.shift(1)

    # ── Gap Up ───────────────────────────────────────────────────────────────
    gap_up = df["Open"].squeeze() > c.shift(1) * 1.005   # gap up 0.5%+

    # ── Inside Bar Breakout ──────────────────────────────────────────────────
    inside_bar = (h < h.shift(1)) & (lo > lo.shift(1))   # inside bar kemarin
    inside_brk = inside_bar.shift(1) & (c > h.shift(2))  # breakout dari inside bar

    # ── Higher High / Higher Low structure ───────────────────────────────────
    higher_high = (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    new_high3   = c == c.rolling(3).max()
    new_high5   = c == c.rolling(5).max()
    new_high10  = c == c.rolling(10).max()

    return pd.DataFrame({
        "close": c,
        # Breakout dasar
        "brk10": brk10, "brk20": brk20, "brk30": brk30, "brk50": brk50,
        # Consolidation breakout
        "consol_brk10": consol_brk10, "consol_brk20": consol_brk20,
        "consol_brk20_mild": consol_brk20_mild, "consol_brk30": consol_brk30,
        # Volume
        "vol12": vol12, "vol15": vol15, "vol20": vol20, "vol25": vol25, "vol_spike": vol_spike,
        # MACD
        "macd_pos": macd_pos, "macd_h_rising": macd_h_rising,
        "macd_cross_up": macd_cross_up, "macd_line_pos": macd_line_pos, "macd_all_pos": macd_all_pos,
        # EMA & Trend
        "above_ema20": above_ema20, "above_ema50": above_ema50, "above_ema200": above_ema200,
        "ema20_x_ema50": ema20_x_ema50, "ema_align_up": ema_align_up,
        # RSI
        "rsi_gt40": rsi_gt40, "rsi_gt45": rsi_gt45, "rsi_gt50": rsi_gt50,
        "rsi_gt55": rsi_gt55, "rsi_lt70": rsi_lt70, "rsi_lt65": rsi_lt65,
        "rsi_rising": rsi_rising,
        # ADX
        "adx_gt20": adx_gt20, "adx_gt25": adx_gt25, "adx_gt30": adx_gt30, "adx_rising": adx_rising,
        # OBV
        "obv_up3": obv_up3, "obv_above_ema": obv_above_ema,
        # Bollinger
        "bb_squeeze": bb_squeeze, "bb_expand": bb_expand, "bb_high": bb_high,
        "bb_brk_upper": bb_brk_upper, "bb_squeeze_brk": bb_squeeze_brk,
        # Candles
        "big_green": big_green, "prev_green": prev_green, "prev_big_green": prev_big_green,
        "gap_up": gap_up,
        # Inside bar
        "inside_brk": inside_brk,
        # Higher High
        "higher_high": higher_high, "new_high5": new_high5, "new_high10": new_high10,
    }, index=df.index).dropna(subset=["brk20", "macd_pos", "vol15"])


def make_conditions(i: pd.DataFrame) -> dict[str, pd.Series]:
    c = i

    return {
        # ================================================================
        # GRUP A: PRICE BREAKOUT DASAR (baseline)
        # ================================================================
        "A1_BRK20":                  c.brk20,
        "A2_BRK20+VOL15":            c.brk20 & c.vol15,
        "A3_BRK20+VOL20":            c.brk20 & c.vol20,
        "A4_BRK20+MACD":             c.brk20 & c.macd_pos,
        "A5_BRK20+MACD+VOL15":       c.brk20 & c.macd_pos & c.vol15,
        "A6_BRK20+MACD+VOL20":       c.brk20 & c.macd_pos & c.vol20,
        "A7_BRK10+MACD+VOL15":       c.brk10 & c.macd_pos & c.vol15,
        "A8_BRK30+MACD+VOL15":       c.brk30 & c.macd_pos & c.vol15,
        "A9_BRK50+MACD+VOL15":       c.brk50 & c.macd_pos & c.vol15,
        "A10_BRK20+MACD+RSI50":      c.brk20 & c.macd_pos & c.rsi_gt50,

        # ================================================================
        # GRUP B: CONSOLIDATION BREAKOUT (kondisi bot saat ini)
        # ================================================================
        "B1_CONSOL_BRK20":                  c.consol_brk20,
        "B2_CONSOL_BRK20+VOL15":            c.consol_brk20 & c.vol15,
        "B3_CONSOL_BRK20+MACD":             c.consol_brk20 & c.macd_pos,
        "B4_CONSOL_BRK20+MACD+VOL15":       c.consol_brk20 & c.macd_pos & c.vol15,   # <-- KONDISI BOT SEKARANG
        "B5_CONSOL_BRK20+MACD+VOL12":       c.consol_brk20 & c.macd_pos & c.vol12,
        "B6_CONSOL_BRK20+MACD+VOL20":       c.consol_brk20 & c.macd_pos & c.vol20,
        "B7_CONSOL_BRK20_MILD+MACD+VOL15":  c.consol_brk20_mild & c.macd_pos & c.vol15,
        "B8_CONSOL_BRK10+MACD+VOL15":       c.consol_brk10 & c.macd_pos & c.vol15,
        "B9_CONSOL_BRK30+MACD+VOL15":       c.consol_brk30 & c.macd_pos & c.vol15,

        # ================================================================
        # GRUP C: BREAKOUT + ADX (konfirmasi kekuatan tren)
        # ================================================================
        "C1_BRK20+ADX20+VOL15":             c.brk20 & c.adx_gt20 & c.vol15,
        "C2_BRK20+ADX25+VOL15":             c.brk20 & c.adx_gt25 & c.vol15,
        "C3_BRK20+ADX20+MACD":              c.brk20 & c.adx_gt20 & c.macd_pos,
        "C4_BRK20+ADX25+MACD+VOL15":        c.brk20 & c.adx_gt25 & c.macd_pos & c.vol15,
        "C5_CONSOL_BRK20+ADX20+MACD+VOL":   c.consol_brk20 & c.adx_gt20 & c.macd_pos & c.vol15,
        "C6_CONSOL_BRK20+ADX25+MACD+VOL":   c.consol_brk20 & c.adx_gt25 & c.macd_pos & c.vol15,
        "C7_BRK20+ADX_RISE+MACD+VOL":       c.brk20 & c.adx_rising & c.macd_pos & c.vol15,
        "C8_CONSOL_BRK20+ADX_RISE+VOL15":   c.consol_brk20 & c.adx_rising & c.vol15,

        # ================================================================
        # GRUP D: BREAKOUT + EMA ALIGNMENT
        # ================================================================
        "D1_BRK20+EMA_ALIGN+VOL15":         c.brk20 & c.ema_align_up & c.vol15,
        "D2_BRK20+EMA_ALIGN+MACD":          c.brk20 & c.ema_align_up & c.macd_pos,
        "D3_BRK20+EMA_ALIGN+MACD+VOL15":    c.brk20 & c.ema_align_up & c.macd_pos & c.vol15,
        "D4_CONSOL_BRK20+EMA_ALIGN+VOL15":  c.consol_brk20 & c.ema_align_up & c.vol15,
        "D5_CONSOL_BRK20+EMA_ALIGN+MACD+V": c.consol_brk20 & c.ema_align_up & c.macd_pos & c.vol15,
        "D6_BRK20+EMA200+MACD+VOL15":       c.brk20 & c.above_ema200 & c.macd_pos & c.vol15,
        "D7_BRK20+EMA20+MACD+VOL15":        c.brk20 & c.above_ema20 & c.macd_pos & c.vol15,

        # ================================================================
        # GRUP E: BREAKOUT + OBV (smart money masuk)
        # ================================================================
        "E1_BRK20+OBV+VOL15":               c.brk20 & c.obv_up3 & c.vol15,
        "E2_BRK20+OBV+MACD+VOL15":          c.brk20 & c.obv_up3 & c.macd_pos & c.vol15,
        "E3_CONSOL_BRK20+OBV+MACD+VOL":     c.consol_brk20 & c.obv_up3 & c.macd_pos & c.vol15,
        "E4_BRK20+OBV_EMA+MACD+VOL15":      c.brk20 & c.obv_above_ema & c.macd_pos & c.vol15,
        "E5_CONSOL_BRK20+OBV_EMA+VOL15":    c.consol_brk20 & c.obv_above_ema & c.vol15,

        # ================================================================
        # GRUP F: BOLLINGER BAND BREAKOUT / SQUEEZE
        # ================================================================
        "F1_BB_BRK_UPPER+VOL15":            c.bb_brk_upper & c.vol15,
        "F2_BB_BRK_UPPER+MACD+VOL15":       c.bb_brk_upper & c.macd_pos & c.vol15,
        "F3_BB_SQUEEZE_BRK+MACD+VOL15":     c.bb_squeeze_brk & c.macd_pos & c.vol15,
        "F4_BB_SQUEEZE_BRK+VOL15":          c.bb_squeeze_brk & c.vol15,
        "F5_BB_SQUEEZE_BRK+CONSOL+VOL":     c.bb_squeeze_brk & c.consol_brk20_mild & c.vol15,
        "F6_BRK20+BB_HIGH+MACD+VOL":        c.brk20 & c.bb_high & c.macd_pos & c.vol15,
        "F7_BB_SQUEEZE_BRK+ADX+VOL":        c.bb_squeeze_brk & c.adx_gt20 & c.vol15,

        # ================================================================
        # GRUP G: RSI MOMENTUM + BREAKOUT
        # ================================================================
        "G1_BRK20+RSI50+MACD+VOL15":        c.brk20 & c.rsi_gt50 & c.macd_pos & c.vol15,
        "G2_BRK20+RSI55+MACD+VOL15":        c.brk20 & c.rsi_gt55 & c.macd_pos & c.vol15,
        "G3_CONSOL+RSI50+MACD+VOL15":       c.consol_brk20 & c.rsi_gt50 & c.macd_pos & c.vol15,
        "G4_BRK20+RSI45+MACD+VOL15":        c.brk20 & c.rsi_gt45 & c.macd_pos & c.vol15,
        "G5_BRK20+RSI_RISE+MACD+VOL":       c.brk20 & c.rsi_rising & c.macd_pos & c.vol15,
        "G6_CONSOL+RSI45+MACD+VOL20":       c.consol_brk20 & c.rsi_gt45 & c.macd_pos & c.vol20,
        "G7_BRK20+RSI50+ADX25+VOL":         c.brk20 & c.rsi_gt50 & c.adx_gt25 & c.vol15,

        # ================================================================
        # GRUP H: MACD VARIANTS
        # ================================================================
        "H1_BRK20+MACD_ALL+VOL15":          c.brk20 & c.macd_all_pos & c.vol15,
        "H2_CONSOL+MACD_ALL+VOL15":         c.consol_brk20 & c.macd_all_pos & c.vol15,
        "H3_BRK20+MACD_CROSS+VOL15":        c.brk20 & c.macd_cross_up & c.vol15,
        "H4_BRK20+MACD_H_RISE+VOL15":       c.brk20 & c.macd_h_rising & c.vol15,
        "H5_CONSOL+MACD_H_RISE+VOL15":      c.consol_brk20 & c.macd_h_rising & c.vol15,
        "H6_BRK20+MACD_ALL+RSI50+VOL":      c.brk20 & c.macd_all_pos & c.rsi_gt50 & c.vol15,
        "H7_CONSOL+MACD_ALL+RSI45+VOL":     c.consol_brk20 & c.macd_all_pos & c.rsi_gt45 & c.vol15,

        # ================================================================
        # GRUP I: CANDLE + GAP
        # ================================================================
        "I1_BRK20+BIG_GREEN+VOL15":         c.brk20 & c.big_green & c.vol15,
        "I2_BRK20+BIG_GREEN+MACD+VOL":      c.brk20 & c.big_green & c.macd_pos & c.vol15,
        "I3_CONSOL+BIG_GREEN+MACD+VOL":     c.consol_brk20 & c.big_green & c.macd_pos & c.vol15,
        "I4_GAP_UP+BRK20+MACD+VOL":         c.gap_up & c.brk20 & c.macd_pos & c.vol15,
        "I5_GAP_UP+CONSOL+MACD+VOL":        c.gap_up & c.consol_brk20 & c.macd_pos & c.vol15,
        "I6_GAP_UP+VOL20+MACD":             c.gap_up & c.vol20 & c.macd_pos,

        # ================================================================
        # GRUP J: VOLUME VARIANTS
        # ================================================================
        "J1_BRK20+VOL_SPIKE+MACD":          c.brk20 & c.vol_spike & c.macd_pos,
        "J2_CONSOL+VOL_SPIKE+MACD":         c.consol_brk20 & c.vol_spike & c.macd_pos,
        "J3_BRK20+VOL25+MACD":              c.brk20 & c.vol25 & c.macd_pos,
        "J4_CONSOL+VOL25+MACD":             c.consol_brk20 & c.vol25 & c.macd_pos,

        # ================================================================
        # GRUP K: MULTI-KONFIRMASI (kombinasi terbaik)
        # ================================================================
        "K1_CONSOL+EMA_ALIGN+MACD+VOL+RSI": c.consol_brk20 & c.ema_align_up & c.macd_pos & c.vol15 & c.rsi_gt45,
        "K2_CONSOL+ADX+EMA+MACD+VOL":       c.consol_brk20 & c.adx_gt20 & c.ema_align_up & c.macd_pos & c.vol15,
        "K3_BRK20+ADX+EMA+MACD+VOL+RSI":    c.brk20 & c.adx_gt25 & c.ema_align_up & c.macd_pos & c.vol15 & c.rsi_gt45,
        "K4_CONSOL+OBV+ADX+MACD+VOL":       c.consol_brk20 & c.obv_up3 & c.adx_gt20 & c.macd_pos & c.vol15,
        "K5_BRK30+EMA_ALIGN+MACD+VOL+RSI":  c.brk30 & c.ema_align_up & c.macd_pos & c.vol15 & c.rsi_gt50,
        "K6_CONSOL+BB_HIGH+EMA+MACD+VOL":   c.consol_brk20 & c.bb_high & c.above_ema20 & c.macd_pos & c.vol15,
        "K7_BRK50+MACD+VOL+RSI50":          c.brk50 & c.macd_pos & c.vol15 & c.rsi_gt50,

        # ================================================================
        # GRUP L: MILD KONDISI (sinyal lebih banyak, WR lebih rendah?)
        # ================================================================
        "L1_BRK10+VOL12+MACD":              c.brk10 & c.vol12 & c.macd_pos,
        "L2_BRK20+VOL12":                   c.brk20 & c.vol12,
        "L3_CONSOL_MILD+MACD+VOL12":        c.consol_brk20_mild & c.macd_pos & c.vol12,
        "L4_BRK10+MACD_H_RISE+VOL15":       c.brk10 & c.macd_h_rising & c.vol15,
        "L5_BRK20+MACD_H_RISE+RSI45":       c.brk20 & c.macd_h_rising & c.rsi_gt45,
    }


# ── Backtest loop ─────────────────────────────────────────────────────────────
MIN_BARS = 60
results: dict[tuple, list] = defaultdict(list)

print("Menghitung indikator dan backtest...", flush=True)
for ticker, df in all_data.items():
    try:
        ind = compute(df)
    except Exception:
        continue

    closes = ind["close"]
    conds  = make_conditions(ind)
    valid  = ind.index[MIN_BARS:]

    for date in valid:
        loc = closes.index.get_loc(date)
        entry = float(closes.iloc[loc])
        if entry <= 0:
            continue

        fc = {h: (float(closes.iloc[loc + h]) if loc + h < len(closes) else None)
              for h in HOLD_DAYS}

        for cname, cseries in conds.items():
            try:
                if bool(cseries.loc[date]):
                    for h in HOLD_DAYS:
                        if fc[h] is not None:
                            results[(cname, h)].append((fc[h] - entry) / entry)
            except Exception:
                continue


# ── Composite score & report ──────────────────────────────────────────────────
summary = []
for (cname, h), pnl_list in results.items():
    if len(pnl_list) < MIN_SIGNALS:
        continue
    arr  = np.array(pnl_list)
    n    = len(arr)
    wr   = np.sum(arr >= WIN_PCT) / n
    ag   = np.mean(arr)
    med  = np.median(arr)
    freq_factor = np.log(n) / np.log(100)
    bal_score = wr * max(ag, 0) * freq_factor if ag > 0 else 0
    summary.append((cname, h, n, wr, ag, med, bal_score))

summary.sort(key=lambda x: -x[6])

# ── Full table ────────────────────────────────────────────────────────────────
print(f"\n{'KONDISI':<42} {'H':>3} {'N':>5} {'WR':>7} {'AVG':>7} {'MED':>7} {'SCORE':>7}")
print("-" * 82)
for cname, h, n, wr, ag, med, sc in summary:
    flag = " ***" if wr >= 0.65 else (" **" if wr >= 0.55 else "")
    print(f"{cname:<42} {h:>2}d {n:>5} {wr*100:>6.1f}% {ag*100:>+6.1f}% {med*100:>+6.1f}% {sc*100:>6.2f}{flag}")

# ── Top 25 by balance score ───────────────────────────────────────────────────
print("\n" + "=" * 85)
print(" TOP 25 BALANCED (score = winrate x avg_gain x log(N)/log(100))")
print(f"  {'KONDISI':<42} {'H':>3} {'N':>5} {'WR':>7} {'AVG':>8} {'SCORE':>7}")
print("  " + "-" * 75)
for cname, h, n, wr, ag, med, sc in summary[:25]:
    bar  = "#" * int(wr * 20)
    flag = " ***" if wr >= 0.65 else (" **" if wr >= 0.55 else "")
    print(f"  {cname:<42} {h:>2}d {n:>5} {wr*100:>6.1f}% {ag*100:>+7.1f}% {sc*100:>6.2f}  {bar}{flag}")

# ── Top per hold period ────────────────────────────────────────────────────────
print("\n" + "=" * 85)
for h in HOLD_DAYS:
    sub = [(c, n, w, a, m, s) for (c, hh, n, w, a, m, s) in summary if hh == h]
    sub.sort(key=lambda x: -x[5])
    print(f"\n TOP 15 BALANCED | Hold {h} hari")
    print(f"  {'KONDISI':<42} {'N':>5} {'WR':>7} {'AVG':>8} {'SCORE':>7}")
    print("  " + "-" * 70)
    for cname, n, wr, ag, med, sc in sub[:15]:
        bar  = "#" * int(wr * 20)
        flag = " ***" if wr >= 0.65 else (" **" if wr >= 0.55 else "")
        print(f"  {cname:<42} {n:>5} {wr*100:>6.1f}% {ag*100:>+7.1f}% {sc*100:>6.2f}  {bar}{flag}")

# ── Sweet spot ────────────────────────────────────────────────────────────────
print("\n" + "=" * 85)
sweet = [(c, h, n, w, a, s) for (c, h, n, w, a, _, s) in summary
         if w >= 0.50 and n >= 30 and a >= 0.02]
if sweet:
    print(f" SWEET SPOT: WR>=50% & N>=30 & AvgGain>=2% ({len(sweet)} kondisi)")
    print(f"  {'KONDISI':<42} {'H':>3} {'N':>5} {'WR':>7} {'AVG':>8} {'SCORE':>7}")
    print("  " + "-" * 72)
    for cname, h, n, wr, ag, sc in sorted(sweet, key=lambda x: -x[3]):
        bar = "#" * int(wr * 20)
        print(f"  {cname:<42} {h:>2}d {n:>5} {wr*100:>6.1f}% {ag*100:>+7.1f}% {sc*100:>6.2f}  {bar}")
else:
    print(" [!] Tidak ada kondisi yang masuk sweet spot (WR>=50%, N>=30, Gain>=2%)")

# ── Kondisi bot saat ini sebagai benchmark ────────────────────────────────────
print("\n" + "=" * 85)
print(" BENCHMARK: Kondisi bot saat ini (B4_CONSOL_BRK20+MACD+VOL15)")
bench = [(c, h, n, w, a, m, s) for (c, h, n, w, a, m, s) in summary if "B4_CONSOL" in c]
if bench:
    for cname, h, n, wr, ag, med, sc in bench:
        print(f"  {cname:<42} {h:>2}d {n:>5} {wr*100:>6.1f}% {ag*100:>+7.1f}% med:{med*100:>+5.1f}% score:{sc*100:.2f}")
else:
    print("  [tidak ada data — sinyal terlalu jarang]")

print("\n=== Selesai ===")
