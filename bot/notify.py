"""
Pengiriman embed Discord untuk Gem (v0.5).
Webhook dibaca dari env DISCORD_WEBHOOK_URL (secret repo).
Jika GEM_DRY_RUN=1, embed hanya dicetak dan tidak dikirim.
"""
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from bot.config import FDV_WARN_RATIO, USER_AGENT
from bot.saran import kunci_sinyal_gem, kunci_sinyal_sniper, teks_saran

WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
DRY_RUN_ENV = "GEM_DRY_RUN"
WARNA_ALERT = 15158332

COLOR = {"micro": 15158332, "low": 15105570, "mid": 5763719, "di_atas_2b": 2067276}
STAGE_LABEL = {
    "momentum": "Momentum",
    "akumulasi": "Akumulasi",
    "belum": "Belum Terkonfirmasi",
    "turun": "Sedang Turun",
}
STAGE_KESIMPULAN = {
    "momentum": "🚀 Koin sudah naik tajam. Hindari FOMO, tunggu koreksi atau pakai size kecil.",
    "akumulasi": "🌱 Tahap awal. Volume harian dan 4H mendukung, harga belum melonjak.",
    "belum": "🔎 Belum terkonfirmasi. Volume belum mendukung, tunggu konfirmasi sebelum masuk.",
    "turun": "📉 Sedang turun. Tunggu harga stabil dan volume kembali naik.",
}
TIER_LABEL = {
    "micro": "Micro cap · risiko tinggi",
    "low": "Low cap · risiko tinggi",
    "mid": "Mid cap",
    "di_atas_2b": "Large cap · di atas 2 miliar",
}
UKURAN_SARAN = {
    "micro": "maks 1% modal",
    "low": "maks 2% modal",
    "mid": "maks 2% modal (usulan)",
    "di_atas_2b": "maks 2% modal (usulan)",
}


def fmt_usd(x):
    if x is None:
        return "N/A"
    if x >= 1e9:
        return f"${x / 1e9:.2f} miliar"
    if x >= 1e6:
        return f"${x / 1e6:.1f} juta"
    return f"${x / 1e3:.0f} ribu"


def fmt_harga(p, qp=None):
    """Harga untuk ditampilkan dan disalin ke Pionex.
    Memakai quotePrecision (qp) bila ada. Tanpa notasi ilmiah, dan tidak pernah menjadi nol
    untuk harga yang sebenarnya positif (misalnya koin dengan harga sangat kecil)."""
    if p is None:
        return "N/A"
    p = float(p)
    if p == 0:
        return "0"
    if qp is not None:
        s = f"{p:.{int(qp)}f}"
        if float(s) != 0.0:
            return s
    a = abs(p)
    if a >= 1000:
        d = 2
    elif a >= 10:
        d = 3
    elif a >= 1:
        d = 4
    elif a >= 0.1:
        d = 5
    elif a >= 0.01:
        d = 6
    else:
        d = min(14, max(8, -int(math.floor(math.log10(a))) + 3))
    return f"{p:.{d}f}"


def _baris_fdv(rasio):
    if rasio is None:
        return "**FDV/MC** tidak diketahui (tanpa batas supply)"
    if rasio > FDV_WARN_RATIO:
        return f"**FDV/MC** `{rasio:.2f}x` ⚠️ di atas {FDV_WARN_RATIO:.1f}x"
    return f"**FDV/MC** `{rasio:.2f}x` · aman"


def _bullet(items, maks):
    teks = [f"• {x}" for x in list(items)[:maks]]
    return "\n".join(teks) if teks else "-"


