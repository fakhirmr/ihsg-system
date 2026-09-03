"""
IHSG Trading System — Base Agent
All analysis agents inherit from this class.

LLM Backend: Groq (Llama 3.3 70B) and/or Google Gemini 2.5 Flash.
Controlled via LLM_PROVIDER env var: "groq" | "gemini" | "auto"
In "auto" mode: Groq is primary with Gemini as fallback (and vice-versa).
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from config import (
    GROQ_API_KEY, GROQ_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    LLM_PROVIDER, MAX_TOKENS,
)

logger = logging.getLogger(__name__)

# ── Groq client (lazy-initialised) ───────────────────────────────────────────
_groq_client = None

def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY not set in .env")
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


# ── Gemini client (lazy-initialised) ─────────────────────────────────────────
_gemini_client = None

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set in .env")
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


# ── Rate limiter ─────────────────────────────────────────────────────────────
# Jeda MINIMUM antar panggilan, bukan tidur tetap sebelum tiap panggilan.
# Versi lama tidur 12 detik tiap kali dipanggil (5 req/menit) padahal kuota
# Groq 30 req/menit — Supervisor closing 58 saham jadi makan 24-33 menit dari
# batas 45 menit. Di sini waktu yang sudah habis untuk request sebelumnya
# ikut diperhitungkan, jadi jeda hanya ditambahkan bila benar-benar perlu.
_MIN_INTERVAL = {"groq": 2.5, "gemini": 4.5}   # detik -> 24 rpm / 13 rpm
_last_call: dict[str, float] = {}


def _is_error_response(raw: str) -> bool:
    """
    Semua jalur gagal di kelas ini memulangkan JSON berbentuk {"error": ...}.
    Dipakai dispatcher untuk memutuskan apakah perlu pindah backend.
    """
    if not raw:
        return True
    text = raw.lstrip()
    if not text.startswith("{"):
        return False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return '"error"' in text[:200]
    return isinstance(parsed, dict) and "error" in parsed and len(parsed) == 1


def _throttle(backend: str) -> None:
    interval = _MIN_INTERVAL[backend]
    last = _last_call.get(backend)
    if last is not None:
        wait = interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[backend] = time.monotonic()


class BaseAgent(ABC):
    """
    Abstract base class for all analysis agents.

    Supports two LLM backends:
      - Groq (Llama 3.3 70B)   — free: 14,400 req/day, 30 req/min
      - Gemini 2.5 Flash        — free: 1,500 req/day, 15 req/min

    LLM_PROVIDER controls which is used:
      "groq"   → Groq only
      "gemini" → Gemini only
      "auto"   → Groq primary, Gemini fallback on rate-limit (recommended)
    """

    def __init__(self) -> None:
        self.name: str = self.__class__.__name__
        self.max_tokens: int = MAX_TOKENS
        self.provider: str = LLM_PROVIDER.lower()

    # ── Primary dispatcher ────────────────────────────────────────────────────

    def call_claude(
        self, system_prompt: str, user_message: str, _retries: int = 3
    ) -> str:
        """
        Send a request to the configured LLM and return the text response.
        Method name kept as call_claude for compatibility with all agent subclasses.

        Backend pilihan didahulukan, tapi backend lain TETAP dicoba kalau
        yang pertama gagal — apa pun penyebabnya.

        Versi lama hanya berpindah ke cadangan saat kena rate limit, dan hanya
        dalam mode "auto". Akibatnya ketika model Groq ditarik (404) atau kunci
        Gemini kedaluwarsa (401), seluruh lapisan LLM ikut mati padahal backend
        satunya sehat.
        """
        backends = {"groq": self._call_groq, "gemini": self._call_gemini}

        primary = self.provider if self.provider in backends else "groq"
        order = [primary] + [b for b in backends if b != primary]

        result = ""
        for i, backend in enumerate(order):
            result = backends[backend](system_prompt, user_message, _retries)
            if not _is_error_response(result):
                return result
            if i + 1 < len(order):
                logger.warning(
                    f"[{self.name}] {backend} gagal ({result[:120]}) "
                    f"— beralih ke {order[i + 1]}."
                )
        return result

    # ── Groq backend ──────────────────────────────────────────────────────────

    def _call_groq(
        self, system_prompt: str, user_message: str, _retries: int = 3
    ) -> str:
        """Call Groq API (Llama 3.3 70B). Retries on 429 with backoff."""
        for attempt in range(1, _retries + 1):
            try:
                client = _get_groq_client()
                _throttle("groq")
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    max_tokens=self.max_tokens,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                )
                logger.debug(f"[{self.name}] Groq OK (attempt {attempt})")
                return response.choices[0].message.content.strip()

            except RuntimeError as exc:
                logger.error(f"[{self.name}] Groq config error: {exc}")
                return '{"error": "Groq not configured — set GROQ_API_KEY in .env"}'

            except Exception as exc:
                err_msg = str(exc)
                if "401" in err_msg or "invalid_api_key" in err_msg.lower() or "authentication" in err_msg.lower():
                    logger.error(f"[{self.name}] Invalid Groq API key.")
                    return '{"error": "Invalid Groq API key"}'
                elif "429" in err_msg or "rate_limit" in err_msg.lower():
                    wait = 30
                    logger.warning(
                        f"[{self.name}] Groq rate limit hit (attempt {attempt}/{_retries}). "
                        f"Waiting {wait}s..."
                    )
                    if attempt < _retries:
                        time.sleep(wait)
                        continue
                    return '{"error": "Rate limit — all retries exhausted"}'
                else:
                    logger.error(f"[{self.name}] Groq API error: {exc}")
                    return f'{{"error": "{err_msg}"}}'

        return '{"error": "Unexpected exit from Groq retry loop"}'

    # ── Gemini backend ────────────────────────────────────────────────────────

    def _call_gemini(
        self, system_prompt: str, user_message: str, _retries: int = 3
    ) -> str:
        """Call Google Gemini 2.5 Flash API. Retries on 429 with backoff."""
        from google.genai import types

        combined_prompt = f"{system_prompt}\n\n{user_message}"

        for attempt in range(1, _retries + 1):
            try:
                client = _get_gemini_client()
                _throttle("gemini")
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=combined_prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=self.max_tokens * 2,  # Gemini needs more room
                    ),
                )
                logger.debug(f"[{self.name}] Gemini OK (attempt {attempt})")
                return response.text.strip()

            except RuntimeError as exc:
                logger.error(f"[{self.name}] Gemini config error: {exc}")
                return '{"error": "Gemini not configured — set GEMINI_API_KEY in .env"}'

            except Exception as exc:
                err_msg = str(exc)
                if "401" in err_msg or "API_KEY_INVALID" in err_msg or "invalid" in err_msg.lower() and "key" in err_msg.lower():
                    logger.error(f"[{self.name}] Invalid Gemini API key.")
                    return '{"error": "Invalid Gemini API key"}'
                elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower():
                    wait = 30
                    logger.warning(
                        f"[{self.name}] Gemini rate limit hit (attempt {attempt}/{_retries}). "
                        f"Waiting {wait}s..."
                    )
                    if attempt < _retries:
                        time.sleep(wait)
                        continue
                    return '{"error": "Gemini rate limit — all retries exhausted"}'
                else:
                    logger.error(f"[{self.name}] Gemini API error: {exc}")
                    return f'{{"error": "{err_msg}"}}'

        return '{"error": "Unexpected exit from Gemini retry loop"}'

    # ── JSON helper ───────────────────────────────────────────────────────────

    def call_claude_json(
        self, system_prompt: str, user_message: str, fallback: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Call the configured LLM and parse the response as JSON.
        Returns `fallback` if parsing fails.
        Handles markdown fences and stray text before/after JSON (common with Gemini).
        """
        raw = self.call_claude(system_prompt, user_message)
        clean = raw.strip()

        # Strip markdown fences if present (```json ... ```)
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1]) if len(lines) > 2 else clean
            clean = clean.strip()

        # Try direct parse first
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            pass

        # Fallback: find the outermost { } JSON block in the text
        # (Gemini sometimes adds prose before/after the JSON)
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = clean[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        logger.warning(
            f"[{self.name}] Failed to parse JSON response. "
            f"Raw (first 200 chars): {raw[:200]}"
        )
        return fallback

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def analyze(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Run the agent's analysis and return a result dict."""
