"""書類取得 API type=1 の ZIP から、XBRL インスタンスのテキストブロックを取り出す。

type=5 の CSV ではテキストブロックの HTML が外れて表のセルが区切りなしでつながり、
30,000 字で切れる。type=1 のインスタンス (XBRL/PublicDoc/*.xbrl) にはエスケープされた
HTML が全文入っているので、表を列に分けたい要素だけをここから取る。
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

JPCRP_NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/"

# 取り出すテキストブロック（jpcrp_cor の要素名）
TEXTBLOCK_ELEMENTS = (
    "OverviewOfAffiliatedEntitiesTextBlock",  # 関係会社の状況
    "MajorFacilitiesTextBlock",  # 主要な設備の状況
    "InformationForEachOfMainCustomersTextBlock",  # 主要な顧客ごとの情報
)

# 表の構造に要らない属性。生の HTML の大半はインラインの style で、
# これを外すと保存する大きさが数分の 1 になる
_DROP_ATTRS = re.compile(r'\s(?:style|class|id|width|height|valign|align)="[^"]*"')


def instance_from_zip(raw: bytes) -> bytes | None:
    """ZIP から本文の XBRL インスタンスを返す。無ければ None。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.startswith("XBRL/PublicDoc/") and n.endswith(".xbrl")
        ]
        return zf.read(names[0]) if names else None


def extract_textblocks(
    instance: bytes, elements: tuple[str, ...] = TEXTBLOCK_ELEMENTS
) -> list[tuple[str, str, str]]:
    """インスタンスから ``(element_id, context_id, html)`` を文書順に返す。

    ``element_id`` は CSV と同じ ``jpcrp_cor:<要素名>`` の形にそろえる。
    """
    wanted = set(elements)
    out: list[tuple[str, str, str]] = []
    for _, el in ET.iterparse(io.BytesIO(instance), events=("end",)):
        ns, _, local = el.tag.lstrip("{").rpartition("}")
        # 名前空間は jpcrp/<タクソノミの日付>/jpcrp_cor。日付は改定ごとに変わるので見ない
        if local in wanted and ns.startswith(JPCRP_NS) and ns.endswith("/jpcrp_cor"):
            html = clean_html(el.text or "")
            out.append((f"jpcrp_cor:{local}", el.get("contextRef", ""), html))
        el.clear()
    return out


def clean_html(html: str) -> str:
    """表の構造に要らない属性を外す。"""
    return _DROP_ATTRS.sub("", html)