def bangun_embed(h):
    base = h["base"]
    stage = h["stage"]
    tier = h["tier"]
    rasio = h.get("rasio_fdv")
    qp = h.get("qp")
    f = lambda x: fmt_harga(x, qp)
    waktu = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M WITA")

    vol_1d = f"{h['vol_1d']:.2f}x" if h.get("vol_1d") is not None else "N/A"
    data_pasar = (
        f"**Harga Pionex** `{f(h['harga_pionex'])}`\n"
        f"**24 jam** `{h['ch24']:+.1f}%` · **7 hari** `{h['ch7d']:+.1f}%`\n"
        f"**MC** {fmt_usd(h['mc'])} · **Vol/MC** {h['vmc'] * 100:.0f}%\n"
        f"**ATR 4H** `{h['atr_pct']:.1f}%` · **Vol harian** {vol_1d}\n"
        f"{_baris_fdv(rasio)}"
    )

    perhatikan = [TIER_LABEL.get(tier, tier)] + list(h.get("peringatan", []))[:5]

    if stage == "momentum" and h.get("ema20"):
        strategi = (
            f"**Trailing Buy** atau **Smart Trade** dengan buy-in di area EMA20 (≈ `{f(h['ema20'])}`)\n"
            "• Alternatif: **Infinity Grid** (tanpa batas atas, risiko naik jika tren berbalik)"
        )
    else:
        strategi = (
            "**Trailing Buy** atau **DCA** (masuk bertahap)\n"
            "• Alternatif: **Spot Grid** jika harga bergerak sideways"
        )
    limit = h["harga_pionex"] * 0.98

    fields = [
        {"name": "⚡ KESIMPULAN", "value": STAGE_KESIMPULAN.get(stage, "-"), "inline": False},
        {"name": "📊 DATA PASAR", "value": data_pasar, "inline": False},
        {"name": "🔍 KENAPA", "value": _bullet(h.get("alasan", []), 3), "inline": False},
        {"name": "⚠️ PERHATIKAN", "value": "\n".join(f"• {p}" for p in perhatikan), "inline": False},
        {"name": "💼 UKURAN SARAN", "value": UKURAN_SARAN.get(tier, "-"), "inline": False},
        {"name": "🤖 EKSEKUSI",
         "value": f"{strategi}\n📋 `{base}/USDT · Buy limit ≤ {f(limit)}`",
         "inline": False},
    ]

    sumber_candle = str(h.get("sumber_candle", "?")).upper()
    return {
        "title": f"💎 {base}/USDT · {STAGE_LABEL.get(stage, stage)}",
        "description": (
            f"**Skor {h['skor']}/100** · {TIER_LABEL.get(tier, tier)}\n"
            f"📡 Harga Pionex · Candle {sumber_candle} · MC {h.get('sumber_mc', '?')} · {waktu}"
        ),
        "color": COLOR.get(tier, 9807270),
        "fields": fields,
        "footer": {"text": "Bukan saran finansial · data bisa berubah"},
    }


def kirim_embed(embed):
    """Mengembalikan (berhasil: bool, keterangan: str)."""
    if os.environ.get(DRY_RUN_ENV, "").strip() == "1":
        print(f"[DRY RUN] {embed['title']}")
        if embed.get("description"):
            print(embed["description"])
        for f in embed.get("fields", []):
            print(f"  [{f['name']}] {f['value']}")
        return False, "dry_run"

    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url:
        return False, "webhook_kosong"

    body = json.dumps({"embeds": [embed]}).encode("utf-8")
    keterangan = "gagal"
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                if 200 <= r.status < 300:
                    return True, "ok"
                keterangan = f"http_{r.status}"
        except urllib.error.HTTPError as e:
            keterangan = f"http_{e.code}"
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(2 * (attempt + 1))
                continue
            return False, keterangan
        except (urllib.error.URLError, TimeoutError) as e:
            keterangan = f"jaringan_{type(e).__name__}"
            time.sleep(2 * (attempt + 1))
    return False, keterangan


def kirim_alert(judul, pesan):
    """Alarm untuk kegagalan total (misalnya Pionex atau data market cap tidak bisa diambil)."""
    return kirim_embed({
        "title": judul,
        "description": pesan,
        "color": WARNA_ALERT,
        "footer": {"text": "Bukan saran finansial"},
    })


# ---------------------------------------------------------------------------
# Embed sniper dan grid (diport dari buat_embed_sinyal dan buat_embed_grid legacy v4.4.0).
# Fungsi di bawah ini hanya membangun dict embed. Pengiriman memakai kirim_embed().
# ---------------------------------------------------------------------------

