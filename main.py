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

import json
import logging
import os
import sys
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import duckdb
import pyarrow as pa
from dbt.cli.main import dbtRunner

from queria import Secret

from edinet import affiliates, taxonomy, xbrl
from edinet.xbrl_facts import parse_instance, parse_labels
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
XBRL_FACTS_TABLE = "edinet._source.xbrl_facts"
XBRL_CONTEXTS_TABLE = "edinet._source.xbrl_contexts"
XBRL_LABELS_TABLE = "edinet._source.xbrl_labels"
XBRL_NAMESPACES_TABLE = "edinet._source.xbrl_namespaces"
TAXONOMY_LABELS_TABLE = "edinet._source.taxonomy_labels"
XBRL_PROGRESS_TABLE = "edinet._source.xbrl_fetch_progress"
AFFILIATES_TABLE = "edinet._source.affiliated_entities"

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

_FINANCIAL_PROGRESS_ARROW_SCHEMA = pa.schema(
    [
        ("doc_id", pa.string()),
        ("fetched_at", pa.timestamp("us")),
        ("row_count", pa.int32()),
        ("status", pa.string()),
    ]
)

_XBRL_FACTS_ARROW_SCHEMA = pa.schema(
    [
        ("doc_id", pa.string()),
        ("seq", pa.int32()),
        ("element_id", pa.string()),
        ("context_id", pa.string()),
        ("unit_id", pa.string()),
        ("decimals", pa.string()),
        ("is_nil", pa.bool_()),
        ("value", pa.string()),
        ("_fetched_at", pa.timestamp("us")),
    ]
)

_XBRL_CONTEXTS_ARROW_SCHEMA = pa.schema(
    [("doc_id", pa.string()), ("context_id", pa.string())]
    + [(f, pa.string()) for f in ("period_start", "period_end", "instant", "members")]
)

_LABEL_FIELDS = [("element_id", pa.string()), ("role", pa.string()), ("lang", pa.string()), ("label", pa.string())]
_XBRL_LABELS_ARROW_SCHEMA = pa.schema([("doc_id", pa.string()), *_LABEL_FIELDS])
_TAXONOMY_LABELS_ARROW_SCHEMA = pa.schema(
    [("release", pa.string()), ("namespace", pa.string()), ("file", pa.string()), *_LABEL_FIELDS]
)
_XBRL_NAMESPACES_ARROW_SCHEMA = pa.schema(
    [("doc_id", pa.string()), ("prefix", pa.string()), ("uri", pa.string())]
)

