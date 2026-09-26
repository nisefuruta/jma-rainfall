"""表の解析（jma_rainfall.tables）の試験。保存した気象庁のページ（tests/fixtures）を使い、気象庁へはアクセスしない。

千葉（47682、官署）と我孫子（0376、アメダス）の 2013-10-16（台風第26号）。
同じ日の降水量を10分値・日別値・時間値の3つの表から取り出し、合計が一致することも確かめる。
"""

import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from jma_rainfall.tables import (TableError, parse_annual, parse_daily, parse_hourly, parse_rank,
                                 parse_tenmin, parse_value)

FIXTURES = Path(__file__).parent / "fixtures"
DAY = datetime.date(2013, 10, 16)


def soup(name):
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "html.parser")


@pytest.mark.parametrize("text, expected", [   # 気象庁の「値欄の記号の説明」による
    ("12.5", (12.5, "normal")),
    ("0.0", (0.0, "normal")),
    ("--", (0.0, "none")),
    ("///", (None, "missing")),
    ("×", (None, "missing")),
    ("", (None, "blank")),
    (" ", (None, "blank")),
    ("#", (None, "doubtful")),
    ("3.0 )", (3.0, "quasi")),
    ("3.0]", (3.0, "insufficient")),
    ("-- ]", (0.0, "insufficient")),
])
def test_parse_value(text, expected):
    assert parse_value(text) == expected


@pytest.mark.parametrize("text", ["12.5?", "abc", "1.0 *"])
def test_parse_value_unknown_raises(text):
    # 解釈できない値を欠測扱いにして隠さない
    with pytest.raises(TableError, match="解釈できません"):
        parse_value(text)


# block_no, 種別（a: アメダス / s: 官署）, 日合計, 最大1時間, 最大10分（日別値表の公表値）
STATIONS = [("47682", "s", 238.0, 61.5, 14.5), ("0376", "a", 196.5, 39.0, 9.5)]


@pytest.mark.parametrize("block_no, kind, total, max1h, max10m", STATIONS)
def test_three_tables_agree(block_no, kind, total, max1h, max10m):
    tenmin = parse_tenmin(soup(f"10min_{block_no}_20131016.html"), DAY)
    assert len(tenmin) == 144
    assert tenmin[0]["時分"] == "00:10" and tenmin[-1]["区間終了時刻"] == "2013-10-17 00:00"
    assert round(sum(r["降水量_mm"] for r in tenmin), 1) == total

    daily = {r["日付"]: r for r in parse_daily(soup(f"daily_{block_no}_201310.html"), 2013, 10)}
    assert len(daily) == 31
    assert daily["2013-10-16"] == {"日付": "2013-10-16", "日合計_日別値_mm": total, "最大1時間_日別値_mm": max1h,
                                   "最大10分_日別値_mm": max10m, "日別値_品質": "normal"}

    hourly = parse_hourly(soup(f"hourly_{block_no}_20131016.html"), DAY)
    assert [r["時"] for r in hourly] == list(range(1, 25))
    assert round(sum(r["降水量_mm"] for r in hourly), 1) == total
    # 公表値の最大10分間は1分ずつずらして求めた値なので、固定の区切りのコマの最大以上になる
    assert max(r["降水量_mm"] for r in tenmin) <= max10m


def test_tenmin_max_slot_chiba():
    tenmin = parse_tenmin(soup("10min_47682_20131016.html"), DAY)
    top = max(tenmin, key=lambda r: r["降水量_mm"])
    assert (top["降水量_mm"], top["時分"]) == (13.0, "05:10")


def test_parse_annual_a5():
    df = parse_annual(soup("annually_a5_47682.html"))
    assert df.columns[0] == "年"
    row = df[df["年"] == "2013"].iloc[0]
    assert row["N時間降水量/最大1時間降水量（10分間隔）/降水量(mm)"] == "61.0"
    assert row["N時間降水量/最大1時間降水量（10分間隔）/月/日 時:分"] == "10/16 05:40"
    assert row["N時間降水量/最大3時間降水量/降水量(mm)"] == "131.0"


def test_parse_rank():
    df = parse_rank(soup("rank_47682.html"))
    row = df[df["要素"] == "日降水量 (mm)"].iloc[0]
    assert row["1位"] == "351.0 (2026/8/13)"
    assert list(df.columns) == ["要素"] + [f"{i}位" for i in range(1, 11)] + ["統計期間"]
    assert len(df) == 37 and "利用される方へ" not in set(df["要素"])   # ページの案内の表を混ぜない


def test_wrong_page_raises_table_error():
    # 10分値のページを時間値・日別値として解析すると、表の構造が違うことを知らせる
    with pytest.raises(TableError):
        parse_hourly(soup("10min_47682_20131016.html"), DAY)
    with pytest.raises(TableError):
        parse_daily(soup("hourly_47682_20131016.html"), 2013, 10)


def broken(name, change):
    """保存したページを読み、change で壊したものを返す。"""
    page = soup(name)
    change(page.find("table", id="tablefix1"))
    return page


def drop_row(label):
    def change(table):
        next(tr for tr in table.find_all("tr") if tr.find("td") and tr.find("td").get_text(strip=True) == label).decompose()
    return change


def set_cell(label, text):
    def change(table):
        tr = next(tr for tr in table.find_all("tr") if tr.find("td") and tr.find("td").get_text(strip=True) == label)
        tr.find_all("td")[3].string = text
    return change


def short_row(label):
    def change(table):
        tr = next(tr for tr in table.find_all("tr") if tr.find("td") and tr.find("td").get_text(strip=True) == label)
        for td in tr.find_all("td")[2:]:
            td.decompose()
    return change


@pytest.mark.parametrize("parse, name, change, message", [
    (lambda p: parse_tenmin(p, DAY), "10min_47682_20131016.html", drop_row("12:00"), "144行"),
    (lambda p: parse_tenmin(p, DAY), "10min_47682_20131016.html", short_row("12:00"), "列数"),
    (lambda p: parse_tenmin(p, DAY), "10min_47682_20131016.html", set_cell("12:00", "1.0?"), "解釈できません"),
    (lambda p: parse_hourly(p, DAY), "hourly_47682_20131016.html", drop_row("5"), "1〜24時"),
    (lambda p: parse_hourly(p, DAY), "hourly_47682_20131016.html", short_row("5"), "列数"),
    (lambda p: parse_daily(p, 2013, 10), "daily_47682_201310.html", drop_row("15"), "連続していません"),
    (lambda p: parse_daily(p, 2013, 10), "daily_47682_201310.html", short_row("15"), "列数"),
    (parse_annual, "annually_a5_47682.html", short_row("2013"), "列数"),
    (lambda p: parse_tenmin(p, DAY), "10min_47682_20131016.html", lambda t: t.attrs.pop("id"), "表が見つかりません"),
])
def test_broken_tables_raise(parse, name, change, message):
    # 行が欠けた・列が足りない・値が読めない表を、黙って飛ばしたり欠測にしたりしない
    with pytest.raises(TableError, match=message):
        parse(broken(name, change))


def test_daily_partial_month():
    # 当月のように月末より前で終わる表は、そろっている日の分だけ返す（欠けた日は取得処理が知らせる）
    def keep_first_ten(table):
        for tr in table.find_all("tr"):
            td = tr.find("td")
            if td and td.get_text(strip=True).isdigit() and int(td.get_text(strip=True)) > 10:
                tr.decompose()
    records = parse_daily(broken("daily_47682_201310.html", keep_first_ten), 2013, 10)
    assert [r["日付"] for r in records] == [f"2013-10-{d:02d}" for d in range(1, 11)]