COLOR_GREEN, COLOR_YELLOW, COLOR_ORANGE = 5763719, 16705372, 15105570
COLOR_PURPLE = 10181046
EMBED_BATAS_NILAI = 1024  # batas Discord per field

EMOJI_SETUP = {"DIP_TREN": "📉", "DIP_REVERSAL": "🔄", "BREAKOUT": "🚀", "BUY_DIP": "📉", "BUY_BREAKOUT": "🚀"}
LABEL_SETUP = {
    "DIP_TREN": "Buy Dip", "BUY_DIP": "Buy Dip",
    "DIP_REVERSAL": "Dip Reversal (counter-trend)",
    "BREAKOUT": "Breakout", "BUY_BREAKOUT": "Breakout",
}
MODE_LABEL = {"aggressive": "BULLISH", "neutral": "NETRAL", "defensive": "BEARISH"}
MODE_ICON = {"aggressive": "🟢", "neutral": "🟡", "defensive": "🔴"}
MODE_MULT = {"aggressive": "1.0", "neutral": "0.8", "defensive": "0.5"}


def grade(skor):
    """Huruf, label, dan warna berdasarkan skor. Sama dengan legacy."""
    if skor >= 75:
        return "A", "Very Strong", COLOR_GREEN
    if skor >= 62:
        return "B", "Good", COLOR_YELLOW
    return "C", "Decent", COLOR_ORANGE


def status_jendela_likuiditas(dt_utc):
    if 13 <= dt_utc.hour < 17:
        return "🔥 HIGH-LIQUIDITY WINDOW (info) | 13:00-17:00 UTC (21:00-01:00 WITA)"
    return "💤 OUTSIDE WINDOW (info) | 13:00-17:00 UTC (21:00-01:00 WITA)"


def _footer(line1, now_ms):
    dt_utc = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    return {"text": f"{line1}\n{status_jendela_likuiditas(dt_utc)}"}


def _freshness(candle_close_ms, now_ms):
    if candle_close_ms is None:
        return "⏱️ —"
    age_min = max(0, int((now_ms - candle_close_ms) / 60000))
    if age_min <= 0:
        return "⏱️ baru"
    if age_min <= 15:
        return f"⏱️ fresh {age_min}m"
    if age_min <= 60:
        return f"⏱️ {age_min}m"
    return f"⚠️ {age_min}m — cek ulang"


def _field(nama, nilai, inline=False):
    return {"name": nama, "value": nilai[:EMBED_BATAS_NILAI], "inline": inline}


def _pick_emoji_alasan(a):
    al = a.lower()
    if "volume" in al:
        return "📈"
    if any(k in al for k in ("cmf", "flow", "obv")):
        return "💧"
    if "macd" in al:
        return "📊"
    if "divergence" in al or "divergensi" in al:
        return "🔄"
    if "rsi" in al:
        return "📉"
    if "adx" in al:
        return "🔥"
    if "trend" in al or "daily" in al or "tren" in al:
        return "📊"
    if "btc" in al:
        return "🌍"
    return "✅"


def kesimpulan_sniper(h):
    subtipe = h.get("subtipe", "DIP_TREN")
    skor = h.get("skor", 0)
    btc_berlawanan = any("btc" in k.lower() for k in h.get("kurang", []))
    if subtipe == "DIP_REVERSAL":
        teks = "Pembalikan awal di zona oversold."
        teks += (" Setup kuat, tapi melawan tren harian. Size dikecilkan."
                 if skor >= 75 else
                 " Lebih berisiko karena melawan tren harian. Pakai size kecil.")
    elif subtipe in ("BREAKOUT", "BUY_BREAKOUT"):
        teks = "Breakout terkonfirmasi volume."
        teks += (" Momentum kuat. Tunggu retest, jangan FOMO."
                 if skor >= 75 else " Gunakan limit order, bukan market.")
    else:
        teks = "Sinyal beli valid."
        if skor >= 75:
            teks += " Volume dan flow mendukung. Layak dipertimbangkan sekarang."
        elif skor >= 62:
            teks += " Pantau candle 4H berikutnya sebelum entry."
        else:
            teks += " Gunakan size kecil dengan SL ketat."
    if btc_berlawanan and subtipe != "DIP_REVERSAL":
        teks += " ⚠️ BTC bearish. Size sudah dikurangi."
    return teks


