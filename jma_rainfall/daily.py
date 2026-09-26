#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JMA 日別値データ収集モジュール

気象庁「過去の気象データ検索」の日別値ページから、公表値の
「降水量の合計 / 最大1時間降水量 / 最大10分間降水量」を取得する。

その日の最大値が必要なときは、10分値表のコマから自前で計算せず、この日別値表の
公表値を使う。日別値表は1分値を1分ずつずらして求めた最大であり、10分値表の
00:10 / 00:20 … という区切りに合わない降り方（連続する1時間が日をまたぐ場合を含む）
でも取りこぼさない。10分ごとの推移が必要なときは10分値表（tenmin）を使う。
"""

import datetime
import itertools

import pandas as pd

from .fetch import FetchError, Pacer, fetch_station_page, new_session, record_failure
from .tables import PRECIP_FIELDS, TableError, parse_daily

PAGE = "daily_{kind}1"

COLUMNS = ["日付"] + list(PRECIP_FIELDS) + ["日別値_品質"]


def date_range(start, end):
    """start から end までの日付（両端を含む）を順に返す。"""
    return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]


class JMADailyCollector:
    """日別値（公表値）の収集クラス。1地点1か月＝1リクエスト。"""

    def __init__(self, station, verify_ssl=True, pacer=None, session=None):
        """
        Args:
            station (dict): 観測所マスタの1件（jma_rainfall.stations 参照）
            verify_ssl (bool): SSL証明書を検証するか
            pacer (Pacer): 取得の歩調（省略時は2秒間隔・200回ごとに60秒休止・3回連続失敗で打ち切り）
            session (requests.Session): 使い回すセッション
        """
        self.station = station
        self.verify_ssl = verify_ssl
        self.pacer = pacer or Pacer()
        self.session = session or new_session()
        self.failures = []

    def collect(self, start_date, end_date):
        """期間に含まれる各月を取得し、期間内の日だけの DataFrame を返す。

        取れなかった月と、表に行がなかった日（まだ公表されていない日など）は failures に残して次の月へ進む。
        失敗が続くと Pacer が FetchAborted で打ち切る。
        """
        records = []
        for (year, month), days in itertools.groupby(date_range(start_date, end_date), lambda d: (d.year, d.month)):
            want = [d.isoformat() for d in days]
            target = f"{year}年{month}月 日別値"
            failed = False
            try:
                soup = fetch_station_page(self.session, self.station, PAGE, year, month,
                                          verify_ssl=self.verify_ssl)
                month_records = [r for r in parse_daily(soup, year, month) if r["日付"] in want]
            except (FetchError, TableError) as e:
                failed = True
                record_failure(self.failures, self.station, target, str(e))
            else:
                records.extend(month_records)
                have = {r["日付"] for r in month_records}
                absent = [d for d in want if d not in have]
                if absent:
                    record_failure(self.failures, self.station, target,
                                   f"表に {absent[0]}〜{absent[-1]} の {len(absent)} 日の行がありません（まだ公表されていない可能性があります）")
            self.pacer.done(failed)
        return pd.DataFrame(records, columns=COLUMNS)
