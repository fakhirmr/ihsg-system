"""
IHSG Trading System — Agent Result Cache
Caches non-real-time agent results to disk to reduce API calls.
- Fundamental: 24-hour TTL per ticker
- Sentiment: 4-hour TTL per ticker
- Macro: shared across all tickers, 2-hour TTL
- News deduplication: 6-hour TTL per news fingerprint
"""
from __future__ import annotations

import hashlib
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
TTL_NEWS_DEDUP: int    = 12 * 3600   # 12 hours — deduplication set berita (ticker-level)
TTL_ARTICLE_DEDUP: int =  7 * 24 * 3600  #  7 days  — deduplication per artikel


def _load() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            # File corrupt (misalnya terpotong saat restart) — backup dan mulai fresh
            backup = CACHE_FILE.with_suffix(".bak")
            try:
                CACHE_FILE.replace(backup)
            except Exception:
                pass
            logger.warning(f"[Cache] File corrupt, reset ke empty ({exc}). Backup: {backup}")
    return {}


def _save(data: dict) -> None:
    try:
        # Tulis ke .tmp dulu, lalu rename — atomic, aman dari partial write saat restart
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CACHE_FILE)
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


def exists(key: str, ttl: int) -> bool:
    """Return True if the key exists in cache and has not expired."""
    data = _load()
    entry = data.get(key)
    return bool(entry and (time.time() - entry.get("ts", 0)) < ttl)


def mark(key: str) -> None:
    """
    Simpan sebuah 'flag' tanpa value berarti.
    Berguna untuk menandai event sudah terjadi (misal: berita sudah dikirim).
    """
    data = _load()
    data[key] = {"ts": time.time(), "value": True}
    _save(data)
    logger.debug(f"[Cache] MARK: {key}")


def hash_news_titles(news_list: list[dict]) -> str:
    """
    Buat fingerprint unik dari sekumpulan judul berita.
    Digunakan untuk deduplication — jika berita sama, hash akan sama.
    """
    titles = sorted(
        item.get("title", "").strip().lower()
        for item in news_list
        if item.get("title")
    )
    raw = "|".join(titles)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
