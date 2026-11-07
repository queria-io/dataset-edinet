import io
import zipfile
from html import escape

import duckdb

import main
from tests_py.test_affiliates import INLINE

NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2025-11-01/jpcrp_cor"


def _zip(html: str) -> bytes:
    instance = (
        f'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:jpcrp_cor="{NS}">'
        f'<jpcrp_cor:OverviewOfAffiliatedEntitiesTextBlock contextRef="FilingDateInstant">'
        f'{escape(html)}</jpcrp_cor:OverviewOfAffiliatedEntitiesTextBlock>'
        f'<jpcrp_cor:NumberOfEmployees contextRef="CurrentYearInstant">10</jpcrp_cor:NumberOfEmployees>'
        "</xbrli:xbrl>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/jpcrp030000-asr-001_E00000-000_2026-03-31_01_2026-06-26.xbrl", instance)
    return buf.getvalue()


class FakeClient:
    def get_document_xbrl(self, doc_id: str) -> bytes | None:
        return _zip(INLINE.replace("<td>", '<td style="x">')) if doc_id == "S1" else None


def test_ingest_textblocks_and_reparse():
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS edinet")
    conn.execute("CREATE SCHEMA edinet._source")
    main._ensure_xbrl_tables(conn)
    main.ingest_textblocks(conn, FakeClient(), ["S1", "S2"])

    blocks = conn.execute(f"SELECT doc_id, element_id, html FROM {main.TEXTBLOCKS_TABLE}").fetchall()
    assert [(d, e) for d, e, _ in blocks] == [("S1", "jpcrp_cor:OverviewOfAffiliatedEntitiesTextBlock")]
    assert "style=" not in blocks[0][2]
    status = conn.execute(f"SELECT doc_id, status FROM {main.XBRL_PROGRESS_TABLE} ORDER BY 1").fetchall()
    assert status == [("S1", "done"), ("S2", "empty")]

    names = f"SELECT name, category FROM {main.AFFILIATES_TABLE} ORDER BY row_seq"
    expected = [("㈱エー（注）３", "連結子会社"), ("㈱ビー", "持分法適用関連会社"), ("㈱シー", "持分法適用関連会社")]
    assert conn.execute(names).fetchall() == expected
    main.reparse_affiliates(conn)
    assert conn.execute(names).fetchall() == expected
