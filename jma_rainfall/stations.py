#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
観測所マスタ検索モジュール

stations.json（build_station_master.py で生成）を読み込み、
地点名・カナ・block_no・緯度経度から観測所を引く機能を提供する。
"""

import json
import logging
import math
import unicodedata
from pathlib import Path

MASTER_PATH = Path(__file__).with_name("stations.json")

log = logging.getLogger(__name__)


class StationNotFound(Exception):
    """該当する観測所が見つからない。"""


class AmbiguousStation(Exception):
    """地点名が複数の観測所に一致した。candidates に候補を持つ。"""

    def __init__(self, key, candidates):
        self.key = key
        self.candidates = candidates
        shown = 20   # 1文字の指定などで数百件になっても、読める長さにとどめる
        names = "、".join(f"{s['pref']}の{s['name']}（地点番号 {s['block_no']}）" for s in candidates[:shown])
        rest = f" ほか {len(candidates) - shown} 地点" if len(candidates) > shown else ""
        super().__init__(f"地点名「{key}」に該当する観測所が {len(candidates)} 地点あり、1つに決められません: {names}{rest}")


def _normalize(text):
    """全角・半角と大文字小文字を吸収した比較用キーを返す。"""
    return unicodedata.normalize("NFKC", str(text)).strip().lower()


def load_stations(path=None):
    """マスタを読み込んで観測所のリストを返す。"""
    p = Path(path) if path else MASTER_PATH
    # 次の操作: --master で指定したファイルなら指定を見直す。同梱のマスタならインストールが壊れている
    hint = ("  --master のパスを確かめてください（通常は --master を指定しなくても、パッケージに含まれるマスタを使います）。"
            if path else "  パッケージに含まれる観測所マスタ（stations.json）がありません。jma-rainfall をインストールし直してください。")
    if not p.exists():
        raise StationNotFound(f"観測所マスタが見つかりません: {p}\n{hint}")
    try:
        with p.open(encoding="utf-8") as f:
            stations = json.load(f)["stations"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as e:
        raise StationNotFound(f"観測所マスタの形式が正しくありません: {p}（詳細: {type(e).__name__}: {e}）\n"
                              "  観測所マスタは build_station_master.py で作った JSON ファイルです。\n"
                              f"{hint}") from None
    if not stations:
        raise StationNotFound(f"観測所マスタに観測所が1つもありません: {p}\n{hint}")
    # 取得の途中で壊れた項目に当たらないよう、読み込むときに確かめる
    need = ("name", "block_no", "prec_no", "pref", "kind")
    bad = [s for s in stations if not isinstance(s, dict) or any(k not in s for k in need) or s["kind"] not in ("a", "s")]
    if bad:
        raise StationNotFound(f"観測所マスタの形式が正しくありません: {p}\n"
                              f"  {len(bad)} 地点で、地点名・地点番号などの項目が欠けているか、種別が a（アメダス）・s（官署）の"
                              f"どちらでもありません（例: {str(bad[0])[:120]}）。\n"
                              "  観測所マスタは build_station_master.py で作った JSON ファイルです。\n"
                              f"{hint}")
    return stations


def filter_stations(stations, pref=None, precip_only=False, active_only=False,
                    keyword=None):
    """都道府県・観測要素・キーワードで絞り込む。"""
    result = stations
    if pref:
        key = _normalize(pref)
        # 「京都」は「東京都」にも部分一致するため、完全一致 → 前方一致 → 部分一致の順に絞る
        narrowed = []
        for match in (lambda p: p == key,
                      lambda p: p.startswith(key),
                      lambda p: key in p):
            narrowed = [s for s in result if match(_normalize(s["pref"]))]
            if narrowed:
                break
        result = narrowed
    if precip_only:
        result = [s for s in result if s["has_precip"]]
    if active_only:
        result = [s for s in result if s["active"]]
    if keyword:
        key = _normalize(keyword)
        result = [s for s in result
                  if key in _normalize(s["name"]) or key in _normalize(s["kana"])]
    return result


def find_station(key, stations=None, pref=None):
    """
    地点名・カナ・block_no から観測所を1件特定する。

    複数該当した場合は AmbiguousStation を送出する（pref で絞り込める）。
    """
    stations = stations if stations is not None else load_stations()
    if pref:
        stations = filter_stations(stations, pref=pref)

    nkey = _normalize(key)

    # block_no は先頭ゼロの有無を問わず一致させる
    if nkey.isdigit():
        hits = [s for s in stations
                if s["block_no"] == nkey or s["block_no"].lstrip("0") == nkey.lstrip("0")]
        if hits:
            return _single(key, hits)

    # 地点名・カナの完全一致 → 前方一致 → 部分一致の順に試す
    for exact, match in ((True, lambda s: _normalize(s["name"]) == nkey or _normalize(s["kana"]) == nkey),
                         (False, lambda s: _normalize(s["name"]).startswith(nkey)),
                         (False, lambda s: nkey in _normalize(s["name"]) or nkey in _normalize(s["kana"]))):
        hits = [s for s in stations if match(s)]
        if hits:
            station = _single(key, hits)
            if not exact:
                log.warning("「%s」は地点名と完全には一致しないため、名前の一部が一致する %s（%s、地点番号 %s）を対象にしました。"
                            "違う地点のときは、--station に地点番号（block_no）を指定してください。",
                            key, station["name"], station["pref"], station["block_no"])
            return station

    raise StationNotFound(
        f"観測所「{key}」が見つかりません" + (f"（--pref {pref} の中で探しました）。" if pref else "。") + "\n"
        "  地点名と地点番号（block_no）は jma-rainfall stations --pref 千葉県 --keyword 千葉 のように一覧で確認できます"
        "（廃止された地点は --include_closed を付けると表示します）。")


def _single(key, hits):
    """候補を1件に絞る。現存地点を優先し、なお複数なら例外。"""
    # 同一地点が複数の都道府県に載っている（富士山は山梨県と静岡県）。block_no が同じなら同じ地点として1件にする
    hits = list({s["block_no"]: s for s in hits}.values())
    if len(hits) > 1:
        active = [s for s in hits if s["active"]]
        if len(active) == 1:
            log.warning("「%s」に該当する観測所が %d 地点あるため、現存する %s（%s、地点番号 %s）を対象にしました。"
                        "廃止された地点を取得するときは、--station に地点番号（block_no）を指定してください"
                        "（jma-rainfall stations --include_closed で確認できます）。",
                        key, len(hits), active[0]["name"], active[0]["pref"], active[0]["block_no"])
            return active[0]
        raise AmbiguousStation(key, active or hits)
    return hits[0]


def distance_km(lat1, lon1, lat2, lon2):
    """2地点間の大円距離（km）。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_stations(lat, lon, stations=None, count=5, precip_only=True,
                     active_only=True):
    """指定座標に近い観測所を [(距離km, 観測所), ...] で返す。"""
    stations = stations if stations is not None else load_stations()
    stations = filter_stations(stations, precip_only=precip_only,
                               active_only=active_only)
    scored = [(distance_km(lat, lon, s["lat"], s["lon"]), s)
              for s in stations if s["lat"] is not None and s["lon"] is not None]
    scored.sort(key=lambda x: x[0])
    return scored[:count]


def limitations(station):
    """降水量の取得に関わる注意（降水量を観測していない、廃止）を短い文のリストで返す。なければ空。"""
    notes = []
    if not station["has_precip"]:
        notes.append("降水量なし")
    if not station["active"]:
        notes.append(f"廃止 {station['end_date']}")
    return notes


def describe(station, distance=None):
    """一覧表示用の1行文字列を返す。"""
    kind = "アメダス" if station["kind"] == "a" else "官署"
    flags = limitations(station)
    tail = f"  [{'/'.join(flags)}]" if flags else ""
    dist = f"  {distance:5.1f}km" if distance is not None else ""
    return (f"{station['pref']:<8} {station['name']:<8} ({station['kana']})"
            f"  prec_no={station['prec_no']:>2} block_no={station['block_no']:>5}"
            f"  {kind}{dist}{tail}")
