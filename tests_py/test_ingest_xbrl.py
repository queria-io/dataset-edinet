import io
import zipfile
from html import escape

import duckdb
import pytest

import main
from tests_py.test_affiliates import INLINE

NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2025-11-01/jpcrp_cor"


LABELS = (
    '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">'
    '<link:labelLink><link:loc xlink:href="x.xsd#jpcrp030000-asr_E00000-000_FooSegmentMember" xlink:label="foo"/>'
    '<link:label xlink:label="label_foo" xlink:role="http://www.xbrl.org/2003/role/label" xml:lang="ja">フー事業</link:label>'
    '<link:labelArc xlink:from="foo" xlink:to="label_foo"/></link:labelLink></link:linkbase>'
)


def _zip(html: str) -> bytes:
    instance = (
        f'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:jpcrp_cor="{NS}">'
        f'<jpcrp_cor:OverviewOfAffiliatedEntitiesTextBlock contextRef="FilingDateInstant">'
        f'{escape(html)}</jpcrp_cor:OverviewOfAffiliatedEntitiesTextBlock>'
        f'<jpcrp_cor:NumberOfEmployees contextRef="CurrentYearInstant" unitRef="pure" decimals="0">10</jpcrp_cor:NumberOfEmployees>'
        '<xbrli:context id="CurrentYearInstant"><xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period></xbrli:context>'
        "</xbrli:xbrl>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/jpcrp030000-asr-001_E00000-000_2026-03-31_01_2026-06-26.xbrl", instance)
        zf.writestr("XBRL/PublicDoc/jpcrp030000-asr-001_E00000-000_2026-03-31_01_2026-06-26_lab.xml", LABELS)
    return buf.getvalue()


class FakeClient:
    def get_document_xbrl(self, doc_id: str) -> bytes | None:
        return _zip(INLINE.replace("<td>", '<td style="x">')) if doc_id == "S1" else None


def test_ingest_xbrl_and_reparse(monkeypatch):
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS edinet")
    conn.execute("CREATE SCHEMA edinet._source")
    main._ensure_xbrl_tables(conn)
    main.ingest_xbrl(conn, FakeClient(), ["S1", "S2"])

    facts = conn.execute(
        f"SELECT doc_id, seq, element_id, unit_id, decimals, value FROM {main.XBRL_FACTS_TABLE} ORDER BY seq"
    ).fetchall()
    assert [(d, q, e) for d, q, e, *_ in facts] == [
        ("S1", 0, "jpcrp_cor:OverviewOfAffiliatedEntitiesTextBlock"),
        ("S1", 1, "jpcrp_cor:NumberOfEmployees"),
    ]
    assert "style=" not in facts[0][5]
    assert facts[1][3:] == ("pure", "0", "10")
    assert conn.execute(f"SELECT context_id, instant FROM {main.XBRL_CONTEXTS_TABLE}").fetchall() == [
        ("CurrentYearInstant", "2026-03-31")
    ]
    assert conn.execute(f"SELECT element_id, lang, label FROM {main.XBRL_LABELS_TABLE}").fetchall() == [
        ("jpcrp030000-asr_E00000-000:FooSegmentMember", "ja", "フー事業")
    ]
    status = conn.execute(f"SELECT doc_id, status FROM {main.XBRL_PROGRESS_TABLE} ORDER BY 1").fetchall()
    assert status == [("S1", "done"), ("S2", "empty")]

    names = f"SELECT name, category FROM {main.AFFILIATES_TABLE} ORDER BY row_seq"
    expected = [("㈱エー（注）３", "連結子会社"), ("㈱ビー", "持分法適用関連会社"), ("㈱シー", "持分法適用関連会社")]
    assert conn.execute(names).fetchall() == expected
    # DuckLake のファイル一覧の代わりに進捗表をそのまま読む
    monkeypatch.setattr(
        main, "_fetched_doc_ids", lambda conn, table: conn.execute(f"SELECT doc_id FROM edinet._source.{table}").fetch_arrow_table()
    )
    main.reparse_affiliates(conn)
    assert conn.execute(names).fetchall() == expected


def test_fetch_deadline():
    from datetime import datetime

    start = datetime(2026, 11, 8, 20, 0)
    assert main.fetch_deadline("05:00", start) == datetime(2026, 11, 9, 5, 0)
    assert main.fetch_deadline("23:30", start) == datetime(2026, 11, 8, 23, 30)
    assert main.fetch_deadline(None, start) is None


def test_steps():
    assert main.steps([]) == ["ingest", "build"]
    assert main.steps(["build"]) == ["build"]
    assert main.steps(["build", "ingest"]) == ["ingest", "build"]
    with pytest.raises(SystemExit):
        main.steps(["default"])


