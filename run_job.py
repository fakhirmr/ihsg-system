"""
IHSG Trading System -- Cloud Job Runner
========================================
Digunakan oleh GitHub Actions / Railway untuk menjalankan
satu job tertentu berdasarkan argumen, lalu keluar.

Usage:
    python run_job.py --job macro
    python run_job.py --job technical
    python run_job.py --job sentiment
    python run_job.py --job fundamental_weekly
    python run_job.py --job supervisor
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

VALID_JOBS = ["macro", "technical", "sentiment", "fundamental_weekly", "supervisor"]

_JOB_FN = {
    "macro":              "run_macro",
    "technical":          "run_technical_volume",
    "sentiment":          "run_sentiment_scan",
    "fundamental_weekly": "run_fundamental_weekly",
    "supervisor":         "run_supervisor_closing",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="IHSG Single Job Runner")
    parser.add_argument(
        "--job", required=True, choices=VALID_JOBS,
        help=f"Job to run: {', '.join(VALID_JOBS)}"
    )
    args = parser.parse_args()

    import scheduler as _sched

    fn_name = _JOB_FN[args.job]

    fn = getattr(_sched, fn_name, None)
    if fn is None:
        raise NotImplementedError(f"Fungsi '{fn_name}' belum ada di scheduler.py")

    logger.info(f"Menjalankan job: {args.job}")
    fn()
    logger.info(f"Job selesai: {args.job}")


if __name__ == "__main__":
    main()
