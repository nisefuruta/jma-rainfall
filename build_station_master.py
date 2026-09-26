#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
観測所マスタ生成スクリプト

気象庁「過去の気象データ検索」の地点選択ページから、全国の観測所情報
（地点名・カナ・種別・緯度経度・標高・観測要素・廃止年月日）を収集し、
jma_rainfall/stations.json として保存する。

マスタはリポジトリにコミット済みのため、通常の利用時に実行する必要はない。
観測所の新設・廃止を反映したいときだけ実行する（気象庁サーバへ約60回アクセス）。
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from jma_rainfall.cli._common import describe_read_error
from jma_rainfall.fetch import DEFAULT_INTERVAL, FetchAborted, FetchError, Pacer, fetch_text, new_session

INDEX_URL = "https://www.data.jma.go.jp/stats/etrn/select/prefecture00.php"
PREF_URL = "https://www.data.jma.go.jp/stats/etrn/select/prefecture.php"

# <area ...> タグから prec_no と alt（地方・都道府県名）を取り出す
AREA_RE = re.compile(r'<area\b[^>]*>', re.I)
ALT_RE = re.compile(r'alt="([^"]*)"')
PREC_RE = re.compile(r'prec_no=(\d+)')

# 地点マップ上の JavaScript 呼び出し
#   viewPoint(as, bk_no, ch, ch_kn, lat_d, lat_m, lon_d, lon_m, height,
#             f_pre, f_wsp, f_tem, f_sun, f_snc, f_hum, ed_y, ed_m, ed_d, bikou1..5)
VIEWPOINT_RE = re.compile(r"viewPoint\(([^)]*)\)")
ARG_RE = re.compile(r"'([^']*)'")

_SESSION = new_session()


def fetch_prefectures(verify_ssl=True):
    """都道府県・地方の一覧 [(prec_no, 名称), ...] を返す。"""
    html = fetch_text(_SESSION, INDEX_URL, {"prec_no": "", "block_no": ""}, verify_ssl=verify_ssl)
    prefs = []
    seen = set()
    for tag in AREA_RE.findall(html):
        m_prec = PREC_RE.search(tag)
        m_alt = ALT_RE.search(tag)
        if not (m_prec and m_alt):
            continue
        prec_no = m_prec.group(1)
        if prec_no in seen:
            continue
        seen.add(prec_no)
        prefs.append((prec_no, m_alt.group(1)))
    return prefs


def _to_deg(deg, minute):
    """度・分を十進度に変換する。値が欠けている場合は None。"""
    try:
        return round(float(deg) + float(minute) / 60.0, 5)
    except (TypeError, ValueError):
        return None


def parse_stations(html, prec_no, pref_name):
    """1都道府県のページから観測所情報を抽出する。"""
    stations = {}
    for call in VIEWPOINT_RE.findall(html):
        a = ARG_RE.findall(call)
        if len(a) < 18:
            continue
        kind, block_no = a[0], a[1]
        # 関数定義そのもの（引数名が並ぶ行）を弾く
        if kind not in ("a", "s") or not block_no.isdigit():
            continue
        if block_no in stations:  # 同一地点が複数の area タグに現れるため
            continue
        end_y, end_m, end_d = a[15], a[16], a[17]
        active = end_y == "9999"
        stations[block_no] = {
            "prec_no": prec_no,
            "block_no": block_no,
            "pref": pref_name,
            "name": a[2],
            "kana": a[3],
            "kind": kind,                       # 'a'=アメダス, 's'=官署
            "lat": _to_deg(a[4], a[5]),
            "lon": _to_deg(a[6], a[7]),
            "elevation": a[8],
            "has_precip": a[9] == "1",
            "active": active,
            "end_date": None if active else f"{end_y}-{end_m}-{end_d}",
            "note": a[18] if len(a) > 18 and a[18] else "",
        }
    return list(stations.values())


