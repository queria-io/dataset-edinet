"""金融庁が年 1 回公表する EDINET タクソノミから、要素のラベル（日本語名・英語名）を取り出す。

公表ページは https://www.fsa.go.jp/search/ の「YYYY年版EDINETタクソノミの公表」。
案（パブリックコメント中）のものは取らない。
"""

from __future__ import annotations

import io
import re
import urllib.request
import zipfile

from edinet.xbrl_facts import parse_labels

BASE = "https://www.fsa.go.jp"
_PAGE = re.compile(r'href="(?:https://www\.fsa\.go\.jp)?/search/(\d{8})\.html"')
_TITLE = re.compile(r"<title>(\d{4})年版EDINETタクソノミの公表")
_ZIP = re.compile(r'href="(/search/\d{8}/1c_Taxonomy\.zip)"')


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "queria-dataset-edinet"})
    with urllib.request.urlopen(req, timeout=120) as res:
        return res.read()


def list_releases() -> list[tuple[str, str]]:
    """公表済みの年版と ZIP の URL を ``(年版, url)`` で返す。"""
    index = fetch(f"{BASE}/search/index.html").decode("utf-8", "replace")
    pages = (fetch(f"{BASE}/search/{p}.html").decode("utf-8", "replace") for p in sorted(set(_PAGE.findall(index))))
    return [
        (t.group(1), BASE + z.group(1))
        for html in pages
        if (t := _TITLE.search(html)) and (z := _ZIP.search(html))
    ]


def release_labels(raw_zip: bytes) -> list[tuple[str, str, str, str]]:
    """タクソノミの ZIP から全ラベルを ``(element_id, role, lang, label)`` で返す。"""
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        names = [
            n for n in zf.namelist() if "/taxonomy/" in n and n.endswith(("_lab.xml", "_lab-en.xml"))
        ]
        return [row for n in names for row in parse_labels(zf.read(n))]
