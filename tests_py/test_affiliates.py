from edinet.affiliates import category_of, parse_affiliated_entities

# 区分が表の中の行にある型（見出し 1 段）。改ページで続きの表と損益情報の表が続く
INLINE = """
<p>４【関係会社の状況】</p>
<table>
<tr><td>名称</td><td>住所</td><td>資本金又は出資金</td><td>主要な事業の内容</td><td>議決権の所有割合(％)</td><td>関係内容</td></tr>
<tr><td>（連結子会社）</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>㈱エー（注）３</td><td>東京都港区</td><td>1,000千円</td><td>外食事業</td><td>100.0</td><td>役員の兼任５名</td></tr>
<tr><td>（持分法適用関連会社）</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>㈱ビー</td><td>東京都大田区</td><td>40,000千円</td><td>－</td><td>49.0</td><td>－</td></tr>
</table>
<table>
<tr><td>㈱シー</td><td>大阪市北区</td><td>百万円 10</td><td>小売</td><td>100(100)</td><td>－</td></tr>
</table>
<p>（注）３ 特定子会社であります。</p>
<table>
<tr><td>会社名</td><td>売上高</td><td>経常利益</td><td>当期純利益</td><td>純資産額</td><td>総資産額</td></tr>
<tr><td>㈱エー</td><td>15,264</td><td>480</td><td>919</td><td>△2,447</td><td>1,408</td></tr>
</table>
"""

# 区分が表の外の段落にある型（見出し 2 段、関係内容が colspan=2）
OUTSIDE = """
<p>(1）連結子会社</p>
<table><tr><td>2026年3月31日現在</td></tr></table>
<table>
<tr><td rowspan="2">名称</td><td rowspan="2">住所</td><td rowspan="2">資本金（百万円）</td><td rowspan="2">主要な事業の内容</td><td rowspan="2">議決権の所有割合（％）</td><td colspan="2">関係内容</td></tr>
<tr><td>役員の兼任等</td><td>営業上の取引等</td></tr>
<tr><td>エフ㈱</td><td>川崎市</td><td>100</td><td>サービス</td><td>100.0</td><td>有</td><td>製品の購入</td></tr>
<tr><td>Ｇ社</td><td>ドイツ</td><td>千ユーロ 272,752</td><td>サービス</td><td>100.0 (100.0)</td><td>無</td><td></td></tr>
</table>
<p>(2）持分法適用関連会社</p>
<table>
<tr><td rowspan="2">名称</td><td rowspan="2">住所</td><td rowspan="2">資本金（百万円）</td><td rowspan="2">主要な事業の内容</td><td rowspan="2">議決権の所有割合（％）</td><td colspan="2">関係内容</td></tr>
<tr><td>役員の兼任等</td><td>営業上の取引等</td></tr>
<tr><td>Ｈ㈱</td><td>東京都千代田区</td><td>5</td><td>製造</td><td>30.0</td><td>有</td><td>－</td></tr>
</table>
"""


def test_inline_categories_and_continuation():
    rows = parse_affiliated_entities(INLINE)
    assert [(r["category"], r["name"]) for r in rows] == [
        ("連結子会社", "㈱エー（注）３"),
        ("持分法適用関連会社", "㈱ビー"),
        ("持分法適用関連会社", "㈱シー"),
    ]
    assert rows[0]["address"] == "東京都港区"
    assert rows[0]["capital"] == "1,000千円"
    assert rows[0]["voting_rights"] == "100.0"
    assert rows[0]["relationship"] == "役員の兼任５名"
    assert rows[2]["voting_rights"] == "100(100)"


def test_outside_categories_and_two_level_header():
    rows = parse_affiliated_entities(OUTSIDE)
    assert [(r["category"], r["name"]) for r in rows] == [
        ("連結子会社", "エフ㈱"),
        ("連結子会社", "Ｇ社"),
        ("持分法適用関連会社", "Ｈ㈱"),
    ]
    assert rows[0]["relationship"] == "役員の兼任等: 有 / 営業上の取引等: 製品の購入"
    assert rows[0]["capital_header"] == "資本金（百万円）"
    assert rows[1]["capital"] == "千ユーロ 272,752"


def test_category_of():
    assert category_of("（連結子会社）") == "連結子会社"
    assert category_of("(1）連結子会社") == "連結子会社"
    assert category_of("(3) その他の関係会社") == "その他の関係会社"
    assert category_of("㈱エー") is None


def _names(html):
    return [(r["category"], r["name"]) for r in parse_affiliated_entities(html)]


ROW = "<tr><td>{}</td><td>{}</td><td>{}</td><td>事業</td><td>{}</td><td>{}</td></tr>"
CAT = "<tr><td>{}</td><td></td><td></td><td></td><td>{}</td><td></td></tr>"
HEAD = "<tr><td>名称</td><td>住所</td><td>資本金</td><td>主要な事業の内容</td><td>議決権の所有割合</td><td>関係内容</td></tr>"


