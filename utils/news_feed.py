"""
IHSG Trading System — News Feed (RSS Indonesia)
================================================
Sumber berita utama untuk sistem. Menggantikan yfinance `.news` yang
untuk ticker .JK dan ^JKSE praktis sudah tidak ter-update.

Sumber (semua RSS publik, tanpa auth):
  - Kontan Investasi   : berita pasar modal & emiten
  - CNBC Indonesia     : market & makro
  - Antara Ekonomi     : makro & kebijakan

Dua fungsi utama:
  fetch_market_rss()  -> berita pasar/makro untuk konteks IHSG
  match_ticker_news() -> saring pool berita untuk satu emiten

Pencocokan emiten memakai konvensi IDX: kode saham ditulis kapital
di dalam kurung, mis. "Laba J Resources (PSAB) Melonjak 803%".
"""
from __future__ import annotations

import html
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

# ── Sumber RSS ────────────────────────────────────────────────────────────────
RSS_SOURCES: list[tuple[str, str]] = [
    ("Kontan",         "https://investasi.kontan.co.id/rss"),
    ("CNBC Indonesia", "https://www.cnbcindonesia.com/market/rss"),
    ("Antara",         "https://www.antaranews.com/rss/ekonomi.xml"),
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IHSGBot/1.0)"}
_TIMEOUT = 12
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """
    Rapikan teks RSS agar aman ditempel ke pesan Telegram parse_mode=HTML.

    Tiga langkah, urutannya penting:
      1. buang tag HTML dari deskripsi
      2. unescape entity — tanpa ini '&nbsp;' terkirim mentah ke Telegram
      3. escape ulang & < > — judul seperti "Laba <b>naik</b> & untung"
         akan merusak parsing HTML Telegram kalau dibiarkan apa adanya
    """
    stripped = html.unescape(_TAG_RE.sub(" ", text or ""))
    normalised = re.sub(r"\s+", " ", stripped.replace("\xa0", " ")).strip()
    return html.escape(normalised, quote=False)


def _parse_pubdate(raw: str) -> int | None:
    """RFC-822 ('Thu, 03 Sep 2026 18:04:46 +0700') -> unix timestamp."""
    if not raw:
        return None
    try:
        return int(parsedate_to_datetime(raw).timestamp())
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _clean_url(raw: str) -> str:
    """Escape & di URL supaya valid sebagai atribut href pada HTML Telegram."""
    return html.escape((raw or "").strip(), quote=False)


