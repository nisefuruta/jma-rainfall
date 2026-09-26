"""サブコマンドで共通のオプションと引数の解釈。"""

import argparse
import datetime
import errno
import sys
from pathlib import Path

import pandas as pd

from jma_rainfall import stations as st
from jma_rainfall.fetch import DEFAULT_INTERVAL


class UsageError(Exception):
    """オプションの組み合わせの誤り。main() がサブコマンドの使い方と一緒に表示する。"""


class CliError(Exception):
    """利用者が確かめる必要のある誤り（読めない既存ファイルなど）。main() が表示して終了コード1にする。"""


def parse_date(text):
    """YYYY-MM-DD / YYYYMMDD / YYYY/M/D を date に変換する。"""
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"日付として解釈できません: {text}（2013-10-16、20131016、2013/10/16 のいずれかの形で指定してください）")


def parse_latlon(text):
    """'35.83,140.14' を (緯度, 経度) に変換する。"""
    try:
        lat, lon = (float(x) for x in text.replace(" ", "").split(","))
        return lat, lon
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"緯度,経度の形式で指定してください（例: 35.8320,140.1450）: {text}")


def describe_read_error(e):
    """ファイルを読み書きできなかった例外を、利用者が原因を推測できる日本語の短い説明にする（元の文は「詳細」に残す）。"""
    if isinstance(e, PermissionError):
        reason = "アクセスが拒否されました。Excel などのソフトで開いているか、読み書きの権限がありません"
    elif isinstance(e, FileNotFoundError):
        return "ファイルまたはフォルダが見つかりません"   # 詳細の英語の文はパスの繰り返しだけなので付けない
    elif isinstance(e, UnicodeDecodeError):
        reason = "文字コードが UTF-8 ではありません。Excel などで別の文字コードで保存し直した可能性があります"
    elif isinstance(e, pd.errors.EmptyDataError):
        return "ファイルが空です"
    elif isinstance(e, pd.errors.ParserError):
        reason = "CSV・TSV の形式として読めません。行によって列の数が違います"
    elif isinstance(e, OSError) and e.errno == errno.ENOSPC:
        reason = "ディスクの空き容量が足りません"
    else:
        reason = "OS がエラーを返しました"
    return f"{reason}（詳細: {e}）"


def read_dates_file(path):
    """1行に1日ずつ書いた日付のファイルを読む。誤りはファイル名と行番号を付けて UsageError にする。"""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as e:   # 「Unicode テキスト」（UTF-16）で保存したファイルなど
        fix = ("\n  メモ帳で開き、「名前を付けて保存」で文字コードを UTF-8 にして保存し直してください。"
               if isinstance(e, UnicodeDecodeError) else "")
        raise UsageError(f"--dates-file に指定したファイルを読めません: {path}\n"
                         f"  原因: {describe_read_error(e)}{fix}")
    dates = []
    for n, line in enumerate(lines, 1):
        for text in line.split():
            try:
                dates.append(parse_date(text))
            except argparse.ArgumentTypeError as e:
                raise UsageError(f"--dates-file {path} の {n} 行目: {e}")
    if not dates:
        raise UsageError(f"--dates-file {path} に日付がありません。取得する日を1行に1日ずつ（例: 2013-10-16）書いてください。")
    # 同じ日を重ねて書いても1回だけ取得する（2回取得して同じ行を追記しない）
    return list(dict.fromkeys(dates))


def check_not_future(dates):
    """未来の日付があれば UsageError にする（気象庁にない日を取りにいかない）。"""
    today = datetime.date.today()
    future = sorted(d for d in dates if d > today)
    if future:
        raise UsageError(f"未来の日付は指定できません: {future[0]}" + (f" ほか {len(future) - 1} 日" if len(future) > 1 else "")
                         + f"。今日（{today}）までの日付を指定してください。")


