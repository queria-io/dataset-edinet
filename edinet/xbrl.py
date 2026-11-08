"""書類取得 API type=1 の ZIP から、XBRL インスタンスと会社のラベルを取り出す。

type=5 の CSV は EDINET がこのインスタンスを 1 値 1 行に変換したもので、テキストブロックの
HTML・会社独自の項目やセグメントの名前・丸めの桁が落ちる。財務の値はこの ZIP から取る。
"""

from __future__ import annotations

import io
import re
import zipfile

# 表の構造に要らない属性。生の HTML の大半はインラインの style で、
# これを外すと保存する大きさが数分の 1 になる
_DROP_ATTRS = re.compile(r'\s(?:style|class|id|width|height|valign|align)="[^"]*"')


def label_files_from_zip(raw: bytes) -> list[bytes]:
    """ZIP から会社独自の要素・メンバーのラベル（日本語・英語）を返す。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return [
            zf.read(n)
            for n in zf.namelist()
            if n.startswith("XBRL/PublicDoc/") and n.endswith(("_lab.xml", "_lab-en.xml"))
        ]


def instance_from_zip(raw: bytes) -> bytes | None:
    """ZIP から本文の XBRL インスタンスを返す。無ければ None。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.startswith("XBRL/PublicDoc/") and n.endswith(".xbrl")
        ]
        return zf.read(names[0]) if names else None


def clean_html(html: str) -> str:
    """表の構造に要らない属性を外す。"""
    return _DROP_ATTRS.sub("", html)