def fetch_rss(source_name: str, url: str, max_items: int = 40) -> list[dict]:
    """Ambil dan parse satu feed RSS. Gagal = list kosong, tidak melempar."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"[RSS] {source_name} HTTP {resp.status_code}")
            return []
        root = ET.fromstring(resp.content)
    except Exception as exc:
        logger.warning(f"[RSS] {source_name} gagal: {type(exc).__name__}: {exc}")
        return []

    items: list[dict] = []
    for node in root.findall(".//item")[:max_items]:
        title = _clean(node.findtext("title") or "")
        if not title:
            continue
        items.append({
            "title": title,
            "publisher": source_name,
            "link": _clean_url(node.findtext("link") or ""),
            "summary": _clean(node.findtext("description") or "")[:400],
            "pub_ts": _parse_pubdate(node.findtext("pubDate") or ""),
        })
    logger.debug(f"[RSS] {source_name}: {len(items)} item")
    return items


def fetch_all_rss(max_age_hours: int = 24) -> list[dict]:
    """
    Gabungkan semua sumber RSS jadi satu pool, dedup per judul,
    buang yang lebih tua dari `max_age_hours`, urutkan terbaru dulu.
    """
    cutoff = time.time() - max_age_hours * 3600
    seen: set[str] = set()
    pool: list[dict] = []

    for source_name, url in RSS_SOURCES:
        for item in fetch_rss(source_name, url):
            key = item["title"].lower()
            if key in seen:
                continue
            if item["pub_ts"] is not None and item["pub_ts"] < cutoff:
                continue
            seen.add(key)
            pool.append(item)

    pool.sort(key=lambda x: x["pub_ts"] or 0, reverse=True)
    logger.info(f"[RSS] Pool berita: {len(pool)} artikel (<= {max_age_hours} jam)")
    return pool


# ── Relevansi pasar / makro ───────────────────────────────────────────────────
_MARKET_KEYWORDS = {
    "ihsg", "bursa", "idx", "saham", "emiten", "indeks", "bei",
    "bank indonesia", "bi rate", "suku bunga", "rupiah", "inflasi",
    "the fed", "fomc", "obligasi", "sbn", "yield", "asing", "net buy",
    "net sell", "msci", "ftse", "rights issue", "dividen", "buyback",
    "ipo", "laba", "kinerja", "batu bara", "nikel", "cpo", "minyak",
    "emas", "komoditas", "ekspor", "impor", "apbn", "pajak", "ojk",
}


def is_market_relevant(item: dict) -> bool:
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    return any(kw in text for kw in _MARKET_KEYWORDS)


def fetch_market_rss(max_items: int = 8, max_age_hours: int = 24) -> list[dict]:
    """Berita pasar/makro terbaru yang relevan untuk IHSG."""
    pool = fetch_all_rss(max_age_hours=max_age_hours)
    return [it for it in pool if is_market_relevant(it)][:max_items]


# ── Verifikasi penyebutan kode emiten ─────────────────────────────────────────

def _mentions_code(text: str, code: str) -> int:
    """
    Seberapa kuat sebuah teks menyebut kode emiten.
      3 = kode di dalam kurung, "(PSAB)" — konvensi IDX, paling andal
      2 = kode sebagai token KAPITAL berdiri sendiri, "BUMI hingga BUVA"
      0 = tidak disebut

    Kapital penuh itu wajib: tanpa syarat ini, kode seperti BUMI, RAJA,
    RATU, DEWA, dan ELIT akan tertarik oleh kata bahasa Indonesia biasa
    ("Bank Bumi Arta", "Raja Dividen").
    """
    if re.search(r"\(\s*" + re.escape(code) + r"\s*\)", text):
        return 3
    if re.search(r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])", text):
        return 2
    return 0


# ── Berita per emiten via Google News ─────────────────────────────────────────
_GNEWS_URL = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=id&gl=ID&ceid=ID:id"
)

# Google News menempelkan nama sumber di akhir judul: "Judul berita - Kontan"
_GNEWS_SUFFIX_RE = re.compile(r"\s+-\s+[^-]{2,40}$")


def fetch_google_news(
    code: str, max_items: int = 5, max_age_hours: int = 48
) -> list[dict]:
    """
    Berita untuk satu kode emiten lewat Google News RSS Indonesia.

    Query pakai tanda kutip pada kode saham supaya hasilnya spesifik,
    dan kata 'saham' supaya kode yang juga kata biasa (BUMI, RAJA, RATU)
    tidak menarik berita di luar konteks pasar modal.
    """
    query = urllib.parse.quote(f'"{code}" saham')
    cutoff = time.time() - max_age_hours * 3600

    try:
        resp = requests.get(
            _GNEWS_URL.format(query=query), headers=_HEADERS, timeout=_TIMEOUT
        )
        if resp.status_code != 200:
            logger.warning(f"[GNews] {code} HTTP {resp.status_code}")
            return []
        root = ET.fromstring(resp.content)
    except Exception as exc:
        logger.warning(f"[GNews] {code} gagal: {type(exc).__name__}: {exc}")
        return []

    items: list[dict] = []
    for node in root.findall(".//item"):
        title = _clean(node.findtext("title") or "")
        if not title:
            continue
        pub_ts = _parse_pubdate(node.findtext("pubDate") or "")
        if pub_ts is not None and pub_ts < cutoff:
            continue

        summary = _clean(node.findtext("description") or "")[:400]

        # Pencarian Google tidak peka huruf besar-kecil, jadi hasilnya harus
        # diverifikasi ulang di sini — kalau tidak, query "BUMI" akan
        # memulangkan berita "Bank Bumi Arta (BNBA)".
        if not _mentions_code(title + " " + summary, code):
            continue

        publisher = _clean(node.findtext("source") or "")
        items.append({
            "title": _GNEWS_SUFFIX_RE.sub("", title) if publisher else title,
            "publisher": publisher or "Google News",
            "link": _clean_url(node.findtext("link") or ""),
            "summary": summary,
            "pub_ts": pub_ts,
        })
        if len(items) >= max_items:
            break

    logger.debug(f"[GNews] {code}: {len(items)} item (<= {max_age_hours} jam)")
    return items


# ── Pencocokan per emiten ─────────────────────────────────────────────────────

def _code_of(ticker: str) -> str:
    return ticker.upper().replace(".JK", "").strip()


def _name_tokens(company_name: str) -> list[str]:
    """
    Ambil kata khas dari nama perusahaan untuk pencocokan longgar.
    Buang bentuk badan hukum & kata terlalu umum agar tidak salah cocok.
    """
    stop = {
        "persero", "indonesia", "international", "internasional",
        "group", "grup", "company", "corporation", "corp", "industries",
        "industry", "energy", "resources", "mining", "bank", "the", "and",
        "dan", "sejahtera", "makmur", "jaya", "utama", "abadi", "mandiri",
    }
    words = re.findall(r"[A-Za-z]{4,}", company_name or "")
    return [w.lower() for w in words if w.lower() not in stop]


def match_ticker_news(
    ticker: str,
    company_name: str = "",
    pool: list[dict] | None = None,
    max_items: int = 5,
) -> list[dict]:
    """
    Saring pool berita untuk satu emiten.

    Aturan cocok (urut dari paling kuat):
      1. Kode saham dalam kurung — '(PSAB)' — konvensi IDX, paling andal
      2. Kode saham sebagai token KAPITAL berdiri sendiri — 'PSAB naik'.
         Wajib kapital penuh supaya kode seperti BUMI, RAJA, RATU, DEWA
         tidak tertukar dengan kata bahasa Indonesia biasa.
      3. Dua kata khas nama perusahaan muncul bersamaan

    `pool` sebaiknya di-fetch sekali lalu dipakai ulang untuk semua ticker.
    """
    if pool is None:
        pool = fetch_all_rss()

    code = _code_of(ticker)
    tokens = _name_tokens(company_name)

    scored: list[tuple[int, dict]] = []
    for item in pool:
        text = item.get("title", "") + " " + item.get("summary", "")
        score = _mentions_code(text, code)
        if not score and len(tokens) >= 2:
            low = text.lower()
            if sum(1 for t in tokens if t in low) >= 2:
                score = 1
        if score:
            scored.append((score, item))

    scored.sort(key=lambda x: (-x[0], -(x[1].get("pub_ts") or 0)))
    return [item for _, item in scored[:max_items]]


def fetch_stock_news(
    ticker: str,
    company_name: str = "",
    pool: list[dict] | None = None,
    max_items: int = 5,
    max_age_hours: int = 48,
) -> list[dict]:
    """
    Berita gabungan untuk satu emiten: Google News (cakupan luas) +
    pool RSS Indonesia (sumber terkurasi). Dedup per judul, terbaru dulu.
    """
    code = _code_of(ticker)
    merged: list[dict] = []
    seen: set[str] = set()

    for item in fetch_google_news(code, max_items=max_items, max_age_hours=max_age_hours):
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)

    if pool:
        for item in match_ticker_news(ticker, company_name, pool, max_items=max_items):
            key = item["title"].lower()
            if key not in seen:
                seen.add(key)
                merged.append(item)

    merged.sort(key=lambda x: x.get("pub_ts") or 0, reverse=True)
    return merged[:max_items]
