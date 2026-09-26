#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
気象庁ページ取得の共通処理

10分値（tenmin）と日別値（daily）で同じ取得方法・同じ再試行方針を使うためのモジュール。
長期間の収集が1回の通信エラーで丸ごと失われないよう、一時的な失敗だけを再試行する。
"""

import logging
import ssl
import time

import requests
import truststore
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

RETRIES = 3
BACKOFF = 1.0
# 気象庁サーバへのアクセス間隔の既定値（秒）。CLI の --interval の既定もこれを使う
DEFAULT_INTERVAL = 2.0
# 「過去の気象データ検索」の表示ページ。page の {kind} には観測所の種別（a: アメダス / s: 官署）が入る
ETRN_URL = "https://www.data.jma.go.jp/stats/etrn/view/{page}.php"


class _SystemTrustAdapter(HTTPAdapter):
    """サーバ証明書を OS の証明書ストア（Windows の「信頼されたルート証明機関」など）で検証するアダプタ。

    verify=True の要求だけに適用する。verify=False（--disable_ssl_verify）や CA ファイルの指定は
    requests の既定の処理に任せる（truststore の文脈は検証なしに切り替えられない）。
    build_connection_pool_key_attributes は requests 2.32.3 以降にある（それより前では呼ばれず certifi に戻る）。
    """

    def __init__(self, *args, **kwargs):
        self._context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        super().__init__(*args, **kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        if verify is True:
            pool_kwargs["ssl_context"] = self._context
        return host_params, pool_kwargs


def new_session():
    """気象庁へのアクセスに使うセッションを作る。

    requests は既定では certifi 同梱の CA だけを信頼するため、社内網の UTM が SSL 検査で
    差し替えた証明書を検証できない。OS の証明書ストアを使えば、ブラウザと同じく
    UTM の CA 証明書（社内で配布・登録済みのもの）で検証が通る。
    プロセス全体の ssl を書き換える truststore.inject_into_ssl() は、このパッケージを使う側にも
    影響するので使わず、このセッションだけに適用する。
    """
    session = requests.Session()
    session.mount("https://", _SystemTrustAdapter())
    return session


class FetchError(Exception):
    """再試行しても目的のページを取得できなかったことを表す。"""


class FetchAborted(Exception):
    """失敗が続いたので取得を打ち切ったことを表す。"""


class Pacer:
    """長期の取得で気象庁サーバに負荷をかけない（ブロックされない）ための歩調。

    1回ごとに interval 秒あけ、rest_every 回ごとに rest_sec 秒休む。
    失敗が max_consecutive_fail 回続いたら FetchAborted で打ち切る（再試行で叩き続けない）。
    1つのスレッドから順に呼ぶ前提で、並行取得には対応しない（並行化するとアクセスの頻度が上がり、この歩調の意味がなくなる）。
    """

    def __init__(self, interval=DEFAULT_INTERVAL, rest_every=200, rest_sec=60, max_consecutive_fail=3, sleep=time.sleep):
        self.interval = interval
        self.rest_every = rest_every
        self.rest_sec = rest_sec
        self.max_consecutive_fail = max_consecutive_fail
        self.count = 0
        self.fail_run = 0
        self._sleep = sleep

    def done(self, failed=False, n=1):
        """n 回ぶんの取得が終わったことを知らせる。間隔・休止をとり、連続失敗を数える。"""
        self._sleep(self.interval)
        before = self.count
        self.count += n
        self.fail_run = self.fail_run + 1 if failed else 0
        if self.fail_run >= self.max_consecutive_fail:
            raise FetchAborted(f"気象庁のページの取得に{self.fail_run}回続けて失敗したため、"
                               "サーバに負荷をかけないよう取得を中止しました。\n"
                               "  失敗の理由は、この前に表示した「取得できませんでした」の行を見てください。"
                               "通信やサーバの一時的な問題なら、時間をおいてからもう一度実行してください。")
        if self.rest_every and self.count // self.rest_every > before // self.rest_every:
            log.info("気象庁サーバへの負荷を減らすため、%d回取得したところで%d秒休止します。", self.count, self.rest_sec)
            self._sleep(self.rest_sec)


def _describe_request_error(e):
    """通信の失敗を日本語の短い説明にする（requests の英語の文は FetchError の「詳細」に残す）。"""
    if isinstance(e, requests.exceptions.Timeout):
        return "気象庁サーバから応答がありません（タイムアウト）"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "気象庁サーバに接続できません（ネットワークの接続やプロキシの設定を確かめてください）"
    if e.response is not None:
        return f"気象庁サーバがエラーを返しました（HTTP {e.response.status_code}）"
    return "通信に失敗しました"


# 4xx のうち、利用者が次の操作を決められるもの
_CLIENT_ERRORS = {
    403: "気象庁サーバにアクセスを拒否されました（HTTP 403）。アクセスが多すぎて一時的に制限された可能性があります。"
         "時間をおいてから、--interval を大きくして実行してください",
    404: "気象庁のページが見つかりません（HTTP 404）。ページのアドレスが変わった可能性があります",
    429: "気象庁サーバへのアクセスが多すぎると応答されました（HTTP 429）。"
         "時間をおいてから、--interval を大きくして実行してください",
}


def fetch_text(session, url, params, verify_ssl=True, timeout=30, retries=RETRIES):
    """
    ページを取得して文字列で返す。

    タイムアウト・接続エラー・サーバエラー(5xx)は間隔を空けて再試行する（再試行したことはログに出す）。
    4xx と証明書の検証の失敗は、再試行しても結果が変わらないため、その場で FetchError にする。
    """
    last_error = None
    for attempt in range(retries):
        try:
            res = session.get(url, params=params, verify=verify_ssl, timeout=timeout)
            res.raise_for_status()
        except requests.exceptions.SSLError as e:
            raise FetchError("気象庁サーバの証明書を検証できません。社内網で SSL 検査を行っている場合は、"
                             f"README の「SSL証明書の問題」の手順で対処してください（詳細: {e}）") from e
        except requests.RequestException as e:
            status = e.response.status_code if e.response is not None else None
            if status is not None and 400 <= status < 500:
                raise FetchError(_CLIENT_ERRORS.get(status, f"気象庁サーバがエラーを返しました（HTTP {status}）")) from e
            last_error = e
            if attempt < retries - 1:
                wait = BACKOFF * 2 ** attempt
                log.info("%s。%.0f秒後に再試行します（%d回目の失敗。最大%d回試します）",
                         _describe_request_error(e), wait, attempt + 1, retries)
                time.sleep(wait)
        else:
            res.encoding = "utf-8"   # 気象庁のページは UTF-8（ヘッダの文字コードの指定に頼らない）
            return res.text
    raise FetchError(f"{retries}回試しましたが取得できませんでした。{_describe_request_error(last_error)}"
                     f"（詳細: {last_error}）") from last_error


def fetch_station_page(session, station, page, year="", month="", day="", view="", verify_ssl=True):
    """観測所の表示ページ（page は "10min_{kind}1"、"annually_{kind}" など）を取得して BeautifulSoup で返す。"""
    if station["kind"] not in ("a", "s"):
        raise ValueError(f"観測所マスタの「{station['name']}」（地点番号 {station['block_no']}）の種別 {station['kind']!r} が、"
                         "a（アメダス）・s（官署）のどちらでもありません。"
                         "観測所マスタ（stations.json）が壊れているか、形式の違うファイルを使っている可能性があります")
    url = ETRN_URL.format(page=page.format(kind=station["kind"]))
    params = {"prec_no": station["prec_no"], "block_no": station["block_no"],
              "year": year, "month": month, "day": day, "view": view}
    return BeautifulSoup(fetch_text(session, url, params, verify_ssl=verify_ssl), "html.parser")


def record_failure(failures, station, target, reason, **extra):
    """取得できなかった対象を failures に加え、ログに警告を出す（握りつぶさず、終了コードと表に反映させる）。

    表示はライブラリを使う側が logging の設定で決める。設定がなくても警告は標準エラーに出る。
    """
    log.warning("%s（地点番号 %s） %s: 取得できませんでした（%s）", station["name"], station["block_no"], target, reason)
    failures.append({"地点": station["name"], "block_no": station["block_no"],
                     "対象": target, "理由": reason, **extra})
