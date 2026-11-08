"""金融庁が年 1 回公表する EDINET タクソノミから、要素のラベル（日本語名・英語名）を取り出す。

公表ページは https://www.fsa.go.jp/search/ の「YYYY年版EDINETタクソノミの公表」。
案（パブリックコメント中）のものは取らない。一覧のページには 2022 年版以降しか載っていないので、
EDINET で閲覧できる期間（10 年）にかかる古い年版のページは PAST_PAGES に置く。
"""

from __future__ import annotations

import io
import re
import zipfile

from edinet.codelist import _download as fetch  # 一時的なエラーをリトライする
from edinet.xbrl_facts import parse_labels

BASE = "https://www.fsa.go.jp"
_PAGE = re.compile(r'href="(?:https://www\.fsa\.go\.jp)?/search/(\d{8})\.html"')
_TITLE = re.compile(r"<title>[^<]*EDINETタクソノミ[^<]*の公表")
_ZIP = re.compile(r'href="(/search/\d{8}/1c(?:_Taxonomy)?\.zip)"')
# 2015〜2021 年版の公表ページ
# 2018-03-16 は年版の途中の改訂（CG・IFRS 詳細タグ付け対応版。名前空間の日付は 2018-03-31）
PAST_PAGES = ("20150310", "20160314", "20170228", "20180228", "20180316", "20190228", "20191101", "20201110")
# ZIP の中のパス …/taxonomy/<モジュール>/<日付>/… から、インスタンスの名前空間と同じ部分を取る
_NAMESPACE = re.compile(r"/taxonomy/([^/]+/\d{4}-\d{2}-\d{2})/")


def list_releases(skip: set[str] = frozenset()) -> list[tuple[str, str]]:
    """公表済みのタクソノミを ``(公表ページの日付, ZIP の URL)`` で返す。案は除く。

    年版の途中の改訂もあるので、公表ページの日付（20251111 など）で年版を区別する。
    ``skip`` の日付（取り込み済み）のページは読まない。
    """
    index = fetch(f"{BASE}/search/index.html").decode("utf-8", "replace")
    pages = ((p, fetch(f"{BASE}/search/{p}.html").decode("utf-8", "replace")) for p in sorted({*PAST_PAGES, *_PAGE.findall(index)} - skip))
    return [
        (p, BASE + z.group(1))
        for p, html in pages
        if (t := _TITLE.search(html)) and "（案）" not in t.group(0) and (z := _ZIP.search(html))
    ]


def release_labels(raw_zip: bytes) -> list[tuple[str, str, str, str, str, str]]:
    """タクソノミの ZIP から全ラベルを ``(namespace, file, element_id, role, lang, label)`` で返す。

    同じ名前空間に新旧のラベルファイル（jpdei_2013-08-31_lab.xml と jpdei_2024-11-01_lab.xml）が
    並ぶことがあるので、ファイル名も返す。

    ``namespace`` は ``jppfs/2025-11-01`` の形で、書類のインスタンスの名前空間
    (``…/taxonomy/jppfs/2025-11-01/jppfs_cor``) と突き合わせて、書類と同じ年版のラベルを選ぶのに使う。
    """
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        names = [
            (m.group(1), n)
            for n in zf.namelist()
            if n.endswith(("_lab.xml", "_lab-en.xml")) and (m := _NAMESPACE.search(n))
        ]
        return [(ns, n.rsplit("/", 1)[-1], *row) for ns, n in names for row in parse_labels(zf.read(n))]