def test_build_only_refreshes_taxonomy_first(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "ingest", lambda: calls.append("ingest"))
    monkeypatch.setattr(main, "refresh_taxonomy", lambda: calls.append("taxonomy"))
    monkeypatch.setattr(main, "run_dbt", lambda *_: calls.append("dbt"))
    monkeypatch.setattr(main, "forget_old_snapshots", lambda: calls.append("forget"))
    for argv, expected in ((["build"], ["forget", "taxonomy", "dbt"]), ([], ["forget", "ingest", "dbt"])):
        calls.clear()
        monkeypatch.setattr(main.sys, "argv", ["main.py", *argv])
        main.main()
        assert calls == expected


def test_keep_zip_and_drop_pushed(tmp_path, monkeypatch):
    import hashlib

    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS edinet")
    conn.execute("CREATE SCHEMA edinet._source")
    main._ensure_xbrl_tables(conn)
    main.ingest_xbrl(conn, FakeClient(), ["S1", "S2"], folder=tmp_path)

    raw = (tmp_path / "S1.zip").read_bytes()
    assert not (tmp_path / "S2.zip").exists()
    assert conn.execute(f"SELECT doc_id, sha256, bytes FROM {main.XBRL_FILES_TABLE}").fetchall() == [
        ("S1", hashlib.sha256(raw).hexdigest(), len(raw))
    ]

    # fdl のビルドでなければ消さない
    monkeypatch.delenv("FDL_WORKING", raising=False)
    main.drop_pushed_zips(conn, tmp_path)
    assert (tmp_path / "S1.zip").exists()
    (tmp_path / "S3.zip").write_bytes(b"PK")
    monkeypatch.setenv("FDL_WORKING", "work.sqlite")
    main.drop_pushed_zips(conn, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["S3.zip"]


def test_no_folder_keeps_nothing():
    assert main.keep_zip(None, "S1", b"PK", None) == []


def test_refetch_docs_without_zip(monkeypatch):
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS edinet")
    conn.execute("CREATE SCHEMA edinet._source")
    main._ensure_xbrl_tables(conn)
    conn.execute(
        f'CREATE TABLE {main.DOCUMENTS_TABLE} ("docID" VARCHAR, "docTypeCode" VARCHAR, '
        '"xbrlFlag" VARCHAR, "withdrawalStatus" VARCHAR, "submitDateTime" VARCHAR)'
    )
    conn.execute(
        f"INSERT INTO {main.DOCUMENTS_TABLE} VALUES "
        "('NEW', '120', '1', '0', '2026-11-10'), ('OLD', '120', '1', '0', '2026-11-01'), "
        "('KEPT', '120', '1', '0', '2026-11-05'), ('EMPTY', '120', '1', '0', '2026-11-06')"
    )
    conn.execute(
        f"INSERT INTO {main.XBRL_PROGRESS_TABLE} VALUES "
        "('OLD', now(), 1, 'done'), ('KEPT', now(), 1, 'done'), ('EMPTY', now(), 0, 'empty')"
    )
    conn.execute(f"INSERT INTO {main.XBRL_FILES_TABLE} VALUES ('KEPT', 'x', 1, now())")
    monkeypatch.setattr(
        main,
        "_fetched_doc_ids",
        lambda conn, table, where="true": conn.execute(
            f"SELECT doc_id FROM edinet._source.{table} WHERE {where}"
        ).to_arrow_table(),
    )
    # 未取得の書類が先、ZIP を残していない取得済みの書類はその後
    assert main._xbrl_docs_to_fetch(conn, keep_zips=True) == ["NEW", "OLD"]
    assert main._xbrl_docs_to_fetch(conn) == ["NEW"]
    monkeypatch.setenv("EDINET_MAX_XBRL_DOCS_PER_RUN", "1")
    assert main._xbrl_docs_to_fetch(conn, keep_zips=True) == ["NEW"]


def test_forget_old_snapshots_keeps_files(monkeypatch, tmp_path):
    monkeypatch.setenv("FDL_WORKING", str(tmp_path / "work.sqlite"))
    monkeypatch.setenv("FDL_DATA_PATH", str(tmp_path / "data") + "/")
    with main._ducklake_connect() as conn:
        for i in range(3):
            conn.execute("CREATE OR REPLACE TABLE edinet.t AS SELECT ? AS x", [i])
    main.forget_old_snapshots()
    with main._ducklake_connect() as conn:
        snapshots = conn.execute("SELECT snapshot_id FROM ducklake_snapshots('edinet')").fetchall()
        assert snapshots == [(3,)]
        assert conn.execute("SELECT x FROM edinet.t").fetchall() == [(2,)]
    assert len(list((tmp_path / "data" / "main" / "t").iterdir())) == 3
