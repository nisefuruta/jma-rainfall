"""
気象庁「過去の気象データ検索」の表の解析（I/O なし）。

取得したページ（BeautifulSoup）を受け取り、値を取り出して返すだけの関数を集める。
通信・待ち時間・保存・表示は扱わない（tenmin / daily / hourly / annual の取得処理と CLI が行う）。
列は表のヘッダのラベルと対応付けて決める（位置決め打ちはしない）。
行と値は検査し、想定と違えば TableError にする（黙って飛ばしたり、欠測扱いにしたりしない）。
"""

import datetime

import pandas as pd

# 品質。気象庁の「値欄の記号の説明」（https://www.data.jma.go.jp/stats/data/mdrr/man/remark.html）による。
# 並びは弱い順で、日別値の3項目の代表（最も弱いもの）を決めるのに使う。
QUALITIES = (
    "missing",       # ×、///: 欠測、観測を行っていない
    "doubtful",      # #: 値にかなり疑問があるため表示していない
    "blank",         # 空白: 観測を行っていない、通信障害、掲載していない
    "insufficient",  # 値 ]: 資料不足値（統計の対象資料が許容範囲を超えて欠けている。値そのものは信用できない）
    "quasi",         # 値 ): 準正常値（統計の対象資料が許容範囲で欠けている。正常値と同等に扱う）
    "none",          # --: 該当現象なし（0.0mm）
    "normal",        # 正常値
)
# 値がない品質（降水量_mm が None になる）
NO_VALUE = frozenset({"missing", "doubtful", "blank"})

SYMBOLS = {"": (None, "blank"), "×": (None, "missing"), "///": (None, "missing"), "#": (None, "doubtful")}
SUFFIXES = {")": "quasi", "]": "insufficient"}

# 降水量3項目の列は、表のヘッダのラベルから決める（位置決め打ちはしない）。
#   アメダス: 降水量 > 合計(mm) / 最大1時間(mm) / 最大10分間(mm)
#   官署    : 降水量(mm) > 合計 / 最大 > 1時間 / 最大 > 10分間
# 種別によって列構成も段数も違うため、ラベルの部分一致で1列に定まることを確認して使う。
PRECIP_GROUP = "降水量"
PRECIP_FIELDS = {
    "日合計_日別値_mm": "合計",
    "最大1時間_日別値_mm": "1時間",
    "最大10分_日別値_mm": "10分",
}

# 10分値表の時分の行（00:10, 00:20, …, 24:00。区間の終了時刻）
TENMIN_LABELS = [f"{m // 60:02d}:{m % 60:02d}" for m in range(10, 24 * 60 + 1, 10)]


class TableError(Exception):
    """表の構造や値が想定と違うことを表す。"""


def parse_value(text):
    """
    セルの文字列を (数値, 品質) に変換する。品質は QUALITIES のいずれか。

    末尾の ) ] は品質にして値から除く（「-- ]」のように現象なしに付くこともある）。
    解釈できない文字列は欠測扱いにせず TableError にする（表の読み違いや記号の追加を隠さない）。
    """
    s = text.replace("\xa0", " ").strip()
    if s in SYMBOLS:
        return SYMBOLS[s]
    quality = "normal"
    if s[-1] in SUFFIXES:
        quality = SUFFIXES[s[-1]]
        s = s[:-1].strip()
    if s == "--":
        return 0.0, "none" if quality == "normal" else quality
    try:
        return float(s), quality
    except ValueError:
        raise TableError(f"表の値 {text.strip()!r} を解釈できません（このプログラムが対応していない記号の可能性があります。"
                         "値の取り違えを防ぐため、欠測扱いにはしません）") from None


def weakest(qualities):
    """品質のうち最も弱いもの（QUALITIES の並びで先のもの）を返す。"""
    return min(qualities, key=QUALITIES.index)


