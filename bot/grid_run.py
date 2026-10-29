"""
Entry point Spot Grid. Menghubungkan bot/grid.py (analisis), bot/notify.py (embed), dan
bot/state.py (cooldown grid_alerts 24 jam). Sebelumnya modul grid sudah ada tetapi tidak
dipanggil oleh alur mana pun.

Mode default (dry run): alur lengkap, tanpa kirim ke Discord, tanpa menulis state.json.
Mode live (--live): kirim embed Discord, lalu catat cooldown grid_alerts dan cache Pionex.
--dry-run menang atas --live.

Alur:
  0. Gerbang Pionex (live, cache, atau aborted).
  1. Universe Pionex USDT spot, filter turnover 24 jam Binance dan blacklist.
  2. Konteks BTC (mode macro) dari SumberCandle yang sama.
  3. Per koin: gerbang volume dan ticker dicek DULU tanpa candle (analisis_grid dengan list
     kosong menolak lebih awal), jadi candle 4H hanya diambil untuk koin yang layak.
  4. Pilih maksimal GRID_MAX_KIRIM setup: blue chip lebih dulu, lalu ADX terendah.

Jalankan dari root repo: python3 -m bot.grid_run            (dry run)
                         python3 -m bot.grid_run --live     (kirim ke Discord)
"""
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

from bot.grid import analisis_grid
from bot.notify import bangun_embed_grid, kirim_embed
from bot.state import bersihkan, catat_grid, dalam_cooldown_grid, save_state
from bot.sumber import SumberCandle, rantai_dari_env

OUTPUT_DIR = "output"
GRID_MAX_KIRIM = 3
TF_GRID = ["4H"]
LABEL_SUMBER = {"pionex": "Pionex", "binance": "Binance", "okx": "OKX", "bybit": "Bybit"}


def ambil_mode_macro(sumber):
    """Mode BTC (aggressive, neutral, defensive). Netral jika data BTC gagal."""
    from bot.sniper_run import ambil_macro
    m = ambil_macro(sumber)
    return (m or {}).get("mode") or "neutral", m


def urutkan_setup(setups):
    """Blue chip lebih dulu (risiko lebih rendah), lalu ADX paling rendah (paling sideways)."""
    return sorted(setups, key=lambda g: (not g.get("is_blue_chip"), g.get("adx", 99.0)))


def jalankan_grid(universe, sumber, turnover, harga_pionex, mode_macro, st, now_ms,
                  max_kirim=GRID_MAX_KIRIM):
    """
    Inti alur. Semua dependensi diberikan dari luar agar mudah diuji.

    universe: dict base -> pair. turnover: dict base -> volume 24 jam (USD).
    harga_pionex: dict pair -> harga live. Kosong (mode cache) berarti cek ticker dilewati.
    Mengembalikan dict: per_koin (outcome per koin), funnel, setup (semua lolos), terpilih.
    """
    per_koin, funnel, setup = {}, Counter(), []
    for base, pair in universe.items():
        ticker_ada = (pair in harga_pionex) if harga_pionex else True
        last = harga_pionex.get(pair) if harga_pionex else None

        awal = analisis_grid([], base, turnover=turnover.get(base), last_price=last,
                             mode_macro=mode_macro, ticker_ada=ticker_ada)
        if awal.get("tolak") != "data_kurang":
            per_koin[base] = awal.get("tolak", "tidak_diketahui")
            funnel[per_koin[base]] += 1
            continue

        if dalam_cooldown_grid(st, base, now_ms):
            per_koin[base] = "cooldown"
            funnel["cooldown"] += 1
            continue

        bundle = sumber.ambil_bundle(base, pair, TF_GRID)
        if bundle["status"] != "ok":
            per_koin[base] = "data_gagal"
            funnel["data_gagal"] += 1
            continue

        g = analisis_grid(bundle["candles"]["4H"], base, turnover=turnover.get(base),
                          last_price=last, mode_macro=mode_macro, ticker_ada=ticker_ada)
        if "tolak" in g:
            per_koin[base] = g["tolak"]
            funnel[g["tolak"]] += 1
            continue

        # Waktu tutup candle 4H terakhir: dipakai embed untuk menampilkan kesegaran setup
        t_candle = bundle["candles"]["4H"][-1][0]
        g.update({"pair": pair, "sumber": bundle["sumber"], "backup": bundle["backup"],
                  "candle_close_ms": int(t_candle) + 14_400_000})
        per_koin[base] = "setup"
        funnel["setup"] += 1
        setup.append(g)

    urut = urutkan_setup(setup)
    terpilih = urut[:max_kirim]
    funnel["di_luar_kuota"] = max(0, len(urut) - len(terpilih))
    return {"per_koin": per_koin, "funnel": dict(funnel), "setup": urut, "terpilih": terpilih}