def read_output_file(path, columns, station=None, sep=","):
    """既存の出力ファイル（--resume・追記・行の置き換えの対象）を読む。

    読めない、列がこのコマンドの出力と違う、または station（観測所。指定したとき）以外の block_no の行があるときは
    CliError にする（上書き・追記して壊さない。別の観測所の値に追記しない）。
    """
    try:
        df = pd.read_csv(path, sep=sep, encoding="utf-8-sig", dtype={"block_no": str})
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise CliError(f"保存先にある既存のファイルを読めません: {path}\n"
                       f"  原因: {describe_read_error(e)}\n"
                       "  このファイルに書き込むと壊すおそれがあるため、取得を中止しました。ファイルは書き換えていません。\n"
                       "  Excel などで開いているときは閉じてから、もう一度実行してください。\n"
                       "  直らないときは、--output_dir で別の保存先を指定するか、このファイルを別のフォルダへ移してから実行してください。")
    if list(df.columns) != list(columns):
        raise CliError(f"保存先にある既存のファイルの列がこのコマンドの出力と違います: {path}\n"
                       f"  ファイルの列: {'、'.join(map(str, df.columns))}\n"
                       f"  このコマンドの列: {'、'.join(columns)}\n"
                       "  別のコマンドや以前の版で保存したファイルの可能性があります。"
                       "形式の違うファイルに書き込むと値がずれるため、取得を中止しました。ファイルは書き換えていません。\n"
                       "  --output_dir で別の保存先を指定するか、このファイルを別のフォルダへ移してから、もう一度実行してください。")
    others = sorted(set(df["block_no"]) - {station["block_no"]}) if station else []
    if others:
        raise CliError(f"保存先のファイルに、今回の「{station['name']}」（{station['pref']}、地点番号 {station['block_no']}）とは"
                       f"別の観測所（地点番号 {'・'.join(others)}）の値が入っています: {path}\n"
                       "  地点名が同じでも別の観測所なので、このファイルに追記すると値が混ざります。取得を中止しました。\n"
                       "  --output_dir で別の保存先を指定するか、このファイルを別のフォルダへ移してから、もう一度実行してください。")
    return df


def warn_limitations(stations):
    """降水量を観測していない地点・廃止された地点を標準エラーで知らせる。"""
    for s in stations:
        notes = []
        if not s["has_precip"]:
            notes.append("観測所マスタでは降水量を観測していない地点です。降水量を取得できない可能性があります。")
        if not s["active"]:
            notes.append(f"{s['end_date']} に廃止された地点です。廃止より後の日の値はありません。")
        if notes:
            print(f"注意: {describe_short(s)}は" + "".join(notes), file=sys.stderr)


def describe_short(station):
    """メッセージ用の観測所の表記（例: 千葉（千葉県、地点番号 47682））。地点名は同名があるので地点番号を添える。"""
    return f"{station['name']}（{station['pref']}、地点番号 {station['block_no']}）"


def report_failures(failures, hint):
    """取得できなかったものを一覧にして標準エラーに出す。hint には保存したものと次の操作を書く。"""
    print(f"\n取得できなかったデータが {len(failures)} 件あります:", file=sys.stderr)
    for f in failures:
        print(f"  {f['地点']}（地点番号 {f['block_no']}） {f['対象']}: {f['理由']}", file=sys.stderr)
    print(hint, file=sys.stderr)


def add_master_argument(parser):
    """観測所マスタの指定（--master）。"""
    parser.add_argument("--master", help="観測所マスタ（stations.json）のパス。通常は指定しない（省略時はパッケージに含まれるものを使う）")


def add_station_arguments(parser, required=True):
    """観測所の指定（--station、--pref、--master）。"""
    parser.add_argument("--station", nargs="+", required=required, metavar="地点",
                        help="観測所を地点名・カナ・地点番号（block_no）で指定（複数可）。"
                             "地点名と地点番号は jma-rainfall stations で確認できる")
    parser.add_argument("--pref", help="都道府県・地方名（同名地点の絞り込みに使う）")
    add_master_argument(parser)


def find_stations(args, master):
    """--station の地点をすべて観測所マスタで確かめ、観測所のリストを返す（取得を始める前に呼ぶ）。

    同じ地点を重ねて指定したとき（地点名とその block_no など）は1回だけ取得する。
    """
    unique = {}
    for key in args.station or []:
        s = st.find_station(key, master, pref=args.pref)
        if s["block_no"] in unique:
            print(f"注意: {describe_short(s)}が2回以上指定されています。1回だけ取得します。", file=sys.stderr)
        unique.setdefault(s["block_no"], s)
    return list(unique.values())


def add_fetch_arguments(parser):
    """取得と保存のオプション（--output_dir、--interval、--disable_ssl_verify）。"""
    parser.add_argument("--output_dir", default="data", help="保存先のフォルダ（既定: data）")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"気象庁サーバへのアクセス間隔の秒数（既定: {DEFAULT_INTERVAL}）")
    parser.add_argument("--disable_ssl_verify", action="store_true",
                        help="SSL証明書の検証を無効にする（一時的な対処。通常は Windows の証明書ストアで検証する。"
                             "README の「SSL証明書の問題」を参照）")
