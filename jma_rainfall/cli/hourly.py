"""
時間値を取得する（1地点1日＝1リクエスト）。既にファイルにある日は取得しない（中断しても続きから実行できる）。

  jma-rainfall hourly --station 千葉 --pref 千葉県 --start 2013-10-15 --end 2013-10-16
  jma-rainfall hourly --station 千葉 館山 --pref 千葉県 --dates-file days.txt

出力: <保存先>/hourly_<地点>_<地点番号>.csv（UTF-8 BOM 付き）
  列: 地点, block_no（地点番号）, 日付, 時（1〜24。その時刻で終わる1時間）, 降水量_mm, 品質
"""

from pathlib import Path

import pandas as pd
from tqdm import tqdm

from jma_rainfall import stations as st
from jma_rainfall.cli._common import (CliError, UsageError, add_fetch_arguments, add_station_arguments,
                                      check_not_future, find_stations, parse_date, read_dates_file,
                                      read_output_file, report_failures, warn_limitations)
from jma_rainfall.fetch import Pacer, new_session
from jma_rainfall.hourly import COLUMNS, JMAHourlyCollector
from jma_rainfall.daily import date_range

HELP = "時間値"


def add_arguments(parser):
    add_station_arguments(parser)
    parser.add_argument("--start", type=parse_date, help="開始日（例: 2013-10-15）")
    parser.add_argument("--end", type=parse_date, help="終了日（省略時は開始日）")
    parser.add_argument("--dates-file", help="取得する日を1行に1日ずつ（例: 2013-10-16）書いたファイル（--start・--end の代わりに使う）")
    add_fetch_arguments(parser)


def output_path(out_dir, station):
    """保存先のファイル。同名の観測所（「金山」など）で重ならないよう block_no を含める（10分値と同じ）。"""
    return Path(out_dir) / f"hourly_{station['name']}_{station['block_no']}.csv"


def check_legacy_file(out_dir, station):
    """以前の版（0.8 まで）の時間値のファイルが保存先にあれば CliError にする。

    0.7 までは hourly_<地点>.csv、0.8 は今の名前で、どちらも block_no の列がなく、どの観測所の値か確かめられない。
    黙って無視すると取得済みの日を取り直して2つのファイルができ、そのまま追記すると列がずれる。
    利用者に手で列を加えさせない（Excel で編集すると block_no の先頭の 0 が消える）。移して取り直してもらう。
    """
    old, new = Path(out_dir) / f"hourly_{station['name']}.csv", output_path(out_dir, station)
    # 今の名前で 0.8 と違う列のファイル（別の形式）は、read_output_file が列の違いとして知らせる
    legacy = old if old.exists() else new if new.exists() and read_header(new) == [c for c in COLUMNS if c != "block_no"] else None
    if legacy:
        raise CliError(
            f"保存先に、以前の版（jma-rainfall 0.8 まで）で保存した「{station['name']}」の時間値のファイルがあります: {legacy}\n"
            "  以前の版のファイルには地点番号（block_no）の列がないため、地点名が同じ別の観測所（「金山」など）の値と"
            "区別できません。取り違えを防ぐため、取得を中止しました。\n"
            "  このファイルを別のフォルダへ移してから、もう一度実行してください（取得済みの日も取得し直します）。")


def read_header(path):
    """ファイルの見出しの列名。読めなければ空（読めない理由は read_output_file が知らせる）。"""
    try:
        return list(pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return []


def run(args):
    if args.dates_file and (args.start or args.end):
        raise UsageError("--dates-file と --start・--end は一緒に指定できません。")
    if args.dates_file:
        dates = read_dates_file(args.dates_file)
    elif args.start:
        end = args.end or args.start
        if end < args.start:
            raise UsageError("--end は --start 以降の日付を指定してください。")
        dates = date_range(args.start, end)
    else:
        raise UsageError("--start か --dates-file で取得する日を指定してください。")
    check_not_future(dates)

    targets = find_stations(args, st.load_stations(args.master))   # 取得の前にすべて確かめる
    warn_limitations(targets)
    out_dir = Path(args.output_dir)
    for station in targets:
        check_legacy_file(out_dir, station)
    out_dir.mkdir(parents=True, exist_ok=True)
    pacer, session = Pacer(interval=args.interval), new_session()   # 全地点で共有する
    failures = []
    for station in targets:
        path = output_path(out_dir, station)
        done = set(read_output_file(path, COLUMNS, station)["日付"]) if path.exists() else set()
        todo = [d for d in dates if d.isoformat() not in done]
        print(f"{station['name']}: {len(dates)}日のうち未取得 {len(todo)}日", flush=True)

        def save(d, recs, path=path):   # 1日ごとに追記する（中断しても取得済みの分は残る）
            pd.DataFrame(recs, columns=COLUMNS).to_csv(path, mode="a", header=not path.exists(),
                                                       index=False, encoding="utf-8-sig")

        col = JMAHourlyCollector(station, verify_ssl=not args.disable_ssl_verify, pacer=pacer,
                                 session=session)
        col.collect(tqdm(todo, desc=station["name"], unit="日"), on_day=save)
        failures += col.failures
    if failures:
        report_failures(failures, "取得できた日は保存しました。同じコマンドをもう一度実行すると、取得できなかった日だけを取得し直します。")
        return 1
    return 0
