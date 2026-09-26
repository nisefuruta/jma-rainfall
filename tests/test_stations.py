"""観測所マスタの検索の試験。"""

import pytest

from jma_rainfall import stations as st


def test_non_exact_match_is_reported(caplog):
    assert st.find_station("千", pref="千葉県")["block_no"] == "47682"
    assert "完全には一致しない" in caplog.text


def test_exact_match_is_quiet(caplog):
    st.find_station("千葉", pref="千葉県")
    assert caplog.text == ""


def test_ambiguous_name_raises():
    with pytest.raises(st.AmbiguousStation):
        st.find_station("金山")


def test_same_station_in_two_prefectures_is_one_station():
    # 富士山は山梨県と静岡県の両方に載っている。block_no でも地点名でも1件に決まる
    assert st.find_station("47639")["block_no"] == "47639"
    assert st.find_station("富士山")["block_no"] == "47639"


def test_limitations():
    closed = next(s for s in st.load_stations() if not s["active"])
    assert f"廃止 {closed['end_date']}" in st.limitations(closed)