def _table(soup, name):
    """ページのデータの表（id=tablefix1）を返す。"""
    table = soup.find("table", id="tablefix1")
    if table is None:
        raise TableError(f"{name}の表が見つかりません（観測所の観測期間外の日か、まだ公表されていない日か、"
                         "ページの構成が変わった可能性があります）。")
    return table


def _precip_column(table, name):
    """見出しが「降水量」で始まる列の位置を返す（10分値・時間値）。1列に定まらなければ TableError。"""
    paths = header_paths(table)
    hits = [i for i, path in enumerate(paths) if path.startswith(PRECIP_GROUP)]
    if not hits:
        raise TableError(f"{name}の表に降水量の列がありません（降水量を観測していない地点か、ページの構成が変わった可能性があります）。"
                         f"表の見出し: {'、'.join(paths)}")
    if len(hits) > 1:
        raise TableError(f"{name}の表に降水量の列が {len(hits)} 列あり、1列に決められません（ページの構成が変わった可能性があります）。"
                         f"表の見出し: {'、'.join(paths)}")
    return hits[0]


def _data_rows(table):
    """データの行（td だけの行）のセルのリストを返す。"""
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if cells and all(cell.name == "td" for cell in cells):
            rows.append(cells)
    return rows


def _cell(cells, col, name, label):
    """行の col 列目の文字列を返す。列が足りなければ TableError。"""
    if col >= len(cells):
        raise TableError(f"{name}の表の「{label}」の行の列数（{len(cells)}）が見出しと合いません（ページの構成が変わった可能性があります）。")
    return cells[col].get_text()


def parse_tenmin(soup, date):
    """1日分の10分値表から、10分ごとの降水量を取り出す（00:10〜24:00 の144行がそろうことを確かめる）。"""
    table = _table(soup, "10分値")
    col = _precip_column(table, "10分値")
    rows = []
    for cells in _data_rows(table):
        label = cells[0].get_text().strip()
        value, quality = parse_value(_cell(cells, col, "10分値", label))
        rows.append({"日付": date.isoformat(), "時分": label, "降水量_mm": value, "品質": quality})
    if [r["時分"] for r in rows] != TENMIN_LABELS:
        raise TableError(f"10分値の表の時分の行が 00:10〜24:00 の144行になっていません（{len(rows)} 行）。")
    start = datetime.datetime.combine(date, datetime.time())
    for i, r in enumerate(rows, 1):
        # 「24:00」は当日 23:50〜24:00 の値。区間の終了時刻として持つ
        r["区間終了時刻"] = (start + datetime.timedelta(minutes=10 * i)).strftime("%Y-%m-%d %H:%M")
    return rows


def header_paths(table):
    """
    多段ヘッダ（rowspan / colspan）を展開し、列ごとのラベルの連なりを返す。

    例: 官署の降水量列は '降水量(mm)/最大/1時間' になる。
    """
    header_rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        if any(cell.name != "th" for cell in cells):
            break  # th だけの行がヘッダ。最初のデータ行で打ち切る
        header_rows.append(cells)
    if not header_rows:
        raise TableError("表の見出しの行が見つかりません（ページの構成が変わった可能性があります）。")

    grid = {}
    for r, cells in enumerate(header_rows):
        c = 0
        for cell in cells:
            while (r, c) in grid:
                c += 1
            rowspan = int(cell.get("rowspan", 1))
            colspan = int(cell.get("colspan", 1))
            label = cell.get_text().strip()
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r + dr, c + dc)] = label
            c += colspan

    ncols = max((col for _, col in grid), default=-1) + 1
    paths = []
    for col in range(ncols):
        labels = []
        for r in range(len(header_rows)):
            label = grid.get((r, col))
            if label and (not labels or labels[-1] != label):
                labels.append(label)
        paths.append("/".join(labels))
    return paths


