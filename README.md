# JMA 降水量データ取得ツール

気象庁「過去の気象データ検索」から降水量のデータを取得する Python パッケージ（`jma_rainfall`）と、コマンド `jma-rainfall` です。
10分値・時間値・日別値・年ごとの値（N時間降水量など）・観測史上順位を、地点名で指定して取得できます。

## 特徴

- 観測所を地点名・カナ・block_no・座標で指定できる（全国1,679地点のマスタを同梱）
- 表の列はヘッダのラベルで決める（位置決め打ちで列を取り出さない）
- 取得の失敗を握りつぶさない。取れなかった日は記録して終了コード1にし、再実行でその分だけ取り直す
- 気象庁サーバに負荷をかけない歩調（`Pacer`: 既定2秒間隔・200回ごとに60秒休止・3回連続失敗で打ち切り）

## インストール

[uv](https://docs.astral.sh/uv/) と [Git](https://git-scm.com/) を使います。

```powershell
git clone https://github.com/nisefuruta/jma-rainfall.git
cd jma-rainfall
uv sync
uv run jma-rainfall --help
```

他のプロジェクトから使うときは、`pyproject.toml` に git の依存として加えます。
そのプロジェクトで `uv sync` すると、パッケージと一緒に `jma-rainfall` コマンドもインストールされ、`uv run jma-rainfall` で使えます。

```toml
[project]
dependencies = ["jma-rainfall"]

[tool.uv.sources]
jma-rainfall = { git = "https://github.com/nisefuruta/jma-rainfall" }
```

## コマンド

`jma-rainfall <サブコマンド> [オプション]` の形で使います。取得するものをサブコマンドで選びます。

| サブコマンド | 内容 | 1リクエストの単位 |
| --- | --- | --- |
| `10min` | 10分値（と日別値の公表値の集計） | 1地点1日 |
| `hourly` | 時間値 | 1地点1日 |
| `annual` | 年ごとの値（`--view a5` で N時間降水量など）、`--rank` で観測史上1〜10位 | 1地点1表示（全年） |
| `stations` | 観測所の一覧（同梱のマスタを表示する。気象庁へはアクセスしない） | なし |

```powershell
uv run jma-rainfall 10min --station 我孫子 成田 --pref 千葉県 --start 2026-08-13 --end 2026-08-14
uv run jma-rainfall 10min --near 35.8320,140.1450 --near_count 3 --start 2026-08-13
uv run jma-rainfall hourly --station 千葉 --pref 千葉県 --start 2013-10-15 --end 2013-10-16
uv run jma-rainfall hourly --station 千葉 --pref 千葉県 --dates-file days.txt
uv run jma-rainfall annual --station 千葉 館山 --pref 千葉県 --view a5
uv run jma-rainfall annual --station 千葉 --pref 千葉県 --rank
uv run jma-rainfall stations --pref 千葉県
uv run jma-rainfall stations --near 35.8320,140.1450
```

各サブコマンドのオプションは `jma-rainfall <サブコマンド> --help` で表示します。
取得するサブコマンドに共通のオプション: `--station`（地点名・カナ・block_no。複数可）/ `--pref`（同名の地点の絞り込み）/
`--master`（観測所マスタのパス。省略時は同梱のもの）/ `--output_dir`（保存先。既定 data）/ `--interval`（アクセス間隔の秒数。既定2.0）/ `--disable_ssl_verify`。
地点の指定を誤ったとき、未来の日付を指定したとき、既存の出力ファイルが読めないか形式が違うときは、取得を始める前にエラーで終わります。
降水量を観測していない地点・廃止された地点を指定すると、注意を表示します。
取得できなかった対象は、その場で理由を表示し、最後に一覧にして終了コード1で終わります。

### 10分値（10min）

その他のオプション: `--near`（座標に近い観測所を対象にする。`--pref` を付けるとその中から探す）/ `--start` / `--end` / `--format tsv|csv` /
`--skip_daily` / `--resume`。

| ファイル | 内容 |
| --- | --- |
| `{地点}_{block_no}_10min_{開始}-{終了}.tsv` | 地点 / block_no / 日付 / 時分 / 区間終了時刻 / 降水量_mm / 品質 |
| `summary_10min_{開始}-{終了}.tsv` | 日ごとの状態、10分値のコマの集計、日別値表の公表値（日合計・最大1時間・最大10分）、日合計差。`--skip_daily` では保存しない（集計は画面に表示する） |

> **その日の最大値は「日別値」列（気象庁の公表値）を使ってください。**
> 「コマ」列は 00:10、00:20 … という固定の区切りから求めた参考値です。日別値表の最大値は1分値を1分ずつずらして
> 求めたもので、区切りに合わない降り方や、連続する1時間が日をまたぐ場合も拾えます。
> `日合計差_mm`（コマ合計 − 日別値の合計）が 0 でなければ、収集か解析を疑ってください。

1日分の取得に失敗しても次の日へ進みます（失敗が3回続いたときは、ほかのサブコマンドと同じく打ち切り、取得済みの分を保存して終わります）。失敗した日は日別集計（画面の表示と `summary_10min_*`）の `状態` 列に `10分値取得失敗` が入り、
終了コードは1になります。`--resume` で、144コマそろっている日を飛ばして取り直せます（コマが欠けている日は取り直します）。
日別値表に行がない日（当日など、まだ公表されていない日）も、取得できなかった対象として表示します。

### 時間値（hourly）

`hourly_<地点>_<block_no>.csv`（地点 / block_no / 日付 / 時(1〜24) / 降水量_mm / 品質）に1日ずつ追記します。既にある日は取り直しません。
地点名が同じ観測所（「金山」など）も、block_no で別のファイルになります。既存のファイルに別の観測所（block_no）の値があるとエラーで止めます。
0.8 までの版で保存したファイル（block_no の列がない）が保存先にあるときも、どの観測所の値か確かめられないのでエラーで止めます。
そのファイルを別のフォルダへ移してから、もう一度実行してください（取得済みの日も取得し直します）。
「時」はその時刻で終わる1時間（24 は 23:00〜24:00）。空欄は品質 `blank` です。

### 年ごとの値・観測史上順位（annual）

`annual_<view>.csv` または `rank.csv` に、全地点を1つの表（先頭の列は地点 / block_no）にして出します。値は公表の文字列のまま（`)` `]` などの記号を含む）。
同じ名前のファイルが保存先にあれば、今回取得した地点の行だけを地点番号（block_no）で置き換え、ほかの地点の行は残します。
取得できなかった地点があっても残りの地点を取得し、取れた分を保存します。取得できなかった地点だけを `--station` に指定して実行し直せば、その地点の行が加わります。

| view | 内容 |
| --- | --- |
| （空） | 年の合計・最大（降水量の合計、日最大、最大1時間、最大10分間 など） |
| `a1` | 詳細（最大24時間降水量とその期間 など。官署） |
| `a5` | N時間降水量（1・3・6・12・24・48・72時間の年最大と時刻、1976年以降。2002年以前は毎正時、2003年以降は10分間隔） |

## 品質の表記

気象庁の「[値欄の記号の説明](https://www.data.jma.go.jp/stats/data/mdrr/man/remark.html)」に合わせて、記号を品質に変換します。

| 品質 | 表示 | 意味 | 降水量_mm |
| --- | --- | --- | --- |
| `normal` | 数値 | 正常値 | 値 |
| `none` | `--` | 該当現象なし | 0.0 |
| `quasi` | 数値 `)` | 準正常値（資料の欠けが許容範囲。正常値と同等に扱う） | 値 |
| `insufficient` | 数値 `]` | 資料不足値（資料の欠けが許容範囲を超える。値そのものは信用できない） | 値 |
| `missing` | `×` `///` | 欠測、観測を行っていない | なし |
| `doubtful` | `#` | 値にかなり疑問があるため表示されていない | なし |
| `blank` | 空白 | 観測を行っていない、通信障害、掲載していない | なし |

これ以外の値は解釈せず、その日（月）を取得できなかった対象にします（欠測として扱って隠すことはしません）。
日別値の `日別値_品質` は、3項目のうち最も弱い品質（表の下の行ほど強い）です。

## プログラムから使う

```python
import datetime
from jma_rainfall import stations as st
from jma_rainfall.hourly import JMAHourlyCollector
from jma_rainfall.daily import JMADailyCollector
from jma_rainfall.annual import fetch_annual, fetch_rank
from jma_rainfall.fetch import Pacer

s = st.find_station("千葉", pref="千葉県")                   # 地点名・カナ・block_no で検索
for dist, near in st.nearest_stations(35.8320, 140.1450):   # 座標から最寄りを検索
    print(st.describe(near, dist))

pacer = Pacer()                                             # 取得の歩調。複数の取得で共有する
hourly = JMAHourlyCollector(s, pacer=pacer).collect([datetime.date(2013, 10, 16)])
daily = JMADailyCollector(s, pacer=pacer).collect(datetime.date(2013, 10, 1), datetime.date(2013, 10, 31))
nhour = fetch_annual(s, view="a5")
rank = fetch_rank(s)
```

表の解析だけを使うときは `jma_rainfall.tables`（`parse_tenmin`・`parse_daily`・`parse_hourly`・`parse_annual`・`parse_rank`）を使います。
取得したページ（BeautifulSoup）を受け取って値を返すだけで、通信はしません。
ライブラリは画面に出力しません。取得の失敗は `logging` の警告で知らせ、`failures` に残します。

## 試験

```powershell
uv run pytest
```

保存した気象庁のページ（`tests/fixtures/`）を使うので、気象庁へはアクセスしません。

## 観測所マスタ

`jma_rainfall/stations.json`（全国1,679地点。地点名・カナ・種別・緯度経度・標高・降水量観測の有無・廃止年月日）。
観測所の新設・廃止を反映したいときだけ `uv run python build_station_master.py --output jma_rainfall/stations.json` を実行します（約60回アクセス）。

## SSL証明書の問題

サーバ証明書は、Windows の証明書ストア（「信頼されたルート証明機関」）で検証します（[truststore](https://pypi.org/project/truststore/) を使用）。
ブラウザと同じ証明書を信頼するので、社内網の UTM が SSL/TLS を検査している環境でも、
UTM の CA 証明書が Windows に登録されていれば、設定なしで検証が通ります。

証明書のエラー（`SSLError`、`CERTIFICATE_VERIFY_FAILED`）が出たときは、次の順に確かめます。

1. UTM の CA 証明書が Windows に登録されているか確かめる。
   社内の管理者から CA 証明書のファイル（例: `UTM_CA.cer`）を受け取っている場合は、その拇印（Thumbprint）で探します。

   ```powershell
   $c = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new("C:\path\to\UTM_CA.cer")
   Get-ChildItem Cert:\CurrentUser\Root, Cert:\LocalMachine\Root | Where-Object Thumbprint -eq $c.Thumbprint
   ```

2. 登録されていなければ、証明書ファイルをダブルクリックして「証明書のインストール」を選び、
   「現在のユーザー」の「信頼されたルート証明機関」にインストールします（証明書が本当に社内の UTM のものか、管理者に確かめてから）。
   ブラウザで社外のサイトを開いても警告が出ない環境なら、通常は登録済みです。

**一時的な対処:** 上の方法で解決するまでの間だけ、各サブコマンドの `--disable_ssl_verify` で証明書の検証を無効にできます。
通信の相手を確かめないため、なりすましや改ざんを検出できません。信頼できるネットワークでだけ使い、常用しないでください。

## 注意事項

- 取得先は気象庁「過去の気象データ検索」の HTML ページです（JSON API は保存期間が短く、過去のデータには使えません）
- 出力は Excel で開いても文字化けしないよう UTF-8（BOM 付き）です
- 長期の取得は時間がかかっても間隔を広めに取ってください。失敗が続いたら打ち切り、時間を置いて再実行します
- 利用者向けの HTML ページから取得するので、アクセスの頻度は利用者の責任です。
  `--interval` や `Pacer` の間隔を既定値より短くしないでください
- ページの構成が変わると取得できなくなることがあります。気象庁とは関係のない個人のツールです
- 10分値〜日別値は、毎日1時ごろに前日分までが更新されます（気象庁「[データの更新時刻](https://www.data.jma.go.jp/stats/data/mdrr/man/update_k.html)」）。
  当日の分や、1時ごろより前の前日の分は、まだ表がないため取得できなかった対象になります
- 気象庁は、データの修正があると次の更新で反映します。ページには確定前かどうかの印がないため、このツールは区別しません。
  直近のデータを使うときは、取得した日を記録し、必要なら後日取り直してください

## 出典と利用規約

取得するデータの出典は気象庁ホームページ「[過去の気象データ検索](https://www.data.jma.go.jp/stats/etrn/index.php)」です。
取得したデータを使うときは、気象庁ホームページの[利用規約](https://www.jma.go.jp/jma/kishou/info/coment.html)に従い、出典を記載してください。
同梱の観測所マスタ（`jma_rainfall/stations.json`）も、同じページの観測所の情報から作成したものです。

## ライセンス

このリポジトリのプログラムは [MIT License](LICENSE) です。取得したデータには、上の気象庁の利用規約が適用されます。
