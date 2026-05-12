"""
IHSG Trading System -- News Sentiment Agent (v2)
=================================================
Upgrade dari v1:
- Mendeteksi saham spesifik yang disebut dalam berita
- Menilai dampak berita terhadap fundamental saham
- Mengembalikan 'fundamental_impact' dan 'affected_tickers'
  sehingga scheduler bisa trigger Fundamental Agent otomatis
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah News Sentiment Agent untuk pasar saham Indonesia (IHSG).
Tugasmu mengevaluasi sentimen berita dan dampaknya terhadap saham & fundamental.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "sentiment": "<Bullish|Neutral|Bearish>",
  "confidence": <integer 0-100>,
  "catalysts": ["<katalis positif 1>"],
  "risks": ["<risiko/berita negatif 1>"],
  "corporate_actions": ["<rights issue|buyback|merger|dividen|dll> jika ada"],
  "reasons": ["<alasan sentimen 1>", "<alasan sentimen 2>"],
  "summary": "<ringkasan sentimen dalam Bahasa Indonesia>",
  "fundamental_impact": "<Positive|Neutral|Negative|Unknown>",
  "fundamental_reason": "<alasan dampak terhadap fundamental: revenue, margin, utang, dll>",
  "trigger_fundamental_review": <true|false>,
  "affected_tickers": ["<ticker lain yang terdampak berita ini, tanpa .JK>"]
}

Aturan untuk trigger_fundamental_review = true:
- Ada corporate action besar (merger, rights issue, akuisisi, divestasi)
- Perubahan regulasi signifikan yang mempengaruhi bisnis
- Kejutan earnings (beat/miss signifikan)
- Pergantian manajemen kunci (CEO/CFO)
- Masalah hukum/gagal bayar utang

Sumber prioritas: IDX, Bank Indonesia, Reuters, Bloomberg, CNBC Indonesia, Bisnis.com.
"""

_USER_TEMPLATE = """\
Analisis sentimen berita untuk saham berikut:

Ticker  : {ticker}
Nama    : {company_name}
Sektor  : {sector}
Industri: {industry}
Harga   : {price:,.0f} IDR
Perubahan: {change_pct:+.2f}%

{news_section}

Pertimbangkan:
1. Berita korporasi terkini (earnings, dividen, rights issue, buyback, RUPS)
2. Regulasi pemerintah yang mempengaruhi sektor
3. Pergerakan harga komoditas terkait
4. Sentimen investor asing terhadap sektor
5. Dampak berita terhadap fundamental: pendapatan, margin, utang, arus kas

Daftar saham yang dimonitor (cek apakah ada yang terdampak berita ini):
{watchlist}

Kembalikan HANYA JSON sesuai format.
"""


class NewsSentimentAgent(BaseAgent):
    """
    Analyzes news sentiment for a stock.

    Enhancements over v1:
    - Returns fundamental_impact: how news affects fundamentals
    - Returns trigger_fundamental_review: True if fundamental re-analysis needed
    - Returns affected_tickers: other monitored stocks impacted by the same news
    """

    def analyze(  # type: ignore[override]
        self,
        ticker: str,
        company_name: str = "",
        sector: str = "",
        industry: str = "",
        current_price: float = 0.0,
        day_change_pct: float = 0.0,
        news_text: Optional[str] = None,
        watchlist: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        fallback = {
            "sentiment": "Neutral",
            "confidence": 40,
            "catalysts": [],
            "risks": ["Informasi berita tidak tersedia"],
            "corporate_actions": [],
            "reasons": ["Analisis berdasarkan pengetahuan umum sektor"],
            "summary": "Sentimen dievaluasi berdasarkan pengetahuan umum sektor.",
            "fundamental_impact": "Unknown",
            "fundamental_reason": "Tidak ada berita signifikan yang mempengaruhi fundamental.",
            "trigger_fundamental_review": False,
            "affected_tickers": [],
        }

        # Build news section
        if news_text and news_text.strip():
            news_section = f"=== BERITA TERKINI ===\n{news_text.strip()}"
        else:
            news_section = (
                "=== BERITA ===\n"
                "Tidak ada berita spesifik. "
                "Gunakan pengetahuan umum tentang kondisi terkini sektor ini."
            )

        # Watchlist context (tanpa .JK suffix untuk readability)
        wl_str = ", ".join(
            t.replace(".JK", "") for t in (watchlist or [])
        ) or "Tidak ada watchlist"

        user_message = _USER_TEMPLATE.format(
            ticker=ticker,
            company_name=company_name or ticker,
            sector=sector or "Unknown",
            industry=industry or "Unknown",
            price=current_price,
            change_pct=day_change_pct,
            news_section=news_section,
            watchlist=wl_str,
        )

        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        # Ensure required fields exist
        result.setdefault("fundamental_impact", "Unknown")
        result.setdefault("fundamental_reason", "")
        result.setdefault("trigger_fundamental_review", False)
        result.setdefault("affected_tickers", [])

        logger.info(
            f"[SentimentAgent] {ticker} -> "
            f"Sentiment:{result.get('sentiment')} | "
            f"FundImpact:{result.get('fundamental_impact')} | "
            f"TriggerFund:{result.get('trigger_fundamental_review')} | "
            f"Affected:{result.get('affected_tickers')}"
        )
        return result
