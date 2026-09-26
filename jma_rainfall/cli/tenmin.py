"""
10分値を取得する（1地点1日＝1リクエスト。日別値表の公表値も取得して日ごとに集計する）。

  jma-rainfall 10min --station 我孫子 成田 --pref 千葉県 --start 2026-08-13 --end 2026-08-14
  jma-rainfall 10min --near 35.8320,140.1450 --near_count 3 --start 2026-08-13
  jma-rainfall 10min --station 千葉 --pref 千葉県 --start 2013-10-15 --end 2013-10-16 --skip_daily

観測所の地点名と地点番号（block_no）は jma-rainfall stations で確認できる。
"""

import sys

import pandas as pd
from tqdm import tqdm

from jma_rainfall import stations as st
from jma_rainfall.cli._common import (UsageError, add_fetch_arguments, add_station_arguments, check_not_future,
                                      describe_short, find_stations, parse_date, parse_latlon, read_output_file, report_failures,
                                      warn_limitations)
from jma_rainfall.tables import TENMIN_LABELS
from jma_rainfall.cli.stations import add_filter_arguments
from jma_rainfall.daily import JMADailyCollector
from jma_rainfall.fetch import Pacer, new_session
from jma_rainfall.tenmin import COLUMNS, JMATenMinCollector

HELP = "10分値（と日別値の公表値の集計）"


def add_arguments(parser):
    add_station_arguments(parser, required=False)
    parser.add_argument("--near", type=parse_latlon, metavar="緯度,経度",
                        help="指定した座標に近い観測所を対象にする（例: 35.8320,140.1450）")
    add_filter_arguments(parser)
    parser.add_argument("--start", type=parse_date, required=True, help="開始日（例: 2026-08-13）")
    parser.add_argument("--end", type=parse_date, help="終了日（省略時は開始日）")
    add_fetch_arguments(parser)
    parser.add_argument("--format", choices=["tsv", "csv"], default="tsv", help="出力形式（既定: tsv）")
    parser.add_argument("--skip_daily", action="store_true",
                        help="日別値表（公表値の日合計・最大1時間・最大10分）を取得しない。"
                             "日別集計のファイルも保存せず、10分値のファイルだけを保存する")
    parser.add_argument("--resume", action="store_true",
                        help="前回の出力ファイル（同じ地点・期間・形式）で10分値が144コマそろっている日は取得し直さない"
                             "（中断や取得の失敗のあとに、続きから取得するときに使う）")


def resolve_stations(args, master):
    """--station / --near から対象の観測所リストを決める。"""
    targets = find_stations(args, master)

    if args.near:
        lat, lon = args.near
        # 絞り込み条件は stations と揃える（一覧で見た地点がそのまま対象になるように）。--pref もその中から探す
        for dist, s in st.nearest_stations(lat, lon, st.filter_stations(master, pref=args.pref), count=args.near_count,
                                           precip_only=not args.all_elements,
                                           active_only=not args.include_closed):
            print(f"最寄り観測所: {st.describe(s, dist)}")
            targets.append(s)

    # 同一地点の重複を除く。block_no は全国で一意（富士山のように
    # 複数の prec_no に現れる同一地点があるため prec_no は鍵に含めない）
    unique = {}
    for s in targets:
        if s["block_no"] in unique:
            print(f"注意: {describe_short(s)}が2回以上指定されています。1回だけ取得します。", file=sys.stderr)
        unique.setdefault(s["block_no"], s)
    return list(unique.values())


def merge_rows(existing, collected):
    """既存の行と今回取得した行を突き合わせ、日付・時刻順に並べ直す。"""
    frames = [f for f in (existing, collected) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["日付", "時分"], keep="last")
    # 「時分」は 0:10 と 10:00 の文字列比較が効かないため区間終了時刻で並べる
    return merged.sort_values(["日付", "区間終了時刻"]).reset_index(drop=True)


