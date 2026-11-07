"""type=1 のインスタンスから読んだ値と、type=5 の CSV を書類ごとに突き合わせる（試作の確認用）。

    EDINET_API_KEY=... PYTHONPATH=. uv run python scripts/compare_xbrl_csv.py S100YM3K S100YLZQ

出力: 書類ごとの件数と差分、CSV の列（相対年度・連結/個別・期間/時点・単位）が
インスタンスの何から決まるかの対応表。
"""

from __future__ import annotations

import os
import sys
from collections import Counter

from edinet.client import EdinetClient
from edinet.xbrl import instance_from_zip
from edinet.xbrl_facts import parse_instance

# CSV の列: 要素ID, 項目名, コンテキストID, 相対年度, 連結・個別, 期間・時点, ユニットID, 単位, 値


def _csv_rows(client: EdinetClient, doc_id: str) -> list[list[str | None]]:
    # 監査報告書 (jpaud) はインスタンスが別なので外す
    return [v for t, _, v in client.get_document_csv(doc_id) if not t.startswith("jpaud")]


def compare(client: EdinetClient, doc_id: str) -> Counter:
    instance = instance_from_zip(client.get_document_xbrl(doc_id) or b"")
    _, contexts, facts = parse_instance(instance or b"")
    csv_rows = _csv_rows(client, doc_id)
    by_key = {(f.element_id, f.context_id): f for f in facts}
    csv_keys = {(r[0], r[2]): r for r in csv_rows}
    only_csv = csv_keys.keys() - by_key.keys()
    only_xbrl = by_key.keys() - csv_keys.keys()
    differ = [
        k
        for k in csv_keys.keys() & by_key.keys()
        if not k[0].endswith("TextBlock")
        and not by_key[k].is_nil
        and (csv_keys[k][8] or "") != (by_key[k].value or "")
    ]
    print(f"{doc_id}: csv {len(csv_rows)} xbrl {len(facts)} only_csv {len(only_csv)} "
          f"only_xbrl {len(only_xbrl)} value_differs {len(differ)}")
    for k in [*sorted(only_csv)[:3], *sorted(only_xbrl)[:3], *differ[:5]]:
        print("  ", k, csv_keys.get(k, [None] * 9)[8:9], by_key.get(k) and by_key[k].value)
    # CSV の列がインスタンスの何で決まるかを数える
    out: Counter = Counter()
    for k in csv_keys.keys() & by_key.keys():
        r, f, c = csv_keys[k], by_key[k], contexts[k[1]]
        prefix = k[1].split("_")[0]
        out[("relative_year", prefix, r[3])] += 1
        out[("consolidated", any(m.endswith("NonConsolidatedMember") for _, m in c.members), r[4])] += 1
        out[("period", c.instant is not None, r[5])] += 1
        out[("unit", f.unit_id, r[7])] += 1
        out[("nil", f.is_nil, r[8] if f.is_nil else "-")] += 1
    return out


def main() -> None:
    client = EdinetClient(os.environ["EDINET_API_KEY"])
    total: Counter = Counter()
    for doc_id in sys.argv[1:]:
        total += compare(client, doc_id)
    for key, n in sorted(total.items(), key=str):
        print(n, *key, sep="\t")


if __name__ == "__main__":
    main()
