"""
Uji alur grid (bot/grid_run.py) dan perbaikan terkait sniper. Tanpa jaringan.
Jalankan dari root repo: python3 tests/test_grid_run.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.grid_run import jalankan_grid, kirim_grid, urutkan_setup
from bot.sniper import _konteks_btc, pilih_sinyal
from bot.sniper_run import bangun_mc_map, kirim_terpilih
from bot.sumber import SumberCandle

NOW = 1_700_000_000_000
H4 = 14_400_000


def candles_sideways(n=60, tengah=100.0, amp=0.02):
    """Candle 4H berosilasi tipis di sekitar 'tengah': range kecil, ADX rendah."""
    out = []
    for i in range(n):
        c = tengah * (1 + amp * (1 if i % 2 == 0 else -1) * (0.5 + (i % 7) / 14))
        out.append((NOW - (n - i) * H4, c, c * 1.003, c * 0.997, c, 1000.0))
    return out


def sumber_tetap(candles, gagal=False):
    def f(base, pair, tf, limit, now_ms):
        return (None, "gagal_jaringan") if gagal else (candles, "ok")
    return SumberCandle(rantai=["pionex", "binance"], fetchers={"pionex": f, "binance": f},
                        now_fn=lambda: NOW)


# ---------- grid_run ----------
def test_gerbang_volume_menolak_sebelum_candle_diambil():
    dipanggil = []

    def f(base, pair, tf, limit, now_ms):
        dipanggil.append(base)
        return candles_sideways(), "ok"

    s = SumberCandle(rantai=["pionex"], fetchers={"pionex": f}, now_fn=lambda: NOW)
    r = jalankan_grid({"KECIL": "KECIL_USDT"}, s, {"KECIL": 1e6}, {}, "neutral", {}, NOW)
    assert r["per_koin"]["KECIL"] == "volume_kurang"
    assert dipanggil == []  # hemat panggilan API


def test_ticker_tidak_ada_ditolak_saat_live():
    r = jalankan_grid({"AAA": "AAA_USDT"}, sumber_tetap(candles_sideways()), {"AAA": 5e7},
                      {"BBB_USDT": 1.0}, "neutral", {}, NOW)
    assert r["per_koin"]["AAA"] == "ticker_tidak_ada"


def test_mode_cache_melewati_cek_ticker():
    r = jalankan_grid({"AAA": "AAA_USDT"}, sumber_tetap(candles_sideways()), {"AAA": 5e7},
                      {}, "neutral", {}, NOW)
    assert r["per_koin"]["AAA"] != "ticker_tidak_ada"


def test_cooldown_grid_24_jam_tidak_mengambil_candle():
    st = {"grid_alerts": {"AAA": NOW - 3_600_000}}
    r = jalankan_grid({"AAA": "AAA_USDT"}, sumber_tetap(candles_sideways(), gagal=True),
                      {"AAA": 5e7}, {}, "neutral", st, NOW)
    assert r["per_koin"]["AAA"] == "cooldown"


def test_data_gagal_dilaporkan():
    r = jalankan_grid({"AAA": "AAA_USDT"}, sumber_tetap(candles_sideways(), gagal=True),
                      {"AAA": 5e7}, {}, "neutral", {}, NOW)
    assert r["per_koin"]["AAA"] == "data_gagal"


def test_setup_sideways_lolos_dan_membawa_sumber():
    r = jalankan_grid({"BTC": "BTC_USDT"}, sumber_tetap(candles_sideways()), {}, {}, "neutral", {}, NOW)
    # BTC blue chip: lolos gerbang volume walau turnover kosong
    assert r["per_koin"]["BTC"] in ("setup", "bukan_sideways", "net_per_grid_terlalu_kecil",
                                    "harga_di_pinggir_range", "volatilitas_terlalu_tinggi")
    for g in r["setup"]:
        assert g["pair"] == "BTC_USDT" and g["sumber"] == "pionex" and g["backup"] is False


def test_urutan_blue_chip_dulu_lalu_adx_rendah():
    a = {"base": "A", "is_blue_chip": False, "adx": 10.0}
    b = {"base": "B", "is_blue_chip": True, "adx": 22.0}
    c = {"base": "C", "is_blue_chip": False, "adx": 5.0}
    assert [g["base"] for g in urutkan_setup([a, b, c])] == ["B", "C", "A"]


def test_kuota_kirim_dibatasi():
    pasangan = {f"B{i}": f"B{i}_USDT" for i in range(6)}
    turn = {b: 5e7 for b in pasangan}
    r = jalankan_grid(pasangan, sumber_tetap(candles_sideways()), turn, {}, "neutral", {}, NOW,
                      max_kirim=2)
    assert len(r["terpilih"]) <= 2


def _setup_uji(base="ETH", blue=True):
    return {"base": base, "pair": base + "_USDT", "is_blue_chip": blue, "sumber": "pionex",
            "backup": False, "lower": 98.0, "upper": 112.0, "grids": 40, "net_per_grid": 0.2,
            "range_pct": 8.0, "pos_pct": 50.0, "current_price": 105.0, "tf": "4H",
            "mode_macro": "neutral"}


def test_kirim_grid_cooldown_hanya_jika_berhasil():
    st = {}
    lst = [_setup_uji("ETH"), _setup_uji("SOL")]
    balasan = iter([(True, "ok"), (False, "gagal")])
    terkirim = kirim_grid(lst, st, NOW, kirim=lambda e: next(balasan),
                          bangun=lambda g, **kw: {"title": g["base"]})
    assert [g["base"] for g in terkirim] == ["ETH"]
    assert st["grid_alerts"] == {"ETH": NOW}
    assert lst[1]["status_kirim"] == "gagal"


def test_kirim_grid_dry_run_tidak_mencatat_cooldown():
    st = {}
    kirim_grid([_setup_uji()], st, NOW, kirim=lambda e: (False, "dry_run"),
               bangun=lambda g, **kw: {"title": "x"})
    assert "grid_alerts" not in st


def test_kirim_grid_embed_nyata_tidak_error():
    dapat = []
    kirim_grid([_setup_uji()], {}, NOW, qp_map={"ETH": 2},
               kirim=lambda e: dapat.append(e) or (True, "ok"))
    assert dapat and dapat[0]["title"].endswith("ETH/USDT")


# ---------- perbaikan sniper ----------
def test_konteks_btc_dibawa_kandidat():
    macro = {"mode": "defensive", "bearish": True, "grid": 3}
    assert _konteks_btc(macro) == {"mode_macro": "defensive", "btc_bearish": True, "btc_grid": 3}
    assert _konteks_btc(None) == {"mode_macro": "neutral", "btc_bearish": False, "btc_grid": 0}


def _kandidat(**ekstra):
    k = {"pair": "ETH_USDT", "base": "ETH", "subtipe": "DIP_TREN", "tf": "4H", "entry": 100.0,
         "atr": 2.0, "low_10": 97.0, "chg24": 1.0, "skor": 70, "ambang": 55,
         "alasan": [], "kurang": [], "btc_berlawanan": False, "sumber": "pionex", "backup": False}
    k.update(ekstra)
    return k


def test_btc_bearish_memangkas_alokasi_dan_selisih_dicatat():
    n, _ = pilih_sinyal([_kandidat()], {}, NOW, harga_pionex={"ETH_USDT": 100.5})
    b, _ = pilih_sinyal([_kandidat(mode_macro="defensive", btc_bearish=True)], {}, NOW,
                        harga_pionex={"ETH_USDT": 100.5})
    assert b[0]["rencana"]["alokasi_pct"] < n[0]["rencana"]["alokasi_pct"]
    assert b[0]["rencana"]["mode_macro"] == "defensive"
    assert abs(n[0]["selisih_pionex"] - 0.5) < 1e-9


def test_bangun_mc_map_memilih_peringkat_bukan_baris_terakhir():
    def baris(sim, rank, mc):
        return {"symbol": sim, "rank": rank, "quotes": {"USD": {"market_cap": mc}}}
    rows = [baris("ABC", 5, 5e9), baris("ABC", 900, 2e6), baris("XYZ", None, 1e6),
            baris("NOL", 10, 0)]
    m = bangun_mc_map(rows)
    assert m["ABC"] == 5e9      # koin besar, bukan yang kecil di baris akhir
    assert "NOL" not in m       # tanpa market cap valid
    assert m["XYZ"] == 1e6      # simbol unik tanpa peringkat tetap dipakai


def test_label_sumber_otomatis_dari_candle():
    dapat = []
    k = _kandidat()
    k.update({"entry": 100.0, "sl": 95.0, "tp1": 107.5, "risk_pct": 5.0, "tp1_pct": 7.5,
              "alokasi_pct": 10.0})
    kirim_terpilih([k], {}, NOW, kirim=lambda e: (True, "ok"),
                   bangun=lambda h, sumber_label=None, **kw: dapat.append(sumber_label) or {"title": "x"})
    assert dapat == ["Pionex"]


def test_embed_grid_tampilkan_posisi_dan_kesegaran():
    from bot.notify import bangun_embed_grid
    g = _setup_uji()
    g["pos_pct"] = 45.0
    g["candle_close_ms"] = NOW - 5 * 60_000
    e = bangun_embed_grid(g, sumber_label="Pionex", now_ms=NOW)
    assert "fresh 5m" in e["description"]
    assert "Harga di 45% range" in e["fields"][2]["value"]


TESTS = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]

if __name__ == "__main__":
    gagal = 0
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            gagal += 1
            print(f"GAGAL {t.__name__}: {type(e).__name__}: {e}")
    if gagal:
        print(f"{gagal} dari {len(TESTS)} tes gagal")
        sys.exit(1)
    print(f"SEMUA TES LOLOS ({len(TESTS)} tes)")
