import io
import zipfile
from html import escape

import duckdb

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


def test_ingest_xbrl_and_reparse():
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
    main.reparse_affiliates(conn)
    assert conn.execute(names).fetchall() == expected
