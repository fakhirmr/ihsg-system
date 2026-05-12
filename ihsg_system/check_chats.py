"""Cek semua chat yang dikenal oleh bot (termasuk channel)."""
import requests

TOKEN = "8588965172:AAHdWbIB8R_1S9xGjSu3msI-Ika0ASR99U8"

r = requests.get(
    f"https://api.telegram.org/bot{TOKEN}/getUpdates",
    timeout=10
).json()

updates = r.get("result", [])
if not updates:
    print("Belum ada update. Pastikan:")
    print("1. Bot sudah ditambahkan sebagai Admin di channel")
    print("2. Ada pesan yang dikirim di channel setelah bot ditambahkan")
    print("3. Atau forward pesan dari channel ke @fakhirtradebot")
else:
    chats = {}
    for u in updates:
        # Cek message biasa atau channel_post
        chat_data = (
            u.get("message", {}).get("chat") or
            u.get("channel_post", {}).get("chat") or
            {}
        )
        if chat_data:
            chat_id = str(chat_data.get("id", ""))
            if chat_id and chat_id not in chats:
                chats[chat_id] = chat_data

    print(f"\nDitemukan {len(chats)} chat:\n")
    for cid, c in chats.items():
        tipe  = c.get("type", "")
        nama  = c.get("title") or c.get("first_name", "")
        uname = c.get("username", "")
        print(f"  ID   : {cid}")
        print(f"  Nama : {nama}")
        print(f"  Tipe : {tipe}")
        if uname:
            print(f"  Link : @{uname}")
        print()
