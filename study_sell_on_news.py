"""
Studi: benarkah laporan bagus justru dijual? ("sell on news")
=============================================================
Menguji dugaan bahwa di IHSG laporan keuangan yang melonjak sering jadi
kesempatan pemain besar melepas barang, sehingga harga malah turun.

Datanya nyata dan lengkap: yfinance memuat EPS Estimate, Reported EPS,
dan Surprise(%) untuk ~25 kuartal per emiten — jadi ini bukan soal
pendapat, tapi bisa dihitung.

Yang diukur untuk tiap pengumuman laporan:
  - kejutan (%)            : realisasi vs konsensus
  - lari sebelum rilis     : return t-20 s/d t-1 (sudah "priced in"?)
  - reaksi sesudah rilis   : return t+1, t+5, t+10 dari harga t+1 open

Entry sengaja dipasang di OPEN hari SETELAH pengumuman — itu waktu
paling awal yang realistis bagi orang yang membaca berita paginya.

Usage:
    python study_sell_on_news.py
    python study_sell_on_news.py --years 8
"""
from __future__ import annotations

import argparse
import sys
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

from config import DEFAULT_TICKERS

HOLD = [1, 5, 10]
PRE_WINDOW = 20


def pct(a: float, b: float) -> float | None:
    if not all(isinstance(x, (int, float)) and x == x for x in (a, b)) or b == 0:
        return None
    return (a - b) / abs(b) * 100


def study(years: int) -> list[dict]:
    events: list[dict] = []

    for i, tj in enumerate(DEFAULT_TICKERS, 1):
        code = tj.replace(".JK", "")
        try:
            tk = yf.Ticker(tj)
            ed = tk.earnings_dates
            if ed is None or ed.empty or "Surprise(%)" not in ed.columns:
                continue

            px = tk.history(period=f"{years}y", auto_adjust=True)
            px = px[px["Close"].notna()]
            if len(px) < 100:
                continue
            idx = px.index.tz_localize(None).normalize()
            closes = px["Close"].to_numpy()
            opens = px["Open"].to_numpy()

            for ts, row in ed.iterrows():
                sur = row.get("Surprise(%)")
                if not isinstance(sur, (int, float)) or sur != sur:
                    continue
                day = pd.Timestamp(ts).tz_localize(None).normalize()

                # bar pertama SETELAH tanggal pengumuman
                after = np.searchsorted(idx.to_numpy(), day.to_numpy(), side="right")
                if after < PRE_WINDOW + 1 or after >= len(closes) - max(HOLD):
                    continue

                entry = float(opens[after])
                if entry <= 0:
                    continue

                pre = pct(float(closes[after - 1]), float(closes[after - 1 - PRE_WINDOW]))
                if pre is None:
                    continue

                ev = {
                    "ticker": code,
                    "date": day.date().isoformat(),
                    "surprise": float(sur),
                    "pre": pre,
                }
                for h in HOLD:
                    ev[f"r{h}"] = (float(closes[after + h - 1]) - entry) / entry * 100
                events.append(ev)

        except Exception:
            continue

        if i % 15 == 0:
            print(f"  ...{i}/{len(DEFAULT_TICKERS)} emiten", flush=True)

    return events


def summarise(rows: list[dict], label: str) -> None:
    if not rows:
        print(f"  {label:34} (kosong)")
        return
    line = f"  {label:34} n={len(rows):>4}"
    for h in HOLD:
        r = np.array([x[f"r{h}"] for x in rows])
        line += f"   t+{h}: {r.mean():+6.2f}% ({(r > 0).mean() * 100:4.0f}% naik)"
    print(line)


def main() -> None:
    ap = argparse.ArgumentParser(description="Studi sell on news di IHSG")
    ap.add_argument("--years", type=int, default=8)
    args = ap.parse_args()

    print("=" * 96)
    print(" STUDI: apa yang terjadi SETELAH laporan keuangan dirilis?")
    print("=" * 96)
    print(f" Universe : {len(DEFAULT_TICKERS)} saham watchlist")
    print(f" Riwayat  : {args.years} tahun")
    print(" Entry    : open hari bursa pertama SETELAH tanggal pengumuman")
    print(" Kejutan  : Reported EPS vs EPS Estimate (konsensus yfinance)")
    print("=" * 96)
    print("\nMengumpulkan...", flush=True)

    rows = study(args.years)
    if not rows:
        sys.exit("Tidak ada event yang bisa diukur.")

    print(f"\n{len(rows)} pengumuman terkumpul dari "
          f"{len(set(r['ticker'] for r in rows))} emiten\n")

    print("─" * 96)
    print(" A. Dikelompokkan menurut BESAR KEJUTAN")
    print("─" * 96)
    buckets = [
        ("Kejutan besar  (>  +20%)", lambda r: r["surprise"] > 20),
        ("Kejutan positif (0..+20%)", lambda r: 0 < r["surprise"] <= 20),
        ("Meleset kecil  (-20..0%)", lambda r: -20 <= r["surprise"] <= 0),
        ("Meleset besar  (< -20%)", lambda r: r["surprise"] < -20),
    ]
    for name, f in buckets:
        summarise([r for r in rows if f(r)], name)
    summarise(rows, "SEMUA pengumuman")

    print()
    print("─" * 96)
    print(" B. Kejutan POSITIF, dipilah menurut apakah harga SUDAH lari duluan")
    print("    (hipotesis: kabar baik yang sudah 'priced in' justru dijual)")
    print("─" * 96)
    good = [r for r in rows if r["surprise"] > 0]
    pre_buckets = [
        ("sudah naik > +15% sebelum rilis", lambda r: r["pre"] > 15),
        ("naik +5..+15% sebelum rilis",     lambda r: 5 < r["pre"] <= 15),
        ("datar -5..+5% sebelum rilis",     lambda r: -5 <= r["pre"] <= 5),
        ("turun < -5% sebelum rilis",       lambda r: r["pre"] < -5),
    ]
    for name, f in pre_buckets:
        summarise([r for r in good if f(r)], name)

    print()
    print("─" * 96)
    print(" C. Pembanding: kejutan NEGATIF yang harganya sudah turun duluan")
    print("─" * 96)
    bad = [r for r in rows if r["surprise"] <= 0]
    for name, f in pre_buckets:
        summarise([r for r in bad if f(r)], name)

    print()
    print("─" * 96)
    print(" D. Korelasi")
    print("─" * 96)
    sur = np.array([r["surprise"] for r in rows])
    pre = np.array([r["pre"] for r in rows])
    for h in HOLD:
        r = np.array([r[f"r{h}"] for r in rows])
        print(f"  kejutan vs return t+{h:<2} : {np.corrcoef(sur, r)[0,1]:+.3f}"
              f"    lari-sebelum vs return t+{h:<2} : {np.corrcoef(pre, r)[0,1]:+.3f}")

    print("\n=== Selesai ===")


if __name__ == "__main__":
    main()
