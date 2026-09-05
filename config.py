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
# llama-3.3-70b-versatile sudah DITARIK Groq — panggilan ke model itu
# dijawab 404 model_not_found. Daftar model yang masih hidup bisa dicek
# lewat: Groq(api_key=...).models.list()
GROQ_MODEL: str = "openai/gpt-oss-120b"  # Free tier: 14,400 req/day

# ─── Gemini Model ─────────────────────────────────────────────
GEMINI_MODEL: str = "gemini-2.5-flash"  # Free tier: 1,500 req/day

MAX_TOKENS: int = 2048

# ─── Watchlist ─────────────────────────────────────────────
# Daftar emiten TIDAK disimpan di repo. Ia berasal dari jurnal mentor,
# jadi diperlakukan sebagai miliknya: hidup di data/watchlist.json
# (gitignore) pada mesin lokal, dan di GitHub Actions variable WATCHLIST
# pada runner — workflow menuliskannya ke berkas sebelum job jalan.
#
# Tidak ada daftar cadangan yang ditanam di sini dengan sengaja. Cadangan
# diam-diam berarti sistem bisa berjalan dengan watchlist yang salah tanpa
# ada yang sadar — persis jenis kegagalan senyap yang sudah pernah
# melumpuhkan sistem ini selama 17 hari. Lebih baik berhenti dan berteriak.
DEFAULT_TICKERS_BUILTIN: list[str] = []

# ─── Analysis Settings ─────────────────────────────────────
ANALYSIS_PERIOD: str = "3mo"          # yfinance history period
VOLUME_SPIKE_THRESHOLD: float = 2.5   # relative volume threshold
MIN_CONFIDENCE_ALERT: int = 65        # minimum % to trigger Telegram alert

# ─── Paths ─────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).parent
DATA_DIR: Path = BASE_DIR / "data"
LOGS_DIR: Path = BASE_DIR / "logs"
REPORTS_DIR: Path = BASE_DIR / "reports"


# Watchlist efektif: berkas dulu, benih sebagai cadangan.
def _load_watchlist() -> list[str]:
    """Baca watchlist dari berkas. Kosong bukan kesalahan diam — lihat catatan."""
    import json
    path = BASE_DIR / "data" / "watchlist.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("tickers") or []
        codes = [r["code"] for r in rows if isinstance(r, dict) and r.get("code")]
        if codes:
            return [f"{c}.JK" for c in codes]
    except FileNotFoundError:
        pass
    except Exception as exc:
        import warnings
        warnings.warn(f"watchlist.json ada tapi tidak terbaca: {exc}")
    return list(DEFAULT_TICKERS_BUILTIN)


DEFAULT_TICKERS: list[str] = _load_watchlist()

# Auto-create directories
for _d in (DATA_DIR, LOGS_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SIGNAL_HISTORY_FILE: Path = DATA_DIR / "signal_history.json"
