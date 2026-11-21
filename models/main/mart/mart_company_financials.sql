{{ config(materialized='table') }}

-- 有価証券報告書の当期の財務の値を、1 社 × 1 決算期 1 行に並べる。
-- どの要素を何の列にするかは seeds/financial_items.csv が決める（priority の小さい要素を優先）。
-- 値は財務諸表の本体の要素を先に、主要な経営指標（jpcrp_cor:*SummaryOfBusinessResults）を後に引く。
-- 売上高だけは業種ごとに本体の要素が違う（営業収益・経常収益など）ので、会社が選んで書いた経営指標を先に引く。
-- 書類一覧は取下げの操作日にも同じ doc_id を載せるので、取下げを示す行が 1 つでもある書類は使わない。
-- 先に取下げの行を外してから doc_id で畳むと、取下げ前に取得した行が残る
with docs as (
    select doc_id, edinet_code, sec_code, corporate_number, filer_name, period_start, period_end, submit_date, submit_datetime
    from {{ ref('stg_documents') }}
    where doc_type_code = '120'
        and fund_code is null
    qualify not bool_or(is_withdrawn) over (partition by doc_id)
        and row_number() over (partition by doc_id order by submit_datetime desc) = 1
),

-- 会計基準（Japan GAAP・IFRS・US GAAP）。type=5 の CSV しか無い書類には無い
standards as (
    select
        doc_id,
        any_value(trim(value, chr(9) || chr(10) || chr(13) || ' ')) as accounting_standard
    from {{ ref('stg_xbrl_facts') }}
    where element_id = 'jpdei_cor:AccountingStandardsDEI'
    group by doc_id
),

items as (
    select
        item,
        priority,
        element_id,
        unit_id,
        basis,
        nullif(accounting_standard, '') as accounting_standard
    from {{ ref('financial_items') }}
),

-- 要素は書類の会計基準のものだけを使う。IFRS へ移った年の有報には日本基準の連結の値も載っているが、
-- IFRS の行に日本基準の経常利益などを混ぜない
facts as (
    select
        f.doc_id,
        i.item,
        i.priority,
        i.basis,
        f.context_id like 'Prior1Year%' as is_prior,
        f.context_id not like '%NonConsolidatedMember' as is_consolidated,
        f.numeric_value
    from {{ ref('mart_financial_facts') }} as f
    join items as i
        on f.element_id = i.element_id
        and f.unit_id = i.unit_id
    left join standards as s
        on f.doc_id = s.doc_id
    where f.doc_type_code = '120'
        and f.context_id in (
            'CurrentYearDuration', 'CurrentYearInstant',
            'CurrentYearDuration_NonConsolidatedMember', 'CurrentYearInstant_NonConsolidatedMember',
            'Prior1YearDuration', 'Prior1YearInstant',
            'Prior1YearDuration_NonConsolidatedMember', 'Prior1YearInstant_NonConsolidatedMember'
        )
        and f.numeric_value is not null
        and f.doc_id in (select doc_id from docs)
        and (i.accounting_standard is null or s.accounting_standard is null or i.accounting_standard = s.accounting_standard)
),

-- 連結の総資産・純資産がある書類は連結、無い書類は個別の値を使う。1 書類の中で混ぜない。
-- 従業員数などは連結財務諸表を作らない会社でも連結のコンテキストに書かれることがあるので、判定に使わない
doc_basis as (
    select doc_id, bool_or(is_consolidated) as is_consolidated
    from facts
    where item in ('total_assets', 'net_assets') and not is_prior
    group by doc_id
),

-- 1 書類 × 1 列に 1 つの値。株式数と配当は提出会社の値（個別）しか無いので、basis = company で個別を引く。
-- 連結財務諸表の無い書類は個別の値を先に使い、個別のコンテキストに無い列（連結のコンテキストに書かれた
-- 従業員数など）だけ連結のコンテキストの値を使う。連結財務諸表の無い書類の値はどちらも提出会社の値
picked as (
    select
        f.doc_id,
        f.item,
        f.is_prior,
        arg_min(f.numeric_value, (f.is_consolidated, f.priority)) as value
    from facts as f
    join doc_basis as b using (doc_id)
    where case
        when f.basis = 'company' then not f.is_consolidated
        when b.is_consolidated then f.is_consolidated
        else true
    end
    group by all
),

