"""金融庁が年 1 回公表する EDINET タクソノミから、要素のラベル（日本語名・英語名）を取り出す。

公表ページは https://www.fsa.go.jp/search/ の「YYYY年版EDINETタクソノミの公表」。
案（パブリックコメント中）のものは取らない。一覧のページには 2022 年版以降しか載っていないので、
EDINET で閲覧できる期間（10 年）にかかる古い年版のページは PAST_PAGES に置く。
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
_ZIP = re.compile(r'href="(/search/\d{8}/1c(?:_Taxonomy)?\.zip)"')
# 2015〜2021 年版の公表ページ
PAST_PAGES = ("20150310", "20160314", "20170228", "20180228", "20190228", "20191101", "20201110")
# ZIP の中のパス …/taxonomy/<モジュール>/<日付>/… から、インスタンスの名前空間と同じ部分を取る
_NAMESPACE = re.compile(r"/taxonomy/([^/]+/\d{4}-\d{2}-\d{2})/")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "queria-dataset-edinet"})
    with urllib.request.urlopen(req, timeout=120) as res:
        return res.read()


def list_releases() -> list[tuple[str, str]]:
    """公表済みの年版と ZIP の URL を ``(年版, url)`` で返す。"""
    index = fetch(f"{BASE}/search/index.html").decode("utf-8", "replace")
    pages = (fetch(f"{BASE}/search/{p}.html").decode("utf-8", "replace") for p in sorted({*PAST_PAGES, *_PAGE.findall(index)}))
    return [
        (t.group(1), BASE + z.group(1))
        for html in pages
        if (t := _TITLE.search(html)) and (z := _ZIP.search(html))
    ]


def release_labels(raw_zip: bytes) -> list[tuple[str, str, str, str, str]]:
    """タクソノミの ZIP から全ラベルを ``(namespace, element_id, role, lang, label)`` で返す。

    ``namespace`` は ``jppfs/2025-11-01`` の形で、書類のインスタンスの名前空間
    (``…/taxonomy/jppfs/2025-11-01/jppfs_cor``) と突き合わせて、書類と同じ年版のラベルを選ぶのに使う。
    """
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        names = [
            (m.group(1), n)
            for n in zf.namelist()
            if n.endswith(("_lab.xml", "_lab-en.xml")) and (m := _NAMESPACE.search(n))
        ]
        return [(ns, *row) for ns, n in names for row in parse_labels(zf.read(n))]
