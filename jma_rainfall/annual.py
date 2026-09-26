#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JMA 年ごとの値・観測史上順位の取得モジュール

- 年ごとの値（annually_s.php / annually_a.php）。1地点1表示＝1リクエストで全年が出る。
  view=""   : 年の合計・最大（降水量の合計、日最大、最大1時間、最大10分間 など）
  view="a1" : 詳細（降水量の最大24時間とその期間 など。官署）
  view="a5" : N時間降水量（1・3・6・12・24・48・72時間の年最大と時刻、1976年以降）。
              2002年以前は毎正時で区切った値、2003年以降は10分間隔で求めた値
- 観測史上1〜10位の値（rank_s.php / rank_a.php）

列はヘッダの多段ラベルを「/」でつないだ名前にする（位置決め打ちはしない）。
"""

from .fetch import fetch_station_page, new_session
from .tables import parse_annual, parse_rank


def fetch_annual(station, view="a5", session=None, verify_ssl=True):
    """年ごとの値を取得する（全年を1リクエストで）。値は公表の文字列のまま（記号 ) ] などを含む）。"""
    soup = fetch_station_page(session or new_session(), station, "annually_{kind}", view=view, verify_ssl=verify_ssl)
    df = parse_annual(soup)
    df.insert(0, "地点", station["name"])
    df.insert(1, "block_no", station["block_no"])   # 地点名は一意でないので観測所の番号も入れる
    return df


def fetch_rank(station, session=None, verify_ssl=True):
    soup = fetch_station_page(session or new_session(), station, "rank_{kind}", verify_ssl=verify_ssl)
    df = parse_rank(soup)
    df.insert(0, "地点", station["name"])
    df.insert(1, "block_no", station["block_no"])   # 地点名は一意でないので観測所の番号も入れる
    return df
