"""
IHSG Trading System — Masukkan Jurnal Mentor
=============================================
Membaca jurnal harian (teks bebas) lalu mengubahnya menjadi level per
emiten yang bisa disandingkan dengan sinyal sistem.

RAHASIA: seluruh isi data/journal/ masuk .gitignore dan tidak pernah ikut
snapshot yang terbit ke GitHub Pages.

Cara pakai:
    # tempel jurnal ke sebuah berkas lalu:
    python ingest_journal.py jurnal.txt

    # atau tempel langsung lewat stdin, akhiri dengan Ctrl-Z (Windows):
    python ingest_journal.py

    # tanggal ditentukan sendiri (default: hari ini WIB)
    python ingest_journal.py jurnal.txt --date 2026-09-03

Setelah masuk, segarkan papan lokal:
    python run_job.py --job dashboard --with-journal
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

WIB = ZoneInfo("Asia/Jakarta")


def main() -> None:
    ap = argparse.ArgumentParser(description="Masukkan jurnal mentor ke sistem")
    ap.add_argument("file", nargs="?", help="berkas teks jurnal (kosong = baca stdin)")
    ap.add_argument("--date", help="tanggal jurnal YYYY-MM-DD (default: hari ini WIB)")
    ap.add_argument("--dry-run", action="store_true",
                    help="tampilkan hasil baca tanpa menyimpan")
    args = ap.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        print("Tempel jurnal, akhiri dengan Ctrl-Z lalu Enter (Windows):\n")
        text = sys.stdin.read()

    if not text.strip():
        sys.exit("Jurnal kosong.")

    date_str = args.date or datetime.now(WIB).strftime("%Y-%m-%d")

    from utils.journal import split_blocks, parse_tickers, parse_macro, ingest, RAW_DIR

    if args.dry_run:
        head, blocks = split_blocks(text)
        print(f"\n{len(blocks)} blok emiten: {', '.join(c for c, _ in blocks)}\n")
        for n in parse_tickers(blocks):
            print(f"{n.ticker:6} {n.stance:9} entry={n.entries} "
                  f"target={n.targets} stop={n.stop} ({n.stop_type})")
        print("\nMakro:", parse_macro(head) if head else "(tidak ada)")
        return

    # Simpan teks aslinya juga — kalau pembacaan LLM meleset, sumbernya ada
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{date_str}.txt").write_text(text, encoding="utf-8")

    data = ingest(text, date_str)
    n_tick = len(data["tickers"])
    print(f"\nSelesai. {n_tick} emiten tersimpan untuk {date_str}.")
    print("Segarkan papan lokal:  python run_job.py --job dashboard --with-journal")


if __name__ == "__main__":
    main()
