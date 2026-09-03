# IHSG Multi-Agent Trading & Investment Intelligence System

Sistem analisis saham IHSG berbasis multi-agent yang menggabungkan analisis
fundamental, teknikal, volume, makro ekonomi, dan sentimen berita, lalu
mengirimkan hasilnya ke Telegram.

> ⚠️ **Disclaimer**: Sistem ini alat bantu analisis, bukan rekomendasi
> investasi. Baca bagian [Hasil Backtest](#hasil-backtest-yang-jujur) sebelum
> memakai sinyalnya — salah satu dari dua aturan entry terbukti punya
> ekspektasi **negatif**.

---

## Arsitektur Sistem

```
┌─────────────────────────────────────────────────────┐
│                    SUPERVISOR                       │
│   (Orchestrator — agregasi & resolusi konflik)      │
└─────────┬──────┬──────┬──────┬──────────────────────┘
          │      │      │      │
  ┌───────┘  ┌───┘  ┌───┘  ┌──┘
  ▼          ▼      ▼      ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│Fund. │ │Tech. │ │Vol.  │ │Macro │ │News  │
│Agent │ │Agent │ │Agent │ │Agent │ │Sent. │
└──────┘ └──────┘ └──────┘ └──────┘ └──────┘
  ▼                                     ▼
┌───────────────────┐   ┌───────────────────────────┐
│  Alert Engine     │   │  Learning & Eval Agent    │
│  (Telegram Bot)   │   │  (Signal History & Eval)  │
└───────────────────┘   └───────────────────────────┘
```

---

## Cara Sistem Berjalan

Produksi berjalan di **GitHub Actions**, bukan dari mesin lokal. Setiap job
berdiri sendiri lewat `run_job.py`, dijadwalkan oleh cron di
`.github/workflows/`.

| Workflow | Jadwal (WIB) | Job |
|---|---|---|
| `technical.yml` | tiap 30 menit, 09:00–15:30, Sen–Jum | `--job technical` |
| `sentiment.yml` | tiap 1 jam, 09:00–16:00, Sen–Jum | `--job sentiment` |
| `macro.yml` | 08:00, Sen–Jum | `--job macro` |
| `fundamental.yml` | Senin 07:30 | `--job fundamental_weekly` |
| `supervisor.yml` | 15:50, Sen–Jum | `--job supervisor` |
| `keepalive.yml` | tanggal 1 tiap bulan | menjaga cron tetap aktif |

### Dua hal yang wajib dijaga

**1. Zona waktu.** Runner GitHub berjalan di UTC. Semua jadwal di
`scheduler.py` dinyatakan dalam WIB lewat `ZoneInfo("Asia/Jakarta")`, dan tiap
workflow menyetel `TZ: Asia/Jakarta`. Jangan pernah mengganti `_now()` menjadi
`datetime.now()` polos — itu membuat `_is_market_hours()` menolak setiap scan
teknikal, dan kegagalannya tidak terlihat karena log tetap mencetak "WIB".

**2. Keaktifan repo.** GitHub menonaktifkan semua scheduled workflow setelah
**60 hari tanpa commit**. `keepalive.yml` menulis satu baris timestamp tiap
bulan supaya hitungannya ter-reset. Kalau workflow terlanjur mati:

```bash
gh workflow list --all          # cek status "disabled_inactivity"
gh workflow enable technical.yml
```

---

## Instalasi

```bash
python -m venv venv
venv\Scripts\activate           # Windows
source venv/bin/activate        # Linux/macOS
pip install -r requirements.txt
cp .env.example .env
```

Isi `.env`:

| Variabel | Keterangan |
|---|---|
| `LLM_PROVIDER` | `auto` (disarankan), `groq`, atau `gemini` |
| `GROQ_API_KEY` | gratis dari [console.groq.com](https://console.groq.com) |
| `GEMINI_API_KEY` | gratis dari [aistudio.google.com](https://aistudio.google.com/apikey) |
| `TELEGRAM_BOT_TOKEN` | dari [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | dari [@userinfobot](https://t.me/userinfobot) |

Nilai yang sama harus didaftarkan sebagai **repository secrets** di GitHub
(Settings → Secrets and variables → Actions) agar workflow bisa jalan.

Pakai `LLM_PROVIDER=auto`. Dengan `auto`, backend yang gagal — karena kuota
habis, kunci kedaluwarsa, atau model ditarik — otomatis digantikan yang lain.
Mengunci ke satu backend membuat sistem mati total begitu backend itu bermasalah.

### Model LLM

| Provider | Model | Kuota gratis |
|---|---|---|
| Groq | `openai/gpt-oss-120b` | 14.400 req/hari |
| Gemini | `gemini-2.5-flash` | 1.500 req/hari |

Groq rutin menarik model lama. Kalau muncul `404 model_not_found`, cek daftar
model yang masih hidup lalu perbarui `GROQ_MODEL` di `config.py`:

```python
from groq import Groq
print(sorted(m.id for m in Groq(api_key="...").models.list().data))
```

---

## Sumber Berita

Yahoo Finance `.news` sudah tidak berguna untuk ticker `.JK` maupun `^JKSE` —
artikel terbarunya bisa tertinggal berminggu-minggu. Sumber utama sekarang ada
di `utils/news_feed.py`:

| Sumber | Untuk |
|---|---|
| Kontan Investasi (RSS) | berita pasar modal & emiten |
| CNBC Indonesia (RSS) | market & makro |
| Antara Ekonomi (RSS) | makro & kebijakan |
| Google News RSS | berita per-emiten, query `"KODE" saham` |

Yahoo Finance tetap dipakai sebagai cadangan terakhir.

**Pencocokan emiten** memakai konvensi IDX: kode saham ditulis KAPITAL, sering
di dalam kurung — `Laba J Resources (PSAB) Melonjak 803%`. Syarat kapital penuh
itu penting; tanpanya kode seperti `BUMI`, `RAJA`, `RATU`, `DEWA`, dan `ELIT`
akan tertarik oleh kata bahasa Indonesia biasa ("Bank **Bumi** Arta", "**Raja**
Dividen").

---

## Hasil Backtest yang Jujur

Jalankan `python backtest_honest.py` untuk mereproduksi angka di bawah.

Skrip ini menguji **hanya dua aturan yang benar-benar dikirim ke Telegram**,
dengan exit TP1/SL persis seperti yang tercetak di pesan alert, biaya transaksi
(beli 0,15% + jual 0,25%), entry di open bar berikutnya, dan pembagian
in-sample / out-of-sample.

**Out-of-sample (Apr 2024 – Sep 2026), 58 saham:**

| Aturan | N | Winrate | Ekspektasi | Profit Factor |
|---|---:|---:|---:|---:|
| CONSOL BREAKOUT | 80 | 46% | **−0,60%** | 0,74 |
| BUY ON WEAKNESS | 74 | 27% | **+1,65%** | 1,85 |
| Baseline (entry acak) | 6.508 | 41% | −0,28% | 0,88 |

Cara membacanya:

- **CONSOL BREAKOUT rugi secara struktural.** TP1 hampir selalu jatuh di +4%
  sementara SL di −5%, jadi butuh winrate di atas 55% sekadar untuk impas,
  dan yang tercapai 46% — lebih buruk daripada entry acak.
- **BUY ON WEAKNESS positif, tapi rapuh.** Ekspektasinya ditopang 3 trade besar
  (BNBR +57%, RAJA +31%, BNBR +26%) dari 74 sinyal, semuanya saham lapis bawah
  yang tidak likuid. Tanpa ketiganya hasilnya mendekati nol.
- Universe memakai `DEFAULT_TICKERS` hari ini, jadi saham yang sudah delisting
  tidak terwakili (survivorship bias) — angka di atas masih sedikit optimistis.

### Kenapa angka lama dibuang

Skrip `backtest_balance.py`, `backtest_breakout.py`, `backtest_v2.py`,
`backtest_weakness.py`, dan `backtest_technical.py` menguji ~398 kondisi × 3
hold period ≈ **1.200 kombinasi**, lalu mengambil yang winrate-nya tertinggi
dengan ambang `MIN_SIGNALS = 8`. Dengan 1.200 percobaan, winrate 75% pada n=8
muncul dengan mudah hanya karena kebetulan. Tidak ada biaya transaksi, tidak
ada out-of-sample, dan definisi "menang" (close hari ke-N ≥ +2%, tanpa stop
loss) bukan aturan yang dikirim ke siapa pun. Klaim "winrate ~58%" dan
"WR 75%" yang dulu tercetak di pesan Telegram berasal dari sana dan **tidak
bisa dipertanggungjawabkan**. Skrip-skrip itu masih ada untuk eksplorasi, tapi
jangan dipakai sebagai dasar klaim.

---

## Penggunaan Manual

```bash
python run_job.py --job technical          # satu job, lalu keluar
python scheduler.py                        # scheduler penuh (lokal, 24/7)
python scheduler.py --send-schedule        # kirim kartu jadwal ke Telegram

python main.py --ticker BBRI.JK            # analisis satu saham
python main.py --tickers BBRI.JK BBCA.JK
python main.py --screen --min-confidence 70
python main.py --pre-market
python main.py --after-market
python main.py --evaluate

python backtest_honest.py --years 6 --hold 10
```

---

## Aturan Sinyal Teknikal

Didefinisikan di `scheduler.run_technical_volume()`.

**CONSOL BREAKOUT** — semua syarat wajib terpenuhi:
1. Breakout dari fase konsolidasi (ATR 5 hari sebelumnya < 70% rata-ratanya)
2. `macd_line > 0` dan `macd_histogram > 0`
3. `relative_volume >= 1.5`

**BUY ON WEAKNESS** — semua syarat wajib terpenuhi:
1. Turun ≥ 3 hari beruntun
2. MACD histogram negatif tapi mulai naik
3. `bb_pct < 0.20` dan `RSI < 40`

**RADAR** — hampir memenuhi salah satu kondisi di atas; sifatnya pantauan,
bukan sinyal beli.

### Volume pada bar yang belum tutup

Scan berjalan saat pasar masih buka, sehingga bar harian terakhir dari yfinance
berisi volume **sebagian hari**, sementara rata-rata 20 hari berisi hari penuh.
`utils/data_fetcher.session_elapsed_fraction()` menormalkan `relative_volume`
dengan porsi sesi yang sudah berlalu (sesi IDX Sen–Kam 320 menit, Jum 260
menit). Tanpa ini syarat `>= 1.5x` praktis mustahil terpenuhi sebelum siang.
Nilai mentahnya tetap tersedia di `relative_volume_raw`.

### Stop loss

`support_1` adalah minimum 30 close **termasuk bar sinyal**. Saat sinyal muncul
di titik terendah 30 hari — persis situasi BUY ON WEAKNESS — `support_1` sama
dengan harga entry, dan rumus lama `max(support_1, entry*0.95)` memasang stop
loss di harga beli. Terjadi pada **38% sinyal weakness** dan menekan winrate-nya
ke 9,5%. Rumus sekarang mengambil `min(support_1, entry*0.98)` lalu dibatasi
`entry*0.95`, sehingga stop selalu 2–5% di bawah entry.

---

## Struktur Direktori

```
ihsg_system/
├── run_job.py                 # entry point GitHub Actions (satu job)
├── scheduler.py               # semua job + scheduler loop lokal
├── main.py                    # CLI analisis manual
├── config.py                  # watchlist, model LLM, threshold
├── backtest_honest.py         # backtest aturan produksi (dipakai README)
├── backtest_*.py              # eksplorasi lama — jangan jadi dasar klaim
├── agents/
│   ├── base_agent.py          # dispatcher LLM + rate limiter + fallback
│   ├── fundamental_agent.py
│   ├── technical_agent.py
│   ├── volume_agent.py
│   ├── macro_agent.py
│   ├── news_sentiment_agent.py
│   ├── alert_engine.py
│   ├── learning_agent.py
│   └── supervisor.py
├── utils/
│   ├── news_feed.py           # RSS Indonesia + Google News (sumber utama)
│   ├── data_fetcher.py        # yfinance -> StockData, normalisasi volume
│   ├── technical_calculator.py
│   ├── tradingview_ta.py      # TradingView Scanner (tanpa auth)
│   ├── telegram_sender.py
│   ├── agent_cache.py         # cache hasil agent + penanda dedup
│   ├── report_generator.py
│   └── logger.py
└── .github/workflows/         # 5 job terjadwal + keepalive
```

---

## Manajemen Risiko

1. ❌ **Tidak melakukan auto-trading** — hanya menghasilkan sinyal
2. 🔍 **Deteksi konflik sinyal** antar agent, transparan di laporan Supervisor
3. 📉 **Risiko per posisi dibatasi 5%** lewat batas bawah stop loss
4. 🎯 **Confidence scoring** — naik jika konsensus, turun jika konflik
5. 📝 **Logging** ke `logs/ihsg_system.log` dan ke log GitHub Actions

Perlu diketahui: **rasio risk/reward tidak dijamin 1:1.5.** Pada CONSOL
BREAKOUT rasionya justru sekitar 0,8:1 (TP +4% vs SL −5%) — inilah sebab
ekspektasinya negatif. Versi README sebelumnya mengklaim R:R minimum 1:1.5
"enforced"; klaim itu tidak pernah benar.

---

## Lisensi

MIT License — bebas digunakan untuk keperluan pribadi dan komersial.
