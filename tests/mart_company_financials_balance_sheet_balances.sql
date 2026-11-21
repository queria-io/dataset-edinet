-- 総資産 = 負債 + 純資産 が成り立たない行を返す。結果が 0 行ならテスト成功。
-- 3 つの値は同じ書類の貸借対照表の別々の要素から引いているので、対応表で別の要素を拾ったり、
-- 連結と個別を混ぜたり、外貨建ての値を混ぜたりすると、ここで食い違う。
-- 百万円単位で開示した値の端数の差は認める（総資産の 0.1% か 100 万円の大きい方まで）。

select
    edinet_code,
    filer_name,
    period_end,
    accounting_standard,
    total_assets,
    total_liabilities,
    net_assets,
    doc_id
from {{ ref('mart_company_financials') }}
where abs(total_assets - total_liabilities - net_assets) > greatest(abs(total_assets) * 0.001, 1000000)