_AFFILIATES_ARROW_SCHEMA = pa.schema(
    [("doc_id", pa.string()), ("context_id", pa.string()), ("row_seq", pa.int32())]
    + [(f, pa.string()) for f in affiliates.FIELDS if f != "row_seq"]
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

# 財務 CSV (type=5) の取り込みの対象書類種別。既定では取らない（財務の値は type=1 の XBRL から取る）。
# 既に取った CSV は、XBRL を取り直すまでの書類だけ stg_financial_facts で使う。
# カンマ区切りの docTypeCode（120=有価証券報告書、160=半期報告書 など）を渡すと取る。
DEFAULT_FINANCIAL_DOC_TYPES = ""
# 1 回の実行で財務 CSV を取得する書類数の上限（type=5 は documents より重い）。
DEFAULT_MAX_DOCS_PER_RUN = 1000
# XBRL 本体 (type=1) を取る書類種別と 1 回の上限。ZIP は 1 件 0.6〜3MB で、4 秒間隔で
# 1000 件 = 約 1.5 時間。過去分（約 10 万件）は日次の実行で新しい順に埋まる
DEFAULT_XBRL_DOC_TYPES = "120,160"
DEFAULT_MAX_XBRL_DOCS_PER_RUN = 1000
# 何書類分をまとめて 1 トランザクションでコミットするか。1 書類ごとの COMMIT を
# 避け DuckLake のスナップショット/Parquet ファイル数を抑える。
FINANCIAL_BATCH_SIZE = 50
# 進捗表を読むときに 1 文で読むデータファイルの本数。1 文が認証情報の期限
# （最短 15 分）に収まる大きさにする。
PROGRESS_READ_BATCH_FILES = 500
# 関係会社を作り直すときに 1 文で読む書類の数
REPARSE_BATCH_DOCS = 5000


@contextmanager
def _ducklake_connect() -> Generator[tuple[duckdb.DuckDBPyConnection, Secret | None]]:
    """queria 管理の DuckLake をアタッチした新規 DuckDB セッションを開く。

    ``queria run`` が注入する ``QUERIA_*`` 環境変数（SQLite ライブカタログ
    ``QUERIA_CATALOG_PATH`` とデータ位置 ``QUERIA_DATA_URL``）を使う。カタログが
    存在しない初回アタッチ時には作成される。
    """
    catalog_path = os.environ["QUERIA_CATALOG_PATH"]
    data_url = os.environ["QUERIA_DATA_URL"]
    # ビルドが公開ストレージを読むときの名乗り。queria run が queria-build として
    # 渡す。profiles.yml の config_options と同じ値を、こちらのセッションにも通す。
    # このデータセットは取り込みを dbt ではなくこの接続で行うので、ここが抜けると
    # 読み書きの大半が匿名のまま出ていき、data.queria.io の利用統計に自分のビルドが
    # 利用者として混ざる。接続を開くときにしか決められない設定なので config= で渡す
    conn = duckdb.connect(
        ":memory:",
        config={"custom_user_agent": os.environ.get("QUERIA_USER_AGENT", "")},
    )
    try:
        conn.execute("INSTALL ducklake; LOAD ducklake;")
        conn.execute("INSTALL sqlite; LOAD sqlite;")
        secret: Secret | None = None
        if Secret.needed():
            conn.execute("INSTALL httpfs; LOAD httpfs;")
            # credential_chain はこの拡張にある
            conn.execute("INSTALL aws; LOAD aws;")
            # profiles.yml と同じ。既定の 30 秒だと、大きなテーブルの読み込みが途中で切れる
            conn.execute("SET http_timeout = 300; SET http_retries = 5;")
            # 認証情報は値として持たず、取り直せる形で持つ。鍵はどこにも置かれない。
            # **作っただけでは足りない** — DuckDB は期限を見て取り直さないので、
            # このビルドの長さだと途中で読み書きできなくなる。取り直しは呼ぶ側の
            # 責任で、書き込みループの切れ目では refresh_if_due()、1 時間近くかかる
            # 単発のクエリの前では refresh() を呼ぶこと
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


STEPS = ("ingest", "build")


def steps(argv: list[str]) -> list[str]:
    """``python main.py [ingest] [build]``。省くと両方を順に流す。

    取り込みとビルドを別々に公開できるようにする。ビルドやテストが落ちても、
    取り込みだけを先に公開しておけば、同じ書類を取り直さずに済む。
    """
    unknown = [a for a in argv if a not in STEPS]
    if unknown:
        raise SystemExit(f"unknown step: {' '.join(unknown)} (choose from {', '.join(STEPS)})")
    return [s for s in STEPS if s in argv] if argv else list(STEPS)


def main() -> None:
    target = os.environ.get("DBT_TARGET", "default")
    chosen = steps(sys.argv[1:])
    if "ingest" in chosen:
        ingest()
    # 取り込みだけのときは dbt を流さない。push は前回のビルドの target/manifest.json
    # （compiled_code 入り）を使うので、コードを変えたら取り込みの前にビルドを流すこと
    if "build" in chosen:
        # ビルドだけを流すときも、新しく公表されたタクソノミは先に入れる。
        # 入れないと xbrl_namespaces_have_taxonomy が落ち続け、取り込みの側で入れるまで直らない
        if "ingest" not in chosen:
            refresh_taxonomy()
        run_dbt(DBT_BUILD, target)


def refresh_taxonomy() -> None:
    with _ducklake_connect() as (conn, secret):
        conn.execute("CREATE SCHEMA IF NOT EXISTS edinet._source")
        _ensure_xbrl_tables(conn)
        ingest_taxonomy(conn, secret)


def ingest() -> None:
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
        _ensure_xbrl_tables(conn)
        targets = _dates_to_fetch(conn, start, end)
        logger.info("fetch %d dates (%s 〜 %s)", len(targets), start, end)
        ingest_documents(conn, client, targets)
        ingest_companies(conn)
        ingest_funds(conn)
        doc_ids = _docs_to_fetch(conn, secret)
        logger.info("fetch financial CSV for %d docs", len(doc_ids))
        ingest_financial_facts(conn, client, doc_ids, secret)
        xbrl_doc_ids = _xbrl_docs_to_fetch(conn, secret)
        logger.info("fetch XBRL for %d docs", len(xbrl_doc_ids))
        ingest_xbrl(conn, client, xbrl_doc_ids, secret)
        ingest_taxonomy(conn, secret)
        if os.environ.get("EDINET_REPARSE_AFFILIATES") == "1":
            reparse_affiliates(conn, secret)


DBT_BUILD: tuple[tuple[str, ...], ...] = (
    ("seed",),
    ("run",),
    # 取り込みの抜け（タクソノミの新しい公表・相対年度の命名・項目名）を見つけるテスト。
    # 落ちたら公開しない
    (
        "test",
        "--select",
        "xbrl_namespaces_have_taxonomy",
        "stg_financial_facts_labels_present",
        "stg_financial_facts_relative_year_known",
    ),
    ("docs", "generate"),
)


def run_dbt(commands: tuple[tuple[str, ...], ...], target: str) -> None:
    """``dbt deps`` のあとに、``commands`` を ``--target`` 付きで順に流す。"""
    dbt = dbtRunner()
    for args in (["deps"], *([*cmd, "--target", target] for cmd in commands)):
        if not dbt.invoke(args).success:
            raise SystemExit(f"dbt {' '.join(args)} failed")


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
    batch_progress: list[dict] = []
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
        # 1 行ずつの INSERT は行ごとに parquet ファイルを作る（インライン化は
        # ATTACH で無効）。進捗表は _docs_to_fetch が毎回全件読むので、
        # ファイル数がそのまま R2 への GET 回数になる。facts と同じく 1 回で入れる。
        conn.register(
            "_progress",
            pa.Table.from_pylist(
                batch_progress, schema=_FINANCIAL_PROGRESS_ARROW_SCHEMA
            ),
        )
        conn.execute(f"INSERT INTO {FINANCIAL_PROGRESS_TABLE} SELECT * FROM _progress")
        conn.unregister("_progress")
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
        batch_progress.append(
            {
                "doc_id": doc_id,
                "fetched_at": now,
                "row_count": len(rows),
                "status": "done" if rows else "empty",
            }
        )
        batch_docs.append(doc_id)
        if len(batch_docs) >= FINANCIAL_BATCH_SIZE:
            flush()
            n = conn.execute(
                f"SELECT count(*) FROM {FINANCIAL_FACTS_TABLE}"
            ).fetchone()[0]
            logger.info("  %d/%d docs (total fact rows=%d)", i, total, n)
    flush()


AFFILIATES_ELEMENT = "jpcrp_cor:OverviewOfAffiliatedEntitiesTextBlock"


def _affiliate_rows(doc_id: str, facts: list[dict]) -> list[dict]:
    """関係会社の状況のテキストブロックを 1 社 1 行に分ける。"""
    return [
        {"doc_id": doc_id, "context_id": f["context_id"], **row}
        for f in facts
        if f["element_id"] == AFFILIATES_ELEMENT and f["value"]
        for row in affiliates.parse_affiliated_entities(f["value"])
    ]


def _read_xbrl(doc_id: str, raw: bytes | None, now: datetime) -> dict[str, list[dict]]:
    """type=1 の ZIP を、値・context・会社のラベル・関係会社の行に分ける。"""
    instance = xbrl.instance_from_zip(raw) if raw else None
    if instance is None:
        return {"facts": [], "contexts": [], "labels": [], "namespaces": [], "affiliates": []}
    namespaces, contexts, facts = parse_instance(instance)
    fact_rows = [
        {
            "doc_id": doc_id,
            "seq": seq,
            "element_id": f.element_id,
            "context_id": f.context_id,
            "unit_id": f.unit_id,
            "decimals": f.decimals,
            "is_nil": f.is_nil,
            # テキストブロックの HTML は表の構造に要らない属性を外して持つ
            "value": xbrl.clean_html(f.value) if f.value and f.element_id.endswith("TextBlock") else f.value,
            "_fetched_at": now,
        }
        for seq, f in enumerate(facts)
    ]
    return {
        "facts": fact_rows,
        "contexts": [
            {
                "doc_id": doc_id,
                "context_id": cid,
                "period_start": c.start,
                "period_end": c.end,
                "instant": c.instant,
                "members": json.dumps([list(m) for m in c.members]) if c.members else None,
            }
            for cid, c in contexts.items()
        ],
        "labels": [
            {"doc_id": doc_id, "element_id": e, "role": r, "lang": lang, "label": text}
            for lab in xbrl.label_files_from_zip(raw)
            for e, r, lang, text in parse_labels(lab)
        ],
        "namespaces": [{"doc_id": doc_id, "prefix": p, "uri": u} for p, u in namespaces.items()],
        "affiliates": _affiliate_rows(doc_id, fact_rows),
    }


_XBRL_TABLES = (
    ("facts", XBRL_FACTS_TABLE, _XBRL_FACTS_ARROW_SCHEMA),
    ("contexts", XBRL_CONTEXTS_TABLE, _XBRL_CONTEXTS_ARROW_SCHEMA),
    ("labels", XBRL_LABELS_TABLE, _XBRL_LABELS_ARROW_SCHEMA),
    ("namespaces", XBRL_NAMESPACES_TABLE, _XBRL_NAMESPACES_ARROW_SCHEMA),
    ("affiliates", AFFILIATES_TABLE, _AFFILIATES_ARROW_SCHEMA),
    ("progress", XBRL_PROGRESS_TABLE, _FINANCIAL_PROGRESS_ARROW_SCHEMA),
)


def fetch_deadline(until: str | None, start: datetime) -> datetime | None:
    """``HH:MM`` を、``start`` の後で最初に来るその時刻にする。

    夜間だけ取得するとき（20 時に始めて 5 時に取得をやめる）に使う。取得をやめたあとに
    dbt と公開が走るので、その時間を見込んだ時刻を渡す。
    """
    if not until:
        return None
    t = datetime.combine(start.date(), datetime.strptime(until, "%H:%M").time())
    return t if t > start else t + timedelta(days=1)


def ingest_xbrl(
    conn: duckdb.DuckDBPyConnection,
    client: EdinetClient,
    doc_ids: list[str],
    secret: Secret | None = None,
) -> None:
    """XBRL 本体 (type=1) を取り、値・context・会社のラベルと関係会社の表を入れる。

    値はインスタンスに書かれたまま入れ、項目名や相対年度は dbt で付ける。
    テキストブロックの HTML も値として残すので、関係会社の読み方を直したときは
    取り直さずに ``reparse_affiliates`` で作り直せる。バッチの扱いは ``ingest_financial_facts`` と同じ。
    """
    total = len(doc_ids)
    batch: dict[str, list[dict]] = {key: [] for key, _, _ in _XBRL_TABLES}
    docs: list[str] = []

    def flush() -> None:
        if not docs:
            return
        ph = ", ".join("?" for _ in docs)
        conn.execute("BEGIN")
        for key, table, schema in _XBRL_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE doc_id IN ({ph})", docs)
            if batch[key]:
                conn.register("_batch", pa.Table.from_pylist(batch[key], schema=schema))
                conn.execute(f"INSERT INTO {table} SELECT * FROM _batch")
                conn.unregister("_batch")
        conn.execute("COMMIT")
        for rows in batch.values():
            rows.clear()
        docs.clear()

    until = fetch_deadline(os.environ.get("EDINET_FETCH_UNTIL"), datetime.now())
    for i, doc_id in enumerate(doc_ids, 1):
        if until is not None and datetime.now() >= until:
            logger.info("  stop at %s (EDINET_FETCH_UNTIL), %d/%d docs", until, i - 1, total)
            break
        if secret is not None:
            secret.refresh_if_due()
        now = datetime.now()
        read = _read_xbrl(doc_id, client.get_document_xbrl(doc_id), now)
        for key, rows in read.items():
            batch[key].extend(rows)
        n = len(read["facts"])
        batch["progress"].append(
            {"doc_id": doc_id, "fetched_at": now, "row_count": n, "status": "done" if n else "empty"}
        )
        docs.append(doc_id)
        if len(docs) >= FINANCIAL_BATCH_SIZE:
            flush()
            logger.info("  %d/%d docs (XBRL)", i, total)
    flush()


def reparse_affiliates(conn: duckdb.DuckDBPyConnection, secret: Secret | None = None) -> None:
    """保存済みの HTML から関係会社の表を全件作り直す（読み方を直したとき用）。

    値の表は大きく、1 本のクエリで読むと認証情報の期限を越えるので、書類を束ごとに読み、
    束の切れ目で認証情報を取り直す。
    """
    doc_ids = _fetched_doc_ids(conn, secret, "xbrl_fetch_progress")["doc_id"].to_pylist()
    found: list[tuple] = []
    for start in range(0, len(doc_ids), REPARSE_BATCH_DOCS):
        if secret is not None:
            secret.refresh_if_due()
        conn.register("_docs", pa.table({"doc_id": doc_ids[start : start + REPARSE_BATCH_DOCS]}))
        found += conn.execute(
            f"SELECT f.doc_id, f.element_id, f.context_id, f.value FROM {XBRL_FACTS_TABLE} AS f "
            "JOIN _docs USING (doc_id) WHERE f.element_id = ?",
            [AFFILIATES_ELEMENT],
        ).fetchall()
        conn.unregister("_docs")
    rows = [
        row
        for doc_id, element_id, context_id, value in found
        for row in _affiliate_rows(doc_id, [{"element_id": element_id, "context_id": context_id, "value": value}])
    ]
    if secret is not None:
        secret.refresh_if_due()
    conn.execute("BEGIN")
    conn.execute(f"DELETE FROM {AFFILIATES_TABLE}")
    if rows:
        conn.register("_batch", pa.Table.from_pylist(rows, schema=_AFFILIATES_ARROW_SCHEMA))
        conn.execute(f"INSERT INTO {AFFILIATES_TABLE} SELECT * FROM _batch")
        conn.unregister("_batch")
    conn.execute("COMMIT")
    logger.info("reparsed affiliated entities: %d rows from %d docs", len(rows), len(found))


def ingest_taxonomy(conn: duckdb.DuckDBPyConnection, secret: Secret | None = None) -> None:
    """金融庁が公表したタクソノミのうち、まだ入れていない年版のラベルを入れる。

    公表ページや ZIP を読めないときはその年版を飛ばし、次の実行で取り直す
    （ラベルの抜けは dbt のテストが見つける）。
    """
    if secret is not None:
        secret.refresh_if_due()
    have = {r[0] for r in conn.execute(f"SELECT DISTINCT release FROM {TAXONOMY_LABELS_TABLE}").fetchall()}
    try:
        releases = taxonomy.list_releases(skip=have)
    except OSError as e:
        logger.warning("taxonomy: release list unavailable (%s)", e)
        return
    for release, url in releases:
        try:
            labels = taxonomy.release_labels(taxonomy.fetch(url))
        except (OSError, zipfile.BadZipFile) as e:
            logger.warning("taxonomy %s: unavailable (%s)", release, e)
            continue
        rows = [
            {"release": release, "namespace": ns, "file": f, "element_id": e, "role": r, "lang": lang, "label": text}
            for ns, f, e, r, lang, text in labels
        ]
        if not rows:
            logger.warning("taxonomy %s: no labels found in %s", release, url)
        # ZIP の取得に時間がかかるので、書き込みの前に認証情報を取り直す
        if secret is not None:
            secret.refresh_if_due()
        conn.execute("BEGIN")
        conn.register("_batch", pa.Table.from_pylist(rows, schema=_TAXONOMY_LABELS_ARROW_SCHEMA))
        conn.execute(f"INSERT INTO {TAXONOMY_LABELS_TABLE} SELECT * FROM _batch")
        conn.unregister("_batch")
        conn.execute("COMMIT")
        logger.info("taxonomy %s: %d labels", release, len(rows))


def _xbrl_docs_to_fetch(
    conn: duckdb.DuckDBPyConnection, secret: Secret | None = None
) -> list[str]:
    """XBRL を未取得の書類を、提出日時の新しい順に返す（上限あり）。"""
    doc_types = [
        t.strip()
        for t in os.environ.get("EDINET_XBRL_DOC_TYPES", DEFAULT_XBRL_DOC_TYPES).split(",")
        if t.strip()
    ]
    if not doc_types:
        return []
    cap = int(
        os.environ.get("EDINET_MAX_XBRL_DOCS_PER_RUN", str(DEFAULT_MAX_XBRL_DOCS_PER_RUN))
    )
    ph = ", ".join("?" for _ in doc_types)
    conn.register("_fetched", _fetched_doc_ids(conn, secret, "xbrl_fetch_progress"))
    if secret is not None:
        secret.refresh_if_due()
    rows = conn.execute(
        f"""
        SELECT d."docID"
        FROM {DOCUMENTS_TABLE} AS d
        LEFT JOIN _fetched AS p ON d."docID" = p.doc_id
        WHERE d."docTypeCode" IN ({ph})
          AND d."xbrlFlag" = '1'
          AND d."withdrawalStatus" = '0'
          AND p.doc_id IS NULL
        GROUP BY d."docID"
        ORDER BY max(d."submitDateTime") DESC
        LIMIT {cap}
        """,
        doc_types,
    ).fetchall()
    conn.unregister("_fetched")
    return [r[0] for r in rows]


def _fetched_doc_ids(
    conn: duckdb.DuckDBPyConnection,
    secret: Secret | None = None,
    table: str = "financial_fetch_progress",
) -> pa.Table:
    """進捗表に登録済みの doc_id を、データファイルを束ごとに読んで返す。

    進捗表は小さなファイルが数万本に分かれていて、1 本のクエリで全件読むと
    1 時間を超える。1 本の文が認証情報の期限を越えると取り直しようがない
    （2026-10-07〜10-10 の 4 連続失敗はちょうど 1 時間で 403 になった）ので、
    束の切れ目で ``refresh_if_due()`` を呼ぶ。

    削除ファイルは見ない。この表の DELETE は同じトランザクションで同じ doc_id を
    入れ直すときだけなので、データファイルの doc_id の和集合が表の doc_id と一致する。
    """
    files = [
        r[0]
        for r in conn.execute(
            "SELECT data_file FROM ducklake_list_files("
            f"'edinet', '{table}', schema => '_source')"
        ).fetchall()
    ]
    logger.info("%s: %d files", table, len(files))
    seen: set[str] = set()
    for start in range(0, len(files), PROGRESS_READ_BATCH_FILES):
        if secret is not None:
            secret.refresh_if_due()
        batch = files[start : start + PROGRESS_READ_BATCH_FILES]
        rows = conn.execute(
            "SELECT DISTINCT doc_id FROM read_parquet(?)", [batch]
        ).fetchall()
        seen.update(r[0] for r in rows)
    return pa.table({"doc_id": pa.array(sorted(seen), pa.string())})


def _docs_to_fetch(
    conn: duckdb.DuckDBPyConnection, secret: Secret | None = None
) -> list[str]:
    """財務 CSV 未取得の対象書類 docID を提出日時の新しい順に返す（上限あり）。

    対象は ``EDINET_FINANCIAL_DOC_TYPES``（既定は空で取らない）かつ csvFlag=1 かつ未取下げ。
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
    conn.register("_fetched", _fetched_doc_ids(conn, secret))
    if secret is not None:
        secret.refresh_if_due()
    rows = conn.execute(
        f"""
        SELECT d."docID"
        FROM {DOCUMENTS_TABLE} AS d
        LEFT JOIN _fetched AS p ON d."docID" = p.doc_id
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
    conn.unregister("_fetched")
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


def _ensure_xbrl_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {XBRL_FACTS_TABLE} "
        "(doc_id VARCHAR, seq INTEGER, element_id VARCHAR, context_id VARCHAR, unit_id VARCHAR, "
        "decimals VARCHAR, is_nil BOOLEAN, value VARCHAR, _fetched_at TIMESTAMP)"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {XBRL_CONTEXTS_TABLE} "
        "(doc_id VARCHAR, context_id VARCHAR, period_start VARCHAR, period_end VARCHAR, "
        "instant VARCHAR, members VARCHAR)"
    )
    labels = "element_id VARCHAR, role VARCHAR, lang VARCHAR, label VARCHAR"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {XBRL_LABELS_TABLE} (doc_id VARCHAR, {labels})")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TAXONOMY_LABELS_TABLE} (release VARCHAR, namespace VARCHAR, file VARCHAR, {labels})")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {XBRL_NAMESPACES_TABLE} (doc_id VARCHAR, prefix VARCHAR, uri VARCHAR)")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {XBRL_PROGRESS_TABLE} "
        "(doc_id VARCHAR, fetched_at TIMESTAMP, row_count INTEGER, status VARCHAR)"
    )
    cols = ", ".join(f"{f} VARCHAR" for f in affiliates.FIELDS if f != "row_seq")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {AFFILIATES_TABLE} "
        f"(doc_id VARCHAR, context_id VARCHAR, row_seq INTEGER, {cols})"
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
