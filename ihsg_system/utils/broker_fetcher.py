"""
IDX Broker Transaction Summary Fetcher
Mengambil data transaksi broker market-wide dari API IDX.

Endpoint IDX hanya menyediakan data agregat semua broker (bukan per-saham).
Data ini digunakan untuk analisis Net Foreign Flow pasar secara keseluruhan.

Schema API (https://idx.co.id/primary/TradingSummary/GetBrokerSummary):
  IDFirm    : kode broker
  FirmName  : nama broker
  Volume    : total lot yang diperdagangkan
  Value     : total nilai transaksi (IDR)
  Frequency : jumlah transaksi
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_scraper = None

def _get_scraper():
    global _scraper
    if _scraper is None:
        try:
            import cloudscraper
            _scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
        except ImportError:
            logger.warning("[BrokerFetcher] cloudscraper tidak tersedia, fallback ke requests")
            _scraper = requests.Session()
    return _scraper


# Kode broker asing yang diketahui di IDX
FOREIGN_BROKER_CODES: set[str] = {
    "YU",  # CGS International / CIMB Securities
    "CS",  # Credit Suisse Securities
    "DB",  # Deutsche Securities Indonesia
    "MS",  # Morgan Stanley Sekuritas
    "JP",  # JP Morgan Securities
    "BK",  # Bank of America Merrill Lynch
    "AK",  # UBS Sekuritas Indonesia
    "RX",  # Macquarie Securities
    "ZP",  # Maybank Sekuritas Indonesia
    "LG",  # Citigroup Securities
    "KI",  # CLSA Sekuritas
    "ML",  # Merrill Lynch
    "AI",  # Samsung Securities
    "FS",  # First State Investments
    "MK",  # Nomura Securities
    "RS",  # RHB Sekuritas
}

IDX_BROKER_API = "https://idx.co.id/primary/TradingSummary/GetBrokerSummary"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*",
    "Referer": "https://idx.co.id/id/market-data/stocks/broker-transaction-summary/",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_market_broker_summary(trade_date: Optional[date] = None) -> dict:
    """
    Ambil ringkasan transaksi broker market-wide dari IDX.

    Returns dict dengan key:
    - date
    - total_value         : total nilai transaksi semua broker (IDR)
    - foreign_value       : total nilai transaksi broker asing (IDR)
    - domestic_value      : total nilai transaksi broker domestik (IDR)
    - foreign_value_pct   : % nilai transaksi oleh broker asing
    - top_foreign_brokers : list [(code, name, value), ...] broker asing paling aktif
    - top_domestic_brokers: list [(code, name, value), ...] broker domestik paling aktif
    - top_brokers_all     : list [(code, name, value, is_foreign), ...] semua top 15
    - broker_count        : jumlah broker aktif
    - foreign_broker_count: jumlah broker asing aktif
    - error               : pesan error jika gagal, None jika sukses
    """
    if trade_date is None:
        trade_date = date.today()

    date_str = trade_date.strftime("%Y-%m-%d")
    params = {
        "startDate": date_str,
        "endDate": date_str,
        "draw": 1,
        "start": 0,
        "length": 100,
    }

    try:
        scraper = _get_scraper()
        resp = scraper.get(IDX_BROKER_API, params=params, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        raw = resp.json()
        return _parse_response(raw, trade_date)

    except Exception as exc:
        logger.warning(f"[BrokerFetcher] Market summary: {type(exc).__name__} — {exc}")
        return _empty_result(trade_date, error=str(exc))


def _parse_response(raw: dict, trade_date: date) -> dict:
    rows = raw.get("data") or raw.get("Data") or raw.get("Rows") or []

    if not rows:
        return _empty_result(trade_date, error="Data kosong dari IDX API")

    brokers: list[dict] = []
    for row in rows:
        code = str(row.get("IDFirm") or row.get("BrokerCode") or "").strip().upper()
        name = str(row.get("FirmName") or row.get("BrokerName") or "").strip()
        value = float(row.get("Value") or 0)
        volume = float(row.get("Volume") or 0)
        freq = float(row.get("Frequency") or 0)

        if not code:
            continue

        brokers.append({
            "code": code,
            "name": name,
            "value": value,
            "volume": volume,
            "frequency": freq,
            "is_foreign": code in FOREIGN_BROKER_CODES,
        })

    if not brokers:
        return _empty_result(trade_date, error="Tidak ada data broker valid")

    foreign = [b for b in brokers if b["is_foreign"]]
    domestic = [b for b in brokers if not b["is_foreign"]]

    total_value = sum(b["value"] for b in brokers)
    foreign_value = sum(b["value"] for b in foreign)
    domestic_value = sum(b["value"] for b in domestic)
    foreign_pct = (foreign_value / total_value * 100) if total_value > 0 else 0.0

    sorted_all = sorted(brokers, key=lambda b: b["value"], reverse=True)
    sorted_foreign = sorted(foreign, key=lambda b: b["value"], reverse=True)
    sorted_domestic = sorted(domestic, key=lambda b: b["value"], reverse=True)

    return {
        "date": str(trade_date),
        "total_value": total_value,
        "foreign_value": foreign_value,
        "domestic_value": domestic_value,
        "foreign_value_pct": round(foreign_pct, 1),
        "top_foreign_brokers": [(b["code"], b["name"], b["value"]) for b in sorted_foreign[:8]],
        "top_domestic_brokers": [(b["code"], b["name"], b["value"]) for b in sorted_domestic[:5]],
        "top_brokers_all": [(b["code"], b["name"], b["value"], b["is_foreign"]) for b in sorted_all[:15]],
        "broker_count": len(brokers),
        "foreign_broker_count": len(foreign),
        "error": None,
    }


def _empty_result(trade_date: date, error: str = "") -> dict:
    return {
        "date": str(trade_date),
        "total_value": 0.0,
        "foreign_value": 0.0,
        "domestic_value": 0.0,
        "foreign_value_pct": 0.0,
        "top_foreign_brokers": [],
        "top_domestic_brokers": [],
        "top_brokers_all": [],
        "broker_count": 0,
        "foreign_broker_count": 0,
        "error": error,
    }
