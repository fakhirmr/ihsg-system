"""
IHSG Trading System — Macro Economic Agent
Assesses global and domestic macro conditions and their impact on IHSG sectors.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Macro Economic Agent yang sangat berpengalaman dalam pasar modal Indonesia (IHSG).
Tugasmu menilai kondisi makro ekonomi global dan domestik serta dampaknya terhadap sektor-sektor di IHSG.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "market_condition": "<Risk-On Bullish|Mild Bullish|Neutral|Mild Bearish|Risk-Off Bearish>",
  "sentiment": "<Bullish|Neutral|Bearish>",
  "positive_sectors": ["<sektor1>", "<sektor2>"],
  "negative_sectors": ["<sektor1>"],
  "neutral_sectors": ["<sektor1>"],
  "key_drivers": ["<driver utama 1>", "<driver utama 2>"],
  "risks": ["<risiko makro 1>"],
  "ihsg_bias": "<Bullish|Neutral|Bearish>",
  "summary": "<ringkasan kondisi makro dalam Bahasa Indonesia>"
}

Sektor IHSG yang harus dipertimbangkan:
Perbankan, Energi, Batu Bara, Nikel, Emas & Mineral, Telekomunikasi, Konsumer,
Properti, Infrastruktur, Kesehatan, Teknologi, Otomotif, Semen, Retail.
"""

_USER_TEMPLATE = """\
Berdasarkan pengetahuanmu tentang kondisi makro ekonomi terkini (per {context}), berikan analisis makro untuk pasar IHSG.

Pertimbangkan faktor-faktor berikut:
- BI Rate dan kebijakan moneter Bank Indonesia
- The Fed dan suku bunga global
- Inflasi Indonesia dan global
- Nilai tukar USD/IDR
- Harga komoditas: batubara, CPO, nikel, emas, minyak
- Kondisi ekonomi China (mitra dagang utama)
- Sentimen risk-on/risk-off global
- Geopolitik yang relevan
- Aliran modal asing ke pasar berkembang (EM flows)

Ticker yang sedang dianalisis: {ticker}
Sektor: {sector}

Kembalikan HANYA JSON sesuai format.
"""


class MacroAgent(BaseAgent):
    """Evaluates macro economic conditions and their sector impact on IHSG."""

    def analyze(  # type: ignore[override]
        self, ticker: str = "", sector: str = "", context: str = "saat ini"
    ) -> dict[str, Any]:
        """
        Analyze macro conditions.

        Args:
            ticker:  Optional ticker being analyzed (for sector context).
            sector:  Sector of the ticker.
            context: Date/time context string.

        Returns:
            Dict with market_condition, sentiment, positive/negative sectors, etc.
        """
        fallback = {
            "market_condition": "Neutral",
            "sentiment": "Neutral",
            "positive_sectors": [],
            "negative_sectors": [],
            "neutral_sectors": [],
            "key_drivers": ["Analisis makro tidak tersedia"],
            "risks": ["Data makro tidak dapat diakses"],
            "ihsg_bias": "Neutral",
            "summary": "Kondisi makro tidak dapat dianalisis saat ini.",
        }

        user_message = _USER_TEMPLATE.format(
            context=context,
            ticker=ticker or "General IHSG",
            sector=sector or "Semua Sektor",
        )

        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        logger.info(
            f"[MacroAgent] Condition:{result.get('market_condition')} "
            f"IHSG Bias:{result.get('ihsg_bias')} "
            f"Positive Sectors:{result.get('positive_sectors', [])}"
        )
        return result
