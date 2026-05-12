"""
IHSG Trading System -- Cloud Job Runner
========================================
Digunakan oleh GitHub Actions / Railway untuk menjalankan
satu job tertentu berdasarkan argumen, lalu keluar.

Usage:
    python run_job.py --job macro
    python run_job.py --job premarket
    python run_job.py --job full_scan
    python run_job.py --job quick_scan
    python run_job.py --job aftermarket
"""
from __future__ import annotations

import argparse
import io
import logging
import sys

# Force UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("JobRunner")

VALID_JOBS = ["macro", "premarket", "full_scan", "quick_scan", "aftermarket", "sentiment", "fundamental_weekly", "supervisor"]


def main() -> None:
    parser = argparse.ArgumentParser(description="IHSG Single Job Runner")
    parser.add_argument(
        "--job", required=True, choices=VALID_JOBS,
        help=f"Job to run: {', '.join(VALID_JOBS)}"
    )
    args = parser.parse_args()

    # Import scheduler functions
    from scheduler import (
        run_macro, run_premarket,
        run_full_scan, run_quick_scan, run_aftermarket,
        run_sentiment_scan, run_fundamental_weekly, run_supervisor_closing,
    )

    job_map = {
        "macro":               run_macro,
        "premarket":           run_premarket,
        "full_scan":           run_full_scan,
        "quick_scan":          run_quick_scan,
        "aftermarket":         run_aftermarket,
        "sentiment":           run_sentiment_scan,
        "fundamental_weekly":  run_fundamental_weekly,
        "supervisor":          run_supervisor_closing,
    }

    logger.info(f"Menjalankan job: {args.job}")
    job_map[args.job]()
    logger.info(f"Job selesai: {args.job}")


if __name__ == "__main__":
    main()
