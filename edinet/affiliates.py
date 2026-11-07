"""「関係会社の状況」の HTML を、1 社 1 行に分ける。

列は見出しの文字で決める。区分（連結子会社・持分法適用関連会社など）は会社によって
書き方が違い、次のどれでも拾う:
- 表の中の行（名称の列だけに「（連結子会社）」）
- 表の外の段落（「(1）連結子会社」「連結子会社は、次のとおりであります。」）
- 見出しのセル（「名称 （連結子会社）」）や、名称のセルの頭（「(連結子会社)㈱A」）
改ページで分かれた見出しの無い表は、直前の見出しと区分を引き継ぐ。
値は記載のまま返し、注記番号・資本金の単位・議決権の内数の分解は dbt 側で行う。
"""

from __future__ import annotations

import re

from edinet.html_table import SEP, Table, parse_blocks

# 見出しの文字 → 列名。上から順に最初に当たったものを使う
_COLUMNS = (
    ("name", ("名称", "会社名")),
    ("address", ("住所", "所在地")),
    ("capital", ("資本金", "出資金")),
    ("business", ("事業",)),
    ("voting_rights", ("議決権",)),
    ("relationship", ("関係内容",)),
)

_CATEGORY_NAMES = (
    r"(?:その他の関係会社|親会社|連結子会社|非連結子会社|持分法適用[^\s（(）)、。は]*|関連会社)"
    r"(?:の(?:子会社|関係会社|関連会社))?"
)
_NUMBERING = r"(?:[（(]?[0-9０-９]+\s*[)）.．]|[①-⑳]|[ア-ン]\s*[.．])?"
_CATEGORY = re.compile(rf"^{_NUMBERING}[（(]?({_CATEGORY_NAMES})[)）]?$")
# 「連結子会社は、次のとおりであります。」
_CATEGORY_SENTENCE = re.compile(rf"^{_NUMBERING}({_CATEGORY_NAMES})は[、,]")
# 名称のセルの頭の「(連結子会社)」
_CATEGORY_PREFIX = re.compile(rf"^[（(]({_CATEGORY_NAMES})[)）]\s*")
# 会社名ではない行（「その他17社」「計」）
_NOT_COMPANY = re.compile(r"^(その他\s*[0-9０-９]*\s*社?|[0-9０-９]+\s*社|計|合計|小計)$")
# 分けた結果がこれだけなら、折り返しや注記を分けてしまったとみなす
_FRAGMENT = re.compile(
    r"^((（注[）)]|\(注\)|※)[0-9０-９,、，・.．\s]*|株式会社|有限会社|有限公司|"
    r"CO\.?,?\s*LTD\.?|INC\.?|LIMITED|[〔(（][0-9.．]+[〕)）])$",
    re.IGNORECASE,
)
# 子会社の売上高などを並べた「主要な損益情報等」の表の見出し
_SUMMARY_WORDS = ("売上高", "経常利益", "純資産額", "営業収益")

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


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def category_of(text: str) -> str | None:
    """区分の見出しなら区分名を返す（「（連結子会社）」「(1）連結子会社」→ 連結子会社）。"""
    t = _squash(text)
    m = _CATEGORY.match(t) or _CATEGORY_SENTENCE.match(t)
    return m.group(1) if m else None


def _column_of(header: str) -> str | None:
    h = _squash(header)
    return next((col for col, words in _COLUMNS if any(w in h for w in words)), None)


def _is_name_header(text: str) -> bool:
    t = _squash(text)
    return t.startswith("名称") or t in ("会社名", "会社の名称")


def _header(table: Table) -> tuple[int, dict[int, list[int]], dict[int, str], str | None] | None:
    """見出し行を探し、(データの開始行, 列名→列番号, 列番号→見出しの文字, 見出しの区分) を返す。

    名称の見出しのある行から、同じ名称の見出しが続く行までを見出しとする（2 段の見出し）。
    売上高などの並ぶ損益情報の表は対象外。
    """
    start = next(
        (i for i, row in enumerate(table) if any(_is_name_header(c.text) for c in row)), None
    )
    if start is None:
        return None
    name_col = next(c for c, cell in enumerate(table[start]) if _is_name_header(cell.text))
    name_text = table[start][name_col].text
    end = start
    while end + 1 < len(table) and table[end + 1][name_col].text == name_text:
        end += 1
    texts = {
        c: "/".join(dict.fromkeys(table[r][c].text for r in range(start, end + 1) if table[r][c].text))
        for c in range(len(table[start]))
    }
    if any(w in t for t in texts.values() for w in _SUMMARY_WORDS):
        return None
    cols: dict[str, list[int]] = {}
    for c, t in texts.items():
        if (col := _column_of(t)) is not None:
            cols.setdefault(col, []).append(c)
    category = category_of(_squash(name_text).removeprefix("名称"))
    return end + 1, cols, texts, category


