{{ config(materialized='table') }}

-- 有報「主要な経営指標等の推移」(jpcrp_cor:*SummaryOfBusinessResults) を
-- 「企業×会計年度×標準指標」のワイドテーブルに pivot した正規化層。
--
-- コンテキストIDから年度・連結区分を導出する（連結・個別列は summary 行では
-- 'その他' 固定で使えないため）:
--   - 年度オフセット: CurrentYear*=0 / Prior1Year*=-1 … Prior4Year*=-4
--     （フロー項目=Duration・ストック項目=Instant が同じ接頭辞を共有するので統一できる）
--   - 連結/個別: _NonConsolidatedMember 接尾辞が個別、無印が連結
-- 連結優先（連結があれば連結のみ、無ければ個別）で 1 書類×会計年度=1 行にする。
-- 会計基準横断: IFRS 採用企業は要素名が異なる（例 RevenueIFRS…）ため JP GAAP 名と
-- coalesce して同じ列に寄せる。
with summary as (
    select
        doc_id,
        edinet_code,
        sec_code,
        corporate_number,
        filer_name,
        period_end,
        element_id,
        unit_id,
        numeric_value,
        context_id not like '%NonConsolidatedMember%' as is_consolidated,
        case
            when context_id like 'CurrentYear%' then 0
            when context_id like 'Prior1Year%' then -1
            when context_id like 'Prior2Year%' then -2
            when context_id like 'Prior3Year%' then -3
            when context_id like 'Prior4Year%' then -4
        end as year_offset
    from {{ ref('stg_financial_facts') }}
    where element_id like 'jpcrp\_cor:%SummaryOfBusinessResults' escape '\'
),

-- 提出日時点など5期比較外（year_offset 不明）は除外
in_range as (
    select * from summary where year_offset is not null
),

-- 連結優先: 書類に連結行があれば連結のみ、無ければ個別のみを採用
doc_pref as (
    select doc_id, max(is_consolidated::int) as has_consolidated
    from in_range
    group by doc_id
),

picked as (
    select r.*
    from in_range as r
    join doc_pref as p using (doc_id)
    where (p.has_consolidated = 1 and r.is_consolidated)
       or (p.has_consolidated = 0 and not r.is_consolidated)
)

select
    doc_id,
    edinet_code,
    sec_code,
    corporate_number,
    filer_name,
    period_end,
    year_offset,
    year(period_end) + year_offset as fiscal_year,
    coalesce(
        max(case when element_id = 'jpcrp_cor:RevenueIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:NetSalesSummaryOfBusinessResults' then numeric_value end)
    ) as net_sales,
    max(case when element_id = 'jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults' then numeric_value end) as ordinary_income,
    max(case when element_id = 'jpcrp_cor:ProfitLossBeforeTaxIFRSSummaryOfBusinessResults' then numeric_value end) as profit_before_tax,
    max(case when element_id = 'jpcrp_cor:NetIncomeLossSummaryOfBusinessResults' then numeric_value end) as net_income,
    coalesce(
        max(case when element_id = 'jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults' then numeric_value end)
    ) as profit_attributable_to_owners,
    coalesce(
        max(case when element_id = 'jpcrp_cor:ComprehensiveIncomeAttributableToOwnersOfParentIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:ComprehensiveIncomeSummaryOfBusinessResults' then numeric_value end)
    ) as comprehensive_income,
    coalesce(
        max(case when element_id = 'jpcrp_cor:EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:NetAssetsSummaryOfBusinessResults' then numeric_value end)
    ) as net_assets,
    coalesce(
        max(case when element_id = 'jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:TotalAssetsSummaryOfBusinessResults' then numeric_value end)
    ) as total_assets,
    max(case when element_id = 'jpcrp_cor:CapitalStockSummaryOfBusinessResults' then numeric_value end) as capital_stock,
    max(case when element_id = 'jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults' then numeric_value end) as total_issued_shares,
    -- IFRS 採用企業の1株当たり親会社所有者帰属持分は、要素名に反して
    -- EquityToAssetRatioIFRS… に格納される（item_name「１株当たり親会社所有者帰属持分
    -- （IFRS）」・unit_id JPYPerShares）。JPY 建てのものだけをこの列に寄せる。
    coalesce(
        max(case when element_id = 'jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:EquityToAssetRatioIFRSSummaryOfBusinessResults' and unit_id = 'JPYPerShares' then numeric_value end)
    ) as net_assets_per_share,
    coalesce(
        max(case when element_id = 'jpcrp_cor:BasicEarningsLossPerShareIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults' then numeric_value end)
    ) as basic_eps,
    coalesce(
        max(case when element_id = 'jpcrp_cor:DilutedEarningsLossPerShareIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:DilutedEarningsPerShareSummaryOfBusinessResults' then numeric_value end)
    ) as diluted_eps,
    -- EquityToAssetRatioIFRS… は要素名に反して自己資本比率ではなく1株当たり持分
    -- （unit_id JPYPerShares）なのでここでは使わない。IFRS の自己資本比率に相当するのは
    -- RatioOfOwnersEquityToGrossAssetsIFRS…（unit_id pure）。
    coalesce(
        max(case when element_id = 'jpcrp_cor:RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults' then numeric_value end)
    ) as equity_to_asset_ratio,
    coalesce(
        max(case when element_id = 'jpcrp_cor:RateOfReturnOnEquityIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:RateOfReturnOnEquitySummaryOfBusinessResults' then numeric_value end)
    ) as roe,
    coalesce(
        max(case when element_id = 'jpcrp_cor:PriceEarningsRatioIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:PriceEarningsRatioSummaryOfBusinessResults' then numeric_value end)
    ) as per,
    max(case when element_id = 'jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults' then numeric_value end) as dividend_per_share,
    max(case when element_id = 'jpcrp_cor:InterimDividendPaidPerShareSummaryOfBusinessResults' then numeric_value end) as interim_dividend_per_share,
    max(case when element_id = 'jpcrp_cor:PayoutRatioSummaryOfBusinessResults' then numeric_value end) as payout_ratio,
    coalesce(
        max(case when element_id = 'jpcrp_cor:CashAndCashEquivalentsIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:CashAndCashEquivalentsSummaryOfBusinessResults' then numeric_value end)
    ) as cash_and_equivalents,
    coalesce(
        max(case when element_id = 'jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults' then numeric_value end)
    ) as operating_cash_flow,
    coalesce(
        max(case when element_id = 'jpcrp_cor:CashFlowsFromUsedInInvestingActivitiesIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:NetCashProvidedByUsedInInvestingActivitiesSummaryOfBusinessResults' then numeric_value end)
    ) as investing_cash_flow,
    coalesce(
        max(case when element_id = 'jpcrp_cor:CashFlowsFromUsedInFinancingActivitiesIFRSSummaryOfBusinessResults' then numeric_value end),
        max(case when element_id = 'jpcrp_cor:NetCashProvidedByUsedInFinancingActivitiesSummaryOfBusinessResults' then numeric_value end)
    ) as financing_cash_flow
from picked
group by doc_id, edinet_code, sec_code, corporate_number, filer_name, period_end, year_offset
