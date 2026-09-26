"""CLI の入力の解釈の試験（気象庁へはアクセスしない）。"""

import datetime

import pytest

from jma_rainfall import stations as st
from jma_rainfall.cli import main
from jma_rainfall.cli._common import UsageError, read_dates_file


def test_read_dates_file(tmp_path):
    path = tmp_path / "days.txt"
    path.write_text("2013-10-16\n2013/10/17 20131018\n\n", encoding="utf-8-sig")   # BOM 付き、1行に複数
    assert read_dates_file(path) == [datetime.date(2013, 10, d) for d in (16, 17, 18)]


def test_read_dates_file_removes_duplicates(tmp_path):
    path = tmp_path / "days.txt"
    path.write_text("2013-10-16\n2013-10-15\n20131016\n", encoding="utf-8")
    assert read_dates_file(path) == [datetime.date(2013, 10, 16), datetime.date(2013, 10, 15)]


def test_near_without_stations_is_error(capsys):
    with pytest.raises(SystemExit) as e:
        main(["10min", "--near", "35.83,140.14", "--pref", "存在しない県", "--start", "2013-10-16"])
    assert e.value.code == 2 and "条件に合う観測所がありません" in capsys.readouterr().err


def test_same_station_twice_is_fetched_once():
    from argparse import Namespace

    from jma_rainfall.cli._common import find_stations
    targets = find_stations(Namespace(station=["千葉", "47682"], pref="千葉県"), st.load_stations())
    assert [s["block_no"] for s in targets] == ["47682"]


def test_read_dates_file_errors(tmp_path):
    path = tmp_path / "days.txt"
    path.write_text("2013-10-16\n2013-13-01\n", encoding="utf-8")
    with pytest.raises(UsageError, match="2 行目"):
        read_dates_file(path)
    with pytest.raises(UsageError, match="読めません"):
        read_dates_file(tmp_path / "none.txt")


def test_unknown_station_exits_before_fetching(capsys):
    assert main(["hourly", "--station", "存在しない地点", "--start", "2013-10-16"]) == 1
    assert "見つかりません" in capsys.readouterr().err


@pytest.mark.parametrize("argv, message", [
    (["hourly", "--station", "千葉", "--start", "2099-01-01"], "未来の日付"),
    (["hourly", "--station", "千葉", "--start", "2013-10-16", "--end", "2013-10-15"], "--end は --start 以降"),
    (["10min", "--station", "千葉", "--start", "2099-01-01"], "未来の日付"),
    (["annual", "--station", "千葉", "--rank", "--view", "a1"], "一緒に指定できません"),
])
def test_usage_errors_before_fetching(argv, message, capsys):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2 and message in capsys.readouterr().err


