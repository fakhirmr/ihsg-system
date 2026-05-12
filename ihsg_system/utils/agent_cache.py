"""
IHSG Trading System — Agent Result Cache
Caches non-real-time agent results to disk to reduce API calls.
- Fundamental: 24-hour TTL per ticker
- Sentiment: 4-hour TTL per ticker
- Macro: shared across all tickers, 2-hour TTL
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_FILE: Path = DATA_DIR / "agent_cache.json"

# TTL in seconds
TTL_FUNDAMENTAL: int = 24 * 3600   # 24 hours
TTL_SENTIMENT: int   =  4 * 3600   #  4 hours
TTL_MACRO: int       =  2 * 3600   #  2 hours


def _load() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[Cache] Failed to write cache: {exc}")


def get(key: str, ttl: int) -> Optional[dict[str, Any]]:
    """Return cached value if it exists and is not older than `ttl` seconds."""
    data = _load()
    entry = data.get(key)
    if entry and (time.time() - entry.get("ts", 0)) < ttl:
        logger.debug(f"[Cache] HIT: {key}")
        return entry["value"]
    return None


def set(key: str, value: dict[str, Any]) -> None:
    """Store a value in the cache with the current timestamp."""
    data = _load()
    data[key] = {"ts": time.time(), "value": value}
    _save(data)
    logger.debug(f"[Cache] SET: {key}")