def test_category_in_header_cell_and_two_level_voting():
    html = """<table>
    <tr><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
    <tr><td rowspan="2">名称 （連結子会社）</td><td rowspan="2">住所</td><td rowspan="2">資本金又は出資金 （百万円）</td><td rowspan="2">主要な事業の内容</td><td colspan="2">議決権の所有（被所有）割合</td><td rowspan="2">関係内容</td></tr>
    <tr><td>所有割合 （％）</td><td>被所有割合（％）</td></tr>
    <tr><td>㈱ワイ</td><td>東京都港区</td><td>20</td><td>物流事業</td><td>62.5</td><td>－</td><td>委託</td></tr>
    </table>"""
    rows = parse_affiliated_entities(html)
    assert _names(html) == [("連結子会社", "㈱ワイ")]
    assert rows[0]["voting_rights"] == "所有割合 （％）: 62.5 / 被所有割合（％）: －"


def test_category_prefix_in_name_cell():
    html = "<table>" + HEAD + ROW.format("(連結子会社)㈱A", "川崎市", "20百万円", "60.0", "－") + ROW.format("㈱B", "香港", "1,360百万円", "100.0", "－") + ROW.format("（持分法適用関連会社） ㈱C", "東京都", "10", "30", "－") + "</table>"
    assert _names(html) == [("連結子会社", "㈱A"), ("連結子会社", "㈱B"), ("持分法適用関連会社", "㈱C")]


def test_category_sentence_and_owned_by_marker_row():
    html = "<p>親会社は、次のとおりであります。</p><table>" + HEAD + ROW.format("㈱P", "東京都", "100", "被所有 51.0", "－") + "</table>"
    assert _names(html) == [("親会社", "㈱P")]
    html = "<table>" + HEAD + CAT.format("(親会社)", "（被所有）") + ROW.format("㈱P※１、２", "東京都", "11,707", "41.0(6.8)", "－") + CAT.format("(その他の関係会社の子会社)", "") + ROW.format("㈱Q", "大阪", "5", "－", "－") + "</table>"
    assert _names(html) == [("親会社", "㈱P※１、２"), ("その他の関係会社の子会社", "㈱Q")]


def test_drops_other_counts_and_summary_tables():
    html = "<p>（連結子会社）</p><table>" + HEAD + ROW.format("㈱A", "東京都", "10", "100", "－") + CAT.format("その他17社", "") + CAT.format("計", "") + "</table>"
    html += "<table><tr><td>名称</td><td>売上高</td><td>経常利益</td><td>当期純利益</td><td>純資産額</td><td>総資産額</td></tr><tr><td>㈱A</td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr></table>"
    assert _names(html) == [("連結子会社", "㈱A")]


def test_rowspan_name_is_one_company():
    html = "<p>（連結子会社）</p><table>" + HEAD + """
    <tr><td rowspan="2">㈱A</td><td rowspan="2">香港</td><td rowspan="2">1,360百万円</td><td rowspan="2">投資</td><td rowspan="2">100.0</td><td>資金の貸付</td></tr>
    <tr><td>役員の兼任１名</td></tr></table>"""
    rows = parse_affiliated_entities(html)
    assert _names(html) == [("連結子会社", "㈱A")]
    assert rows[0]["relationship"] == "資金の貸付 役員の兼任１名"


def test_companies_stacked_in_one_cell():
    html = "<table>" + HEAD + """
    <tr><td><p>(連結子会社)</p><p>㈱S</p><p></p><p>S U.S.A.,</p><p>Inc.</p></td><td><p>東京都中央区</p><p>&#160;</p><p>米国デンバー市</p></td>
    <td><p>100</p><p></p><p>10,125 (US$)</p></td><td>事業</td><td><p>100</p><p></p><p>100 (100)</p></td><td>－</td></tr></table>"""
    rows = parse_affiliated_entities(html)
    assert _names(html) == [("連結子会社", "㈱S"), ("連結子会社", "S U.S.A., Inc.")]
    assert rows[1]["address"] == "米国デンバー市"
    assert rows[1]["capital"] == "10,125 (US$)"


def test_unit_only_row_applies_to_next_company():
    html = "<p>（連結子会社）</p><table>" + HEAD + "<tr><td></td><td></td><td>千HK$</td><td></td><td></td><td></td></tr>" + ROW.format("A Ltd.", "香港", "30,700", "100", "－") + "</table>"
    assert parse_affiliated_entities(html)[0]["capital"] == "千HK$ 30,700"


def test_wrapped_lines_are_not_split():
    html = "<p>（連結子会社）</p><table>" + HEAD + """
    <tr><td><p>株式会社</p><p>ツーアール</p></td><td><p>沖縄県</p><p>浦添市</p></td><td>50</td><td>事業</td><td>100</td><td>－</td></tr>
    <tr><td><p>㈱カゴシマバンズ</p><p></p><p>（注）３</p></td><td><p>鹿児島県</p><p></p><p>霧島市</p></td><td>10</td><td>事業</td><td>100</td><td>－</td></tr>
    </table>"""
    assert _names(html) == [("連結子会社", "株式会社 ツーアール"), ("連結子会社", "㈱カゴシマバンズ （注）３")]
    assert parse_affiliated_entities(html)[0]["address"] == "沖縄県 浦添市"


def test_category_row_spanning_all_columns():
    html = "<p>（連結子会社）</p><table>" + HEAD + ROW.format("㈱A", "東京都", "10", "100", "－")
    html += '<tr><td colspan="6">（持分法適用関連会社）</td></tr>' + ROW.format("㈱B", "大阪", "5", "30", "－") + "</table>"
    assert _names(html) == [("連結子会社", "㈱A"), ("持分法適用関連会社", "㈱B")]
