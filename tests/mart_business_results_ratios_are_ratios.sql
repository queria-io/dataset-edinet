-- 比率として格納されるべき列に、比率でない値（金額・1株当たり金額など）が
-- 混入していないことを検証する。結果が0行ならテスト成功。
--
-- EDINET タクソノミには要素名と実体が食い違うものがある。例えば
-- jpcrp_cor:EquityToAssetRatioIFRSSummaryOfBusinessResults は名前に反して
-- 「１株当たり親会社所有者帰属持分（IFRS）」（unit_id = JPYPerShares）であり、
-- これを自己資本比率として拾うと 131631.99 のような値が入る。
--
-- 自己資本比率は総資産に占める自己資本の割合なので 1 を超えることはない
-- （債務超過で負値にはなる）。上限だけを検証する。

select
    doc_id,
    edinet_code,
    filer_name,
    period_end,
    year_offset,
    equity_to_asset_ratio
from {{ ref('mart_business_results') }}
where equity_to_asset_ratio > 1
