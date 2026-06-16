"""
IHSG Trading System — Fundamental Analysis Agent
Analyzes financial health, valuation, and earnings quality of a stock.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from agents.base_agent import BaseAgent
from utils.data_fetcher import StockData

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Fundamental Analysis Agent untuk saham IHSG Indonesia yang sangat berpengalaman.
Tugasmu menganalisis data keuangan yang diberikan dan memberikan penilaian fundamental.

Kembalikan HANYA JSON valid tanpa teks tambahan, tanpa markdown, tanpa komentar.
Format JSON wajib:
{
  "score": <integer 0-100>,
  "status": "<Strong Bullish|Bullish|Neutral|Weak|Bearish>",
  "strengths": ["<poin kekuatan 1>", "<poin kekuatan 2>"],
  "weaknesses": ["<poin kelemahan 1>"],
  "risks": ["<risiko utama 1>"],
  "per_assessment": "<Murah|Wajar|Mahal|Tidak diketahui>",
  "summary": "<ringkasan satu paragraf dalam Bahasa Indonesia>"
}
"""


class FundamentalAgent(BaseAgent):
    """Evaluates stock fundamentals using financial statement data."""

    def analyze(self, stock_data: StockData) -> dict[str, Any]:  # type: ignore[override]
        """
        Run fundamental analysis on the provided stock data.

        Args:
            stock_data: Populated StockData object from data_fetcher.

        Returns:
            Dict with keys: score, status, strengths, weaknesses, risks,
            per_assessment, summary, raw_output.
        """
        fallback = {
            "score": 50,
            "status": "Neutral",
            "strengths": ["Data tidak mencukupi"],
            "weaknesses": ["Data tidak mencukupi"],
            "risks": ["Keterbatasan data"],
            "per_assessment": "Tidak diketahui",
            "summary": "Analisis fundamental tidak dapat diselesaikan karena keterbatasan data.",
        }

        if not stock_data.is_valid:
            logger.warning(f"[FundamentalAgent] Invalid stock data for {stock_data.ticker}")
            fallback["summary"] = f"Data tidak valid: {stock_data.error}"
            return fallback

        user_message = self._build_prompt(stock_data)
        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)
        result["raw_output"] = user_message  # keep context for supervisor
        logger.info(
            f"[FundamentalAgent] {stock_data.ticker} → "
            f"Score:{result.get('score')} Status:{result.get('status')}"
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_prompt(self, sd: StockData) -> str:
        info = sd.info
        lines: list[str] = [
            f"=== FUNDAMENTAL DATA: {sd.ticker} ({sd.company_name}) ===",
            f"Sektor: {sd.sector} | Industri: {sd.industry}",
            f"Harga Saat Ini: {sd.current_price:,.0f} IDR",
            f"Market Cap: {sd.market_cap / 1e12:.2f} T IDR" if sd.market_cap > 0 else "Market Cap: N/A",
            "",
        ]

        # ── Valuation ratios from info ──────────────────────
        per = info.get("trailingPE") or info.get("forwardPE")
        pbv = info.get("priceToBook")
        div_yield = info.get("dividendYield")
        roe = info.get("returnOnEquity")
        roa = info.get("returnOnAssets")
        debt_equity = info.get("debtToEquity")
        op_margin = info.get("operatingMargins")
        profit_margin = info.get("profitMargins")
        rev_growth = info.get("revenueGrowth")
        earnings_growth = info.get("earningsGrowth")
        current_ratio = info.get("currentRatio")
        quick_ratio = info.get("quickRatio")
        fcf = info.get("freeCashflow")

        lines += [
            "--- VALUATION ---",
            f"PER (TTM): {per:.2f}x" if per else "PER: N/A",
            f"PBV: {pbv:.2f}x" if pbv else "PBV: N/A",
            f"Dividend Yield: {div_yield*100:.2f}%" if div_yield else "Dividend Yield: N/A",
            "",
            "--- PROFITABILITAS ---",
            f"ROE: {roe*100:.2f}%" if roe else "ROE: N/A",
            f"ROA: {roa*100:.2f}%" if roa else "ROA: N/A",
            f"Operating Margin: {op_margin*100:.2f}%" if op_margin else "Operating Margin: N/A",
            f"Net Profit Margin: {profit_margin*100:.2f}%" if profit_margin else "Net Profit Margin: N/A",
            "",
            "--- PERTUMBUHAN ---",
            f"Revenue Growth (YoY): {rev_growth*100:.2f}%" if rev_growth else "Revenue Growth: N/A",
            f"Earnings Growth (YoY): {earnings_growth*100:.2f}%" if earnings_growth else "Earnings Growth: N/A",
            "",
            "--- LIKUIDITAS & UTANG ---",
            f"Current Ratio: {current_ratio:.2f}x" if current_ratio else "Current Ratio: N/A",
            f"Quick Ratio: {quick_ratio:.2f}x" if quick_ratio else "Quick Ratio: N/A",
            f"Debt/Equity: {debt_equity:.2f}x" if debt_equity else "Debt/Equity: N/A",
            f"Free Cash Flow: {fcf/1e9:.2f}B IDR" if fcf else "Free Cash Flow: N/A",
            "",
        ]

        # ── Income Statement (simplified) ───────────────────
        lines.append("--- LAPORAN KEUANGAN (3 TAHUN TERAKHIR) ---")
        if not sd.financials.empty:
            try:
                fin = sd.financials
                for row_label in ["Total Revenue", "Gross Profit", "Net Income"]:
                    matching = [r for r in fin.index if row_label.lower() in str(r).lower()]
                    if matching:
                        row = fin.loc[matching[0]]
                        cols = row.index[:3]
                        vals = " | ".join(
                            f"{row[c]/1e9:.1f}B" if pd.notna(row[c]) else "N/A"
                            for c in cols
                        )
                        lines.append(f"{row_label}: {vals}")
            except Exception as exc:
                lines.append(f"(Laporan keuangan tidak dapat dibaca: {exc})")
        else:
            lines.append("Laporan keuangan tidak tersedia.")

        lines.append("")
        lines.append(
            "Berikan analisis fundamental komprehensif berdasarkan data di atas. "
            "Fokus pada kualitas earnings, valuasi, pertumbuhan, dan risiko fundamental. "
            "Kembalikan HANYA JSON sesuai format."
        )

        return "\n".join(lines)
