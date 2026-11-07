"""テキストブロックの HTML を、表の外の段落と表（セルの 2 次元配列）の並びに分ける。

rowspan・colspan は展開し、結合されたセルは同じ文字列を各マスに置く。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

Block = tuple[str, str | list[list[str]]]  # ("text", 段落) か ("table", 行の配列)

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br"}
_SPACES = re.compile(r"[\s　]+")


def normalize_text(s: str) -> str:
    return _SPACES.sub(" ", s).strip()


@dataclass
class _Table:
    rows: list[list[tuple[str, int, int]]] = field(default_factory=list)
    cell: list[str] | None = None
    span: tuple[int, int] = (1, 1)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.text: list[str] = []
        self.tables: list[_Table] = []  # 入れ子の表に備えて積む

    def _flush_text(self) -> None:
        t = normalize_text("".join(self.text))
        if t:
            self.blocks.append(("text", t))
        self.text = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table":
            if not self.tables:
                self._flush_text()
            self.tables.append(_Table())
        elif not self.tables:
            if tag in _BLOCK_TAGS:
                self._flush_text()
        elif tag == "tr":
            self.tables[-1].rows.append([])
        elif tag in ("td", "th"):
            t = self.tables[-1]
            t.cell = []
            t.span = (_int(a.get("rowspan")), _int(a.get("colspan")))
        elif tag == "br" and self.tables[-1].cell is not None:
            self.tables[-1].cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if not self.tables:
            if tag in _BLOCK_TAGS:
                self._flush_text()
            return
        t = self.tables[-1]
        if tag in ("td", "th") and t.cell is not None:
            if not t.rows:
                t.rows.append([])
            t.rows[-1].append((normalize_text("".join(t.cell)), *t.span))
            t.cell = None
        elif tag == "table":
            done = self.tables.pop()
            grid = _expand(done.rows)
            if self.tables:
                # 入れ子の表はセルの文字として親に流す
                parent = self.tables[-1]
                if parent.cell is not None:
                    parent.cell.append(" ".join(" ".join(r) for r in grid))
            elif grid:
                self.blocks.append(("table", grid))

    def handle_data(self, data: str) -> None:
        if self.tables:
            t = self.tables[-1]
            if t.cell is not None:
                t.cell.append(data)
        else:
            self.text.append(data)


def _int(v: str | None) -> int:
    try:
        return max(1, int(v or 1))
    except ValueError:
        return 1


def _expand(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """rowspan・colspan を展開して長方形の表にする。"""
    grid: dict[tuple[int, int], str] = {}
    for r, cells in enumerate(rows):
        c = 0
        for text, rowspan, colspan in cells:
            while (r, c) in grid:
                c += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r + dr, c + dc)] = text
            c += colspan
    if not grid:
        return []
    height = max(r for r, _ in grid) + 1
    width = max(c for _, c in grid) + 1
    return [[grid.get((r, c), "") for c in range(width)] for r in range(height)]


def parse_blocks(html: str) -> list[Block]:
    """HTML を文書順の段落と表に分ける。"""
    p = _Parser()
    p.feed(html)
    p.close()
    p._flush_text()
    return p.blocks
