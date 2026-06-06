"""
cleanup_telegram_spam.py
========================
Hapus pesan duplikat di channel Telegram yang sudah terkirim.

Cara kerja:
  1. Ambil recent channel_post via getUpdates (menangkap pesan yang belum
     di-acknowledge oleh bot sebagai "sudah diproses").
  2. Kelompokkan pesan berdasarkan FINGERPRINT = judul/baris pertama pesan
     (mis. "Market & Macro News Analysis", "Sentiment Alert — BBCA", dst).
  3. Dalam setiap kelompok, pertahankan pesan TERBARU, hapus sisanya.

Usage:
    cd ihsg_system
    python cleanup_telegram_spam.py              # Preview + hapus
    python cleanup_telegram_spam.py --dry-run    # Preview saja, tidak hapus
    python cleanup_telegram_spam.py --ids 101 102 103  # Hapus ID tertentu langsung
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# --- path setup ---------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
CHAT_ID = TELEGRAM_CHAT_ID.strip()


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _api(method: str, **kwargs) -> dict:
    try:
        resp = requests.post(f"{BASE}/{method}", json=kwargs, timeout=15)
        return resp.json()
    except Exception as exc:
        return {"ok": False, "description": str(exc)}


def delete_message(message_id: int, dry_run: bool = False) -> bool:
    if dry_run:
        print(f"    [DRY-RUN] hapus message_id={message_id}")
        return True
    result = _api("deleteMessage", chat_id=CHAT_ID, message_id=message_id)
    ok = result.get("ok", False)
    if not ok:
        desc = result.get("description", "?")
        print(f"    [GAGAL] id={message_id}: {desc}")
    return ok


def fetch_channel_posts(limit: int = 100) -> list[dict]:
    """
    Ambil channel_post pending dari getUpdates.
    Bot hanya mendapat channel_post jika ia admin channel dan
    allowed_updates menyertakan 'channel_post'.
    """
    posts: list[dict] = []
    offset: int | None = None

    while True:
        params: dict = {
            "limit": min(limit - len(posts), 100),
            "allowed_updates": ["channel_post"],
            "timeout": 0,
        }
        if offset is not None:
            params["offset"] = offset

        resp = requests.get(f"{BASE}/getUpdates", params=params, timeout=20)
        updates: list[dict] = resp.json().get("result", [])

        if not updates:
            break

        for upd in updates:
            post = upd.get("channel_post")
            if post:
                post_chat = str(post.get("chat", {}).get("id", ""))
                # Chat ID bisa -100xxxxxxxxxx (channel) atau -xxxxxxxxxx
                if CHAT_ID.lstrip("-") in post_chat.lstrip("-"):
                    posts.append(post)
            offset = upd["update_id"] + 1

        if len(updates) < 100 or len(posts) >= limit:
            break

    return posts


def probe_message(message_id: int) -> dict | None:
    """
    Coba forward pesan dari channel ke chat yang sama untuk mengambil isinya.
    Hanya digunakan pada mode --range; hasilnya langsung dihapus.
    """
    result = _api(
        "forwardMessage",
        chat_id=CHAT_ID,
        from_chat_id=CHAT_ID,
        message_id=message_id,
    )
    if result.get("ok"):
        fwd = result["result"]
        # Hapus pesan forward yang baru dibuat (tidak perlu tersimpan)
        _api("deleteMessage", chat_id=CHAT_ID, message_id=fwd["message_id"])
        return fwd
    return None


# ── Fingerprinting ─────────────────────────────────────────────────────────────

# Pola yang dikenali sebagai "tipe" pesan IHSG System
_TYPE_PATTERNS = [
    r"Market & Macro News Analysis",
    r"Macro Update",
    r"Sentiment Alert — (\S+)",
    r"Sentiment Scan",
    r"Technical & Volume (Alert|Digest)",
    r"BREAKOUT ALERT",
    r"Fundamental Weekly Report",
    r"Fundamental Review \(Dipicu Sentimen\)",
    r"Supervisor Closing Report",
    r"Net Foreign Flow",
    r"IHSG System — Jadwal Agent",
]
_TYPE_RE = re.compile("|".join(f"({p})" for p in _TYPE_PATTERNS), re.IGNORECASE)


def message_fingerprint(text: str) -> str:
    """
    Kembalikan key unik berdasarkan TIPE pesan.
    Pesan dengan tipe sama dianggap duplikat kandidat.
    Contoh: semua "Sentiment Alert — BBCA" punya fingerprint "sentiment_bbca".
    """
    if not text:
        return "unknown"

    first_line = text.split("\n")[0].strip()
    # Hapus tag HTML dari baris pertama
    first_line = re.sub(r"<[^>]+>", "", first_line).strip()

    m = _TYPE_RE.search(first_line)
    if m:
        matched = m.group(0)
        # Normalise: lowercase, no spaces
        key = re.sub(r"\s+", "_", matched.lower().strip())
        key = re.sub(r"[^a-z0-9_]", "", key)
        return key

    # Fallback: 60 karakter pertama
    return first_line[:60].lower().replace(" ", "_")


# ── Main logic ─────────────────────────────────────────────────────────────────

def find_duplicates(posts: list[dict]) -> dict[str, list[dict]]:
    """
    Kelompokkan posts berdasarkan fingerprint.
    Kembalikan hanya kelompok yang punya > 1 anggota (duplikat).
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for post in posts:
        text = post.get("text", "")
        fp = message_fingerprint(text)
        groups[fp].append(post)

    return {fp: msgs for fp, msgs in groups.items() if len(msgs) > 1}