-- 前期の値（Prior1Year）は、増収率と ROE・ROA の期首の値に使う。同じ書類に載った前期の値なので、
-- 遡って修正した会社でも当期と同じ基準で比べられる
wide as (
    select
        doc_id,
        {%- for item in [
            'revenue', 'cost_of_sales', 'gross_profit', 'sga_expenses', 'operating_income',
            'ordinary_income', 'profit_before_tax', 'net_income',
            'total_assets', 'current_assets', 'total_liabilities', 'current_liabilities',
            'net_assets', 'owners_equity', 'subscription_rights', 'non_controlling_interests',
            'cash_and_equivalents', 'operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow',
            'capital_expenditure', 'depreciation', 'research_and_development_expenses',
            'number_of_employees', 'total_issued_shares', 'basic_eps', 'bps', 'dividend_per_share'
        ] %}
        max(value) filter (where item = '{{ item }}' and not is_prior) as {{ item }},
        {%- endfor %}
        {%- for item in ['revenue', 'total_assets', 'net_assets', 'owners_equity', 'subscription_rights', 'non_controlling_interests'] %}
        max(value) filter (where item = '{{ item }}' and is_prior) as {{ item }}_prior{{ ',' if not loop.last }}
        {%- endfor %}
    from picked
    group by doc_id
),

-- 日本基準の自己資本は、純資産から新株予約権と非支配株主持分を引いた額。
-- IFRS・米国基準は親会社の所有者に帰属する持分の要素をそのまま使う
equity as (
    select
        w.doc_id,
        {%- for suffix in ['', '_prior'] %}
        coalesce(
            w.owners_equity{{ suffix }},
            if(
                coalesce(s.accounting_standard, 'Japan GAAP') = 'Japan GAAP',
                w.net_assets{{ suffix }} - coalesce(w.subscription_rights{{ suffix }}, 0)
                    - coalesce(w.non_controlling_interests{{ suffix }}, 0),
                null
            )
        ) as owners_equity{{ suffix }}{{ ',' if not loop.last }}
        {%- endfor %}
    from wide as w
    left join standards as s using (doc_id)
),

shaped as (
    select
        d.edinet_code,
        d.sec_code,
        d.corporate_number,
        d.filer_name,
        year(d.period_end) as fiscal_year,
        d.period_start,
        d.period_end,
        s.accounting_standard,
        b.is_consolidated,
        w.revenue,
        w.cost_of_sales,
        w.gross_profit,
        w.sga_expenses,
        w.operating_income,
        w.ordinary_income,
        w.profit_before_tax,
        w.net_income,
        w.total_assets,
        w.current_assets,
        w.total_liabilities,
        w.current_liabilities,
        w.net_assets,
        e.owners_equity,
        w.cash_and_equivalents,
        w.operating_cash_flow,
        w.investing_cash_flow,
        w.financing_cash_flow,
        w.capital_expenditure,
        w.depreciation,
        w.research_and_development_expenses,
        w.number_of_employees::bigint as number_of_employees,
        w.total_issued_shares::bigint as total_issued_shares,
        w.basic_eps,
        w.bps,
        w.dividend_per_share,
        w.revenue_prior,
        w.total_assets_prior,
        e.owners_equity_prior,
        d.doc_id,
        d.submit_date
    from docs as d
    join wide as w using (doc_id)
    join equity as e using (doc_id)
    join doc_basis as b using (doc_id)
    left join standards as s using (doc_id)
    -- 同じ決算期に有報が複数ある会社は、値を取れた書類のうち最後に提出した書類を使う
    qualify row_number() over (
        partition by d.edinet_code, d.period_end order by d.submit_datetime desc, d.doc_id desc
    ) = 1
)

-- ROE・ROA は期首と期末の平均で割る（決算短信の自己資本当期純利益率と同じ）
select
    * exclude (revenue_prior, total_assets_prior, owners_equity_prior, doc_id, submit_date),
    round(operating_income / nullif(revenue, 0), 4) as operating_margin,
    round(net_income / nullif((owners_equity + owners_equity_prior) / 2, 0), 4) as roe,
    round(net_income / nullif((total_assets + total_assets_prior) / 2, 0), 4) as roa,
    round(owners_equity / nullif(total_assets, 0), 4) as equity_ratio,
    round(revenue / nullif(revenue_prior, 0) - 1, 4) as revenue_growth,
    doc_id,
    submit_date,
    'https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?' || doc_id as source_url
from shaped
