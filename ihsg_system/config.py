"""
IHSG Trading System — Configuration
All settings and constants live here.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ─── Load .env ─────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

# ─── API Keys ──────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── LLM Provider ─────────────────────────────────────────────
# Options: "groq" | "gemini" | "auto"
# "auto" = Groq as primary, Gemini as fallback (and vice versa on rate limit)
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "auto")

# ─── Groq Model ───────────────────────────────────────────────
GROQ_MODEL: str = "llama-3.3-70b-versatile"  # Free tier: 14,400 req/day

# ─── Gemini Model ─────────────────────────────────────────────
GEMINI_MODEL: str = "gemini-2.5-flash"  # Free tier: 1,500 req/day

MAX_TOKENS: int = 2048

# ─── Default Watchlist (Yahoo Finance format) ──────────────
DEFAULT_TICKERS: list[str] = [
    "BBRI.JK", "BBCA.JK", "BMRI.JK", "TLKM.JK",
    "ASII.JK", "ANTM.JK", "PTBA.JK", "MDKA.JK",
    "GOTO.JK", "BYAN.JK",
]

# ─── Analysis Settings ─────────────────────────────────────
ANALYSIS_PERIOD: str = "3mo"          # yfinance history period
VOLUME_SPIKE_THRESHOLD: float = 2.5   # relative volume threshold
MIN_CONFIDENCE_ALERT: int = 65        # minimum % to trigger Telegram alert

# ─── Paths ─────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).parent
DATA_DIR: Path = BASE_DIR / "data"
LOGS_DIR: Path = BASE_DIR / "logs"
REPORTS_DIR: Path = BASE_DIR / "reports"

# Auto-create directories
for _d in (DATA_DIR, LOGS_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SIGNAL_HISTORY_FILE: Path = DATA_DIR / "signal_history.json"
