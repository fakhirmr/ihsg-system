# IHSG Multi-Agent Trading & Investment Intelligence System

Sistem analisis saham IHSG berbasis multi-agent AI (Claude) yang menggabungkan analisis fundamental, teknikal, volume, makro ekonomi, dan sentimen berita untuk menghasilkan sinyal trading yang berkualitas tinggi.

---

## Arsitektur Sistem

```
┌─────────────────────────────────────────────────────┐
│                    SUPERVISOR AI                    │
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

## Instalasi

### 1. Clone / Download Proyek

```bash
cd ihsg_system
```

### 2. Buat Virtual Environment (Rekomendasi)

```bash
python -m venv venv
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Konfigurasi Environment

```bash
cp .env.example .env
```

Edit file `.env` dan isi:
- `ANTHROPIC_API_KEY` — API key dari [console.anthropic.com](https://console.anthropic.com)
- `TELEGRAM_BOT_TOKEN` — Token bot dari [@BotFather](https://t.me/BotFather)
- `TELEGRAM_CHAT_ID` — Chat ID tujuan (gunakan [@userinfobot](https://t.me/userinfobot))

---

## Cara Penggunaan

### Analisis Satu Saham

```bash
python main.py --ticker BBRI.JK
python main.py --ticker ANTM.JK --news "Harga nikel naik 5%"
python main.py --ticker BBCA.JK --no-alert
python main.py --ticker TLKM.JK --json
```

### Analisis Beberapa Saham

```bash
python main.py --tickers BBRI.JK BBCA.JK BMRI.JK
```

### Screening Semua Saham Default

```bash
python main.py --screen
python main.py --screen --min-confidence 70
python main.py --screen --no-alert
```

### Laporan Pre-Market

```bash
python main.py --pre-market
```

### Laporan After-Market

```bash
python main.py --after-market
```

### Evaluasi Performa Signal

```bash
python main.py --evaluate
```

---

## Format Output Sinyal

```
==================================================
🟢  SIGNAL: BUY  |  Confidence: 84%
==================================================
  Ticker  : BBRI.JK
  Company : Bank Rakyat Indonesia
  Sector  : Financial Services
  Price   : 4,850 IDR (+1.25%)
  Entry   : 4,850
  TP1     : 5,050
  TP2     : 5,200
  SL      : 4,720
  TF      : Swing

  📈 Technical: BUY 82%
     • Breakout resistance valid
     • Volume 3x rata-rata
     • EMA 20/50 bullish alignment

  📊 Fundamental: Bullish (Score: 78)
     ✅ ROE tinggi
     ✅ Laba tumbuh konsisten

  📦 Volume: Unusual Bullish Activity (Unusual: True)
     • Volume 3.2x avg 20 hari

  🌐 Macro: Mild Bullish
     ✅ Positive: Perbankan, Konsumer

  📰 Sentiment: Bullish 72%
     • Sentimen perbankan positif
==================================================
```

---

## Konfigurasi (config.py)

| Parameter | Default | Keterangan |
|---|---|---|
| `DEFAULT_TICKERS` | 10 saham | Daftar saham default untuk screening |
| `ANALYSIS_PERIOD` | `3mo` | Periode history harga (yfinance format) |
| `VOLUME_SPIKE_THRESHOLD` | `2.5` | Threshold relative volume untuk spike |
| `MIN_CONFIDENCE_ALERT` | `65` | Minimum confidence untuk kirim alert Telegram |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Model Claude yang digunakan |

---

## Manajemen Risiko

Sistem ini dirancang dengan prinsip manajemen risiko ketat:

1. ❌ **Tidak melakukan auto-trading** — hanya menghasilkan sinyal
2. ⚠️ **Menghindari saham suspend** — filter likuiditas aktif
3. 🔍 **Deteksi konflik sinyal** — transparent multi-agent conflict detection
4. 📊 **Risk:Reward minimum 1:1.5** — enforced pada level generation
5. 🎯 **Confidence scoring** — boost jika konsensus, penalti jika konflik
6. 📝 **Logging lengkap** — semua keputusan tercatat di `logs/ihsg_system.log`

> ⚠️ **Disclaimer**: Sistem ini adalah alat bantu analisis, bukan rekomendasi investasi. Selalu lakukan riset mandiri dan konsultasikan dengan financial advisor sebelum mengambil keputusan investasi.

---

## Struktur Direktori

```
ihsg_system/
├── main.py                    # CLI entry point
├── config.py                  # Konfigurasi sistem
├── requirements.txt
├── .env.example               # Template environment variables
├── README.md
├── agents/
│   ├── base_agent.py          # Abstract base (Claude API)
│   ├── fundamental_agent.py   # Analisis fundamental
│   ├── technical_agent.py     # Analisis teknikal + sinyal entry/TP/SL
│   ├── volume_agent.py        # Deteksi volume abnormal
│   ├── macro_agent.py         # Kondisi makro ekonomi
│   ├── news_sentiment_agent.py # Sentimen berita
│   ├── alert_engine.py        # Pengiriman alert Telegram
│   ├── learning_agent.py      # Evaluasi performa historis
│   └── supervisor.py          # Supervisor AI (orchestrator)
├── utils/
│   ├── data_fetcher.py        # Wrapper yfinance → StockData
│   ├── technical_calculator.py # Komputasi indikator teknikal
│   ├── telegram_sender.py     # Kirim pesan Telegram
│   ├── report_generator.py    # Format laporan
│   └── logger.py              # Setup logging
├── data/
│   └── signal_history.json    # Historis sinyal (auto-generated)
├── logs/
│   └── ihsg_system.log        # Log file (auto-generated)
└── reports/
    └── *.txt                  # Laporan harian (auto-generated)
```

---

## Automasi (Opsional)

### Cron Job untuk Laporan Harian

```bash
# Pre-market setiap hari kerja jam 08:30 WIB
30 8 * * 1-5 cd /path/to/ihsg_system && python main.py --pre-market

# After-market setiap hari kerja jam 16:30 WIB
30 16 * * 1-5 cd /path/to/ihsg_system && python main.py --after-market

# Screening jam 09:00 dan 13:00 WIB
0 9,13 * * 1-5 cd /path/to/ihsg_system && python main.py --screen --min-confidence 70
```

---

## Update Outcome Signal

Setelah posisi ditutup, update outcome untuk melatih Learning Agent:

```python
from agents.learning_agent import LearningAgent

agent = LearningAgent()
agent.update_outcome(
    ticker="BBRI.JK",
    timestamp="2025-05-12",  # prefix ISO timestamp
    outcome="WIN",           # WIN | LOSS | BREAKEVEN
    return_pct=4.12,
)
```

---

## Lisensi

MIT License — Bebas digunakan untuk keperluan pribadi dan komersial.
