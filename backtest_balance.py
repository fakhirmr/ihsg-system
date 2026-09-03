"""
Backtest: Cari balance antara winrate, avg gain, dan frekuensi sinyal.
Composite score = winrate * avg_gain * log(n) / log(30)
Fokus: variasi kondisi buy on weakness + momentum reversal.
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

HOLD_DAYS   = [5, 10]   # fokus ke hold yang lebih bermakna
WIN_PCT     = 0.02
MIN_SIGNALS = 15         # minimum sinyal agar dianggap reliable
LOOKBACK    = 400

end   = datetime.today()
start = end - timedelta(days=LOOKBACK + 30)

print("=== Backtest Balance: Winrate x Gain x Frekuensi ===")
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
    o  = df["Open"].squeeze()

    # EMA
    ema20  = c.ewm(span=20,  adjust=False).mean()
    ema50  = c.ewm(span=50,  adjust=False).mean()

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    # Stochastic %K (14,3)
    low14   = lo.rolling(14).min()
    high14  = h.rolling(14).max()
    stoch_k = 100 * (c - low14) / (high14 - low14).replace(0, np.nan)
    stoch_d = stoch_k.rolling(3).mean()
    stoch_cross_up = (stoch_k > stoch_d) & (stoch_k.shift(1) <= stoch_d.shift(1))

    # MACD
    ema12  = c.ewm(span=12, adjust=False).mean()
    ema26  = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_s = macd_l.ewm(span=9, adjust=False).mean()
    macd_h = macd_l - macd_s
    macd_h_rising   = macd_h > macd_h.shift(1)
    macd_h_rising2  = macd_h_rising & (macd_h.shift(1) > macd_h.shift(2))  # 2 bar naik
    macd_neg_rising = (macd_h < 0) & macd_h_rising
    macd_cross_up   = (macd_l > macd_s) & (macd_l.shift(1) <= macd_s.shift(1))

    # Bollinger Bands
    sma20  = c.rolling(20).mean()
    std20  = c.rolling(20).std()
    bb_lo  = sma20 - 2 * std20
    bb_up  = sma20 + 2 * std20
    bb_pct = (c - bb_lo) / (bb_up - bb_lo).replace(0, np.nan)

    # ATR
    tr    = pd.concat([(h-lo), (h-c.shift()).abs(), (lo-c.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()

    # OBV + slope
    obv      = (v.where(c > c.shift(1), 0) - v.where(c < c.shift(1), 0)).cumsum()
    obv_up3  = (obv > obv.shift(1)) & (obv.shift(1) > obv.shift(2)) & (obv.shift(2) > obv.shift(3))
    obv_up5  = obv > obv.shift(5)

    # Volume
    avg_vol  = v.rolling(20).mean()
    rel_vol  = v / avg_vol.replace(0, np.nan)
    vol15    = rel_vol >= 1.5
    vol20    = rel_vol >= 2.0

    # ADX & DI
    up_m  = h - h.shift()
    dn_m  = lo.shift() - lo
    pdm   = ((up_m > dn_m) & (up_m > 0)) * up_m
    ndm   = ((dn_m > up_m) & (dn_m > 0)) * dn_m
    pdi   = 100 * pdm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    ndi   = 100 * ndm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    bull_di = pdi > ndi   # +DI baru melewati -DI = bullish reversal

    # Williams %R
    willr  = -100 * (high14 - c) / (high14 - low14).replace(0, np.nan)
    willr_rising = willr > willr.shift(1)

    # Consecutive down days
    dn = c < c.shift(1)
    down2 = dn & dn.shift(1)
    down3 = down2 & dn.shift(2)
    down4 = down3 & dn.shift(3)
    down5 = down4 & dn.shift(4)

    # Candles
    green   = c > o
    red     = c < o
    big_red = red & ((o - c) / o > 0.02)    # red candle > 2% body

    # RSI thresholds
    rsi_lt25 = rsi < 25
    rsi_lt30 = rsi < 30
    rsi_lt35 = rsi < 35
    rsi_lt40 = rsi < 40
    rsi_lt45 = rsi < 45
    rsi_lt50 = rsi < 50
    rsi_rising = rsi > rsi.shift(1)
    rsi_rising2 = rsi_rising & (rsi.shift(1) > rsi.shift(2))

    # Bollinger thresholds
    bb_lt10  = bb_pct < 0.10
    bb_lt20  = bb_pct < 0.20
    bb_lt30  = bb_pct < 0.30
    bb_touch = bb_pct < 0.05

    # Price vs EMA
    below_ema20  = c < ema20
    below_ema50  = c < ema50
    pct_below_e20 = (ema20 - c) / ema20
    ema20_gap3   = pct_below_e20 > 0.03
    ema20_gap5   = pct_below_e20 > 0.05

    # Capitulation: big down + spike volume + kemudian green
    capit_vol    = big_red.shift(1) & vol20.shift(1)
    capit_reversal = capit_vol & green

    # Mean reversion: momentum mulai balik setelah oversold
    # Kunci: semua indicator oversold PLUS sinyal awal reversal
    rsi_os_turn  = rsi_lt35 & rsi_rising   # oversold + naik
    stoch_os_turn = (stoch_k < 25) & stoch_cross_up  # stoch OS + cross up
    macd_os_turn  = macd_neg_rising & rsi_lt40        # MACD membalik saat RSI masih rendah

    # Support bounce: low30 area
    low30 = c.rolling(30).min()
    near_low30 = c < low30.shift(1) * 1.03

    return pd.DataFrame({
        "close": c,
        # Down days
        "down2": down2, "down3": down3, "down4": down4, "down5": down5,
        # RSI
        "rsi_lt25": rsi_lt25, "rsi_lt30": rsi_lt30,
        "rsi_lt35": rsi_lt35, "rsi_lt40": rsi_lt40,
        "rsi_lt45": rsi_lt45, "rsi_lt50": rsi_lt50,
        "rsi_rising": rsi_rising, "rsi_rising2": rsi_rising2,
        "rsi_os_turn": rsi_os_turn,
        # MACD
        "macd_h_rising": macd_h_rising, "macd_h_rising2": macd_h_rising2,
        "macd_neg_rising": macd_neg_rising, "macd_cross_up": macd_cross_up,
        "macd_os_turn": macd_os_turn,
        # Stochastic
        "stoch_lt20": stoch_k < 20, "stoch_lt25": stoch_k < 25,
        "stoch_lt30": stoch_k < 30, "stoch_cross_up": stoch_cross_up,
        "stoch_os_turn": stoch_os_turn,
        # Bollinger
        "bb_touch": bb_touch, "bb_lt10": bb_lt10,
        "bb_lt20": bb_lt20, "bb_lt30": bb_lt30,
        # Williams
        "willr_os": willr < -80, "willr_lt70": willr < -70,
        "willr_rising": willr_rising,
        # Volume
        "vol15": vol15, "vol20": vol20,
        # OBV
        "obv_up3": obv_up3, "obv_up5": obv_up5,
        # DI
        "bull_di": bull_di,
        # Candles
        "green": green, "big_red_prev": big_red.shift(1),
        "capit_reversal": capit_reversal,
        # EMA
        "below_ema20": below_ema20, "below_ema50": below_ema50,
        "ema20_gap3": ema20_gap3, "ema20_gap5": ema20_gap5,
        # Near low
        "near_low30": near_low30,
    }, index=df.index).dropna(subset=["rsi_lt35", "macd_neg_rising", "stoch_lt20", "bb_lt10"])


def make_conditions(i: pd.DataFrame) -> dict[str, pd.Series]:
    c = i

    return {
        # ═══════════════════════════════════════════════════════════════════
        # GRUP A: DOWN N DAYS + MACD TURN (baseline terbaik dari v1)
        # ═══════════════════════════════════════════════════════════════════
        "A1_DOWN2+MACD_TURN":            c.down2 & c.macd_neg_rising,
        "A2_DOWN3+MACD_TURN":            c.down3 & c.macd_neg_rising,
        "A3_DOWN4+MACD_TURN":            c.down4 & c.macd_neg_rising,
        "A4_DOWN5+MACD_TURN":            c.down5 & c.macd_neg_rising,

        # + RSI threshold
        "A5_DOWN2+MACD+RSI40":           c.down2 & c.macd_neg_rising & c.rsi_lt40,
        "A6_DOWN3+MACD+RSI40":           c.down3 & c.macd_neg_rising & c.rsi_lt40,
        "A7_DOWN3+MACD+RSI35":           c.down3 & c.macd_neg_rising & c.rsi_lt35,
        "A8_DOWN3+MACD+RSI45":           c.down3 & c.macd_neg_rising & c.rsi_lt45,
        "A9_DOWN4+MACD+RSI40":           c.down4 & c.macd_neg_rising & c.rsi_lt40,
        "A10_DOWN4+MACD+RSI35":          c.down4 & c.macd_neg_rising & c.rsi_lt35,
        "A11_DOWN5+MACD+RSI40":          c.down5 & c.macd_neg_rising & c.rsi_lt40,

        # + Green candle confirmation
        "A12_DOWN2+MACD+GREEN":          c.down2 & c.macd_neg_rising & c.green,
        "A13_DOWN3+MACD+GREEN":          c.down3 & c.macd_neg_rising & c.green,
        "A14_DOWN3+MACD+RSI40+GREEN":    c.down3 & c.macd_neg_rising & c.rsi_lt40 & c.green,
        "A15_DOWN3+MACD+RSI35+GREEN":    c.down3 & c.macd_neg_rising & c.rsi_lt35 & c.green,

        # + Bollinger
        "A16_DOWN3+MACD+BB20":           c.down3 & c.macd_neg_rising & c.bb_lt20,
        "A17_DOWN3+MACD+RSI40+BB20":     c.down3 & c.macd_neg_rising & c.rsi_lt40 & c.bb_lt20,
        "A18_DOWN3+MACD+RSI35+BB20":     c.down3 & c.macd_neg_rising & c.rsi_lt35 & c.bb_lt20,

        # + OBV (smart money mulai akumulasi)
        "A19_DOWN3+MACD+OBV":            c.down3 & c.macd_neg_rising & c.obv_up3,
        "A20_DOWN3+MACD+RSI40+OBV":      c.down3 & c.macd_neg_rising & c.rsi_lt40 & c.obv_up3,
        "A21_DOWN2+MACD+OBV+RSI45":      c.down2 & c.macd_neg_rising & c.obv_up3 & c.rsi_lt45,

        # + Volume spike (capitulation beli)
        "A22_DOWN3+MACD+VOL":            c.down3 & c.macd_neg_rising & c.vol15,
        "A23_DOWN3+MACD+RSI40+VOL":      c.down3 & c.macd_neg_rising & c.rsi_lt40 & c.vol15,

        # ═══════════════════════════════════════════════════════════════════
        # GRUP B: RSI OVERSOLD + MOMENTUM REVERSAL
        # ═══════════════════════════════════════════════════════════════════
        "B1_RSI<35+MACD_TURN":           c.rsi_lt35 & c.macd_neg_rising,
        "B2_RSI<35+MACD_TURN+GREEN":     c.rsi_lt35 & c.macd_neg_rising & c.green,
        "B3_RSI<35+MACD_TURN+OBV":       c.rsi_lt35 & c.macd_neg_rising & c.obv_up3,
        "B4_RSI<35+MACD_TURN+STOCH":     c.rsi_lt35 & c.macd_neg_rising & c.stoch_lt30,
        "B5_RSI<35+MACD_TURN+BB20":      c.rsi_lt35 & c.macd_neg_rising & c.bb_lt20,
        "B6_RSI<35+MACD+OBV+GREEN":      c.rsi_lt35 & c.macd_neg_rising & c.obv_up3 & c.green,
        "B7_RSI<35+MACD+BB+GREEN":       c.rsi_lt35 & c.macd_neg_rising & c.bb_lt20 & c.green,
        "B8_RSI<35+MACD+BB+OBV":         c.rsi_lt35 & c.macd_neg_rising & c.bb_lt20 & c.obv_up3,
        "B9_RSI<40+MACD_TURN+OBV+GREEN": c.rsi_lt40 & c.macd_neg_rising & c.obv_up3 & c.green,
        "B10_RSI<40+MACD+BB20+OBV":      c.rsi_lt40 & c.macd_neg_rising & c.bb_lt20 & c.obv_up3,
        "B11_RSI<30+MACD_TURN":          c.rsi_lt30 & c.macd_neg_rising,
        "B12_RSI<30+MACD+GREEN":         c.rsi_lt30 & c.macd_neg_rising & c.green,
        "B13_RSI<30+MACD+OBV":           c.rsi_lt30 & c.macd_neg_rising & c.obv_up3,
        "B14_RSI<30+MACD+BB+GREEN":      c.rsi_lt30 & c.macd_neg_rising & c.bb_lt20 & c.green,

        # ═══════════════════════════════════════════════════════════════════
        # GRUP C: STOCHASTIC REVERSAL
        # ═══════════════════════════════════════════════════════════════════
        "C1_STOCH_CROSS+RSI40":          c.stoch_cross_up & c.rsi_lt40,
        "C2_STOCH_CROSS+RSI35":          c.stoch_cross_up & c.rsi_lt35,
        "C3_STOCH_CROSS+MACD+RSI40":     c.stoch_cross_up & c.macd_neg_rising & c.rsi_lt40,
        "C4_STOCH_CROSS+BB20+RSI40":     c.stoch_cross_up & c.bb_lt20 & c.rsi_lt40,
        "C5_STOCH_CROSS+OBV+RSI40":      c.stoch_cross_up & c.obv_up3 & c.rsi_lt40,
        "C6_STOCH_CROSS+MACD+OBV":       c.stoch_cross_up & c.macd_neg_rising & c.obv_up3,
        "C7_STOCH_CROSS+ALL":            c.stoch_cross_up & c.macd_neg_rising & c.rsi_lt40 & c.obv_up3,
        "C8_STOCH_OS_TURN+MACD":         c.stoch_os_turn & c.macd_neg_rising,
        "C9_STOCH_OS_TURN+MACD+OBV":     c.stoch_os_turn & c.macd_neg_rising & c.obv_up3,
        "C10_STOCH_OS_TURN+RSI+OBV":     c.stoch_os_turn & c.rsi_lt40 & c.obv_up3,

        # ═══════════════════════════════════════════════════════════════════
        # GRUP D: OBV ACCUMULATION (smart money saat harga lemah)
        # ═══════════════════════════════════════════════════════════════════
        "D1_BELOW_EMA+OBV3+MACD":        c.below_ema20 & c.obv_up3 & c.macd_neg_rising,
        "D2_BELOW_EMA+OBV3+RSI40":       c.below_ema20 & c.obv_up3 & c.rsi_lt40,
        "D3_BELOW_EMA+OBV3+RSI35":       c.below_ema20 & c.obv_up3 & c.rsi_lt35,
        "D4_BELOW_EMA+OBV5+RSI40":       c.below_ema20 & c.obv_up5 & c.rsi_lt40,
        "D5_BELOW_EMA+OBV3+MACD+RSI40":  c.below_ema20 & c.obv_up3 & c.macd_neg_rising & c.rsi_lt40,
        "D6_BELOW_EMA+OBV3+MACD+BB20":   c.below_ema20 & c.obv_up3 & c.macd_neg_rising & c.bb_lt20,
        "D7_GAP_EMA5+OBV+MACD":          c.ema20_gap5 & c.obv_up3 & c.macd_neg_rising,
        "D8_GAP_EMA5+OBV+RSI40":         c.ema20_gap5 & c.obv_up3 & c.rsi_lt40,
        "D9_GAP_EMA3+OBV+MACD+RSI40":    c.ema20_gap3 & c.obv_up3 & c.macd_neg_rising & c.rsi_lt40,

        # ═══════════════════════════════════════════════════════════════════
        # GRUP E: CAPITULATION + REVERSAL
        # ═══════════════════════════════════════════════════════════════════
        "E1_CAPIT+GREEN":                c.capit_reversal,
        "E2_CAPIT+RSI40":                c.capit_reversal & c.rsi_lt40,
        "E3_CAPIT+MACD":                 c.capit_reversal & c.macd_neg_rising,
        "E4_CAPIT+RSI+MACD":             c.capit_reversal & c.rsi_lt40 & c.macd_neg_rising,
        "E5_CAPIT+BB20+RSI":             c.capit_reversal & c.bb_lt20 & c.rsi_lt40,
        "E6_CAPIT+OBV+RSI":              c.capit_reversal & c.obv_up3 & c.rsi_lt40,

        # ═══════════════════════════════════════════════════════════════════
        # GRUP F: MULTI-INDICATOR SWEET SPOT
        # ═══════════════════════════════════════════════════════════════════
        "F1_RSI_OS_TURN+STOCH_TURN":     c.rsi_os_turn & c.stoch_cross_up,
        "F2_RSI_OS_TURN+MACD+OBV":       c.rsi_os_turn & c.macd_neg_rising & c.obv_up3,
        "F3_RSI_OS_TURN+MACD+BB20":      c.rsi_os_turn & c.macd_neg_rising & c.bb_lt20,
        "F4_RSI_OS_TURN+STOCH+MACD":     c.rsi_os_turn & c.stoch_cross_up & c.macd_neg_rising,
        "F5_RSI_OS_TURN+STOCH+OBV":      c.rsi_os_turn & c.stoch_cross_up & c.obv_up3,
        "F6_MACD_CROSS+RSI40+OBV":       c.macd_cross_up & c.rsi_lt40 & c.obv_up3,
        "F7_MACD_CROSS+DOWN3+RSI40":     c.macd_cross_up & c.down3 & c.rsi_lt40,
        "F8_WILLR_OS+MACD+RSI":          (c.willr_os) & c.macd_neg_rising & c.rsi_lt40,
        "F9_WILLR_OS+MACD+OBV":          (c.willr_os) & c.macd_neg_rising & c.obv_up3,
        "F10_BB_TOUCH+MACD+RSI40":       c.bb_lt10 & c.macd_neg_rising & c.rsi_lt40,
        "F11_BB_TOUCH+RSI+GREEN":        c.bb_lt10 & c.rsi_lt35 & c.green,
        "F12_BB_TOUCH+STOCH+MACD":       c.bb_lt10 & c.stoch_cross_up & c.macd_neg_rising,
        "F13_NEAR_LOW+MACD+RSI40":       c.near_low30 & c.macd_neg_rising & c.rsi_lt40,
        "F14_NEAR_LOW+OBV+RSI40":        c.near_low30 & c.obv_up3 & c.rsi_lt40,
        "F15_NEAR_LOW+MACD+OBV+RSI":     c.near_low30 & c.macd_neg_rising & c.obv_up3 & c.rsi_lt40,

        # ═══════════════════════════════════════════════════════════════════
        # GRUP G: KOMBINASI DARI GRUP TERBAIK + VARIASI TAMBAHAN
        # ═══════════════════════════════════════════════════════════════════
        "G1_DOWN3+MACD+STOCH+RSI40":     c.down3 & c.macd_neg_rising & c.stoch_lt30 & c.rsi_lt40,
        "G2_DOWN3+MACD+WILLR+RSI40":     c.down3 & c.macd_neg_rising & c.willr_lt70 & c.rsi_lt40,
        "G3_DOWN3+MACD+OBV+BB20":        c.down3 & c.macd_neg_rising & c.obv_up3 & c.bb_lt20,
        "G4_DOWN3+MACD+OBV+BB+RSI":      c.down3 & c.macd_neg_rising & c.obv_up3 & c.bb_lt20 & c.rsi_lt40,
        "G5_DOWN3+MACD2+RSI40":          c.down3 & c.macd_h_rising2 & c.rsi_lt40,
        "G6_DOWN3+MACD2+OBV":            c.down3 & c.macd_h_rising2 & c.obv_up3,
        "G7_DOWN2+MACD+RSI35+OBV":       c.down2 & c.macd_neg_rising & c.rsi_lt35 & c.obv_up3,
        "G8_DOWN2+MACD+BB10+RSI35":      c.down2 & c.macd_neg_rising & c.bb_lt10 & c.rsi_lt35,
        "G9_DOWN3+MACD+BULL_DI":         c.down3 & c.macd_neg_rising & c.bull_di,
        "G10_DOWN3+MACD+BULL_DI+RSI40":  c.down3 & c.macd_neg_rising & c.bull_di & c.rsi_lt40,
        "G11_DOWN2+RSI35+MACD+GREEN":    c.down2 & c.rsi_lt35 & c.macd_neg_rising & c.green,
        "G12_DOWN2+RSI40+OBV+MACD+BB":   c.down2 & c.rsi_lt40 & c.obv_up3 & c.macd_neg_rising & c.bb_lt20,
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

        fc = {h: (float(closes.iloc[loc+h]) if loc+h < len(closes) else None)
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
    # Composite: winrate * avg_gain * frekuensi_factor
    # Frekuensi factor: log scale, puncak di 100 sinyal
    freq_factor = np.log(n) / np.log(100)
    # Hanya hitung jika avg_gain positif
    bal_score = wr * max(ag, 0) * freq_factor if ag > 0 else 0
    summary.append((cname, h, n, wr, ag, med, bal_score))

summary.sort(key=lambda x: -x[6])  # sort by balance score

# ── Full table ────────────────────────────────────────────────────────────────
print(f"\n{'KONDISI':<38} {'H':>3} {'N':>5} {'WR':>7} {'AVG':>7} {'MED':>7} {'SCORE':>7}")
print("-" * 80)
for cname, h, n, wr, ag, med, sc in summary:
    flag = " ***" if wr >= 0.65 else (" **" if wr >= 0.55 else "")
    print(f"{cname:<38} {h:>2}d {n:>5} {wr*100:>6.1f}% {ag*100:>+6.1f}% {med*100:>+6.1f}% {sc*100:>6.2f}{flag}")

# ── Top 20 by balance score ───────────────────────────────────────────────────
print("\n" + "="*80)
print(" TOP 20 BALANCED (score = winrate x avg_gain x log(N)/log(100))")
print(f"  {'KONDISI':<38} {'H':>3} {'N':>5} {'WR':>7} {'AVG':>8} {'SCORE':>7}")
print("  " + "-"*70)
for cname, h, n, wr, ag, med, sc in summary[:20]:
    bar  = "#" * int(wr * 20)
    flag = " ***" if wr >= 0.65 else (" **" if wr >= 0.55 else "")
    print(f"  {cname:<38} {h:>2}d {n:>5} {wr*100:>6.1f}% {ag*100:>+7.1f}% {sc*100:>6.2f}  {bar}{flag}")

# ── Top per hold period ────────────────────────────────────────────────────────
print("\n" + "="*80)
for h in HOLD_DAYS:
    sub = [(c, n, w, a, m, s) for (c, hh, n, w, a, m, s) in summary if hh == h]
    sub.sort(key=lambda x: -x[5])
    print(f"\n TOP 15 BALANCED | Hold {h} hari")
    print(f"  {'KONDISI':<38} {'N':>5} {'WR':>7} {'AVG':>8} {'SCORE':>7}")
    print("  " + "-"*65)
    for cname, n, wr, ag, med, sc in sub[:15]:
        bar  = "#" * int(wr * 20)
        flag = " ***" if wr >= 0.65 else (" **" if wr >= 0.55 else "")
        print(f"  {cname:<38} {n:>5} {wr*100:>6.1f}% {ag*100:>+7.1f}% {sc*100:>6.2f}  {bar}{flag}")

# ── Sweet spot: WR >= 50%, N >= 30, avg_gain >= 2% ────────────────────────────
print("\n" + "="*80)
sweet = [(c, h, n, w, a, s) for (c, h, n, w, a, _, s) in summary
         if w >= 0.50 and n >= 30 and a >= 0.02]
if sweet:
    print(f" SWEET SPOT: WR>=50% & N>=30 & AvgGain>=2% ({len(sweet)} kondisi)")
    print(f"  {'KONDISI':<38} {'H':>3} {'N':>5} {'WR':>7} {'AVG':>8} {'SCORE':>7}")
    print("  " + "-"*68)
    for cname, h, n, wr, ag, sc in sorted(sweet, key=lambda x: -x[3]):
        bar = "#" * int(wr * 20)
        print(f"  {cname:<38} {h:>2}d {n:>5} {wr*100:>6.1f}% {ag*100:>+7.1f}% {sc*100:>6.2f}  {bar}")
else:
    print(" [!] Tidak ada kondisi yang masuk sweet spot (WR>=50%, N>=30, Gain>=2%)")

print("\n=== Selesai ===")
