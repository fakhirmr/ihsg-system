"""
IHSG Trading System — News Sentiment Agent
Evaluates news sentiment for a specific stock or the overall market.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah News Sentiment Agent untuk pasar saham Indonesia (IHSG).
Tugasmu mengevaluasi sentimen berita terkait saham dan sektor tertentu.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "sentiment": "<Bullish|Neutral|Bearish>",
  "confidence": <integer 0-100>,
  "catalysts": ["<katalis positif 1>", "<katalis positif 2>"],
  "risks": ["<risiko/berita negatif 1>"],
  "corporate_actions": ["<rights issue|buyback|merger|akuisisi|dividen|dll> jika ada"],
  "reasons": ["<alasan sentimen 1>", "<alasan sentimen 2>"],
  "summary": "<ringkasan sentimen dalam Bahasa Indonesia>"
}

Sumber berita prioritas: IDX, Bank Indonesia, Reuters, Bloomberg, CNBC Indonesia, Bisnis.com.
Catatan: Jika informasi berita spesifik tidak tersedia, gunakan pengetahuan umum tentang sektor dan kondisi pasar.
"""

_USER_TEMPLATE = """\
Analisis sentimen berita untuk saham berikut:

Ticker : {ticker}
Nama   : {company_name}
Sektor : {sector}
Industri: {industry}
Harga  : {price:,.0f} IDR
Perubahan: {change_pct:+.2f}%

{news_section}

Pertimbangkan:
1. Berita korporasi terkini (earnings, dividen, rights issue, buyback, RUPS)
2. Regulasi pemerintah yang mempengaruhi sektor
3. Pergerakan harga komoditas terkait
4. Sentimen investor asing terhadap sektor
5. Aksi korporasi yang diumumkan

Kembalikan HANYA JSON sesuai format.
"""


class NewsSentimentAgent(BaseAgent):
    """Analyzes news sentiment for a stock using available information."""

    def analyze(  # type: ignore[override]
        self,
        ticker: str,
        company_name: str = "",
        sector: str = "",
        industry: str = "",
        current_price: float = 0.0,
        day_change_pct: float = 0.0,
        news_text: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Analyze news sentiment.

        Args:
            ticker:         Stock ticker (e.g., 'BBRI.JK').
            company_name:   Full company name.
            sector:         Sector classification.
            industry:       Industry classification.
            current_price:  Latest stock price.
            day_change_pct: Intraday price change percentage.
            news_text:      Optional raw news text to inject into the prompt.

        Returns:
            Dict with sentiment, confidence, catalysts, risks, reasons, summary.
        """
        fallback = {
            "sentiment": "Neutral",
            "confidence": 40,
            "catalysts": [],
            "risks": ["Informasi berita tidak tersedia"],
            "corporate_actions": [],
            "reasons": ["Analisis berdasarkan pengetahuan umum sektor"],
            "summary": "Sentimen dievaluasi berdasarkan pengetahuan umum sektor.",
        }

        # Build news section
        if news_text and news_text.strip():
            news_section = f"=== BERITA TERKINI ===\n{news_text.strip()}"
        else:
            news_section = (
                "=== BERITA ===\n"
                "Tidak ada berita spesifik yang disediakan. "
                "Gunakan pengetahuan umum tentang sektor dan kondisi pasar terkini."
            )

        user_message = _USER_TEMPLATE.format(
            ticker=ticker,
            company_name=company_name or ticker,
            sector=sector or "Unknown",
            industry=industry or "Unknown",
            price=current_price,
            change_pct=day_change_pct,
            news_section=news_section,
        )

        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        logger.info(
            f"[NewsSentimentAgent] {ticker} → "
            f"Sentiment:{result.get('sentiment')} Conf:{result.get('confidence')}%"
        )
        return result
