#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JMA 時間値データ収集モジュール

気象庁「過去の気象データ検索」の時間値ページ（hourly_s1.php / hourly_a1.php）から、
指定した日の1時間ごとの降水量を取得する。1地点1日＝1リクエスト。

降水量の列は表のヘッダのラベル（「降水量」で始まる列）で決める。位置決め打ちはしない。
「時」は1〜24で、その時刻で終わる1時間の値（24 は当日 23:00〜24:00）。
"""

import pandas as pd

from .fetch import FetchError, Pacer, fetch_station_page, new_session, record_failure
from .tables import TableError, parse_hourly

PAGE = "hourly_{kind}1"
COLUMNS = ["地点", "block_no", "日付", "時", "降水量_mm", "品質"]   # 10分値と同じく観測所の番号を入れる


class JMAHourlyCollector:
    """時間値の収集クラス。取得する日を指定し、Pacer の歩調で1日ずつ取る。"""

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

    def collect(self, dates, on_day=None):
        """dates の各日を取得して DataFrame で返す。取れなかった日は failures に残す（握りつぶさない）。
        on_day(date, records) を渡すと1日ごとに呼ぶ（途中で止まっても取得済みの分を保存できるように）。"""
        rows = []
        for d in dates:
            failed = False
            try:
                soup = fetch_station_page(self.session, self.station, PAGE, d.year, d.month, d.day,
                                          verify_ssl=self.verify_ssl)
                recs = [{"地点": self.station["name"], "block_no": self.station["block_no"], **r} for r in parse_hourly(soup, d)]
                rows += recs
                if on_day:
                    on_day(d, recs)
            except (FetchError, TableError) as e:
                failed = True
                record_failure(self.failures, self.station, f"{d} 時間値", str(e))
            self.pacer.done(failed)
        return pd.DataFrame(rows, columns=COLUMNS)