def run_station(station, args, end, pacer, session):
    """1地点を収集・保存し、(日別集計, 失敗リスト) を返す。pacer と session は全地点で共有する。"""
    collector = JMATenMinCollector(
        station=station,
        start_date=args.start,
        end_date=end,
        output_dir=args.output_dir,
        verify_ssl=not args.disable_ssl_verify,
        pacer=pacer,
        session=session,
    )
    sep = "\t" if args.format == "tsv" else ","

    existing, done = None, set()
    if args.resume:
        path = collector.output_path(args.format)
        if path.exists():
            existing = read_output_file(path, COLUMNS, station, sep)
            slots = existing.groupby("日付")["時分"].apply(set)
            done = {d for d, s in slots.items() if s == set(TENMIN_LABELS)}
            partial = sorted(set(slots.index) - done)
            print(f"--resume: {path} にある {len(done)} 日分はスキップします。")
            if partial:
                print(f"--resume: 10分値が144コマそろっていない {len(partial)} 日（{partial[0]} など）は取得し直します。")

    dates = [d for d in collector.dates if d.isoformat() not in done]
    try:
        collected = collector.collect(tqdm(dates, desc=station["name"], unit="日"))
    except BaseException:
        # 中断・想定外の例外でも、取得済みの分は残す（例外はそのまま投げ直す）
        partial = merge_rows(existing, collector.frame())
        if not partial.empty:
            path = collector.save(partial, fmt=args.format)
            print(f"\n途中で止まったため、取得済みの10分値 {len(partial)} 行を {path} に保存しました。",
                  file=sys.stderr)
        raise

    df = merge_rows(existing, collected)
    failures = list(collector.failures)
    # 日別値の取得が打ち切られても10分値が残るよう、先に保存する
    path = collector.save(df, fmt=args.format) if not df.empty else None

    daily = None
    if not args.skip_daily:
        daily_collector = JMADailyCollector(
            station=station,
            verify_ssl=not args.disable_ssl_verify,
            pacer=pacer,
            session=session,
        )
        daily = daily_collector.collect(args.start, end)
        failures.extend(daily_collector.failures)

    print(f"\n■ {station['pref']} {station['name']}（地点番号 {station['block_no']}）"
          f" → {path if path else '出力なし'}")
    summary = collector.summarize(df, daily=daily)
    print(summary.to_string(index=False))
    return summary, failures


def run(args):
    if not (args.station or args.near):
        raise UsageError("--station または --near で観測所を指定してください（一覧は jma-rainfall stations）。")
    end = args.end or args.start
    if end < args.start:
        raise UsageError("--end は --start 以降の日付を指定してください。")
    check_not_future([end])

    master = st.load_stations(args.master)
    targets = resolve_stations(args, master)
    if not targets:
        raise UsageError("--near の条件に合う観測所がありません。--pref の都道府県・地方名が正しいか確かめてください"
                         "（都道府県・地方名は jma-rainfall stations で確認できます）。")
    warn_limitations(targets)
    print(f"対象 {len(targets)} 地点 / {args.start} 〜 {end}")

    pacer, session = Pacer(interval=args.interval), new_session()
    summaries, failures = [], []
    try:
        for station in targets:
            summary, station_failures = run_station(station, args, end, pacer, session)
            summaries.append(summary)
            failures.extend(station_failures)
    finally:
        # 打ち切り・中断でも、集計できた地点の分は保存する
        save_summary(summaries, args, end, complete=len(summaries) == len(targets))

    if failures:
        report_failures(failures, "取得できた日は保存しました。同じコマンドに --resume を付けて実行すると、"
                                  "10分値がそろっている日をスキップして、取得できなかった日を取得し直します。")
        return 1
    return 0


def save_summary(summaries, args, end, complete):
    """日別集計を保存する。

    日別集計のファイルは日別値表の公表値を残すためのもの。--skip_daily では公表値がないので保存しない
    （保存先には10分値のファイルだけを置く。集計は標準出力で確認できる）。
    """
    if not summaries or args.skip_daily:
        return
    merged = pd.concat(summaries, ignore_index=True)
    sep = "\t" if args.format == "tsv" else ","
    out = (f"{args.output_dir}/summary_10min_"
           f"{args.start:%Y%m%d}-{end:%Y%m%d}.{args.format}")
    merged.to_csv(out, sep=sep, index=False, encoding="utf-8-sig")
    print(f"\n日別集計: {out}" + ("" if complete else f"（途中で止まったため {len(summaries)} 地点分だけ）"),
          file=sys.stdout if complete else sys.stderr)
    print("※ 設計値には「日別値」列（気象庁の公表値）を使う。「コマ」列は10分値表の"
          "00:10 / 00:20 … という固定の区切りで求めた参考値で、"
          "その区切りに合わない降り方では公表値より小さく出る。")
