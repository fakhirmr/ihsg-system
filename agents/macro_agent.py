"""
IHSG Trading System — Macro Economic Agent
Assesses global and domestic macro conditions and their impact on IHSG sectors.
Data IHSG real-time (^JKSE) disuntikkan ke prompt agar bias tidak salah.
"""
from __future__ import annotations

import logging
from typing import Any

import yfinance as yf
import pandas as pd

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Kamu adalah Macro Economic Agent yang sangat berpengalaman dalam pasar modal Indonesia (IHSG).
Tugasmu menilai kondisi makro ekonomi global dan domestik serta dampaknya terhadap sektor-sektor di IHSG.

PENTING: Gunakan data teknikal IHSG aktual yang diberikan sebagai landasan utama dalam menentukan ihsg_bias.
Data teknikal LEBIH AKURAT daripada asumsi — jangan override data nyata dengan opini bullish.

Aturan penentuan ihsg_bias berdasarkan data teknikal IHSG:
- IHSG di bawah EMA20 DAN EMA50 → ihsg_bias = "Bearish" (wajib)
- IHSG di bawah EMA200 → tambahan konfirmasi Bearish
- IHSG di atas EMA20 dan EMA50 tapi di bawah EMA200 → ihsg_bias = "Neutral"
- IHSG di atas semua EMA (20/50/200) → bisa "Bullish"
- Perubahan harga mingguan negatif → condong Bearish
- RSI < 45 → momentum lemah/Bearish

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
  "ihsg_technical_summary": "<ringkasan posisi teknikal IHSG dalam 1 kalimat>",
  "summary": "<ringkasan kondisi makro dalam Bahasa Indonesia>"
}

Sektor IHSG yang harus dipertimbangkan:
Perbankan, Energi, Batu Bara, Nikel, Emas & Mineral, Telekomunikasi, Konsumer,
Properti, Infrastruktur, Kesehatan, Teknologi, Otomotif, Semen, Retail.
"""

_USER_TEMPLATE = """\
Berikan analisis makro untuk pasar IHSG pada {context}.

=== DATA TEKNIKAL IHSG (^JKSE) — REAL TIME ===
{ihsg_data}
=== END DATA IHSG ===

Pertimbangkan faktor-faktor makro berikut:
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

INGAT: Gunakan DATA TEKNIKAL IHSG di atas sebagai fakta utama untuk menentukan ihsg_bias.
Jangan bias ke Bullish jika data menunjukkan IHSG sedang dalam tekanan.
Kembalikan HANYA JSON sesuai format.
"""


def _fetch_ihsg_data() -> str:
    """Ambil data IHSG real-time dan hitung indikator teknikal dasar."""
    try:
        ticker = yf.Ticker("^JKSE")
        hist = ticker.history(period="6mo")
        if hist.empty:
            return "Data IHSG tidak tersedia (yfinance gagal)."

        close = hist["Close"]
        current = float(close.iloc[-1])
        prev_day = float(close.iloc[-2]) if len(close) >= 2 else current
        prev_week = float(close.iloc[-6]) if len(close) >= 6 else current
        prev_month = float(close.iloc[-22]) if len(close) >= 22 else current

        chg_day   = (current - prev_day)   / prev_day   * 100
        chg_week  = (current - prev_week)  / prev_week  * 100
        chg_month = (current - prev_month) / prev_month * 100

        ema20  = float(close.ewm(span=20,  adjust=False).mean().iloc[-1])
        ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
        rs    = gain / loss.replace(0, 1e-9)
        rsi   = float(100 - (100 / (1 + rs.iloc[-1])))

        # 52-week high/low
        high52 = float(close.max())
        low52  = float(close.min())
        pct_from_high = (current - high52) / high52 * 100
        pct_from_low  = (current - low52)  / low52  * 100

        # Posisi relatif EMA
        vs_ema20  = (current - ema20)  / ema20  * 100
        vs_ema50  = (current - ema50)  / ema50  * 100
        vs_ema200 = (current - ema200) / ema200 * 100

        # Tentukan trend
        if current > ema20 > ema50:
            trend = "UPTREND"
        elif current < ema20 < ema50:
            trend = "DOWNTREND"
        else:
            trend = "MIXED / SIDEWAYS"

        # Apakah di bawah semua EMA?
        bearish_flags = []
        if current < ema20:
            bearish_flags.append(f"Di BAWAH EMA20 ({vs_ema20:+.2f}%)")
        if current < ema50:
            bearish_flags.append(f"Di BAWAH EMA50 ({vs_ema50:+.2f}%)")
        if current < ema200:
            bearish_flags.append(f"Di BAWAH EMA200 ({vs_ema200:+.2f}%)")

        data_str = f"""\
