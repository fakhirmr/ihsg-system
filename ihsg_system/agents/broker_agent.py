"""
IHSG Trading System — Broker Flow Agent
Menganalisis Net Foreign Flow market-wide dan korelasinya dengan pergerakan saham watchlist.

IDX menyediakan data broker per-pasar (bukan per-saham). Agent ini menganalisis:
1. Dominasi broker asing vs domestik hari ini
2. IHSG performance vs foreign flow
3. Saham di watchlist yang bergerak searah dengan foreign flow
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Net Foreign Flow Analyst untuk pasar saham Indonesia (IDX/IHSG).
Tugasmu menganalisis data aktivitas broker asing vs domestik dan dampaknya ke pasar.

Panduan interpretasi:
- Foreign broker share >35% = partisipasi asing signifikan (bullish sentimen asing)
- Foreign broker share <20% = asing menarik diri (bearish sentimen asing)
- Broker asing top: AK=UBS, ZP=Maybank, YU=CGS/CIMB, MS=MorganStanley, JP=JPMorgan
- Broker domestik top: XL=Stockbit, DX=BCA Sekuritas, YP=Mirae, CC=Mandiri, KK=Phillip

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown:
{
  "market_signal": "<Bullish|Bearish|Neutral>",
  "confidence": <integer 0-100>,
  "foreign_sentiment": "<Accumulation|Distribution|Neutral>",
  "flow_strength": "<Strong|Moderate|Weak>",
  "dominant_foreign_broker": "<kode broker asing paling aktif>",
  "key_observations": ["<observasi 1>", "<observasi 2>", "<observasi 3>"],
  "watchlist_correlation": "<saham watchlist yang paling terpengaruh foreign flow>",
  "summary": "<ringkasan singkat max 150 karakter, Bahasa Indonesia>"
}
"""


class BrokerAgent(BaseAgent):
    """Analisis Net Foreign Flow market-wide dan korelasinya dengan saham watchlist."""

    def analyze(  # type: ignore[override]
        self,
        broker_data: dict,
        ihsg_change_pct: float = 0.0,
        top_movers: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Analisis broker summary market-wide.

        Args:
            broker_data      : dict dari broker_fetcher.fetch_market_broker_summary()
            ihsg_change_pct  : perubahan IHSG hari ini (%)
            top_movers       : list saham watchlist dengan pergerakan signifikan
                               [{"ticker": "BBCA", "change_pct": 2.5, "volume_ratio": 1.8}, ...]

        Returns:
            Dict dengan market_signal, confidence, foreign_sentiment, dll.
        """
        fallback = {
            "market_signal": "Neutral",
            "confidence": 0,
            "foreign_sentiment": "Neutral",
            "flow_strength": "Weak",
            "dominant_foreign_broker": "-",
            "key_observations": ["Data broker tidak tersedia"],
            "watchlist_correlation": "-",
            "summary": "Data broker tidak dapat diambil dari IDX.",
        }

        if broker_data.get("error") or broker_data.get("broker_count", 0) == 0:
            return fallback

        user_message = self._build_prompt(broker_data, ihsg_change_pct, top_movers or [])
        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)

        logger.info(
            f"[BrokerAgent] Market → Signal:{result.get('market_signal')} "
            f"({result.get('confidence')}%) | "
            f"Foreign:{broker_data.get('foreign_value_pct',0):.1f}% | "
            f"IHSG:{ihsg_change_pct:+.2f}%"
        )
        return result

    def _fmt(self, v: float) -> str:
        if abs(v) >= 1e12:
            return f"{v/1e12:.2f}T"
        elif abs(v) >= 1e9:
            return f"{v/1e9:.2f}M"
        elif abs(v) >= 1e6:
            return f"{v/1e6:.1f}jt"
        return f"{v:,.0f}"

    def _build_prompt(
        self,
        bd: dict,
        ihsg_change_pct: float,
        top_movers: list[dict],
    ) -> str:
        trade_date = bd["date"]
        foreign_pct = bd.get("foreign_value_pct", 0)
        ihsg_dir = "▲ naik" if ihsg_change_pct > 0 else ("▼ turun" if ihsg_change_pct < 0 else "→ flat")

        # Format broker asing
        fg_lines = ""
        for code, name, val in bd.get("top_foreign_brokers", [])[:8]:
            pct = val / bd["total_value"] * 100 if bd["total_value"] > 0 else 0
            fg_lines += f"\n  {code} ({name[:25]}): {self._fmt(val)} ({pct:.1f}%)"
        if not fg_lines:
            fg_lines = "\n  (tidak ada broker asing aktif)"

        # Format broker domestik
        dom_lines = ""
        for code, name, val in bd.get("top_domestic_brokers", [])[:5]:
            pct = val / bd["total_value"] * 100 if bd["total_value"] > 0 else 0
            dom_lines += f"\n  {code} ({name[:25]}): {self._fmt(val)} ({pct:.1f}%)"

        # Format top movers watchlist
        mover_lines = ""
        if top_movers:
            for m in top_movers[:10]:
                dir_tag = "▲" if m["change_pct"] > 0 else "▼"
                mover_lines += f"\n  {m['ticker']} {dir_tag}{abs(m['change_pct']):.1f}% Vol:{m.get('volume_ratio',1):.1f}x"
        else:
            mover_lines = "\n  (tidak ada data)"

        lines = [
            f"=== BROKER FLOW ANALYSIS — {trade_date} ===",
            "",
            "--- NET FOREIGN FLOW (MARKET-WIDE) ---",
            f"Total Nilai Transaksi : {self._fmt(bd.get('total_value', 0))}",
            f"Nilai Broker Asing   : {self._fmt(bd.get('foreign_value', 0))} ({foreign_pct:.1f}%)",
            f"Nilai Broker Domestik: {self._fmt(bd.get('domestic_value', 0))} ({100-foreign_pct:.1f}%)",
            f"Jumlah Broker Aktif  : {bd.get('broker_count', 0)} (Asing: {bd.get('foreign_broker_count', 0)})",
            "",
            f"--- PERFORMA IHSG ---",
            f"IHSG Hari Ini: {ihsg_change_pct:+.2f}% ({ihsg_dir})",
            "",
            f"--- BROKER ASING PALING AKTIF ---{fg_lines}",
            "",
            f"--- BROKER DOMESTIK PALING AKTIF ---{dom_lines}",
            "",
            f"--- SAHAM WATCHLIST PERGERAKAN SIGNIFIKAN ---{mover_lines}",
            "",
            "Analisis:",
            f"1. Apakah dominasi asing ({foreign_pct:.1f}%) tergolong tinggi atau rendah?",
            "2. Apakah ada broker asing tertentu yang sangat dominan?",
            "3. Apakah IHSG bergerak searah dengan sentimen asing?",
            "4. Saham watchlist mana yang berpotensi dipengaruhi foreign flow?",
            "Kembalikan HANYA JSON sesuai format.",
        ]

        return "\n".join(lines)
