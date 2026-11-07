{{ config(materialized='view') }}

-- 有報・半期報告書の財務の値を、1 要素 × 1 コンテキスト 1 行の縦持ちで持つ。
-- 元は書類取得 API type=1 の XBRL インスタンス。type=1 を取り直すまでの書類だけ、
-- 以前に取った type=5 の CSV（EDINET がインスタンスを変換したもの）の行を使う。
-- 相対年度・連結/個別・期間/時点・単位は、CSV と同じ値になるよう context から作る。
with docs as (
    -- documents は同一 doc_id が複数の取得日に現れることがある（一覧の再掲）。
    -- 結合でファクトが増殖しないよう doc_id 単位に 1 行へ畳む。
    select *
    from {{ ref('stg_documents') }}
    qualify row_number() over (partition by doc_id order by submit_datetime desc) = 1
),

xbrl_docs as (
    select distinct doc_id from {{ ref('raw_xbrl_facts') }}
),

-- 標準の要素の名前は、その要素を持つ最も新しい年版のタクソノミから取る
taxonomy_names as (
    select element_id, label
    from {{ ref('raw_taxonomy_labels') }}
    where role = 'http://www.xbrl.org/2003/role/label' and lang = 'ja'
    qualify row_number() over (partition by element_id order by release desc) = 1
),

company_names as (
    select doc_id, element_id, label
    from {{ ref('raw_xbrl_labels') }}
    where role = 'http://www.xbrl.org/2003/role/label' and lang = 'ja'
    qualify row_number() over (partition by doc_id, element_id order by label) = 1
),

xbrl as (
    select
        f.doc_id,
        f.element_id,
        coalesce(cn.label, tn.label) as item_name,
        f.context_id,
        -- context ID の頭は EDINET の命名規約で相対年度を表す
        case split_part(f.context_id, '_', 1)
            when 'CurrentYearDuration' then '当期'
            when 'CurrentYearInstant' then '当期末'
            when 'Prior1YearDuration' then '前期'
            when 'Prior1YearInstant' then '前期末'
            when 'Prior2YearDuration' then '前々期'
            when 'Prior2YearInstant' then '前々期末'
            when 'Prior3YearDuration' then '三期前'
            when 'Prior3YearInstant' then '三期前時点'
            when 'Prior4YearDuration' then '四期前'
            when 'Prior4YearInstant' then '四期前時点'
            when 'FilingDateInstant' then '提出日時点'
            when 'InterimDuration' then '当中間期'
            when 'InterimInstant' then '当中間期末'
            when 'Prior1InterimDuration' then '前中間期'
            when 'Prior1InterimInstant' then '前中間期末'
        end as relative_year,
        -- CSV と同じく、日本基準の財務諸表 (jppfs_cor) だけを連結 / 個別に分ける
        case
            when f.element_id not like 'jppfs\_cor:%' escape '\' then 'その他'
            when c.members like '%NonConsolidatedMember%' then '個別'
            else '連結'
        end as consolidated_individual,
        case when c.instant is not null then '時点' else '期間' end as period_instant,
        coalesce(f.unit_id, '－') as unit_id,
        case when f.unit_id is null then '－' when f.unit_id = 'JPY' then '円' end as unit,
        case when f.is_nil then '－' else f.value end as value,
        f.decimals,
        f.is_nil,
        try_cast(c.period_start as date) as context_start,
        try_cast(coalesce(c.period_end, c.instant) as date) as context_end,
        null::varchar as csv_type,
        f.seq as row_seq,
        'xbrl' as source
    from {{ ref('raw_xbrl_facts') }} as f
    left join {{ ref('raw_xbrl_contexts') }} as c
        on f.doc_id = c.doc_id and f.context_id = c.context_id
    left join company_names as cn
        on f.doc_id = cn.doc_id and f.element_id = cn.element_id
    left join taxonomy_names as tn
        on f.element_id = tn.element_id
),

csv as (
    select
        f.doc_id,
        f.element_id,
        f.item_name,
        f.context_id,
        f.relative_year,
        f.consolidated_individual,
        f.period_instant,
        f.unit_id,
        f.unit,
        f.value,
        null::varchar as decimals,
        f.value = '－' as is_nil,
        null::date as context_start,
        null::date as context_end,
        f._csv_type as csv_type,
        f._row_seq as row_seq,
        'csv' as source
    from {{ ref('raw_financial_facts') }} as f
    -- 監査報告書 (jpaud) は type=1 では取っていないので、CSV でもそろえて外す
    where f._csv_type not like 'jpaud%'
        and f.doc_id not in (select doc_id from xbrl_docs)
),

facts as (
    select * from xbrl
    union all
    select * from csv
)

select
    f.doc_id,
    d.edinet_code,
    d.sec_code,
    d.corporate_number,
    d.filer_name,
    d.doc_type_code,
    d.period_end,
    d.submit_date,
    d.doc_description,
    f.element_id,
    f.item_name,
    f.context_id,
    f.relative_year,
    f.consolidated_individual,
    f.period_instant,
    f.unit_id,
    f.unit,
    f.value,
    try_cast(f.value as decimal(38, 4)) as numeric_value,
    f.decimals,
    f.is_nil,
    f.context_start,
    f.context_end,
    f.csv_type,
    f.row_seq,
    f.source
from facts as f
left join docs as d
    on f.doc_id = d.doc_id
