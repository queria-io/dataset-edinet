"""EDINET API v2 書類一覧 API クライアント。

書類一覧 API (documents.json) を日付指定で叩き、提出書類のメタデータ一覧を返す。
レート制限（リクエスト間 3〜5 秒）の遵守と、一時的なエラーへのリトライを担う。

API 仕様: https://disclosure2.edinet-fsa.go.jp/ （EDINET API 仕様書 Version 2）
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

logger = logging.getLogger(__name__)

DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOCUMENT_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# 書類取得 API type=5 の CSV は UTF-16 (BOM 付き)・タブ区切り。BOM 自動判定のため
# 'utf-16' を使う（'utf-16-le' だと先頭セルに ﻿ が残る）。
CSV_ENCODING = "utf-16"

# 有報のテキストブロック（事業の内容・リスク情報等）は数十KB級のセルがあり、
# csv モジュールの既定フィールド上限を超えて落ちるため引き上げる。
csv.field_size_limit(sys.maxsize)


def _csv_type_from_name(name: str) -> str:
    """ZIP 内 CSV ファイル名から様式コードを導出する。

    例: ``XBRL_TO_CSV/jpcrp030000-asr-001_E03398-000_...csv`` → ``jpcrp030000-asr-001``。
    有報本体 (jpcrp030000-asr)・監査報告書 (jpaud-)・投信 (jpsps/jpfnd) 等を区別できる。
    """
    base = name.rsplit("/", 1)[-1]
    return base.split("_", 1)[0]


class EdinetClient:
    """書類一覧 API の薄いクライアント。"""

    def __init__(
        self,
        api_key: str,
        *,
        rate_limit_seconds: float = 4.0,
        max_retries: int = 5,
        timeout: int = 60,
    ) -> None:
        self._api_key = api_key
        self._rate_limit_seconds = rate_limit_seconds
        self._max_retries = max_retries
        self._timeout = timeout
        self._last_request = 0.0

    def get_documents(self, target_date: str) -> list[dict]:
        """指定日 (YYYY-MM-DD) の提出書類一覧を返す。

        type=2 はメタデータと提出書類一覧の両方を取得する。提出が無い日
        （土日・祝日等）は status 200 で空の results が返る。
        """
        params = urllib.parse.urlencode(
            {"date": target_date, "type": "2", "Subscription-Key": self._api_key}
        )
        body = self._request(f"{DOCUMENTS_URL}?{params}", target_date)
        metadata = body.get("metadata") or {}
        status = str(metadata.get("status") or "")
        if status not in ("200", ""):
            logger.warning(
                "date=%s status=%s message=%s",
                target_date,
                status,
                metadata.get("message"),
            )
        return body.get("results") or []

    def get_document_csv(self, doc_id: str) -> list[tuple[str, int, list[str | None]]]:
        """書類取得 API (type=5) で財務 CSV を取得し ``(csv_type, row_seq, values)`` を返す。

        ZIP 内 ``XBRL_TO_CSV/*.csv`` を全て解析する。CSV が無い書類（取下げ等の 404、
        または ZIP でない応答）は空リストを返す。各 CSV は UTF-16・タブ区切りで 1 行目が
        ヘッダー（列順は EDINET 共通で固定なので捨てる）。``values`` は列順の生の値
        リストで、英語キーへの対応づけは呼び出し側 (main.FACT_FIELDS) が行う。
        """
        params = urllib.parse.urlencode(
            {"type": "5", "Subscription-Key": self._api_key}
        )
        url = f"{DOCUMENT_URL.format(doc_id=doc_id)}?{params}"
        raw = self._request_bytes(url, doc_id)
        if raw is None or raw[:2] != b"PK":  # None=404, PK=ZIP マジック
            return []
        out: list[tuple[str, int, list[str | None]]] = []
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [
                n
                for n in zf.namelist()
                if n.startswith("XBRL_TO_CSV/") and n.lower().endswith(".csv")
            ]
            for name in sorted(names):
                csv_type = _csv_type_from_name(name)
                text = zf.read(name).decode(CSV_ENCODING)
                reader = csv.reader(io.StringIO(text), delimiter="\t")
                next(reader, None)  # ヘッダー行を捨てる
                for seq, cols in enumerate(reader):
                    if not cols:
                        continue
                    out.append((csv_type, seq, [c or None for c in cols]))
        return out

    def _request_bytes(self, url: str, doc_id: str) -> bytes | None:
        """type=5 のバイナリ (ZIP) を取得する。404 (CSVなし/取下げ) は None を返す。"""
        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "queria-dataset-edinet/1.0"}
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    logger.info("doc=%s no CSV (404)", doc_id)
                    return None
                if e.code in RETRYABLE_STATUS and attempt < self._max_retries:
                    wait = self._rate_limit_seconds * 2**attempt
                    logger.warning(
                        "doc=%s HTTP %d, retry %d/%d after %.1fs",
                        doc_id,
                        e.code,
                        attempt,
                        self._max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise
            # 応答の読み取り中のタイムアウトや切断は URLError に包まれずに上がってくる
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < self._max_retries:
                    wait = self._rate_limit_seconds * 2**attempt
                    logger.warning(
                        "doc=%s %r, retry %d/%d after %.1fs",
                        doc_id,
                        e,
                        attempt,
                        self._max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError(f"failed to fetch EDINET CSV for {doc_id}")

    def _request(self, url: str, target_date: str) -> dict:
        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "queria-dataset-edinet/1.0"}
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code in RETRYABLE_STATUS and attempt < self._max_retries:
                    wait = self._rate_limit_seconds * 2**attempt
                    logger.warning(
                        "date=%s HTTP %d, retry %d/%d after %.1fs",
                        target_date,
                        e.code,
                        attempt,
                        self._max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise
            # 応答の読み取り中のタイムアウトや切断は URLError に包まれずに上がってくる
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < self._max_retries:
                    wait = self._rate_limit_seconds * 2**attempt
                    logger.warning(
                        "date=%s %r, retry %d/%d after %.1fs",
                        target_date,
                        e,
                        attempt,
                        self._max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError(f"failed to fetch EDINET documents for {target_date}")

    def _throttle(self) -> None:
        """前回リクエストからの経過がレート制限未満なら待機する。"""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._rate_limit_seconds:
            time.sleep(self._rate_limit_seconds - elapsed)
        self._last_request = time.monotonic()