def precip_columns(paths):
    """降水量3項目の列位置を返す。1列に定まらなければ例外にする。"""
    columns = {}
    for name, keyword in PRECIP_FIELDS.items():
        hits = [i for i, path in enumerate(paths)
                if PRECIP_GROUP in path and keyword in path]
        if len(hits) != 1:
            raise TableError(
                f"日別値の表の見出しから、{PRECIP_GROUP}の「{keyword}」の列を1列に決められません（該当 {len(hits)} 列。"
                "降水量を観測していない地点か、ページの構成が変わった可能性があります）。"
                f"表の見出し: {'、'.join(paths)}")
        columns[name] = hits[0]
    return columns


def parse_daily(soup, year, month):
    """1か月分の日別値表から、日ごとの降水量3項目（公表値）を取り出す。

    日の行は1日から連続していることを確かめる（当月は月末より前で終わることがある）。
    """
    table = _table(soup, "日別値")
    columns = precip_columns(header_paths(table))
    records = []
    for cells in _data_rows(table):
        label = cells[0].get_text().strip()
        if not label.isdigit():
            raise TableError(f"日別値の表に日ではない行があります: {label!r}")
        try:
            record = {"日付": datetime.date(year, month, int(label)).isoformat()}
        except ValueError:
            raise TableError(f"日別値の表に {year}年{month}月にない日の行があります: {label}日") from None
        qualities = []
        for name, col in columns.items():
            record[name], quality = parse_value(_cell(cells, col, "日別値", f"{label}日"))
            qualities.append(quality)
        # 3項目のうち最も弱い品質を代表として持つ（記号は値から除いているため）
        record["日別値_品質"] = weakest(qualities)
        records.append(record)
    days = [int(r["日付"][-2:]) for r in records]
    if not days or days != list(range(1, len(days) + 1)):
        raise TableError(f"日別値の表の日の行が1日から連続していません: {days}")
    return records


def parse_hourly(soup, date):
    """1日分の時間値表から24時間分の降水量を取り出す（1〜24時の24行がそろうことを確かめる）。"""
    table = _table(soup, "時間値")
    col = _precip_column(table, "時間値")
    recs = []
    for cells in _data_rows(table):
        label = cells[0].get_text(strip=True)
        value, quality = parse_value(_cell(cells, col, "時間値", f"{label}時"))
        recs.append({"日付": date.isoformat(), "時": label, "降水量_mm": value, "品質": quality})
    if [r["時"] for r in recs] != [str(h) for h in range(1, 25)]:
        raise TableError(f"時間値の表の時の行が1〜24時になっていません（{len(recs)} 行）。")
    for r in recs:
        r["時"] = int(r["時"])
    return recs


def parse_annual(soup):
    """年ごとの値の表を DataFrame にする。列名はヘッダのラベルのつながり（先頭は「年」）。値は公表の文字列のまま。"""
    table = _table(soup, "年ごとの値")
    paths = header_paths(table)
    rows = []
    for cells in _data_rows(table):
        texts = [c.get_text(strip=True) for c in cells]
        if len(texts) != len(paths):
            raise TableError(f"年ごとの値の表の「{texts[0]}」の行の列数（{len(texts)}）が見出し（{len(paths)}）と合いません。")
        rows.append(texts)
    if not rows:
        raise TableError("年ごとの値の表に年の行がありません。")
    return pd.DataFrame(rows, columns=paths)


def parse_rank(soup):
    """観測史上1〜10位の値の表を、要素ごとの行（要素, 1位〜10位, 統計期間）にする。列名は見出しのラベル。値は公表の文字列のまま。"""
    table = _table(soup, "観測史上順位")
    paths = ["要素"] + header_paths(table)[1:]   # 先頭の見出しは「要素名／順位」
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells or all(c.name == "th" for c in cells):
            continue   # 見出しの行
        texts = [c.get_text(" ", strip=True) for c in cells]
        if len(texts) != len(paths):
            raise TableError(f"観測史上順位の表の「{texts[0]}」の行の列数（{len(texts)}）が見出し（{len(paths)}）と合いません。")
        rows.append(texts)
    if not rows:
        raise TableError("観測史上順位の表に要素の行がありません。")
    return pd.DataFrame(rows, columns=paths)
