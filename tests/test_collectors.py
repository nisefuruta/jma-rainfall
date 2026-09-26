"""取得処理（コレクタ）と歩調（Pacer）の試験。通信は保存したページを返す偽のセッションに置き換え、気象庁へはアクセスしない。"""

import datetime
from pathlib import Path

import pytest
import requests

from jma_rainfall import stations as st
from jma_rainfall.daily import JMADailyCollector
from jma_rainfall.fetch import FetchAborted, Pacer
from jma_rainfall.hourly import JMAHourlyCollector
from jma_rainfall.tenmin import JMATenMinCollector

FIXTURES = Path(__file__).parent / "fixtures"
DAY = datetime.date(2013, 10, 16)


class FakeResponse:
    def __init__(self, text):
        self.text, self.encoding = text, None

    def raise_for_status(self):
        pass


class FakeSession:
    """URL のページ名と日付から保存したページを返す。pages にないものは HTTP 404 にする。"""

    def __init__(self, pages):
        self.pages = pages

    def get(self, url, params, verify, timeout):
        name = self.pages.get((url.rsplit("/", 1)[1], params["day"]))
        if name is None:
            res = requests.Response()
            res.status_code = 404
            raise requests.HTTPError(response=res)
        return FakeResponse((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def chiba():
    return st.find_station("千葉", pref="千葉県")


def no_wait(**kw):
    return Pacer(sleep=lambda s: None, **kw)


def test_tenmin_and_daily_summary(chiba, tmp_path):
    session = FakeSession({("10min_s1.php", 16): "10min_47682_20131016.html",
                           ("daily_s1.php", ""): "daily_47682_201310.html"})
    pacer = no_wait()
    col = JMATenMinCollector(chiba, DAY, DAY, output_dir=tmp_path, pacer=pacer, session=session)
    df = col.collect()
    daily = JMADailyCollector(chiba, pacer=pacer, session=session).collect(DAY, DAY)
    row = col.summarize(df, daily=daily).iloc[0]
    assert (row["状態"], row["日合計_コマ_mm"], row["日合計_日別値_mm"], row["日合計差_mm"]) == ("正常", 238.0, 238.0, 0.0)
    assert set(df["地点"]) == {"千葉"} and set(df["block_no"]) == {"47682"}
    assert pacer.count == 2   # 10分値と日別値で1つの Pacer を共有する


def test_failures_are_recorded_and_aborted(chiba, tmp_path, caplog):
    days = [DAY + datetime.timedelta(days=i) for i in range(1, 6)]   # 保存していない日は HTTP 404
    col = JMATenMinCollector(chiba, days[0], days[-1], output_dir=tmp_path, pacer=no_wait(), session=FakeSession({}))
    with pytest.raises(FetchAborted):
        col.collect()
    assert [f["日付"] for f in col.failures] == [d.isoformat() for d in days[:3]]
    assert "取得できませんでした" in caplog.text   # 表示はせず、ログで知らせる


def test_hourly_on_day(chiba):
    saved = []
    col = JMAHourlyCollector(chiba, pacer=no_wait(), session=FakeSession({("hourly_s1.php", 16): "hourly_47682_20131016.html"}))
    df = col.collect([DAY], on_day=lambda d, recs: saved.append((d, len(recs))))
    assert saved == [(DAY, 24)] and round(df["降水量_mm"].sum(), 1) == 238.0 and not col.failures
    assert list(df.columns[:2]) == ["地点", "block_no"] and set(df["block_no"]) == {"47682"}


def test_annual_and_rank_have_block_no(chiba):
    # 地点名は一意でないので、全地点を1つの表にする年ごとの値・順位にも観測所の番号を入れる
    from jma_rainfall.annual import fetch_annual, fetch_rank
    session = FakeSession({("annually_s.php", ""): "annually_a5_47682.html", ("rank_s.php", ""): "rank_47682.html"})
    for df in (fetch_annual(chiba, "a5", session), fetch_rank(chiba, session)):
        assert list(df.columns[:2]) == ["地点", "block_no"] and set(df["block_no"]) == {"47682"}


def test_pacer_rest_and_abort():
    slept = []
    p = Pacer(interval=2.0, rest_every=3, rest_sec=60, sleep=slept.append)
    for failed in (False, True, False, True, True):
        p.done(failed)
    assert slept == [2.0, 2.0, 2.0, 60, 2.0, 2.0]
    with pytest.raises(FetchAborted):
        p.done(True)


def test_daily_reports_days_missing_from_table(chiba, tmp_path, monkeypatch, caplog):
    # 表に行がない日（当月のまだ公表されていない日など）を黙って落とさず、failures に残す
    from jma_rainfall import daily as daily_module
    real = daily_module.parse_daily
    monkeypatch.setattr(daily_module, "parse_daily", lambda soup, y, m: real(soup, y, m)[:10])
    col = JMADailyCollector(chiba, pacer=no_wait(), session=FakeSession({("daily_s1.php", ""): "daily_47682_201310.html"}))
    df = col.collect(datetime.date(2013, 10, 5), datetime.date(2013, 10, 16))
    assert list(df["日付"]) == [f"2013-10-{d:02d}" for d in range(5, 11)]
    assert len(col.failures) == 1 and "2013-10-11〜2013-10-16 の 6 日" in col.failures[0]["理由"]
    assert "取得できませんでした" in caplog.text


def test_unknown_station_kind_raises(chiba):
    with pytest.raises(ValueError, match="種別"):
        JMAHourlyCollector({**chiba, "kind": "x"}, pacer=no_wait(), session=FakeSession({})).collect([DAY])


def test_summary_counts_all_no_value_slots(chiba, tmp_path):
    # 欠測（×）だけでなく、疑問値（#）・空白のコマも欠測数に数え、状態を「欠測あり」にする
    col = JMATenMinCollector(chiba, DAY, DAY, output_dir=tmp_path, pacer=no_wait(),
                             session=FakeSession({("10min_s1.php", 16): "10min_47682_20131016.html"}))
    df = col.collect()
    df.loc[0, ["降水量_mm", "品質"]] = [None, "doubtful"]
    df.loc[1, ["降水量_mm", "品質"]] = [None, "blank"]
    df.loc[2, ["降水量_mm", "品質"]] = [None, "missing"]
    row = col.summarize(df).iloc[0]
    assert (row["欠測数"], row["状態"]) == (3, "欠測あり")
