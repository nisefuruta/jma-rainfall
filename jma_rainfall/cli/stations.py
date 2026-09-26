"""
観測所の一覧を表示する（同梱の観測所マスタを使い、気象庁へはアクセスしない）。

  jma-rainfall stations --pref 千葉県
  jma-rainfall stations --pref 千葉県 --keyword 千葉
  jma-rainfall stations --near 35.8320,140.1450 --near_count 5
"""

from jma_rainfall import stations as st
from jma_rainfall.cli._common import add_master_argument, parse_latlon

HELP = "観測所の一覧（地点名・地点番号 block_no の確認）"
LEGEND = ("block_no は地点番号（--station に指定できる）、prec_no は都府県・地方の番号。"
          "[降水量なし] は降水量を観測していない地点、[廃止 日付] は廃止された地点")


def add_filter_arguments(parser):
    """観測所の絞り込み（stations と 10min の --near で同じ条件にする）。"""
    parser.add_argument("--near_count", type=int, default=3,
                        help="--near で対象とする観測所の数（既定: 3）")
    parser.add_argument("--include_closed", action="store_true", help="廃止された観測所も含める")
    parser.add_argument("--all_elements", action="store_true", help="降水量を観測していない地点も含める")


def add_arguments(parser):
    parser.add_argument("--pref", help="都道府県・地方名")
    parser.add_argument("--keyword", help="地点名・カナの一部で絞り込む")
    parser.add_argument("--near", type=parse_latlon, metavar="緯度,経度",
                        help="指定した座標に近い観測所を表示する（例: 35.8320,140.1450）")
    add_filter_arguments(parser)
    add_master_argument(parser)


def run(args):
    master = st.load_stations(args.master)
    if args.near:
        lat, lon = args.near
        # --pref・--keyword を指定したときは、その中から近い観測所を探す
        master = st.filter_stations(master, pref=args.pref, keyword=args.keyword)
        print(f"座標 {lat}, {lon} に近い観測所:")
        for dist, s in st.nearest_stations(lat, lon, master, count=args.near_count,
                                           precip_only=not args.all_elements,
                                           active_only=not args.include_closed):
            print("  " + st.describe(s, dist))
        print(f"\n{LEGEND}")
        return 0

    hits = st.filter_stations(master, pref=args.pref,
                              precip_only=not args.all_elements,
                              active_only=not args.include_closed,
                              keyword=args.keyword)
    for s in hits:
        print(st.describe(s))
    print(f"\n該当 {len(hits)} 地点 / マスタ全体 {len(master)} 地点")
    print(LEGEND)
    return 0
