"""
jma-rainfall コマンド。取得するものをサブコマンドで選ぶ。

各サブコマンドは add_arguments(parser) でオプションを定義し、run(args) で実行して終了コードを返す。
観測所の指定の誤りと取得の打ち切りは、ここでまとめて標準エラーに出し、終了コード1にする。
"""

import argparse
import logging
import sys

from jma_rainfall import __version__
from jma_rainfall import stations as st
from jma_rainfall.cli import annual, hourly, stations, tenmin
from jma_rainfall.cli._common import CliError, UsageError, describe_read_error
from jma_rainfall.fetch import FetchAborted

SUBCOMMANDS = {
    "10min": tenmin,
    "hourly": hourly,
    "annual": annual,
    "stations": stations,
}

# 取得の途中で止まったとき（失敗が続いた・中断した）の次の操作。保存したファイルは 10min・annual では各処理が表示する
AFTER_STOP = {
    "10min": "同じコマンドに --resume を付けて実行すると、続きから取得できます。",
    "hourly": "取得済みの日は保存しました。同じコマンドをもう一度実行すると、続きから取得します。",
    "annual": "取得できなかった地点を --station に指定して実行すると、その地点の行をファイルに加えます。",
}


def build_parser():
    """コマンド全体の parser と、サブコマンドごとの parser を返す。"""
    parser = argparse.ArgumentParser(
        prog="jma-rainfall",
        description="気象庁「過去の気象データ検索」から降水量を取得する。",
        epilog="各サブコマンドのオプションは jma-rainfall <サブコマンド> --help で表示する。")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<サブコマンド>")
    subparsers = {}
    for name, module in SUBCOMMANDS.items():
        p = sub.add_parser(name, help=module.HELP, description=module.HELP, epilog=module.__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        module.add_arguments(p)
        p.set_defaults(run=module.run)
        subparsers[name] = p
    return parser, subparsers


class LevelFormatter(logging.Formatter):
    """警告以上のログの前に「注意:」「エラー:」を付ける（経過の知らせはそのまま）。"""
    PREFIX = {logging.WARNING: "注意: ", logging.ERROR: "エラー: ", logging.CRITICAL: "エラー: "}

    def format(self, record):
        return self.PREFIX.get(record.levelno, "") + record.getMessage()


def main(argv=None):
    # 日本語を出すので、リダイレクトしたときも UTF-8 で書く
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # ライブラリの警告（取得の失敗、地点名の解釈）と経過（休止・再試行）を標準エラーに出す。警告には「注意:」を付ける
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(LevelFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    parser, subparsers = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "disable_ssl_verify", False):
        print("警告: SSL証明書の検証を無効にしています（--disable_ssl_verify）。"
              "通信の相手を確かめないため、信頼できるネットワークでだけ使ってください。", file=sys.stderr)
    after_stop = AFTER_STOP.get(args.command)
    try:
        return args.run(args)
    except UsageError as e:
        subparsers[args.command].error(str(e))   # 使い方を表示し、終了コード2で終わる
    except st.AmbiguousStation as e:
        print(f"エラー: {e}", file=sys.stderr)
        print("  --pref で都道府県を指定するか、--station に地点番号（block_no）を指定してください。", file=sys.stderr)
    except FetchAborted as e:
        print(f"エラー: {e}", file=sys.stderr)
        if after_stop:
            print(f"  {after_stop}", file=sys.stderr)
    except (st.StationNotFound, CliError) as e:
        print(f"エラー: {e}", file=sys.stderr)
    except OSError as e:   # 保存先に書けない（Excel で開いている、容量不足など）
        target = f": {e.filename}" if e.filename else ""
        print(f"エラー: ファイルの読み書きに失敗しました{target}\n"
              f"  原因: {describe_read_error(e)}\n"
              "  Excel などでファイルを開いているときは閉じてください。保存先のフォルダに書き込めるか、"
              "ディスクの空きがあるかも確かめてから、もう一度実行してください。", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n中断しました。" + (after_stop or ""), file=sys.stderr)
        return 130
    return 1