def _kenapa_sniper(h):
    baris = [f"• {_pick_emoji_alasan(a)} {a}" for a in h.get("alasan", [])[:2]]
    vol_x = h.get("vol_x")
    if vol_x and vol_x > 0:
        em = "🔥" if vol_x >= 2 else "📈" if vol_x >= 1.3 else "📊"
        rng = h.get("range_atr")
        rng_txt = f" · Range {rng:.1f} ATR" if rng is not None else ""
        baris.append(f"• {em} Volume {vol_x:.1f}x{rng_txt}")
    mode = h.get("mode_macro", "neutral")
    grid_btc = h.get("btc_grid", 0)
    label = {"aggressive": "bullish", "neutral": "netral", "defensive": "bearish"}.get(mode, mode)
    baris.append(f"• 🌍 BTC {label} · grid {grid_btc}/10")
    return "\n".join(baris[:4]) or "-"


def _rr(h):
    risk = h["entry"] - h["sl"]
    return (h["tp1"] - h["entry"]) / risk if risk > 0 else 0.0


def bangun_embed_sinyal(h, qp=None, sumber_label="Binance", now_ms=0, btc_grid=None):
    """
    Embed sinyal sniper. h: kandidat dari bot/sniper.pilih_sinyal yang sudah diratakan
    dengan rencana (entry, sl, tp1, tp2, risk_pct, tp1_pct, alokasi_pct).
    qp: quotePrecision Pionex untuk format harga. btc_grid: skor grid BTC 0-10 (opsional).
    """
    base = h["base"]
    subtipe = h.get("subtipe", "DIP_TREN")
    huruf, label_grade, warna = grade(h["skor"])
    mode = h.get("mode_macro", "neutral")
    grid_btc = btc_grid if btc_grid is not None else h.get("btc_grid", 0)
    f = lambda x: fmt_harga(x, qp)

    judul = f"{EMOJI_SETUP.get(subtipe, '📉')} {base}/USDT"
    if h.get("backup"):
        judul += " · ⚠️ BACKUP"
    waktu = datetime.fromtimestamp(now_ms / 1000, tz=timezone(timedelta(hours=8))).strftime("%H:%M WITA")

    deskripsi = (
        f"📰 SINYAL · {waktu} · 🌍 BTC {MODE_ICON.get(mode, '🟡')} **{MODE_LABEL.get(mode, 'NETRAL')}**\n"
        f"📊 Grid {grid_btc}/10 · Mode **{mode.upper()}** ({MODE_MULT.get(mode, '0.8')}x)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**{LABEL_SETUP.get(subtipe, 'Sinyal')} · {h.get('tf', '?')}**\n"
        f"**Grade {huruf}** · Skor {h['skor']} · {_freshness(h.get('candle_close_ms'), now_ms)}"
    )

    aksi = (
        f"**Entry** `{f(h['entry'])}`\n"
        f"**SL** `{f(h['sl'])}` ({h['risk_pct'] * -1:+.2f}%)\n"
        f"**TP** `{f(h['tp1'])}` ({h['tp1_pct']:+.2f}%)\n"
        f"**Size** `{h['alokasi_pct']:.2f}%` akun · RR {_rr(h):.1f}x"
    )

    if h.get("harga_pionex"):
        validasi = f"Pionex `{f(h['harga_pionex'])}` (selisih vs {sumber_label}: {h.get('selisih_pionex', 0.0):+.2f}%)"
    else:
        validasi = "⚠️ Harga Pionex tidak tersedia saat scan. Cek di aplikasi sebelum entry."

    fields = [
        _field("⚡ KESIMPULAN", kesimpulan_sniper(h)),
        _field("🎯 AKSI", aksi),
        _field("🧭 CATATAN SL & TP",
               "SL dan TP dihitung bot dari analisis teknikal sebagai saran default. "
               "Boleh diikuti atau diubah sesuai penilaian Anda."),
        _field("🔍 KENAPA", _kenapa_sniper(h)),
    ]
    if h.get("backup"):
        fields.append(_field("⚠️ Sumber Backup",
                             f"Analisis via **{sumber_label.upper()}** (sumber utama tidak tersedia). "
                             "Cek harga di aplikasi sebelum entry."))
    fields.append(_field("💱 Validasi Pionex", validasi))
    fields.append(_field("🤖 EKSEKUSI",
                         f"{teks_saran(kunci_sinyal_sniper(subtipe))}\n"
                         f"📋 `{base}/USDT | Buy {f(h['entry'])} | SL {f(h['sl'])} | TP {f(h['tp1'])}`"))

    return {
        "title": judul,
        "description": deskripsi,
        "color": warna,
        "fields": fields,
        "footer": _footer(f"📡 {sumber_label} · Bukan saran finansial", now_ms),
    }


