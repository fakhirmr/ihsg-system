"""
IHSG Trading System -- Telegram Setup & Test
============================================
Jalankan script ini untuk:
1. Mendapatkan Chat ID kamu secara otomatis
2. Test kirim pesan ke Telegram
3. Update .env secara otomatis

Usage:
    python setup_telegram.py
"""
from __future__ import annotations

import io
import os
import sys

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
import time
from dotenv import load_dotenv, set_key
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _get(token: str, method: str, params: dict = {}) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    r = requests.get(url, params=params, timeout=10)
    return r.json()


def validate_token(token: str) -> bool:
    resp = _get(token, "getMe")
    if resp.get("ok"):
        bot = resp["result"]
        print(f"\n  ✅ Bot valid!")
        print(f"     Nama  : {bot.get('first_name')}")
        print(f"     Username : @{bot.get('username')}")
        return True
    else:
        print(f"\n  ❌ Token tidak valid: {resp.get('description', 'Unknown error')}")
        return False


def get_chat_id(token: str) -> str | None:
    """Poll getUpdates to find the first incoming chat ID."""
    print("\n  ⏳ Menunggu pesan masuk dari Telegram...")
    print("     → Buka Telegram dan kirim pesan /start ke bot kamu sekarang!")
    print("     → Menunggu 60 detik...\n")

    deadline = time.time() + 60
    seen_ids: set = set()

    while time.time() < deadline:
        resp = _get(token, "getUpdates", {"timeout": 5, "limit": 10})
        if resp.get("ok"):
            updates = resp.get("result", [])
            for update in updates:
                msg = update.get("message") or update.get("channel_post")
                if msg:
                    chat = msg["chat"]
                    chat_id = str(chat["id"])
                    chat_title = chat.get("title") or chat.get("first_name", "")
                    chat_type = chat.get("type", "")

                    if chat_id not in seen_ids:
                        seen_ids.add(chat_id)
                        print(f"  📨 Chat ditemukan!")
                        print(f"     ID   : {chat_id}")
                        print(f"     Nama : {chat_title}")
                        print(f"     Tipe : {chat_type}")
                        return chat_id
        time.sleep(2)

    return None


def send_test_message(token: str, chat_id: str) -> bool:
    msg = (
        "🤖 <b>IHSG Trading System</b>\n\n"
        "✅ Koneksi Telegram berhasil!\n\n"
        "Sistem siap mengirim:\n"
        "• 🟢 Sinyal BUY/SELL real-time\n"
        "• 📊 Laporan pre-market & after-market\n"
        "• ⚠️ Alert konflik & risiko\n\n"
        "<i>Setup selesai. Selamat trading!</i>"
    )
    url = TELEGRAM_API.format(token=token, method="sendMessage")
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=10)
    return resp.json().get("ok", False)


def main():
    print("=" * 55)
    print("  IHSG System — Telegram Setup Wizard")
    print("=" * 55)

    # ── Step 1: Token ──────────────────────────────────────────
    existing_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if existing_token:
        print(f"\n[1/3] Token sudah ada di .env.")
        use_existing = input("      Pakai token yang sama? (y/n): ").strip().lower()
        token = existing_token if use_existing != "n" else ""
    else:
        token = ""

    if not token:
        print("\n[1/3] Masukkan Bot Token dari @BotFather:")
        print("      (Buka Telegram -> cari @BotFather -> /newbot)")
        token = input("      Token: ").strip()

    if not validate_token(token):
        print("\n[ERROR] Setup dibatalkan. Periksa token kamu.")
        sys.exit(1)

    # -- Step 2: Chat ID ----------------------------------------
    existing_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if existing_chat:
        print(f"\n[2/3] Chat ID sudah ada: {existing_chat}")
        use_existing = input("      Pakai Chat ID yang sama? (y/n): ").strip().lower()
        chat_id = existing_chat if use_existing != "n" else None
    else:
        chat_id = None

    if not chat_id:
        print("\n[2/3] Cara mendapatkan Chat ID:")
        print("      Opsi A -- Untuk chat pribadi/grup:")
        print("               Kirim pesan /start ke bot kamu di Telegram")
        print("      Opsi B -- Untuk channel:")
        print("               Tambahkan bot sebagai Admin di channel,")
        print("               lalu forward 1 pesan dari channel ke bot\n")
        print("      Pilih mode:")
        print("      [1] Auto-detect (kirim /start ke bot -> tunggu 60 detik)")
        print("      [2] Input manual Chat ID")
        choice = input("      Pilihan (1/2): ").strip()

        if choice == "2":
            chat_id = input("      Chat ID: ").strip()
        else:
            chat_id = get_chat_id(token)

    if not chat_id:
        print("\n❌ Chat ID tidak ditemukan. Coba lagi atau input manual.")
        sys.exit(1)

    # ── Step 3: Test & Save ────────────────────────────────────
    print(f"\n[3/3] Mengirim pesan test ke {chat_id}...")
    if send_test_message(token, chat_id):
        print("      ✅ Pesan berhasil dikirim!")

        # Save to .env
        set_key(str(ENV_FILE), "TELEGRAM_BOT_TOKEN", token)
        set_key(str(ENV_FILE), "TELEGRAM_CHAT_ID", chat_id)

        print("\n" + "=" * 55)
        print("  ✅ SETUP SELESAI!")
        print(f"     Token   : {token[:10]}...{token[-5:]}")
        print(f"     Chat ID : {chat_id}")
        print("     .env    : Tersimpan otomatis")
        print("=" * 55)
        print("\n  Jalankan sistem:")
        print("  python main.py --ticker BBRI.JK")
        print()
    else:
        print("      ❌ Gagal mengirim pesan. Periksa Chat ID.")
        sys.exit(1)


if __name__ == "__main__":
    main()