def test_dates_file_with_start_is_error(tmp_path, capsys):
    path = tmp_path / "days.txt"
    path.write_text("2013-10-16\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["hourly", "--station", "千葉", "--dates-file", str(path), "--start", "2013-10-16"])
    assert "一緒に指定できません" in capsys.readouterr().err


def test_existing_file_with_other_columns_is_not_appended(tmp_path, capsys):
    # 形式の違うファイルに追記して壊さない（取得を始める前に止める）
    path = tmp_path / "hourly_千葉_47682.csv"
    path.write_text("date,hour,rainfall_mm\n2013-10-15,1,0.0\n", encoding="utf-8-sig")
    assert main(["hourly", "--station", "千葉", "--pref", "千葉県", "--start", "2013-10-16", "--output_dir", str(tmp_path)]) == 1
    assert "列がこのコマンドの出力と違います" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8-sig") == "date,hour,rainfall_mm\n2013-10-15,1,0.0\n"


class FakeHourlyCollector:
    """1日ごとに1行だけ返す時間値の取得（気象庁へはアクセスしない）。取得した (block_no, 日付) を fetched に残す。"""
    fetched = []

    def __init__(self, station, verify_ssl=True, pacer=None, session=None):
        self.station, self.failures = station, []

    def collect(self, dates, on_day=None):
        for d in dates:
            FakeHourlyCollector.fetched.append((self.station["block_no"], d.isoformat()))
            on_day(d, [{"地点": self.station["name"], "block_no": self.station["block_no"], "日付": d.isoformat(), "時": 1,
                        "降水量_mm": 0.0, "品質": "normal"}])


@pytest.fixture
def fake_hourly(monkeypatch):
    from jma_rainfall.cli import hourly
    monkeypatch.setattr(hourly, "JMAHourlyCollector", FakeHourlyCollector)
    monkeypatch.setattr(hourly, "new_session", lambda: None)
    FakeHourlyCollector.fetched = []
    return FakeHourlyCollector.fetched


def test_hourly_same_name_stations_are_saved_separately(tmp_path, fake_hourly):
    # 同名の別の観測所（福島県と岐阜県の金山）は、一緒に指定しても別々の実行でも、それぞれ取得して別のファイルに保存する
    base = ["hourly", "--start", "2013-10-16", "--output_dir", str(tmp_path)]
    assert main([*base, "--station", "金山", "--pref", "福島県"]) == 0
    assert main([*base, "--station", "金山", "--pref", "岐阜県"]) == 0
    assert main([*base, "--station", "1044", "0487", "--end", "2013-10-17"]) == 0
    assert fake_hourly == [("1044", "2013-10-16"), ("0487", "2013-10-16"), ("1044", "2013-10-17"), ("0487", "2013-10-17")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["hourly_金山_0487.csv", "hourly_金山_1044.csv"]


def test_hourly_legacy_file_is_error(tmp_path, capsys, fake_hourly):
    # 0.7 までの名前のファイルは、どの観測所の値か確かめられないので、無視して取り直さずに止める
    old = tmp_path / "hourly_千葉.csv"
    old.write_text("地点,日付,時,降水量_mm,品質\n千葉,2013-10-16,1,0.0,normal\n", encoding="utf-8-sig")
    assert main(["hourly", "--station", "千葉", "--pref", "千葉県", "--start", "2013-10-16", "--output_dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "以前の版（jma-rainfall 0.8 まで）で保存した「千葉」の時間値" in err and "別のフォルダへ移して" in err
    assert fake_hourly == [] and sorted(p.name for p in tmp_path.iterdir()) == ["hourly_千葉.csv"]


def test_hourly_file_of_0_8_is_error(tmp_path, capsys, fake_hourly):
    # 0.8 の時間値のファイル（block_no の列がない）に追記して列をずらさない
    text = "地点,日付,時,降水量_mm,品質\n千葉,2013-10-15,1,0.0,normal\n"
    path = tmp_path / "hourly_千葉_47682.csv"
    path.write_text(text, encoding="utf-8-sig")
    assert main(["hourly", "--station", "千葉", "--pref", "千葉県", "--start", "2013-10-16", "--output_dir", str(tmp_path)]) == 1
    assert "以前の版（jma-rainfall 0.8 まで）で保存した「千葉」の時間値" in capsys.readouterr().err
    assert fake_hourly == [] and path.read_text(encoding="utf-8-sig") == text


@pytest.mark.parametrize("command, name, text", [
    ("hourly", "hourly_千葉_47682.csv", "地点,block_no,日付,時,降水量_mm,品質\n千葉,0376,2013-10-15,1,0.0,normal\n"),
    ("10min", "千葉_47682_10min_20131016-20131016.tsv",
     "地点\tblock_no\t日付\t時分\t区間終了時刻\t降水量_mm\t品質\n千葉\t0376\t2013-10-16\t00:10\t2013-10-16 00:10\t0.0\tnormal\n"),
])
def test_existing_file_of_other_station_is_not_appended(tmp_path, capsys, fake_hourly, command, name, text):
    # 既存のファイルに別の観測所（block_no）の値があれば、追記も上書きもしない（10分値は --resume のとき）
    path = tmp_path / name
    path.write_text(text, encoding="utf-8-sig")
    argv = [command, "--station", "千葉", "--pref", "千葉県", "--start", "2013-10-16", "--output_dir", str(tmp_path)]
    assert main([*argv, "--resume"] if command == "10min" else argv) == 1
    err = capsys.readouterr().err
    assert "別の観測所（地点番号 0376）の値が入っています" in err and "（千葉県、地点番号 47682）" in err
    assert fake_hourly == [] and path.read_text(encoding="utf-8-sig") == text


def test_station_master_is_not_written_when_a_prefecture_has_no_stations(tmp_path, monkeypatch):
    import build_station_master as bsm

    index = '<area alt="千葉県" href="prefecture.php?prec_no=45"><area alt="南極" href="prefecture.php?prec_no=99">'
    chiba = ("viewPoint('a','0376','我孫子','アビコ','35','51.9','140','7.8','5',"
             "'1','1','1','1','0','0','9999','99','99','','','','','')")
    pages = {"": index, "45": chiba, "99": "<html>エラー</html>"}
    monkeypatch.setattr(bsm, "fetch_text", lambda session, url, params, verify_ssl=True: pages[params["prec_no"]])
    out = tmp_path / "stations.json"
    out.write_text("既存", encoding="utf-8")
    with pytest.raises(bsm.BuildError, match="南極（都府県・地方の番号 prec_no=99）のページから観測所を1つも読み取れませんでした"):
        bsm.build(out, interval=0)
    assert out.read_text(encoding="utf-8") == "既存"


def test_master_errors(tmp_path, capsys):
    assert main(["stations", "--master", str(tmp_path / "none.json")]) == 1
    assert "--master のパスを確かめてください" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert main(["stations", "--master", str(bad)]) == 1
    assert "形式が正しくありません" in capsys.readouterr().err


def test_interrupt_is_reported(monkeypatch, capsys):
    from jma_rainfall.cli import hourly

    def interrupted(args):
        raise KeyboardInterrupt
    monkeypatch.setattr(hourly, "run", interrupted)
    assert main(["hourly", "--station", "千葉", "--start", "2013-10-16"]) == 130
    assert "中断しました" in capsys.readouterr().err


def test_station_master_changes_are_reported(tmp_path, capsys):
    import json

    from build_station_master import report_changes   # リポジトリ直下の保守用スクリプト（pyproject の pythonpath）

    base = {"prec_no": "45", "pref": "千葉県", "kana": "", "kind": "a", "lat": 35.0, "lon": 140.0,
            "elevation": "0", "has_precip": True, "note": ""}
    old = [dict(base, block_no="1", name="甲", active=True, end_date=None),
           dict(base, block_no="2", name="乙", active=True, end_date=None)]
    new = [dict(base, block_no="1", name="甲", active=False, end_date="2026-04-01"),
           dict(base, block_no="3", name="丙", active=True, end_date=None)]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps({"stations": old}, ensure_ascii=False), encoding="utf-8")
    report_changes(path, new)
    out = capsys.readouterr().out
    assert "追加地点: 1（丙(3)）" in out and "なくなった地点: 1（乙(2)）" in out and "廃止になった地点: 1（甲(1)）" in out


def test_annual_replaces_rows_of_fetched_stations_only(tmp_path, monkeypatch, capsys):
    # 年ごとの値は、取得した地点の行だけを地点番号で置き換え、ほかの地点の行は残す（失敗した地点だけを取り直しても消えない）
    import pandas as pd
    from jma_rainfall.cli import annual
    from jma_rainfall.fetch import FetchError

    def fake_annual(station, view, session, verify):
        if station["block_no"] == "47672":
            raise FetchError("気象庁サーバがエラーを返しました（HTTP 500）")
        return pd.DataFrame({"地点": [station["name"]], "block_no": [station["block_no"]], "年": [2020], "値": ["新"]})

    monkeypatch.setattr(annual, "fetch_annual", fake_annual)
    monkeypatch.setattr(annual, "new_session", lambda: None)
    path = tmp_path / "annual_a5.csv"
    path.write_text("地点,block_no,年,値\n千葉,47682,2020,旧\n館山,47672,2020,旧\n銚子,47648,2020,旧\n", encoding="utf-8-sig")
    argv = ["annual", "--station", "千葉", "館山", "--pref", "千葉県", "--output_dir", str(tmp_path), "--interval", "0"]
    assert main(argv) == 1                               # 館山は失敗
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"block_no": str})
    assert dict(zip(df["block_no"], df["値"])) == {"47682": "新", "47672": "旧", "47648": "旧"}
    err = capsys.readouterr().err
    assert "取得できなかった地点だけを --station に指定して" in err


def test_annual_legacy_file_is_error_before_fetch(tmp_path, monkeypatch, capsys):
    # 地点番号の列がない以前の版のファイルは、行を置き換えられないので取得の前に止める
    from jma_rainfall.cli import annual

    def no_fetch(*a, **kw):
        raise AssertionError("取得を始めてはいけない")

    monkeypatch.setattr(annual, "fetch_annual", no_fetch)
    path = tmp_path / "annual_a5.csv"
    path.write_text("地点,年,値\n千葉,2020,旧\n", encoding="utf-8-sig")
    assert main(["annual", "--station", "千葉", "--pref", "千葉県", "--output_dir", str(tmp_path)]) == 1
    assert "以前の版（jma-rainfall 0.8 まで）で保存したファイル" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8-sig") == "地点,年,値\n千葉,2020,旧\n"


def test_master_with_broken_station_is_error(tmp_path, capsys):
    # 項目の欠けた観測所や種別の誤りは、取得の途中ではなく、マスタを読み込むときに分かるエラーにする
    import json
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"stations": [{"name": "甲", "block_no": "1", "prec_no": "45", "pref": "千葉県", "kind": "x"}]},
                              ensure_ascii=False), encoding="utf-8")
    assert main(["stations", "--master", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "観測所マスタの形式が正しくありません" in err and "Traceback" not in err


def test_dates_file_in_utf16_is_error(tmp_path):
    # メモ帳や Excel で「Unicode テキスト」（UTF-16）として保存した日付のファイルは、トレースバックでなく原因と直し方を示す
    path = tmp_path / "days.txt"
    path.write_bytes("2013-10-16\r\n".encode("utf-16"))
    with pytest.raises(UsageError, match="文字コードが UTF-8 ではありません(.|\n)*UTF-8 にして保存し直して"):
        read_dates_file(path)


def test_dates_file_in_shift_jis_with_dates_only_is_read(tmp_path):
    # 日付だけのファイルは半角の文字だけなので、Shift-JIS で保存しても UTF-8 と同じバイトで、そのまま読める
    path = tmp_path / "days.txt"
    path.write_bytes("2013-10-16\r\n2013-10-17\r\n".encode("cp932"))
    assert read_dates_file(path) == [datetime.date(2013, 10, 16), datetime.date(2013, 10, 17)]


def test_log_warnings_have_prefix():
    # ライブラリの警告（取得の失敗など）には「注意:」を付け、経過の知らせには付けない
    import logging
    from jma_rainfall.cli import LevelFormatter
    f = LevelFormatter()
    make = lambda level: logging.LogRecord("x", level, "", 0, "取得できませんでした", None, None)
    assert f.format(make(logging.WARNING)) == "注意: 取得できませんでした"
    assert f.format(make(logging.INFO)) == "取得できませんでした"
