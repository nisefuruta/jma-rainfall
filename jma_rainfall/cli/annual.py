"""
年ごとの値・観測史上順位を取得する（1地点1表示＝1リクエストで全年が出る）。

  jma-rainfall annual --station 千葉 館山 --pref 千葉県 --view a5      N時間降水量（1〜72時間の年最大）
  jma-rainfall annual --station 千葉 --pref 千葉県 --view a1           詳細（最大24時間降水量など）
  jma-rainfall annual --station 千葉 --pref 千葉県 --view ""           年の合計・最大（最大10分間など）
  jma-rainfall annual --station 千葉 --pref 千葉県 --rank              観測史上1〜10位の値と統計期間

出力: <保存先>/annual_<view>.csv（--view "" のときは annual_total.csv）または rank.csv。
  全地点を1つの表にする（列 block_no は地点番号）。UTF-8 BOM 付き。値は公表の文字列のまま（記号 ) ] などを含む）。
  同じ名前のファイルがあれば、今回取得した地点（地点番号で区別）の行だけを置き換え、ほかの地点の行は残す。
"""

from pathlib import Path

import pandas as pd

from jma_rainfall import stations as st
from jma_rainfall.annual import fetch_annual, fetch_rank
from jma_rainfall.cli._common import (CliError, UsageError, add_fetch_arguments, add_station_arguments, find_stations,
                                      read_output_file, report_failures, warn_limitations)
from jma_rainfall.fetch import FetchError, Pacer, new_session, record_failure
from jma_rainfall.tables import TableError

HELP = "年ごとの値（N時間降水量など）、--rank で観測史上1〜10位"


def add_arguments(parser):
    add_station_arguments(parser)
    parser.add_argument("--view", help='年ごとの値の種類。a5: N時間降水量（1〜72時間の年最大）、a1: 詳細（最大24時間降水量など）、'
                                       '"": 年の合計・最大（既定: a5。--rank とは一緒に指定できない）')
    parser.add_argument("--rank", action="store_true", help="観測史上1〜10位の値を取得する")
    add_fetch_arguments(parser)


def run(args):
    if args.rank and args.view is not None:
        raise UsageError("--rank と --view は一緒に指定できません。")
    view = "a5" if args.view is None else args.view
    targets = find_stations(args, st.load_stations(args.master))   # 取得の前にすべて確かめる
    warn_limitations(targets)
    path = Path(args.output_dir) / ("rank.csv" if args.rank else f"annual_{view or 'total'}.csv")
    existing = read_existing(path)   # 取得の前に確かめる（読めないファイルのために取得を無駄にしない）
    session, pacer = new_session(), Pacer(interval=args.interval)
    frames, failures = [], []
    target = "観測史上順位" if args.rank else f'年ごとの値（--view "{view}"）'
    try:
        for station in targets:
            failed = False
            try:
                if args.rank:
                    frames.append(fetch_rank(station, session, not args.disable_ssl_verify))
                else:
                    frames.append(fetch_annual(station, view, session, not args.disable_ssl_verify))
            except (FetchError, TableError) as e:   # 1地点の失敗で止めず、記録して次の地点へ進む
                failed = True
                record_failure(failures, station, target, str(e))
            pacer.done(failed)
    finally:   # 失敗が続いて中止したとき・中断したときも、取得できた地点は保存する
        if frames:
            save(path, existing, pd.concat(frames, ignore_index=True))
    if failures:
        report_failures(failures, "取得できなかった地点だけを --station に指定してもう一度実行すると、"
                                  "その地点の行をファイルに加えます（ほかの地点の行は残ります）。")
        return 1
    return 0


def read_existing(path):
    """保存先にある同じ名前のファイル（なければ None）。地点番号（block_no）の列がない以前の版のファイルは CliError。"""
    if not path.exists():
        return None
    df = read_output_file(path, list(pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns))   # 読めなければ CliError
    if "block_no" not in df.columns:
        raise CliError(f"保存先に、以前の版（jma-rainfall 0.8 まで）で保存したファイルがあります: {path}\n"
                       "  以前の版のファイルには地点番号（block_no）の列がないため、今回取得した地点の行を置き換えられません。"
                       "取得を中止しました。ファイルは書き換えていません。\n"
                       "  このファイルを別のフォルダへ移すか、--output_dir で別の保存先を指定してから、もう一度実行してください。")
    return df


def save(path, existing, fetched):
    """取得した地点の行を保存する。既存のファイルがあれば、同じ地点番号の行を置き換え、ほかの地点の行は残す。"""
    kept = 0
    if existing is not None:
        if list(existing.columns) != list(fetched.columns):
            raise CliError(f"保存先にある既存のファイルの列が、今回取得した表と違います: {path}\n"
                           "  気象庁のページの構成が変わった可能性があります。値がずれるのを防ぐため、既存のファイルは書き換えていません。\n"
                           "  このファイルを別のフォルダへ移すか、--output_dir で別の保存先を指定してから、もう一度実行してください。")
        rest = existing[~existing["block_no"].isin(set(fetched["block_no"]))]
        kept = rest["block_no"].nunique()
        fetched = pd.concat([rest, fetched], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fetched.to_csv(path, index=False, encoding="utf-8-sig")
    n = fetched["block_no"].nunique() - kept
    print(f"{path} に {n} 地点を保存しました" + (f"（既存のファイルのほかの {kept} 地点の行は残しました）" if kept else ""))