def kirim_grid(terpilih, st, now_ms, qp_map=None, kirim=kirim_embed, bangun=bangun_embed_grid):
    """Kirim embed tiap setup. Cooldown dicatat hanya jika kirim berhasil."""
    qp_map = qp_map or {}
    terkirim = []
    for g in terpilih:
        label = LABEL_SUMBER.get(str(g.get("sumber") or "").lower(), "Pionex")
        ok, keterangan = kirim(bangun(g, sumber_label=label, now_ms=now_ms,
                                      qp=qp_map.get(g["base"])))
        g["status_kirim"] = keterangan
        if ok:
            catat_grid(st, g["base"], now_ms)
            terkirim.append(g)
    return terkirim


def _ringkas(g):
    return {
        "base": g["base"], "pair": g["pair"], "is_blue_chip": g.get("is_blue_chip"),
        "lower": g["lower"], "upper": g["upper"], "grids": g["grids"],
        "net_per_grid": g["net_per_grid"], "range_pct": g["range_pct"],
        "adx": g.get("adx"), "atr_pct": g.get("atr_pct"), "current_price": g["current_price"],
        "pos_pct": g.get("pos_pct"), "candle_close_ms": g.get("candle_close_ms"),
        "mode_macro": g.get("mode_macro"), "sumber": g.get("sumber"), "backup": g.get("backup"),
        "status_kirim": g.get("status_kirim"),
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    live = "--live" in argv and "--dry-run" not in argv

    from bot.gem import ambil_semua_ticker_pionex
    from bot.gerbang_pionex import gerbang_pionex
    from bot.state import load_state
    from bot.turnover import ambil_turnover_binance
    from bot.universe import bangun_universe, saring_turnover_blacklist

    t0 = time.time()
    st = load_state()

    gate = gerbang_pionex(st.get("pionex_cache"), time.time() * 1000)
    if gate["mode"] == "aborted":
        print(f"ERROR gerbang Pionex: {gate['alasan']}", file=sys.stderr)
        return 1

    included, _ = bangun_universe({b: r["pair"] for b, r in gate["rules"].items()})
    turnover = ambil_turnover_binance()
    if not turnover:
        print("ERROR: turnover Binance tidak tersedia, filter volume tidak bisa dijalankan",
              file=sys.stderr)
        return 1
    included, _gugur = saring_turnover_blacklist(included, turnover)
    if not included:
        print("ERROR: universe kosong setelah filter volume dan blacklist", file=sys.stderr)
        return 1

    harga = {}
    if gate["mode"] == "live":
        harga = {p: t["close"] for p, t in (ambil_semua_ticker_pionex() or {}).items()}

    sumber = SumberCandle(rantai=rantai_dari_env())
    mode_macro, macro = ambil_mode_macro(sumber)

    now_ms = time.time() * 1000
    hasil = jalankan_grid(included, sumber, turnover, harga, mode_macro, st, now_ms)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    keluaran = {
        "dibuat": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gerbang_pionex": gate["mode"], "mode_macro": mode_macro,
        "btc_trend": (macro or {}).get("trend"),
        "funnel": hasil["funnel"],
        "jumlah_setup": len(hasil["setup"]),
        "terpilih": [_ringkas(g) for g in hasil["terpilih"]],
        "sumber": dict(sumber.stat),
    }

    if live:
        qp_map = {b: r.get("qp") for b, r in gate["rules"].items()}
        terkirim = kirim_grid(hasil["terpilih"], st, time.time() * 1000, qp_map=qp_map)
        if gate["cache_baru"]:
            st["pionex_cache"] = gate["cache_baru"]
        bersihkan(st, time.time() * 1000)
        save_state(st)
        keluaran["terkirim"] = len(terkirim)
        keluaran["terpilih"] = [_ringkas(g) for g in hasil["terpilih"]]  # sertakan status_kirim
        print(f"  live: {len(terkirim)} dari {len(hasil['terpilih'])} setup terkirim")
    else:
        print("  dry run: tidak ada pengiriman dan tidak ada state yang ditulis")

    with open(os.path.join(OUTPUT_DIR, "grid.json"), "w", encoding="utf-8") as f:
        json.dump(keluaran, f, indent=2, ensure_ascii=False)

    print(f"Grid scan · gerbang {gate['mode']} · BTC mode {mode_macro} · {time.time() - t0:.1f} detik")
    print(f"  funnel: {hasil['funnel']}")
    print(f"  setup lolos: {len(hasil['setup'])}, terpilih: {len(hasil['terpilih'])}")
    for g in hasil["terpilih"]:
        print(f"    {g['pair']} lower {g['lower']:.6g} upper {g['upper']:.6g} grids {g['grids']} "
              f"net {g['net_per_grid']:.2f}% adx {g.get('adx', 0):.1f} "
              f"{'blue chip' if g.get('is_blue_chip') else 'altcoin'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
