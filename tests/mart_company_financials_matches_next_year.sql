-- 各年の当期純利益を、翌年の有報が前期の値として載せた額と比べる。
-- 年度を取り違えたり（period_end の扱いの誤り）、決算期ごとに選ぶ書類を取り違えたりすると、
-- ほぼすべての行が食い違う。会計方針の変更などで前期の値を遡って修正する会社もあるので、
-- 比べた行の 5% を超えて食い違ったときだけ、食い違った行を返して失敗する。
-- 総資産は 2018 年度の税効果会計の改正（繰延税金資産の表示の変更）で多くの会社が前期の値を
-- 組み替えたため、比べる値に使わない。

with items as (
    select element_id, priority, accounting_standard
    from {{ ref('financial_items') }}
    where item = 'net_income'
),

ordered as (
    select
        *,
        lead(doc_id) over (partition by edinet_code order by period_end) as next_doc_id,
        lead(is_consolidated) over (partition by edinet_code order by period_end) as next_is_consolidated,
        lead(accounting_standard) over (partition by edinet_code order by period_end) as next_accounting_standard
    from {{ ref('mart_company_financials') }}
),

prior_values as (
    select
        f.doc_id,
        f.context_id not like '%NonConsolidatedMember' as is_consolidated,
        i.accounting_standard,
        arg_min(f.numeric_value, i.priority) as prior_value
    from {{ ref('mart_financial_facts') }} as f
    join items as i using (element_id)
    where f.doc_type_code = '120'
        and f.unit_id = 'JPY'
        and f.context_id in ('Prior1YearDuration', 'Prior1YearDuration_NonConsolidatedMember')
        and f.numeric_value is not null
    group by all
),

pairs as (
    select
        o.edinet_code,
        o.filer_name,
        o.period_end,
        o.net_income,
        o.next_doc_id,
        p.prior_value
    from ordered as o
    join prior_values as p
        on p.doc_id = o.next_doc_id
        and p.is_consolidated = o.is_consolidated
        and p.accounting_standard = o.accounting_standard
    where o.is_consolidated = o.next_is_consolidated
        and o.accounting_standard = o.next_accounting_standard
),

mismatched as (
    select *
    from pairs
    where abs(net_income - prior_value) > greatest(abs(prior_value) * 0.001, 1000000)
)

select *
from mismatched
where (select count(*) from mismatched) > 0.05 * (select count(*) from pairs)
