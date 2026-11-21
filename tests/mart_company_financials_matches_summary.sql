-- 財務諸表の本体から引いた値と、同じ書類の「主要な経営指標等の推移」の値を比べる。
-- 有報は同じ数字を 2 か所に載せているので、対応表の誤りがあれば大きく食い違う。
-- 会社の記載の誤り（経営指標の欄だけ千円単位で書いたものなど）も数件あるため、
-- 比べた値の 1% を超えて食い違ったときだけ、食い違った行を返して失敗する。

with pairs(item, summary_element_id) as (
    values
        ('total_assets', 'jpcrp_cor:TotalAssetsSummaryOfBusinessResults'),
        ('total_assets', 'jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults'),
        ('net_assets', 'jpcrp_cor:NetAssetsSummaryOfBusinessResults'),
        ('owners_equity', 'jpcrp_cor:EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults'),
        ('net_income', 'jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults'),
        ('net_income', 'jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults'),
        ('ordinary_income', 'jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults'),
        ('cash_and_equivalents', 'jpcrp_cor:CashAndCashEquivalentsSummaryOfBusinessResults'),
        ('cash_and_equivalents', 'jpcrp_cor:CashAndCashEquivalentsIFRSSummaryOfBusinessResults'),
        ('operating_cash_flow', 'jpcrp_cor:NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults'),
        ('operating_cash_flow', 'jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults'),
        ('investing_cash_flow', 'jpcrp_cor:NetCashProvidedByUsedInInvestingActivitiesSummaryOfBusinessResults'),
        ('investing_cash_flow', 'jpcrp_cor:CashFlowsFromUsedInInvestingActivitiesIFRSSummaryOfBusinessResults'),
        ('financing_cash_flow', 'jpcrp_cor:NetCashProvidedByUsedInFinancingActivitiesSummaryOfBusinessResults'),
        ('financing_cash_flow', 'jpcrp_cor:CashFlowsFromUsedInFinancingActivitiesIFRSSummaryOfBusinessResults')
),

rows as (
    unpivot (
        select
            doc_id, edinet_code, filer_name, period_end, is_consolidated,
            total_assets, net_assets, owners_equity, net_income, ordinary_income,
            cash_and_equivalents, operating_cash_flow, investing_cash_flow, financing_cash_flow
        from {{ ref('mart_company_financials') }}
    )
    on total_assets, net_assets, owners_equity, net_income, ordinary_income,
        cash_and_equivalents, operating_cash_flow, investing_cash_flow, financing_cash_flow
    into name item value value
),

summary as (
    select f.doc_id, p.item, f.context_id not like '%NonConsolidatedMember' as is_consolidated, max(f.numeric_value) as summary_value
    from {{ ref('mart_financial_facts') }} as f
    join pairs as p on f.element_id = p.summary_element_id
    where f.doc_type_code = '120'
        and f.unit_id = 'JPY'
        and f.context_id in (
            'CurrentYearDuration', 'CurrentYearInstant',
            'CurrentYearDuration_NonConsolidatedMember', 'CurrentYearInstant_NonConsolidatedMember'
        )
    group by all
),

compared as (
    select r.*, s.summary_value
    from rows as r
    join summary as s using (doc_id, item, is_consolidated)
),

mismatched as (
    select *
    from compared
    where abs(value - summary_value) > greatest(abs(summary_value) * 0.001, 1000000)
)

select *
from mismatched
where (select count(*) from mismatched) > 0.01 * (select count(*) from compared)
