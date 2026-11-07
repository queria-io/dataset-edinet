"""XBRL インスタンスから値を 1 値 1 行で取り出す。

XBRL の規格どおりに context（期間と軸・メンバー）と unit を読み、値に付ける。
会社ごとの決まりは持たない。
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_NOT_FACT = {"context", "unit", "schemaRef", "roleRef", "arcroleRef", "footnoteLink"}


@dataclass(frozen=True)
class Context:
    start: str | None
    end: str | None
    instant: str | None
    members: tuple[tuple[str, str], ...]  # (軸, メンバー)


@dataclass(frozen=True)
class Fact:
    element_id: str
    context_id: str
    unit_id: str | None
    decimals: str | None
    is_nil: bool
    value: str | None


def _qname(tag: str, prefixes: dict[str, str]) -> str:
    ns, _, local = tag.lstrip("{").rpartition("}")
    return f"{prefixes.get(ns, ns)}:{local}"


def _text(el: ET.Element, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


def _context(el: ET.Element) -> Context:
    p = f"{{{XBRLI}}}period/{{{XBRLI}}}"
    members = tuple(
        (m.get("dimension", ""), (m.text or "").strip())
        for m in el.iter(f"{{{XBRLDI}}}explicitMember")
    )
    return Context(_text(el, p + "startDate"), _text(el, p + "endDate"), _text(el, p + "instant"), members)


def parse_instance(instance: bytes) -> tuple[dict[str, Context], list[Fact]]:
    """インスタンスから (context ID → Context, 値の並び) を返す。値は文書順。"""
    prefixes: dict[str, str] = {}
    contexts: dict[str, Context] = {}
    facts: list[Fact] = []
    depth = 0
    for event, item in ET.iterparse(io.BytesIO(instance), events=("start-ns", "start", "end")):
        if event == "start-ns":
            prefix, uri = item
            prefixes.setdefault(uri, prefix)
            continue
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth != 1:  # ルート直下だけを見る
            continue
        local = item.tag.rpartition("}")[2]
        if local == "context":
            contexts[item.get("id", "")] = _context(item)
        elif local not in _NOT_FACT:
            facts.append(
                Fact(
                    element_id=_qname(item.tag, prefixes),
                    context_id=item.get("contextRef", ""),
                    unit_id=item.get("unitRef"),
                    decimals=item.get("decimals"),
                    is_nil=item.get(XSI_NIL) == "true",
                    value=item.text,
                )
            )
        item.clear()
    return contexts, facts
