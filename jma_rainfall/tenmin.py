#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JMA 10分間降水量データ収集モジュール

気象庁「過去の気象データ検索」の10分値ページから、指定期間・指定地点の
10分間降水量を収集する。表の解析は jma_rainfall.tables.parse_tenmin が行う。

このモジュールが扱うのは 00:10 / 00:20 … という固定の区切りのコマである。
その日の最大値（最大1時間・最大10分間）はコマから計算せず、日別値表の公表値を
使う（jma_rainfall.daily 参照）。
"""

from pathlib import Path

import pandas as pd

from .daily import date_range
from .fetch import FetchError, Pacer, fetch_station_page, new_session, record_failure
from .tables import NO_VALUE, TableError, parse_tenmin

PAGE = "10min_{kind}1"

COLUMNS = ["地点", "block_no", "日付", "時分", "区間終了時刻", "降水量_mm", "品質"]


class JMATenMinCollector:
    """10分間降水量データの収集クラス。"""

    def __init__(self, station, start_date, end_date, output_dir="data",
                 verify_ssl=True, pacer=None, session=None):
        """
        Args:
            station (dict): 観測所マスタの1件（jma_rainfall.stations 参照）
            start_date (datetime.date): 収集開始日
            end_date (datetime.date): 収集終了日
            output_dir (str): 保存先ディレクトリ
            verify_ssl (bool): SSL証明書を検証するか
            pacer (Pacer): 取得の歩調（省略時は2秒間隔・200回ごとに60秒休止・3回連続失敗で打ち切り）。
                複数の地点や日別値の取得と共有すると、休止と打ち切りを全体で数える
            session (requests.Session): 使い回すセッション
        """
        self.station = station
        self.start_date = start_date
        self.end_date = end_date
        self.output_dir = Path(output_dir)
        self.verify_ssl = verify_ssl
        self.pacer = pacer or Pacer()
        self.session = session or new_session()
        # 途中で中断されても取得済みの分を保存できるよう、収集結果を持ち回る
        self.records = []
        self.failures = []

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _fetch(self, date):
        """1日分のページを取得する。一時的な通信の失敗は fetch_text が再試行する。"""
        return fetch_station_page(self.session, self.station, PAGE, date.year, date.month, date.day,
                                  verify_ssl=self.verify_ssl)

    @property
    def dates(self):
        """収集対象の日付を順に返す。"""
        return date_range(self.start_date, self.end_date)

    def frame(self):
        """ここまでに取得できた分を DataFrame で返す（中断時の保存にも使う）。"""
        return pd.DataFrame(self.records, columns=COLUMNS)

    def collect(self, dates=None):
        """
        dates の各日（省略時は期間全体）を収集して DataFrame で返す。

        1日分の取得に失敗しても、その日を self.failures に記録して次の日へ進む
        （1回の通信エラーで期間全体を失わないため）。失敗は summarize の「状態」列と
        呼び出し側の終了コードに必ず反映される。
        失敗が続くと Pacer が FetchAborted で打ち切る。取得済みの分は frame() で取り出せる。

        Args:
            dates: 取得する日（datetime.date の iterable）。--resume で取得済みの日を除くときや、
                進み具合を表示するとき（tqdm で包む）に渡す。
        """
        for date in self.dates if dates is None else dates:
            failed = False
            try:
                rows = [{"地点": self.station["name"], "block_no": self.station["block_no"], **r}
                        for r in parse_tenmin(self._fetch(date), date)]
            except (FetchError, TableError) as e:
                failed = True
                record_failure(self.failures, self.station, f"{date} 10分値", str(e), 日付=date.isoformat())
            else:
                self.records.extend(rows)
            self.pacer.done(failed)
        return self.frame()

    def summarize(self, df, daily=None):
        """
        日別の集計を DataFrame で返す。

        10分値表のコマから求めた値（列名に「コマ」）と、日別値表の公表値
        （列名に「日別値」）を並べる。設計値に使うのは公表値のほう。

        取得できなかった日も「状態」列に理由を入れて必ず1行出す。日付が黙って
        抜けることはない。

        Args:
            df: collect() が返した10分値
            daily: jma_rainfall.daily.JMADailyCollector が返した日別値。
                   None のときは日別値の列を出さない（--skip_daily）。
        """
        groups = dict(tuple(df.groupby("日付", sort=True))) if not df.empty else {}
        official = ({r["日付"]: r for r in daily.to_dict("records")}
                    if daily is not None and not daily.empty else {})
        failed = {f["日付"]: f["理由"] for f in self.failures}

        summary = []
        for date in self.dates:
            key = date.isoformat()
            group = groups.get(key)
            row = {
                "地点": self.station["name"],
                "block_no": self.station["block_no"],
                "日付": key,
                "状態": None,
                "備考": failed.get(key),
                "コマ数": 0 if group is None else len(group),
                # 値のないコマ（欠測・疑問値・空白）を数える
                "欠測数": 0 if group is None else int(group["品質"].isin(NO_VALUE).sum()),
                "日合計_コマ_mm": None,
                "最大10分_コマ_mm": None,
                "最大10分_コマ_時刻": None,
            }
            states = []

            if group is None:
                states.append("10分値取得失敗" if key in failed else "10分値なし")
            else:
                values = group["降水量_mm"]
                idx_max = values.idxmax() if values.notna().any() else None
                row["日合計_コマ_mm"] = round(values.fillna(0.0).sum(), 1)
                if idx_max is not None:
                    row["最大10分_コマ_mm"] = values.loc[idx_max]
                    row["最大10分_コマ_時刻"] = group.loc[idx_max, "時分"]
                if row["欠測数"]:
                    states.append("欠測あり")

            if daily is not None:
                record = official.get(key)
                for name in ("日合計_日別値_mm", "最大1時間_日別値_mm",
                             "最大10分_日別値_mm"):
                    row[name] = None if record is None else record[name]
                row["日別値_品質"] = None if record is None else record["日別値_品質"]
                if record is None:
                    states.append("日別値取得失敗")
                # 2経路（10分値の合計と日別値表）の突き合わせを列として常設する
                total, official_total = row["日合計_コマ_mm"], row["日合計_日別値_mm"]
                diff = (None if total is None or official_total is None
                        else round(total - official_total, 1))
                row["日合計差_mm"] = diff
                if diff is not None and abs(diff) > 0.05:
                    states.append("日合計不一致")

            row["状態"] = "/".join(states) if states else "正常"
            summary.append(row)
        return pd.DataFrame(summary)

    @property
    def basename(self):
        # 同名の観測所（「金山」など）でファイルが衝突しないよう block_no を含める
        return (f"{self.station['name']}_{self.station['block_no']}_10min_"
                f"{self.start_date:%Y%m%d}-{self.end_date:%Y%m%d}")

    def output_path(self, fmt="tsv", basename=None):
        """保存先のパスを返す（--resume で既存ファイルを探すのにも使う）。"""
        return self.output_dir / f"{basename or self.basename}.{fmt}"

    def save(self, df, fmt="tsv", basename=None):
        """収集結果をファイルへ保存し、パスを返す。"""
        sep = "\t" if fmt == "tsv" else ","
        path = self.output_path(fmt, basename)
        # Excel で開いたときに文字化けしないよう BOM 付き UTF-8 で保存する
        df.to_csv(path, sep=sep, index=False, encoding="utf-8-sig")
        return path