def run_cleanup(posts: list[dict], dry_run: bool) -> int:
    """Hapus semua duplikat, pertahankan pesan terbaru per tipe. Return jumlah dihapus."""
    duplicates = find_duplicates(posts)

    if not duplicates:
        print("Tidak ada duplikat ditemukan.")
        return 0

    total_deleted = 0
    for fp, msgs in duplicates.items():
        # Urutkan dari terlama ke terbaru, hapus semua kecuali yg paling baru
        msgs_sorted = sorted(msgs, key=lambda m: m.get("message_id", 0))
        to_delete = msgs_sorted[:-1]   # Semua kecuali yang terakhir (terbaru)
        keep = msgs_sorted[-1]

        keep_ts = datetime.fromtimestamp(keep.get("date", 0)).strftime("%d/%m %H:%M")
        print(f"\n[{fp}] {len(msgs)} pesan — simpan id={keep['message_id']} ({keep_ts}), hapus {len(to_delete)} duplikat:")

        for msg in to_delete:
            ts = datetime.fromtimestamp(msg.get("date", 0)).strftime("%d/%m %H:%M")
            preview = re.sub(r"<[^>]+>", "", msg.get("text", ""))[:60].replace("\n", " ")
            print(f"  → id={msg['message_id']} ({ts}): {preview!r}")
            if delete_message(msg["message_id"], dry_run=dry_run):
                total_deleted += 1
            time.sleep(0.3)  # Hindari rate limit Telegram

    return total_deleted


def delete_by_ids(ids: list[int], dry_run: bool) -> int:
    """Hapus pesan berdasarkan daftar message_id yang diberikan langsung."""
    deleted = 0
    for mid in ids:
        print(f"  Hapus id={mid} ...", end=" ")
        if delete_message(mid, dry_run=dry_run):
            deleted += 1
            print("OK")
        else:
            print("GAGAL")
        time.sleep(0.3)
    return deleted


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Hapus pesan duplikat dari channel Telegram")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview saja — tidak benar-benar menghapus")
    parser.add_argument("--limit", type=int, default=200,
                        help="Jumlah maks channel_post yang diambil (default: 200)")
    parser.add_argument("--ids", type=int, nargs="+", metavar="MSG_ID",
                        help="Hapus message_id tertentu secara langsung")
    args = parser.parse_args()

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN tidak diset.")
        sys.exit(1)
    if not CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID tidak diset.")
        sys.exit(1)

    print(f"Channel: {CHAT_ID}")
    print(f"Mode   : {'DRY-RUN (tidak ada yang dihapus)' if args.dry_run else 'LIVE (akan menghapus)'}\n")

    # Mode 1: Hapus ID yang disebutkan langsung
    if args.ids:
        print(f"Menghapus {len(args.ids)} pesan berdasarkan ID yang diberikan...")
        n = delete_by_ids(args.ids, dry_run=args.dry_run)
        print(f"\nSelesai: {n}/{len(args.ids)} pesan dihapus.")
        return

    # Mode 2: Ambil channel_post via getUpdates, lalu dedup
    print(f"Mengambil channel post dari getUpdates (limit={args.limit})...")
    posts = fetch_channel_posts(limit=args.limit)
    print(f"Ditemukan {len(posts)} channel post.\n")

    if not posts:
        print(
            "Tidak ada channel_post yang ditemukan dari getUpdates.\n"
            "\n"
            "Kemungkinan penyebab:\n"
            "  1. Semua update sudah pernah di-acknowledge sebelumnya.\n"
            "  2. Bot bukan admin channel dengan izin 'Manage messages'.\n"
            "  3. allowed_updates belum menyertakan 'channel_post'.\n"
            "\n"
            "Solusi manual:\n"
            "  Buka Telegram, catat message_id pesan duplikat, lalu jalankan:\n"
            "  python cleanup_telegram_spam.py --ids 12345 12346 12347"
        )
        return

    n = run_cleanup(posts, dry_run=args.dry_run)
    suffix = "(preview)" if args.dry_run else "dihapus"
    print(f"\n=== Selesai: {n} pesan duplikat {suffix} ===")


if __name__ == "__main__":
    main()