def bangun_embed_grid(g, sumber_label="Binance", now_ms=0, qp=None):
    """Embed Spot Grid. g: hasil bot/grid.analisis_grid tanpa kunci 'tolak'."""
    base = g["base"]
    is_bc = g.get("is_blue_chip", False)
    f = lambda x: fmt_harga(x, qp)
    mode = g.get("mode_macro", "neutral")
    label_btc = {"aggressive": "bullish", "neutral": "netral", "defensive": "bearish"}.get(mode, mode)

    if is_bc:
        judul = f"🔷 {base}/USDT"
        badge = "**💠 BLUE CHIP**: risiko lebih rendah"
        warna = COLOR_PURPLE
        kesimpulan = "Pasar sideways dengan range sehat. Bot grid cocok untuk pasar tenang ini."
        kunci = "GRID_BLUE_CHIP"
    else:
        judul = f"⚠️ {base}/USDT"
        badge = "**🚨 ALTCOIN**: bukan blue chip, risiko lebih tinggi"
        warna = COLOR_ORANGE
        kesimpulan = "Pasar sideways, bot grid cocok. Wajib pantau manual dan cut loss jika tembus Lower."
        kunci = "GRID_ALTCOIN"

    segar = _freshness(g.get("candle_close_ms"), now_ms)
    deskripsi = f"**Grid · {g.get('tf', '4H')} · Mode Sideways** · {segar}\n{badge}"
    setup = (
        f"**Lower** `{f(g['lower'])}`\n"
        f"**Upper** `{f(g['upper'])}`\n"
        f"**Current** `{f(g['current_price'])}`\n"
        f"**Grids** {g['grids']} · Arithmetic\n"
        f"**Net/grid** `+{g['net_per_grid']:.2f}%` setelah fee"
    )
    pos = g.get("pos_pct")
    posisi = (f"• 📍 Harga di {pos:.0f}% range (ideal 20–80%)\n" if pos is not None else "")
    kenapa = (
        f"• 📉 ADX 4H rendah (bukan tren)\n"
        f"• 📏 Range {g['range_pct']:.1f}%\n"
        f"{posisi}"
        f"• 🌍 BTC {label_btc}"
    )
    eksekusi = (
        f"{teks_saran(kunci)}\n"
        f"📋 `Pair: {base}/USDT · Lower {f(g['lower'])} · Upper {f(g['upper'])} · Grids {g['grids']}`"
    )
    if not is_bc:
        eksekusi += "\n⚠️ Harga tembus Lower: **CUT LOSS MANUAL**."

    return {
        "title": judul,
        "description": deskripsi,
        "color": warna,
        "fields": [
            _field("⚡ KESIMPULAN", kesimpulan),
            _field("📊 SETUP", setup),
            _field("🔍 KENAPA", kenapa),
            _field("🤖 EKSEKUSI", eksekusi),
        ],
        "footer": _footer(f"📡 {sumber_label} · Bukan saran finansial", now_ms),
    }
