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
