"""「関係会社の状況」の HTML を、1 社 1 行に分ける。

様式は会社ごとに 2 通りある:
- 区分（連結子会社・持分法適用関連会社など）が表の中の行で、1 列目だけに値が入る
- 区分が表の外の段落で、列見出しが 2 段（関係内容が「役員の兼任等」「営業上の取引等」に分かれる）
列は見出しの文字で決める。改ページで分かれた見出しの無い表は、直前の見出しと区分を引き継ぐ。
値は記載のまま返し、資本金の単位や議決権の内数の分解は dbt 側で行う。
"""

from __future__ import annotations

import re

from edinet.html_table import parse_blocks

# 見出しの文字 → 列名。上から順に最初に当たったものを使う
_COLUMNS = (
    ("name", ("名称",)),
    ("address", ("住所", "所在地")),
    ("capital", ("資本金", "出資金")),
    ("business", ("事業",)),
    ("voting_rights", ("議決権",)),
    ("relationship", ("関係内容",)),
)

_CATEGORY = re.compile(
    r"^[（(]?\s*(?:[0-9０-９]+\s*[)）.．]|[①-⑳]|[ア-ン]\s*[.．])?\s*"
    r"(親会社|連結子会社|非連結子会社|持分法適用[^\s（(）)]*|関連会社|その他の関係会社)"
    r"\s*[)）]?$"
)

FIELDS = (
    "row_seq",
    "category",
    "name",
    "address",
    "capital",
    "capital_header",
    "business",
    "voting_rights",
    "relationship",
)


def category_of(text: str) -> str | None:
    """区分の見出しなら区分名を返す（「（連結子会社）」「(1）連結子会社」→ 連結子会社）。"""
    m = _CATEGORY.match(text.replace(" ", ""))
    return m.group(1) if m else None


def _column_of(header: str) -> str | None:
    h = header.replace(" ", "")
    return next(
        (col for col, words in _COLUMNS if any(w in h for w in words)), None
    )


def _header(table: list[list[str]]) -> tuple[int, dict[int, str], dict[int, str]] | None:
    """見出し行を探し、(データの開始行, 列番号→列名, 列番号→見出しの文字) を返す。

    「名称」のある行を見出しとし、rowspan で同じ「名称」が続く行までを見出しに含める
    （2 段の見出し）。先頭が「会社名」で売上高などが並ぶ損益情報の表は対象外。
    """
    start = next(
        (i for i, row in enumerate(table) if any(c.replace(" ", "") == "名称" for c in row)),
        None,
    )
    if start is None:
        return None
    name_col = next(c for c, v in enumerate(table[start]) if v.replace(" ", "") == "名称")
    end = start
    while end + 1 < len(table) and table[end + 1][name_col].replace(" ", "") == "名称":
        end += 1
    texts = {
        c: "/".join(dict.fromkeys(table[r][c] for r in range(start, end + 1) if table[r][c]))
        for c in range(len(table[start]))
    }
    cols = {c: col for c, t in texts.items() if (col := _column_of(t))}
    return end + 1, cols, texts


def _row(cells: list[str], cols: dict[int, str], texts: dict[int, str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for c, col in cols.items():
        v = cells[c] if c < len(cells) else ""
        if not v:
            continue
        # 関係内容は 2 段見出しで複数列になることがある。見出しを付けてつなぐ
        if col == "relationship" and out.get(col):
            sub = texts[c].split("/")[-1]
            out[col] = f"{out[col]} / {sub}: {v}"
        elif col == "relationship" and "/" in texts[c]:
            out[col] = f"{texts[c].split('/')[-1]}: {v}"
        elif col not in out:
            out[col] = v
    capital_col = next((c for c, col in cols.items() if col == "capital"), None)
    out["capital_header"] = texts.get(capital_col) if capital_col is not None else None
    return out


def _is_summary(table: list[list[str]]) -> bool:
    """子会社の売上高などを並べた「主要な損益情報等」の表か。"""
    return any("売上高" in c for row in table[:2] for c in row)


def parse_affiliated_entities(html: str) -> list[dict[str, object]]:
    """関係会社の状況の HTML から、関係会社を 1 社 1 行で返す。"""
    rows: list[dict[str, object]] = []
    category: str | None = None
    header: tuple[dict[int, str], dict[int, str]] | None = None
    for kind, body in parse_blocks(html):
        if kind == "text":
            category = category_of(str(body)) or category
            continue
        table = body
        assert isinstance(table, list)
        found = _header(table)
        if found:
            start, cols, texts = found
            header = (cols, texts)
        elif header and table and len(table[0]) == len(header[1]) and not _is_summary(table):
            # 改ページで分かれた続きの表
            start = 0
        else:
            continue
        cols, texts = header
        for cells in table[start:]:
            values = [v for v in dict.fromkeys(cells) if v]
            if len(values) == 1 and (cat := category_of(values[0])):
                category = cat
                continue
            rec = _row(cells, cols, texts)
            if not rec.get("name"):
                continue
            rows.append({"row_seq": len(rows), "category": category, **rec})
    return [{f: r.get(f) for f in FIELDS} for r in rows]