Harga IHSG       : {current:,.2f}
Perubahan 1 Hari : {chg_day:+.2f}%
Perubahan 1 Minggu: {chg_week:+.2f}%
Perubahan 1 Bulan: {chg_month:+.2f}%

EMA 20           : {ema20:,.2f} ({vs_ema20:+.2f}% dari harga)
EMA 50           : {ema50:,.2f} ({vs_ema50:+.2f}% dari harga)
EMA 200          : {ema200:,.2f} ({vs_ema200:+.2f}% dari harga)
RSI (14)         : {rsi:.1f}
Tren EMA         : {trend}

52-Week High     : {high52:,.2f} ({pct_from_high:+.2f}% dari high)
52-Week Low      : {low52:,.2f} ({pct_from_low:+.2f}% dari low)

Sinyal Bearish   : {', '.join(bearish_flags) if bearish_flags else 'Tidak ada — IHSG di atas semua EMA'}
"""
        logger.info(f"[MacroAgent] IHSG data fetched: {current:,.2f} | Trend: {trend} | RSI: {rsi:.1f}")
        return data_str

    except Exception as e:
        logger.warning(f"[MacroAgent] Gagal fetch data IHSG: {e}")
        return f"Data IHSG gagal diambil: {e}"


class MacroAgent(BaseAgent):
    """Evaluates macro economic conditions and their sector impact on IHSG.
    
    Menggunakan data teknikal IHSG real-time (^JKSE) sebagai anchor
    agar ihsg_bias tidak menyimpang dari kondisi chart aktual.
    """

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
            "ihsg_technical_summary": "Data tidak tersedia.",
            "summary": "Kondisi makro tidak dapat dianalisis saat ini.",
        }

        # Ambil data IHSG real-time
        ihsg_data = _fetch_ihsg_data()

        user_message = _USER_TEMPLATE.format(
            context=context,
            ihsg_data=ihsg_data,
            ticker=ticker or "General IHSG",
            sector=sector or "Semua Sektor",
        )

        result = self.call_claude_json(_SYSTEM_PROMPT, user_message, fallback)
        result.setdefault("ihsg_technical_summary", "")

        # ── Validasi paksa berdasarkan data teknikal nyata ──────────────────────
        # Aturan:
        #   IHSG < EMA20 & EMA50          → wajib Bearish
        #   IHSG < EMA200 saja (di atas 20&50) → Neutral maksimal
        #   LLM bias apa pun → di-override jika bertentangan dengan data

        _below_ema20  = "Di BAWAH EMA20"  in ihsg_data
        _below_ema50  = "Di BAWAH EMA50"  in ihsg_data
        _below_ema200 = "Di BAWAH EMA200" in ihsg_data

        llm_bias = result.get("ihsg_bias", "Neutral")

        if _below_ema20 and _below_ema50:
            # Kondisi jelas bearish — paksa semua field sesuai
            if llm_bias != "Bearish":
                result["ihsg_bias"]       = "Bearish"
                result["sentiment"]       = "Bearish"
                # Pilih market_condition yang valid dan konsisten
                old_cond = result.get("market_condition", "Neutral")
                if "Risk-On" in old_cond or "Mild Bullish" in old_cond or "Bullish" in old_cond:
                    result["market_condition"] = "Mild Bearish"
                elif old_cond == "Neutral":
                    result["market_condition"] = "Mild Bearish"
                # else: sudah Bearish / Risk-Off → biarkan
                logger.warning(
                    f"[MacroAgent] LLM bias override: {llm_bias} → Bearish "
                    f"(IHSG di bawah EMA20 & EMA50 | data nyata)"
                )

        elif _below_ema200 and llm_bias == "Bullish":
            # Di atas EMA20/50 tapi masih di bawah EMA200 → Neutral maksimal
            result["ihsg_bias"]       = "Neutral"
            result["sentiment"]       = "Neutral"
            old_cond = result.get("market_condition", "Neutral")
            if "Bullish" in old_cond or "Risk-On" in old_cond:
                result["market_condition"] = "Neutral"
            logger.warning(
                f"[MacroAgent] LLM bias override: {llm_bias} → Neutral "
                f"(IHSG masih di bawah EMA200)"
            )

        logger.info(
            f"[MacroAgent] Condition:{result.get('market_condition')} "
            f"IHSG Bias:{result.get('ihsg_bias')} "
            f"Positive Sectors:{result.get('positive_sectors', [])}"
        )
        return result
