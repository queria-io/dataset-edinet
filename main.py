"""EDINET 書類一覧 API 取得 + dbt build パイプライン。

書類一覧 API (documents.json) を日付単位で走査し、提出書類のメタデータを
queria の DuckLake カタログ (QUERIA_* 環境変数で注入) の ``_source.documents`` に
書き込んでから dbt で変換する。R2 への公開は queria sync の push が担う。

差分更新:
- 未取得日のみ取得する（進捗を ``_source.fetch_progress`` に日単位で永続化し、
  CI が途中で切れても次回続きから再開する）。
- 直近ウィンドウ (RECENT_REFETCH_DAYS) は毎回再取得し、書類情報修正・取下げを
  取り込む。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import duckdb
import pyarrow as pa
from dbt.cli.main import dbtRunner

from shared.credentials import Secret

from edinet.client import EdinetClient
from edinet.codelist import (
    COMPANY_FIELDS,
    FUND_FIELDS,
    fetch_company_master,
    fetch_fund_master,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

DOCUMENTS_TABLE = "edinet._source.documents"
PROGRESS_TABLE = "edinet._source.fetch_progress"
COMPANIES_TABLE = "edinet._source.companies"
FUNDS_TABLE = "edinet._source.funds"
FINANCIAL_FACTS_TABLE = "edinet._source.financial_facts"
FINANCIAL_PROGRESS_TABLE = "edinet._source.financial_fetch_progress"

# 書類取得 API type=5 CSV の列順（要素ID〜値の固定 9 列）に対応する英語キー。
# _source には全 VARCHAR で保持し、型付け・リネームは stg 層で行う。
FACT_FIELDS = [
    "element_id",  # 要素ID
    "item_name",  # 項目名
    "context_id",  # コンテキストID
    "relative_year",  # 相対年度
    "consolidated_individual",  # 連結・個別
    "period_instant",  # 期間・時点
    "unit_id",  # ユニットID
    "unit",  # 単位
    "value",  # 値
]

# 書類一覧 API results の項目（出力順）。_source には VARCHAR でそのまま保持し、
# 型付け・リネームは stg 層で行う（reinfolib と同じ方針）。
API_FIELDS = [
    "seqNumber",
    "docID",
    "edinetCode",
    "secCode",
    "JCN",
    "filerName",
    "fundCode",
    "ordinanceCode",
    "formCode",
    "docTypeCode",
    "periodStart",
    "periodEnd",
    "submitDateTime",
    "docDescription",
    "issuerEdinetCode",
    "subjectEdinetCode",
    "subsidiaryEdinetCode",
    "currentReportReason",
    "parentDocID",
    "opeDateTime",
    "withdrawalStatus",
    "docInfoEditStatus",
    "disclosureStatus",
    "xbrlFlag",
    "pdfFlag",
    "attachDocFlag",
    "englishDocFlag",
    "csvFlag",
    "legalStatus",
]

_ARROW_SCHEMA = pa.schema(
    [(f, pa.string()) for f in API_FIELDS] + [("_fetch_date", pa.date32())]
)

_COMPANIES_ARROW_SCHEMA = pa.schema(
    [(f, pa.string()) for f in COMPANY_FIELDS] + [("_fetched_at", pa.timestamp("us"))]
)

_FUNDS_ARROW_SCHEMA = pa.schema(
    [(f, pa.string()) for f in FUND_FIELDS] + [("_fetched_at", pa.timestamp("us"))]
)

_FACTS_ARROW_SCHEMA = pa.schema(
    [("doc_id", pa.string())]
    + [(f, pa.string()) for f in FACT_FIELDS]
    + [
        ("_csv_type", pa.string()),
        ("_row_seq", pa.int32()),
        ("_fetched_at", pa.timestamp("us")),
    ]
)

# 閲覧可能期間は書類種別ごとに最長 10 年（縦覧 + 延長）。これより古い日付は
# 原則 0 件で返るため、既定の遡及開始日は約 10 年前に置く。環境変数で上書き可。
DEFAULT_BACKFILL_START = "2016-01-01"
RECENT_REFETCH_DAYS = 7

# 1 回の実行で取得する日数の上限。全期間バックフィル（約 3,800 日）を 1 回で
# 走らせると CI のジョブ上限を超え、push 前に打ち切られて永続化できない。
# 1 回あたりを上限内に収め、毎回 build + push まで完走させる。進捗は
# fetch_progress に永続化され、queria pull で次回取り込まれるため、日次 cron で
# 数日かけて履歴が埋まる。未取得分は新しい日付から順に取得する。
DEFAULT_MAX_DATES_PER_RUN = 1000

# 財務ファクト取り込みの既定。対象書類種別 (docTypeCode) はカンマ区切りで上書き可
# （120=有価証券報告書。140=四半期報告書, 160=半期報告書, 130=訂正有報 等へ拡張可能）。
DEFAULT_FINANCIAL_DOC_TYPES = "120"
# 1 回の実行で財務 CSV を取得する書類数の上限（type=5 は documents より重い）。
DEFAULT_MAX_DOCS_PER_RUN = 1000
# 何書類分をまとめて 1 トランザクションでコミットするか。1 書類ごとの COMMIT を
# 避け DuckLake のスナップショット/Parquet ファイル数を抑える。
FINANCIAL_BATCH_SIZE = 50


@contextmanager
def _ducklake_connect() -> Generator[tuple[duckdb.DuckDBPyConnection, Secret | None]]:
    """queria 管理の DuckLake をアタッチした新規 DuckDB セッションを開く。

    ``queria run`` が注入する ``QUERIA_*`` 環境変数（SQLite ライブカタログ
    ``QUERIA_CATALOG_PATH`` とデータ位置 ``QUERIA_DATA_URL``）を使う。カタログが
    存在しない初回アタッチ時には作成される。
    """
    catalog_path = os.environ["QUERIA_CATALOG_PATH"]
    data_url = os.environ["QUERIA_DATA_URL"]
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL ducklake; LOAD ducklake;")
        conn.execute("INSTALL sqlite; LOAD sqlite;")
        secret: Secret | None = None
        if Secret.needed():
            conn.execute("INSTALL httpfs; LOAD httpfs;")
            # credential_chain はこの拡張にある
            conn.execute("INSTALL aws; LOAD aws;")
            # 認証情報は値として持たず、取り直せる形で持つ。鍵はどこにも置かれない。
            # **作っただけでは足りない** — DuckDB は期限を見て取り直さないので、
            # このビルドの長さだと途中で書けなくなる。取り直しは呼ぶ側の責任で、
            # 書き込みループの切れ目で refresh_if_due() を呼ぶこと
            secret = Secret(conn)
            secret.install()
        conn.execute(
            f"ATTACH 'ducklake:{catalog_path}' AS edinet "
            f"(DATA_PATH '{data_url}', OVERRIDE_DATA_PATH true, "
            f"DATA_INLINING_ROW_LIMIT 0, META_TYPE 'sqlite', "
            f"META_JOURNAL_MODE 'WAL', BUSY_TIMEOUT 5000)"
        )
        yield conn, secret
    finally:
        conn.close()


def main() -> None:
    target = os.environ.get("DBT_TARGET", sys.argv[1] if len(sys.argv) > 1 else "default")

    api_key = os.environ["EDINET_API_KEY"]
    rate = float(os.environ.get("EDINET_RATE_LIMIT_SECONDS", "4"))
    start = date.fromisoformat(
        os.environ.get("EDINET_BACKFILL_START", DEFAULT_BACKFILL_START)
    )
    end = date.today()

    client = EdinetClient(api_key, rate_limit_seconds=rate)
    with _ducklake_connect() as (conn, secret):
        conn.execute("CREATE SCHEMA IF NOT EXISTS edinet._source")
        _ensure_tables(conn)
        targets = _dates_to_fetch(conn, start, end)
        logger.info("fetch %d dates (%s 〜 %s)", len(targets), start, end)
        ingest_documents(conn, client, targets)
        ingest_companies(conn)
        ingest_funds(conn)
        doc_ids = _docs_to_fetch(conn)
        logger.info("fetch financial CSV for %d docs", len(doc_ids))
        ingest_financial_facts(conn, client, doc_ids, secret)

    dbt = dbtRunner()
    for cmd in (
        ["deps"],
        ["seed", "--target", target],
        ["run", "--target", target],
        ["docs", "generate", "--target", target],
    ):
        result = dbt.invoke(cmd)
        if not result.success:
            raise SystemExit(f"dbt {' '.join(cmd)} failed")


def ingest_documents(
    conn: duckdb.DuckDBPyConnection,
    client: EdinetClient,
    targets: list[date],
) -> None:
    """対象日ごとに書類一覧を取得し、日単位で upsert + 進捗永続化する。"""
    total = len(targets)
    for i, d in enumerate(targets, 1):
        results = client.get_documents(d.isoformat())
        _write_date(conn, d, results)
        if i % 50 == 0 or i == total:
            n = conn.execute(f"SELECT count(*) FROM {DOCUMENTS_TABLE}").fetchone()[0]
            logger.info("  %d/%d dates (last=%s, total rows=%d)", i, total, d, n)


def ingest_companies(conn: duckdb.DuckDBPyConnection) -> None:
    """EDINETコードリスト（提出者マスタ）を全件スナップショットで置き換える。"""
    rows = fetch_company_master()
    _replace_snapshot(conn, COMPANIES_TABLE, rows, _COMPANIES_ARROW_SCHEMA)
    logger.info("companies snapshot: %d rows", len(rows))


def ingest_funds(conn: duckdb.DuckDBPyConnection) -> None:
    """ファンドコードリスト（ファンドマスタ）を全件スナップショットで置き換える。"""
    rows = fetch_fund_master()
    _replace_snapshot(conn, FUNDS_TABLE, rows, _FUNDS_ARROW_SCHEMA)
    logger.info("funds snapshot: %d rows", len(rows))


def ingest_financial_facts(
    conn: duckdb.DuckDBPyConnection,
    client: EdinetClient,
    doc_ids: list[str],
    secret: Secret | None = None,
) -> None:
    """対象書類の財務 CSV を取得し、バッチ単位で upsert + 進捗永続化する。

    ``FINANCIAL_BATCH_SIZE`` 書類ごとに 1 トランザクションでまとめて DELETE→INSERT
    する（1 書類ごとの COMMIT を避け DuckLake のスナップショット/ファイル数を抑える）。
    CSV の無い書類は status='empty' を記録し、次回以降の再取得対象から外す。

    **ここが一番長い。** 1000 書類で 1 時間を超え、認証情報より長生きする。
    書類の切れ目 (トランザクションの外) で ``secret`` を取り直す。
    """
    total = len(doc_ids)
    batch_rows: list[dict] = []
    batch_progress: list[tuple] = []
    batch_docs: list[str] = []

    def flush() -> None:
        if not batch_docs:
            return
        ph = ", ".join("?" for _ in batch_docs)
        conn.execute("BEGIN")
        conn.execute(
            f"DELETE FROM {FINANCIAL_FACTS_TABLE} WHERE doc_id IN ({ph})", batch_docs
        )
        conn.execute(
            f"DELETE FROM {FINANCIAL_PROGRESS_TABLE} WHERE doc_id IN ({ph})", batch_docs
        )
        if batch_rows:
            conn.register(
                "_facts", pa.Table.from_pylist(batch_rows, schema=_FACTS_ARROW_SCHEMA)
            )
            conn.execute(f"INSERT INTO {FINANCIAL_FACTS_TABLE} SELECT * FROM _facts")
            conn.unregister("_facts")
        conn.executemany(
            f"INSERT INTO {FINANCIAL_PROGRESS_TABLE} VALUES (?, ?, ?, ?)", batch_progress
        )
        conn.execute("COMMIT")
        batch_rows.clear()
        batch_progress.clear()
        batch_docs.clear()

    for i, doc_id in enumerate(doc_ids, 1):
        # トランザクションの外。間隔が来るまで何もしないので毎回呼んでよい
        if secret is not None:
            secret.refresh_if_due()
        rows = client.get_document_csv(doc_id)
        now = datetime.now()
        for csv_type, row_seq, values in rows:
            padded = (values + [None] * len(FACT_FIELDS))[: len(FACT_FIELDS)]
            rec: dict[str, object] = {"doc_id": doc_id}
            rec.update(dict(zip(FACT_FIELDS, padded)))
            rec["_csv_type"] = csv_type
            rec["_row_seq"] = row_seq
            rec["_fetched_at"] = now
            batch_rows.append(rec)
        batch_progress.append((doc_id, now, len(rows), "done" if rows else "empty"))
        batch_docs.append(doc_id)
        if len(batch_docs) >= FINANCIAL_BATCH_SIZE:
            flush()
            n = conn.execute(
                f"SELECT count(*) FROM {FINANCIAL_FACTS_TABLE}"
            ).fetchone()[0]
            logger.info("  %d/%d docs (total fact rows=%d)", i, total, n)
    flush()


def _docs_to_fetch(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """財務 CSV 未取得の対象書類 docID を提出日時の新しい順に返す（上限あり）。

    対象は ``EDINET_FINANCIAL_DOC_TYPES``（既定 120=有報）かつ csvFlag=1 かつ未取下げ。
    ``financial_fetch_progress`` に登録済みの書類は除外する（空 CSV も status='empty'
    で登録済みなので再取得しない）。1 回の実行を ``EDINET_MAX_DOCS_PER_RUN`` 件に収める。
    """
    doc_types = [
        t.strip()
        for t in os.environ.get(
            "EDINET_FINANCIAL_DOC_TYPES", DEFAULT_FINANCIAL_DOC_TYPES
        ).split(",")
        if t.strip()
    ]
    if not doc_types:
        return []
    cap = int(os.environ.get("EDINET_MAX_DOCS_PER_RUN", str(DEFAULT_MAX_DOCS_PER_RUN)))
    ph = ", ".join("?" for _ in doc_types)
    rows = conn.execute(
        f"""
        SELECT d."docID"
        FROM {DOCUMENTS_TABLE} AS d
        LEFT JOIN {FINANCIAL_PROGRESS_TABLE} AS p ON d."docID" = p.doc_id
        WHERE d."docTypeCode" IN ({ph})
          AND d."csvFlag" = '1'
          AND d."withdrawalStatus" = '0'
          AND p.doc_id IS NULL
        GROUP BY d."docID"
        ORDER BY max(d."submitDateTime") DESC
        LIMIT {cap}
        """,
        doc_types,
    ).fetchall()
    return [r[0] for r in rows]


def _replace_snapshot(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[dict],
    schema: pa.Schema,
) -> None:
    """小さな全件 CSV を 1 トランザクションで全件 DELETE → INSERT する。

    日付単位の差分は持たず、毎回最新スナップショットに置き換える。
    """
    fetched_at = datetime.now()
    for r in rows:
        r["_fetched_at"] = fetched_at
    conn.execute("BEGIN")
    conn.execute(f"DELETE FROM {table}")
    if rows:
        conn.register("_snapshot", pa.Table.from_pylist(rows, schema=schema))
        conn.execute(f"INSERT INTO {table} SELECT * FROM _snapshot")
        conn.unregister("_snapshot")
    conn.execute("COMMIT")


def _ensure_tables(conn: duckdb.DuckDBPyConnection) -> None:
    cols = ", ".join(f'"{f}" VARCHAR' for f in API_FIELDS)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {DOCUMENTS_TABLE} ({cols}, _fetch_date DATE)"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {PROGRESS_TABLE} "
        "(fetch_date DATE, fetched_at TIMESTAMP, doc_count INTEGER)"
    )
    company_cols = ", ".join(f'"{f}" VARCHAR' for f in COMPANY_FIELDS)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {COMPANIES_TABLE} "
        f"({company_cols}, _fetched_at TIMESTAMP)"
    )
    fund_cols = ", ".join(f'"{f}" VARCHAR' for f in FUND_FIELDS)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {FUNDS_TABLE} ({fund_cols}, _fetched_at TIMESTAMP)"
    )
    fact_cols = ", ".join(f'"{f}" VARCHAR' for f in FACT_FIELDS)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {FINANCIAL_FACTS_TABLE} "
        f"(doc_id VARCHAR, {fact_cols}, _csv_type VARCHAR, _row_seq INTEGER, "
        "_fetched_at TIMESTAMP)"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {FINANCIAL_PROGRESS_TABLE} "
        "(doc_id VARCHAR, fetched_at TIMESTAMP, row_count INTEGER, status VARCHAR)"
    )


def _dates_to_fetch(
    conn: duckdb.DuckDBPyConnection, start: date, end: date
) -> list[date]:
    """直近ウィンドウ（毎回再取得）+ 未取得のバックログ（新しい日付優先、上限あり）を昇順で返す。

    1 回の実行を ``EDINET_MAX_DATES_PER_RUN`` 日以内に収め、毎回 build + push まで
    完走させる。直近ウィンドウは書類情報修正・取下げの取り込みのため常に再取得し、
    残り枠で未取得のバックログを新しい日付から埋める。
    """
    done = _fetched_dates(conn)
    cap = int(os.environ.get("EDINET_MAX_DATES_PER_RUN", str(DEFAULT_MAX_DATES_PER_RUN)))
    recent_floor = max(start, end - timedelta(days=RECENT_REFETCH_DAYS - 1))

    recent: list[date] = []
    d = recent_floor
    while d <= end:
        recent.append(d)
        d += timedelta(days=1)

    backlog: list[date] = []  # 新しい日付から順に未取得日を集める
    d = recent_floor - timedelta(days=1)
    while d >= start:
        if d not in done:
            backlog.append(d)
        d -= timedelta(days=1)

    remaining = max(0, cap - len(recent))
    return sorted(set(recent + backlog[:remaining]))


def _fetched_dates(conn: duckdb.DuckDBPyConnection) -> set[date]:
    try:
        return {
            row[0]
            for row in conn.execute(
                f"SELECT fetch_date FROM {PROGRESS_TABLE}"
            ).fetchall()
        }
    except duckdb.CatalogException:
        return set()


def _write_date(
    conn: duckdb.DuckDBPyConnection, d: date, results: list[dict]
) -> None:
    """1 日分の書類一覧を 1 トランザクションで置き換え、進捗を記録する。"""
    rows = [_normalize(r, d) for r in results]
    conn.execute("BEGIN")
    conn.execute(f"DELETE FROM {DOCUMENTS_TABLE} WHERE _fetch_date = ?", [d])
    if rows:
        conn.register("_batch", pa.Table.from_pylist(rows, schema=_ARROW_SCHEMA))
        conn.execute(f"INSERT INTO {DOCUMENTS_TABLE} SELECT * FROM _batch")
        conn.unregister("_batch")
    conn.execute(f"DELETE FROM {PROGRESS_TABLE} WHERE fetch_date = ?", [d])
    conn.execute(
        f"INSERT INTO {PROGRESS_TABLE} VALUES (?, ?, ?)",
        [d, datetime.now(), len(rows)],
    )
    conn.execute("COMMIT")


def _normalize(row: dict, d: date) -> dict:
    """API の 1 件を _source.documents の行 dict に変換する（全項目 VARCHAR）。"""
    out: dict[str, object] = {
        f: (None if row.get(f) is None else str(row.get(f))) for f in API_FIELDS
    }
    out["_fetch_date"] = d
    return out


if __name__ == "__main__":
    main()