def build(output, verify_ssl=True, interval=DEFAULT_INTERVAL):
    """全国の観測所マスタを生成して JSON へ保存する。取得に失敗したら、既存のマスタは書き換えない。"""
    if not verify_ssl:
        print("警告: SSL証明書の検証を無効にしています（--disable_ssl_verify）。"
              "通信の相手を確かめないため、信頼できるネットワークでだけ使ってください。", file=sys.stderr)

    pacer = Pacer(interval=interval)
    prefs = fetch_prefectures(verify_ssl)
    print(f"都道府県・地方: {len(prefs)} 件")
    pacer.done()
    if not prefs:
        raise BuildError("気象庁の地点選択ページから、都道府県・地方の一覧を1つも読み取れませんでした。\n"
                         "  ページが一時的にエラーを返したか、ページの構成が変わった可能性があります。\n"
                         "  時間をおいてもう一度実行し、同じエラーになるときは、このスクリプトの fetch_prefectures"
                         "（都道府県・地方の一覧の読み取り処理）を直す必要があります。")

    all_stations = []
    for prec_no, name in prefs:
        html = fetch_text(_SESSION, PREF_URL, {"prec_no": prec_no, "block_no": ""}, verify_ssl=verify_ssl)
        stations = parse_stations(html, prec_no, name)
        all_stations.extend(stations)
        print(f"  prec_no={prec_no:>2} {name}: {len(stations)} 地点")
        pacer.done()
        # 観測所のない都道府県・地方はない（最少は南極の1地点）。0件はエラーページか構成の変更で読めなかったもので、
        # 保存するとその地域の観測所がマスタから消える
        if not stations:
            raise BuildError(f"{name}（都府県・地方の番号 prec_no={prec_no}）のページから観測所を1つも読み取れませんでした。\n"
                             "  気象庁のページが一時的にエラーを返したか、ページの構成が変わった可能性があります。\n"
                             "  時間をおいてもう一度実行し、同じエラーになるときは、このスクリプトの parse_stations"
                             "（観測所の読み取り処理）を直す必要があります。")

    no_coord = [s for s in all_stations if s["lat"] is None or s["lon"] is None]
    if no_coord:
        print(f"警告: 座標を読み取れない観測所が {len(no_coord)} 地点あります。これらの地点はマスタに保存しますが、"
              "--near で近い観測所を探すときには対象になりません: "
              + "、".join(f"{s['name']}（地点番号 {s['block_no']}）" for s in no_coord[:10])
              + (f" ほか {len(no_coord) - 10} 地点" if len(no_coord) > 10 else ""), file=sys.stderr)

    all_stations.sort(key=lambda s: (s["prec_no"], s["block_no"]))
    path = Path(output)
    report_changes(path, all_stations)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"stations": all_stations}, f, ensure_ascii=False, indent=1)

    active = sum(1 for s in all_stations if s["active"])
    precip = sum(1 for s in all_stations if s["has_precip"])
    print(f"\n保存: {path}")
    print(f"  合計 {len(all_stations)} 地点 / 現存 {active} 地点 / 降水量観測あり {precip} 地点")


class BuildError(Exception):
    """マスタを作れないことを表す（既存のマスタは書き換えない）。"""


def report_changes(path, stations):
    """既存のマスタとの違い（追加・なくなった地点、廃止になった地点）を表示する。"""
    if not path.exists():
        return
    try:
        old = {s["block_no"]: s for s in json.loads(path.read_text(encoding="utf-8"))["stations"]}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"警告: 既存のマスタを読めないため、新しいマスタとの違いを表示できません: {path}（詳細: {e}）\n"
              "  既存のマスタは新しいマスタで上書きします。", file=sys.stderr)
        return
    new = {s["block_no"]: s for s in stations}
    added = [new[b] for b in new.keys() - old.keys()]
    removed = [old[b] for b in old.keys() - new.keys()]
    closed = [new[b] for b in new.keys() & old.keys() if old[b]["active"] and not new[b]["active"]]
    for label, items in (("追加", added), ("なくなった", removed), ("廃止になった", closed)):
        print(f"既存のマスタから{label}地点: {len(items)}"
              + ("（" + "、".join(f"{s['name']}({s['block_no']})" for s in items[:20]) + "）" if items else ""))


def main():
    parser = argparse.ArgumentParser(
        description="気象庁の地点選択ページから観測所マスタ（stations.json）を作り直す。"
                    "気象庁サーバへ約60回アクセスするので、観測所の新設・廃止を反映するときだけ実行する。")
    parser.add_argument("--output", type=str, default="jma_rainfall/stations.json",
                        help="保存先（既定: jma_rainfall/stations.json）")
    parser.add_argument("--disable_ssl_verify", action="store_true",
                        help="SSL証明書の検証を無効にする（一時的な対処。通常は Windows の証明書ストアで検証する。README の「SSL証明書の問題」を参照）")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"気象庁サーバへのアクセス間隔の秒数（既定: {DEFAULT_INTERVAL}）")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")   # Pacer の休止を表示する
    try:
        build(args.output, verify_ssl=not args.disable_ssl_verify, interval=args.interval)
    except (FetchError, FetchAborted, BuildError) as e:
        print(f"エラー: {e}\n  観測所マスタ（{args.output}）は書き換えていません。", file=sys.stderr)
        sys.exit(1)
    except OSError as e:   # 保存先に書けない（エディタで開いている、権限がないなど）
        print(f"エラー: 観測所マスタを保存できません: {args.output}\n  原因: {describe_read_error(e)}\n"
              "  ファイルを開いているソフトを閉じるか、--output で書き込める保存先を指定して、もう一度実行してください。",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