def _value(cells: list[str], cols: list[int], texts: dict[int, str]) -> str | None:
    """1 つの列名に複数の列が当たるとき（2 段の見出し）は、下の段の見出しを付けてつなぐ。"""
    parts = [(texts[c].split("/")[-1], cells[c]) for c in cols if c < len(cells) and cells[c]]
    if len(cols) == 1:
        return parts[0][1] if parts else None
    return " / ".join(f"{h}: {v}" for h, v in parts) or None


def _flat(text: str) -> str:
    return " ".join(line for line in text.split("\n") if line and line != SEP)


def _split_lines(cells: list[str], name_col: int) -> list[list[str]]:
    """1 つのセルに空の段落で区切って複数の会社を並べた行を分ける。

    名称の区切りの数と同じ数に区切られたセルだけを分け、ほかのセルは各行に写す。
    区切りの無い改行（折り返し）では分けない。分けた名称が「株式会社」や注記だけになる
    ときは分けない。
    """
    groups = [g.strip("\n") for g in cells[name_col].split(SEP)]
    n = len(groups)
    split_cols = [c for c, v in enumerate(cells) if v and len(v.split(SEP)) == n]
    names = [_flat(g) for g in groups]
    if n == 1 or len(split_cols) < 2 or any(_FRAGMENT.match(x.strip()) for x in names):
        return [[_flat(c) for c in cells]]
    return [
        [_flat(v.split(SEP)[i]) if c in split_cols else _flat(v) for c, v in enumerate(cells)]
        for i in range(n)
    ]


def parse_affiliated_entities(html: str) -> list[dict[str, object]]:
    """関係会社の状況の HTML から、関係会社を 1 社 1 行で返す。"""
    rows: list[dict[str, object]] = []
    category: str | None = None
    header: tuple[dict[str, list[int]], dict[int, str]] | None = None
    for kind, body in parse_blocks(html):
        if kind == "text":
            category = category_of(str(body)) or category
            continue
        table: Table = body  # type: ignore[assignment]
        found = _header(table)
        if found:
            start, cols, texts, head_category = found
            header = (cols, texts)
            category = head_category or category
        elif header and table and len(table[0]) == len(header[1]) and not _has_summary(table):
            start = 0  # 改ページで分かれた続きの表
        else:
            continue
        cols, texts = header
        name_col = cols["name"][0]
        others = [c for col in ("address", "capital", "business") for c in cols.get(col, [])]
        pending_unit: str | None = None
        for row in table[start:]:
            # rowspan で上から写した名称は前の会社の続き（関係内容だけ複数の行に分かれる表）
            if row[name_col].from_above and rows:
                extra = _value([c.text if not c.from_above else "" for c in row], cols.get("relationship", []), texts)
                if extra:
                    prev = rows[-1]
                    prev["relationship"] = " ".join(filter(None, [prev.get("relationship"), extra]))  # type: ignore[list-item]
                continue
            texts_row = [c.text for c in row]
            # 名称のセルの 1 行目が区分だけ（「(連結子会社)」の下に会社が並ぶ）なら外す
            first, _, rest = texts_row[name_col].partition("\n")
            if rest and (cat := category_of(first)):
                category = cat
                texts_row[name_col] = rest.removeprefix(SEP).lstrip("\n")
            for cells in _split_lines(texts_row, name_col):
                name = cells[name_col]
                # 区分の行: 名称の列が区分で、住所・資本金・事業が空か区分と同じ文字（colspan）
                if (cat := category_of(name)) and all(cells[c] in ("", name) for c in others):
                    category = cat
                    continue
                if not name:
                    # 資本金の列に単位だけが入った行（「千US$」）は次の会社の単位
                    cap = _value(cells, cols.get("capital", []), texts)
                    if cap and not re.search(r"[0-9０-９]", cap):
                        pending_unit = cap
                    continue
                if m := _CATEGORY_PREFIX.match(name):
                    category = m.group(1)
                    cells = cells.copy()
                    cells[name_col] = name[m.end():]
                if _NOT_COMPANY.match(_squash(cells[name_col])):
                    continue
                rec = {col: _value(cells, cs, texts) for col, cs in cols.items()}
                if pending_unit and rec.get("capital"):
                    rec["capital"] = f"{pending_unit} {rec['capital']}"
                pending_unit = None
                capital_cols = cols.get("capital", [])
                rec["capital_header"] = texts[capital_cols[0]] if capital_cols else None
                rows.append({"row_seq": len(rows), "category": category, **rec})
    return [{f: r.get(f) for f in FIELDS} for r in rows]


def _has_summary(table: Table) -> bool:
    return any(w in c.text for row in table[:2] for c in row for w in _SUMMARY_WORDS)
